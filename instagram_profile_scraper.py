#!/usr/bin/env python3
"""
Batch Instagram profile metadata extractor.

Reads Instagram profile URLs or usernames from input.txt (one per line), then
exports username, raw profile picture URL, follower count, bio, and a few extra
fields to CSV and JSON.

This script is designed for small batches (default hard cap: 20 profiles).
It can optionally use an authenticated Instagram cookie jar to access richer
profile data such as `profile_pic_url_hd`, then falls back to public HTML
metadata when authenticated requests are unavailable or blocked.

Usage:
    python3 instagram_profile_scraper.py
    python3 instagram_profile_scraper.py --input input.txt --csv output.csv --json output.json
    python3 instagram_profile_scraper.py --cookies-file instagram_cookies.txt

Cookie file formats supported:
    - Netscape/Mozilla cookie jar exported from a browser extension
    - JSON array of cookie objects with `name` and `value`

You can also set:
    IG_COOKIES_FILE=/path/to/instagram_cookies.txt
"""

from __future__ import annotations

import argparse
import csv
import html
import http.cookiejar
import json
import mimetypes
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print(
        "Missing dependency: requests\n"
        "Install it with: pip3 install requests",
        file=sys.stderr,
    )
    raise SystemExit(1)


USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
COMMENT_PREFIXES = ("#", "//", ";")
DEFAULT_MAX_PROFILES = 20
DEFAULT_TIMEOUT = 20
DEFAULT_PROFILE_PICS_DIR = "profile_pics"
PROFILE_INFO_ENDPOINTS = (
    "https://www.instagram.com/api/v1/users/web_profile_info/",
    "https://i.instagram.com/api/v1/users/web_profile_info/",
)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0",
}
API_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "X-IG-App-ID": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass
class ProfileRow:
    input_value: str
    username: str | None
    full_name: str | None
    profile_url: str | None
    profile_pic_url: str | None
    profile_pic_path: str | None
    followers: int | None
    bio: str | None
    external_url: str | None
    is_private: bool | None
    is_verified: bool | None
    success: bool
    error: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Instagram profile metadata from input.txt")
    parser.add_argument("--input", default="input.txt", help="Input file with one Instagram URL/username per line")
    parser.add_argument("--csv", default="instagram_profiles.csv", help="CSV output path")
    parser.add_argument("--json", default="instagram_profiles.json", help="JSON output path")
    parser.add_argument(
        "--max-profiles",
        type=int,
        default=DEFAULT_MAX_PROFILES,
        help=f"Maximum number of profiles to process (default: {DEFAULT_MAX_PROFILES})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Delay in seconds between requests to reduce rate-limit risk",
    )
    parser.add_argument(
        "--profile-pics-dir",
        default=DEFAULT_PROFILE_PICS_DIR,
        help=f"Directory where profile pictures are saved (default: {DEFAULT_PROFILE_PICS_DIR})",
    )
    parser.add_argument(
        "--cookies-file",
        default=os.getenv("IG_COOKIES_FILE"),
        help="Optional path to Instagram cookies for authenticated requests and higher-quality profile pictures",
    )
    return parser.parse_args()


def clean_line(line: str) -> str:
    value = line.strip()
    if not value:
        return ""
    if value.startswith(COMMENT_PREFIXES):
        return ""
    return value


def extract_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Empty line")

    if raw.startswith("@"):  # allow @username
        raw = raw[1:]

    if USERNAME_RE.fullmatch(raw):
        return raw

    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower().replace("www.", "")
    if host not in {"instagram.com", "instagr.am"}:
        raise ValueError(f"Not an Instagram URL: {value}")

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"No username found in URL: {value}")

    first = parts[0]
    reserved = {
        "p",
        "reel",
        "reels",
        "stories",
        "explore",
        "accounts",
        "about",
        "developer",
        "directory",
        "tv",
    }
    if first in reserved:
        raise ValueError(f"URL does not look like a profile URL: {value}")

    if not USERNAME_RE.fullmatch(first):
        raise ValueError(f"Invalid Instagram username extracted from: {value}")

    return first


def load_inputs(path: Path, max_profiles: int) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    usernames: list[str] = []
    seen: set[str] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        value = clean_line(line)
        if not value:
            continue
        username = extract_username(value)
        key = username.lower()
        if key in seen:
            continue
        seen.add(key)
        usernames.append(username)

    if not usernames:
        raise ValueError("No valid Instagram usernames/URLs found in input file")

    if len(usernames) > max_profiles:
        raise ValueError(
            f"Found {len(usernames)} unique profiles, which exceeds the --max-profiles limit of {max_profiles}"
        )

    return usernames


