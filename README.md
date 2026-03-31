# insta scraper

basically this script takes a list of instagram usernames / profile urls from `input.txt`, hits instagram, and dumps profile data into:

- `instagram_profiles.csv`
- `instagram_profiles.json`
- `profile_pics/`

it also tries to save each pfp locally as:

- `profile_pics/<username>.jpg`

## what it grabs rn

- username
- full name
- profile url
- profile pic url
- local saved profile pic path
- follower count
- bio
- external url
- private / verified flags
- success / error state

## how it works

there are basically 2 paths:

1. public fallback path
2. authenticated cookie path

if you run it with no cookies, it scrapes the public profile html and grabs whatever is visible there.

if you run it with cookies, it tries instagram's richer web profile api first, which is way better because it can return stuff like `profile_pic_url_hd` or at least a noticeably better profile pic url than the public page.

if the authenticated request gets blocked / rate limited / whatever, it falls back to the public html path instead of just dying.

## install

```bash
pip3 install -r requirements.txt
```

or just:

```bash
pip3 install requests
```

## input format

put one username or instagram profile url per line in `input.txt`

all of these work:

```txt
realinstaaccount
@realinstaaccount
https://www.instagram.com/realinstaaccount/
instagram.com/realinstaaccount
```

comments are ignored if the line starts with:

```txt
#
//
;
```

duplicate usernames also get deduped automatically.

## normal run

```bash
python3 instagram_profile_scraper.py
```

## run with cookies for better pfps

```bash
python3 instagram_profile_scraper.py --cookies-file instagram_cookies.txt
```

or:

```bash
IG_COOKIES_FILE=instagram_cookies.txt python3 instagram_profile_scraper.py
```

## cookie stuff

if you want the better chance of getting higher quality profile pics, use cookies.

the script supports:

- Netscape / Mozilla `cookies.txt`
- JSON cookie exports with `name` + `value`

for chromium based browsers, easiest way is:

1. install a cookie export extension
2. open `https://www.instagram.com/` while logged in
3. export cookies for instagram
4. save that as `instagram_cookies.txt`
5. run the script with `--cookies-file instagram_cookies.txt`

cookie files are sensitive btw. they basically act like your logged-in session.

already ignored in `.gitignore`:

- `instagram_cookies.txt`
- `instagram_cookies.json`
- `cookies.txt`
- `*.cookies`

still just don't upload them anywhere weird.

## cli flags

```bash
python3 instagram_profile_scraper.py \
  --input input.txt \
  --csv instagram_profiles.csv \
  --json instagram_profiles.json \
  --profile-pics-dir profile_pics \
  --max-profiles 20 \
  --delay 1.5 \
  --cookies-file instagram_cookies.txt
```

quick breakdown:

- `--input`: source file with usernames / urls
- `--csv`: csv output path
- `--json`: json output path
- `--profile-pics-dir`: where downloaded pfps go
- `--max-profiles`: safety cap so you don't accidentally blast a huge list
- `--delay`: delay between usernames
- `--cookies-file`: optional authenticated cookie export

## output notes

`instagram_profiles.json` is the nicer one if you wanna inspect stuff programmatically.

`instagram_profiles.csv` is good if you wanna open it fast in sheets / excel / whatever.

`profile_pic_url` is the raw image url that got used for that row.

`profile_pic_path` is where the local file got saved.

if cookies worked, `profile_pic_url` will usually be better than the public fallback one.
in earlier runs the public path was giving stuff closer to `s100x100`, while the authenticated one was giving `s320x320`.

## known limitations

- instagram changes things constantly
- public endpoints get rate limited randomly
- cookie-auth helps, but isn't magic
- profile pic quality depends on what instagram actually returns
- file extension is guessed from the response / url, but most pfps end up as `.jpg`
- this is built for small batches, not big scraping jobs

## if something breaks

usual stuff to try:

1. make sure your cookies are fresh and you're still logged into instagram in browser
2. re-export `instagram_cookies.txt`
3. slow it down a bit with a bigger `--delay`
4. try again later if instagram is throwing temp blocks
5. check the `error` field in `instagram_profiles.json`
6. just get gpt or claude to fix it lol

## current project files

- `instagram_profile_scraper.py`: main script
- `input.txt`: your usernames / urls
- `instagram_profiles.csv`: csv output
- `instagram_profiles.json`: json output
- `profile_pics/`: downloaded pfps
- `instagram_cookies.txt`: optional local cookie file, should stay private

## quick example flow

```bash
pip3 install -r requirements.txt
python3 instagram_profile_scraper.py --cookies-file instagram_cookies.txt
```

then check:

- `instagram_profiles.json`
- `profile_pics/`

that's basically it
