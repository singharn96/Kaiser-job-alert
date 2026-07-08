#!/usr/bin/env python3
"""
Kaiser Permanente NorCal RN Job Alert
-------------------------------------
Checks Kaiser's careers site for Registered Nurse postings in Northern
California, compares them against a locally-stored history, and sends a
Telegram message for any NEW posting.

Runs unattended on GitHub Actions (see .github/workflows/job-check.yml).
Everything it uses is free: GitHub Actions + Telegram Bot API.

Environment variables required (set as GitHub repo secrets):
    TELEGRAM_BOT_TOKEN   token from @BotFather
    TELEGRAM_CHAT_ID     your numeric id from @userinfobot

Optional environment variables:
    KEYWORDS             comma-separated search terms
                         (default: "registered nurse,RN")
    MAX_PAGES            how many result pages to walk per term (default 5)
"""

import json
import os
import re
import sys
import time
import html
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE = "https://www.kaiserpermanentejobs.org"
RESULTS_ENDPOINT = BASE + "/search-jobs/results"

SEEN_FILE = Path(__file__).with_name("seen_jobs.json")

KEYWORDS = [
    k.strip()
    for k in os.environ.get("KEYWORDS", "registered nurse,RN").split(",")
    if k.strip()
]
MAX_PAGES = int(os.environ.get("MAX_PAGES", "5"))
RECORDS_PER_PAGE = 50

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# --------------------------------------------------------------------------- #
# Northern California location filter
# --------------------------------------------------------------------------- #
# We match on the posting's location text. A city must appear AND the state
# must read as California (Kaiser writes "California" or "CA"). We also keep
# an explicit exclude list so Southern California / out-of-state look-alikes
# (Richmond VA vs Richmond CA, Fontana in SoCal, etc.) don't slip through.

NORCAL_CITIES = {
    # Greater Sacramento
    "sacramento", "south sacramento", "roseville", "rancho cordova",
    "elk grove", "folsom", "citrus heights", "carmichael", "davis",
    "woodland", "west sacramento", "rocklin", "lincoln", "auburn",
    "yuba city", "marysville", "grass valley",
    # Napa / Solano / North Bay
    "vacaville", "fairfield", "vallejo", "napa", "petaluma", "santa rosa",
    "rohnert park", "novato", "san rafael",
    # East Bay
    "oakland", "richmond", "san leandro", "hayward", "fremont", "union city",
    "walnut creek", "antioch", "pittsburg", "martinez", "pleasanton",
    "dublin", "livermore", "berkeley", "emeryville", "san ramon", "brentwood",
    # San Francisco / Peninsula
    "san francisco", "south san francisco", "daly city", "redwood city",
    "san mateo", "burlingame", "millbrae", "san bruno",
    # South Bay
    "san jose", "santa clara", "sunnyvale", "mountain view", "milpitas",
    "campbell", "gilroy", "morgan hill", "los gatos", "cupertino",
    # Central Valley (northern) & coast
    "stockton", "modesto", "manteca", "tracy", "santa cruz", "watsonville",
    "fresno", "clovis", "madera", "merced", "turlock",
}

# Cities to reject outright (Southern CA + common out-of-state collisions).
EXCLUDE_CITIES = {
    "fontana", "ontario", "riverside", "san bernardino", "los angeles",
    "irvine", "anaheim", "downey", "baldwin park", "harbor city",
    "woodland hills", "panorama city", "west los angeles", "san diego",
    "moreno valley", "murrieta", "santa clarita", "bakersfield",
    "kern", "orange county", "long beach", "pasadena",
}