def make_session(headers: dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.headers.update(headers)
    return session


def load_cookie_jar(path: Path) -> requests.cookies.RequestsCookieJar:
    if not path.exists():
        raise FileNotFoundError(f"Cookie file not found: {path}")

    raw = path.read_text(encoding="utf-8").strip()
    jar = requests.cookies.RequestsCookieJar()

    if raw.startswith("["):
        cookies = json.loads(raw)
        for item in cookies:
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue
            jar.set(
                name,
                value,
                domain=item.get("domain", ".instagram.com"),
                path=item.get("path", "/"),
            )
        return jar

    cookie_jar = http.cookiejar.MozillaCookieJar(str(path))
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    for cookie in cookie_jar:
        jar.set_cookie(cookie)
    return jar


def make_authenticated_session(cookies_file: str | None) -> requests.Session | None:
    if not cookies_file:
        return None

    session = make_session(API_HEADERS)
    session.cookies.update(load_cookie_jar(Path(cookies_file)))
    return session


def guess_image_extension(response: requests.Response, image_url: str) -> str:
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"

    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed in {".jpg", ".jpe", ".jpeg"}:
        return ".jpg"
    if guessed:
        return guessed
    return ".jpg"


def download_profile_picture(
    username: str,
    image_url: str,
    output_dir: Path,
    auth_session: requests.Session | None = None,
) -> Path:
    request_headers = {"Referer": f"https://www.instagram.com/{username}/"}
    response = (
        auth_session.get(image_url, headers=request_headers, timeout=DEFAULT_TIMEOUT)
        if auth_session
        else requests.get(
            image_url,
            headers={**BROWSER_HEADERS, **request_headers},
            timeout=DEFAULT_TIMEOUT,
        )
    )
    response.raise_for_status()

    output_dir.mkdir(parents=True, exist_ok=True)
    extension = guess_image_extension(response, image_url)
    output_path = output_dir / f"{username}{extension}"
    output_path.write_bytes(response.content)
    return output_path


def extract_meta_content(page_html: str, attr_name: str, attr_value: str) -> str | None:
    patterns = (
        rf'<meta[^>]+{attr_name}="{re.escape(attr_value)}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+{attr_name}="{re.escape(attr_value)}"',
    )
    for pattern in patterns:
        match = re.search(pattern, page_html)
        if match:
            return html.unescape(match.group(1))
    return None


def parse_count(raw_value: str | None) -> int | None:
    if not raw_value:
        return None

    compact = raw_value.strip().upper().replace(",", "")
    multiplier = 1
    if compact.endswith("K"):
        multiplier = 1000
        compact = compact[:-1]
    elif compact.endswith("M"):
        multiplier = 1000000
        compact = compact[:-1]

    try:
        return int(float(compact) * multiplier)
    except ValueError:
        return None


def build_profile_row(
    username: str,
    *,
    full_name: str | None,
    profile_pic_url: str | None,
    followers: int | None,
    bio: str | None,
    external_url: str | None,
    is_private: bool | None,
    is_verified: bool | None,
) -> ProfileRow:
    if not profile_pic_url:
        raise ValueError("Could not find profile picture URL")

    return ProfileRow(
        input_value=username,
        username=username,
        full_name=full_name,
        profile_url=f"https://www.instagram.com/{username}/",
        profile_pic_url=profile_pic_url,
        profile_pic_path=None,
        followers=followers,
        bio=bio,
        external_url=external_url,
        is_private=is_private,
        is_verified=is_verified,
        success=True,
        error=None,
    )


def fetch_profile_from_api(auth_session: requests.Session, username: str) -> ProfileRow:
    attempted_statuses: list[str] = []

    for endpoint in PROFILE_INFO_ENDPOINTS:
        try:
            response = auth_session.get(
                endpoint,
                params={"username": username},
                headers={"Referer": f"https://www.instagram.com/{username}/"},
                timeout=DEFAULT_TIMEOUT,
            )
            attempted_statuses.append(f"{endpoint} -> {response.status_code}")
            if response.status_code == 404:
                raise ValueError("Profile not found")
            if response.status_code != 200:
                continue

            user = response.json().get("data", {}).get("user")
            if not user:
                continue

            return build_profile_row(
                username=user.get("username") or username,
                full_name=user.get("full_name") or None,
                profile_pic_url=user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
                followers=user.get("edge_followed_by", {}).get("count"),
                bio=user.get("biography") or None,
                external_url=user.get("external_url") or None,
                is_private=user.get("is_private"),
                is_verified=user.get("is_verified"),
            )
        except requests.RequestException as exc:
            attempted_statuses.append(f"{endpoint} -> request error: {exc}")

    raise ValueError(f"Authenticated API did not return profile data ({'; '.join(attempted_statuses)})")


def fetch_profile_from_html(username: str) -> ProfileRow:
    try:
        response = requests.get(
            f"https://www.instagram.com/{username}/",
            headers={**BROWSER_HEADERS, "Referer": "https://www.instagram.com/"},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code == 404:
            raise ValueError("Profile not found")
        response.raise_for_status()

        page_html = response.text
        profile_pic_url = extract_meta_content(page_html, "property", "og:image")
        og_description = extract_meta_content(page_html, "property", "og:description")
        page_description = extract_meta_content(page_html, "name", "description")
        og_title = extract_meta_content(page_html, "property", "og:title")

        followers = None
        if og_description:
            followers_match = re.search(r"^([0-9.,]+[KM]?) Followers", og_description, re.IGNORECASE)
            followers = parse_count(followers_match.group(1) if followers_match else None)

        full_name = None
        if og_title:
            title_match = re.match(r"(.+?) \(@[A-Za-z0-9._]+\) • Instagram photos and videos", og_title)
            if title_match:
                full_name = title_match.group(1)

        bio = None
        if page_description:
            bio_match = re.search(r'on Instagram: "(.+)"$', page_description, re.DOTALL)
            if bio_match:
                bio = bio_match.group(1)

        return build_profile_row(
            username=username,
            full_name=full_name,
            profile_pic_url=profile_pic_url,
            followers=followers,
            bio=bio,
            external_url=None,
            is_private="This Account is Private" in page_html,
            is_verified=" verified " in page_html.lower() if "verified" in page_html.lower() else None,
        )
    except Exception as exc:
        return ProfileRow(
            input_value=username,
            username=None,
            full_name=None,
            profile_url=None,
            profile_pic_url=None,
            profile_pic_path=None,
            followers=None,
            bio=None,
            external_url=None,
            is_private=None,
            is_verified=None,
            success=False,
            error=str(exc),
        )


def fetch_profile(username: str, auth_session: requests.Session | None = None) -> ProfileRow:
    if auth_session is not None:
        try:
            return fetch_profile_from_api(auth_session, username)
        except Exception as exc:
            html_row = fetch_profile_from_html(username)
            if html_row.success:
                html_row.error = f"Authenticated fetch failed, used public fallback: {exc}"
                return html_row

            html_row.error = f"Authenticated fetch failed: {exc}; public fallback failed: {html_row.error}"
            return html_row

    return fetch_profile_from_html(username)


def write_csv(rows: Iterable[ProfileRow], path: Path) -> None:
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_json(rows: Iterable[ProfileRow], path: Path) -> None:
    payload = [asdict(row) for row in rows]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    csv_path = Path(args.csv)
    json_path = Path(args.json)
    profile_pics_dir = Path(args.profile_pics_dir)
    auth_session = None

    try:
        usernames = load_inputs(input_path, args.max_profiles)
    except Exception as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 1

    if args.cookies_file:
        try:
            auth_session = make_authenticated_session(args.cookies_file)
            print(f"Loaded Instagram cookies from {Path(args.cookies_file).resolve()}")
        except Exception as exc:
            print(f"Cookie warning: {exc}", file=sys.stderr)
            print("Falling back to public profile HTML only.", file=sys.stderr)

    rows: List[ProfileRow] = []
    total = len(usernames)
    for index, username in enumerate(usernames, start=1):
        row = fetch_profile(username, auth_session=auth_session)
        if row.success and row.username and row.profile_pic_url:
            try:
                picture_path = download_profile_picture(
                    row.username,
                    row.profile_pic_url,
                    profile_pics_dir,
                    auth_session=auth_session,
                )
                row.profile_pic_path = str(picture_path.resolve())
            except Exception as exc:
                row.success = False
                row.error = f"Profile loaded, but picture download failed: {exc}"
        rows.append(row)
        status = "ok" if row.success else "failed"
        print(f"[{index}/{total}] {username}: {status}")
        if index < total:
            time.sleep(max(args.delay, 0))

    if not rows:
        print("No rows produced.", file=sys.stderr)
        return 1

    write_csv(rows, csv_path)
    write_json(rows, json_path)

    ok_count = sum(1 for row in rows if row.success)
    print(f"\nDone. {ok_count}/{len(rows)} profiles fetched successfully.")
    print(f"CSV:  {csv_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    return 0 if ok_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
