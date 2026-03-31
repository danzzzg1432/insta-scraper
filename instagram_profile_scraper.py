#!/usr/bin/env python3
"""
Batch Instagram profile metadata extractor.

Reads Instagram profile URLs or usernames from input.txt (one per line), then
exports username, raw profile picture URL, follower count, bio, and a few extra
fields to CSV and JSON.

This script is designed for small batches (default hard cap: 20 profiles).
It uses Instagram's current web profile endpoint with browser-like headers.

Usage:
    python3 instagram_profile_scraper.py
    python3 instagram_profile_scraper.py --input input.txt --csv output.csv --json output.json

"""

from __future__ import annotations

import argparse
import csv
import json
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
PROFILE_ENDPOINTS = (
    "https://www.instagram.com/api/v1/users/web_profile_info/",
    "https://i.instagram.com/api/v1/users/web_profile_info/",
)


@dataclass
class ProfileRow:
    input_value: str
    username: str | None
    full_name: str | None
    profile_url: str | None
    profile_pic_url: str | None
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


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
        }
    )
    return session


def fetch_profile(session: requests.Session, username: str) -> ProfileRow:
    try:
        payload = None
        last_status = None

        for endpoint in PROFILE_ENDPOINTS:
            response = session.get(
                endpoint,
                params={"username": username},
                headers={"Referer": f"https://www.instagram.com/{username}/"},
                timeout=DEFAULT_TIMEOUT,
            )
            last_status = response.status_code
            if response.status_code == 404:
                raise ValueError("Profile not found")
            response.raise_for_status()

            data = response.json()
            user = data.get("data", {}).get("user")
            if user:
                payload = user
                break

        if not payload:
            raise ValueError(f"No profile data returned (status {last_status})")

        return ProfileRow(
            input_value=username,
            username=payload.get("username"),
            full_name=payload.get("full_name") or None,
            profile_url=f"https://www.instagram.com/{payload.get('username') or username}/",
            profile_pic_url=payload.get("profile_pic_url_hd") or payload.get("profile_pic_url"),
            followers=payload.get("edge_followed_by", {}).get("count"),
            bio=payload.get("biography") or None,
            external_url=payload.get("external_url") or None,
            is_private=payload.get("is_private"),
            is_verified=payload.get("is_verified"),
            success=True,
            error=None,
        )
    except Exception as exc:
        return ProfileRow(
            input_value=username,
            username=None,
            full_name=None,
            profile_url=None,
            profile_pic_url=None,
            followers=None,
            bio=None,
            external_url=None,
            is_private=None,
            is_verified=None,
            success=False,
            error=str(exc),
        )


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

    try:
        usernames = load_inputs(input_path, args.max_profiles)
    except Exception as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 1

    session = make_session()

    rows: List[ProfileRow] = []
    total = len(usernames)
    for index, username in enumerate(usernames, start=1):
        row = fetch_profile(session, username)
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