STATE_OK = re.compile(r"\b(california|,?\s*ca)\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Fetching & parsing
# --------------------------------------------------------------------------- #

def fetch_page(keyword: str, page: int) -> str:
    """Return the raw HTML fragment of one results page for a keyword."""
    params = {
        "Keywords": keyword,
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "SortCriteria": 1,      # 1 = date
        "SortDirection": 1,     # 1 = newest first
        "SearchType": 5,
    }
    resp = requests.get(
        RESULTS_ENDPOINT, params=params, headers=HEADERS, timeout=30
    )
    resp.raise_for_status()
    # Radancy returns JSON: {"results": "<html>", "totalHits": N, ...}
    try:
        data = resp.json()
        return data.get("results", "") or ""
    except ValueError:
        # Fallback: some deployments return the HTML directly.
        return resp.text


def parse_jobs(fragment: str) -> list[dict]:
    """Extract job dicts from a results HTML fragment."""
    soup = BeautifulSoup(fragment, "html.parser")
    jobs = []
    # Radancy renders each result as an <a> wrapping the title/location.
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/job/" not in href:
            continue
        title_el = a.find(["h2", "h3"])
        title = (title_el.get_text(strip=True) if title_el
                 else a.get_text(" ", strip=True))
        loc_el = a.select_one(".job-location, .location, [class*='location']")
        location = loc_el.get_text(strip=True) if loc_el else ""
        url = href if href.startswith("http") else BASE + href
        job_id = href.rstrip("/").split("/")[-1]
        if not title:
            continue
        jobs.append(
            {
                "id": job_id,
                "title": html.unescape(title),
                "location": html.unescape(location),
                "url": url,
            }
        )
    return jobs


def is_rn(job: dict) -> bool:
    """Keep only Registered Nurse roles (title contains RN / Registered Nurse)."""
    t = job["title"].lower()
    if "registered nurse" in t:
        return True
    # Match RN as a standalone token so "BuRN unit" etc. don't false-positive.
    return re.search(r"\brn\b", t) is not None


def is_norcal(job: dict) -> bool:
    loc = job["location"].lower()
    if not loc:
        return False
    if any(bad in loc for bad in EXCLUDE_CITIES):
        return False
    if not STATE_OK.search(job["location"]):
        return False
    return any(city in loc for city in NORCAL_CITIES)


def collect_jobs() -> dict[str, dict]:
    """Walk all keywords/pages and return {job_id: job} for NorCal RN roles."""
    found: dict[str, dict] = {}
    for keyword in KEYWORDS:
        for page in range(1, MAX_PAGES + 1):
            try:
                fragment = fetch_page(keyword, page)
            except Exception as exc:  # network / HTTP errors
                print(f"[warn] fetch failed ({keyword} p{page}): {exc}")
                break
            page_jobs = parse_jobs(fragment)
            if not page_jobs:
                # No more results for this keyword.
                break
            kept = 0
            for job in page_jobs:
                if is_rn(job) and is_norcal(job):
                    found[job["id"]] = job
                    kept += 1
            print(f"[info] {keyword!r} page {page}: "
                  f"{len(page_jobs)} parsed, {kept} NorCal RN kept")
            time.sleep(1)  # be polite
    return found


# --------------------------------------------------------------------------- #
# History (seen_jobs.json)
# --------------------------------------------------------------------------- #

def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #

def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[error] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[error] Telegram send failed: {resp.status_code} {resp.text}")
    else:
        print("[info] Telegram message sent.")


def notify_new_jobs(new_jobs: list[dict]) -> None:
    # Telegram caps messages at ~4096 chars; chunk into batches.
    header = f"🏥 <b>{len(new_jobs)} new Kaiser NorCal RN posting" \
             f"{'s' if len(new_jobs) != 1 else ''}</b>\n\n"
    blocks = []
    for j in new_jobs:
        loc = f" — {html.escape(j['location'])}" if j["location"] else ""
        blocks.append(
            f"• <a href=\"{html.escape(j['url'])}\">"
            f"{html.escape(j['title'])}</a>{loc}"
        )
    msg = header
    for block in blocks:
        if len(msg) + len(block) + 2 > 3800:
            send_telegram(msg)
            msg = ""
        msg += block + "\n\n"
    if msg.strip():
        send_telegram(msg)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    print(f"[info] keywords={KEYWORDS} max_pages={MAX_PAGES}")
    current = collect_jobs()
    print(f"[info] total NorCal RN roles found: {len(current)}")

    seen = load_seen()
    first_run = len(seen) == 0

    new_ids = [jid for jid in current if jid not in seen]

    # Merge current into history (keep old entries so re-listings don't re-alert).
    for jid, job in current.items():
        seen[jid] = job
    save_seen(seen)

    if first_run:
        msg = (
            "✅ <b>Kaiser NorCal RN alert is live.</b>\n\n"
            f"Baseline captured: {len(current)} current RN postings.\n"
            "From now on you'll only be pinged about <b>new</b> ones."
        )
        send_telegram(msg)
        print("[info] first run — baseline saved, no per-job alerts.")
        return 0

    if not new_ids:
        print("[info] no new postings this run.")
        return 0

    new_jobs = [current[jid] for jid in new_ids]
    print(f"[info] {len(new_jobs)} new posting(s) — alerting.")
    notify_new_jobs(new_jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
