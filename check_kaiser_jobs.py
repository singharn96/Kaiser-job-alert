#!/usr/bin/env python3
"""
Kaiser Permanente NorCal Nursing Job Alert
------------------------------------------
Reads Kaiser's public jobs RSS feed, keeps postings in Northern California
whose title matches your keywords (Staff Nurse, Emergency, Short Hour / SH),
compares them against a stored history, and sends a Telegram message for any
NEW posting.

Runs unattended on GitHub Actions (see .github/workflows/job-check.yml).
Everything it uses is free: GitHub Actions + Telegram Bot API.

Why the RSS feed? Kaiser's careers site (Radancy) renders its search results
in the browser via a session-based call that a plain script can't replay. The
RSS feed at /rss is a stable, public endpoint listing every posting with its
title, location, link and post date — exactly what a job alert needs.

Environment variables required (set as GitHub repo secrets):
    TELEGRAM_BOT_TOKEN   token from @BotFather
    TELEGRAM_CHAT_ID     your numeric id from @userinfobot

Optional environment variables:
    KEYWORDS   comma-separated title terms to match
               (default: "staff nurse,emergency,short hour,SH")
"""

import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

FEED_URL = "https://www.kaiserpermanentejobs.org/rss"
SEEN_FILE = Path(__file__).with_name("seen_jobs.json")

KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("KEYWORDS", "staff nurse,emergency,short hour,SH").split(",")
    if k.strip()
]

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# --------------------------------------------------------------------------- #
# Northern California location filter
# --------------------------------------------------------------------------- #
# Each RSS title ends with "(City, State, Country)". We read that city/state,
# keep it only if the state is California, the city is a NorCal city, and it's
# not one of the Southern-California / out-of-state look-alikes.

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

EXCLUDE_CITIES = {
    "fontana", "ontario", "riverside", "san bernardino", "los angeles",
    "irvine", "anaheim", "downey", "baldwin park", "harbor city",
    "woodland hills", "panorama city", "west los angeles", "san diego",
    "moreno valley", "murrieta", "santa clarita", "bakersfield",
    "long beach", "pasadena",
}

STATE_OK = re.compile(r"\bcalifornia\b", re.IGNORECASE)
LOC_PAREN = re.compile(r"\(([^)]+)\)\s*$")


# --------------------------------------------------------------------------- #
# Fetch & parse the feed
# --------------------------------------------------------------------------- #

def fetch_feed() -> str:
    resp = requests.get(FEED_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.text


def parse_feed(xml_text: str) -> list[dict]:
    """Return a list of {id, title, location, url, posted} from the RSS."""
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.iter("item"):
        def text(tag):
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        raw_title = html.unescape(text("title"))
        link = text("link")
        guid = text("guid") or link
        posted = text("pubDate")

        loc = ""
        m = LOC_PAREN.search(raw_title)
        if m:
            loc = m.group(1).strip()
        # Clean title = drop the trailing " - (Location)" for display.
        title = LOC_PAREN.sub("", raw_title).rstrip(" -").strip()

        jobs.append(
            {
                "id": guid.rstrip("/").split("/")[-1] or guid,
                "title": title,
                "location": loc,
                "url": link,
                "posted": posted,
            }
        )
    return jobs


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #

def title_matches(title: str) -> bool:
    """True if the title contains any configured keyword.

    Short alphabetic keywords (<= 3 chars, e.g. "SH") are matched as whole
    words so they don't fire on substrings; longer keywords match anywhere.
    """
    low = title.lower()
    for kw in KEYWORDS:
        if len(kw) <= 3 and kw.isalpha():
            if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", low):
                return True
        elif kw in low:
            return True
    return False


def is_norcal(location: str) -> bool:
    loc = location.lower()
    if not loc:
        return False
    if any(bad in loc for bad in EXCLUDE_CITIES):
        return False
    if not STATE_OK.search(location):
        return False
    city = loc.split(",")[0].strip()
    # match on the leading city token, but also allow city mentioned anywhere
    return city in NORCAL_CITIES or any(c in loc for c in NORCAL_CITIES)


def collect_jobs() -> dict[str, dict]:
    xml_text = fetch_feed()
    all_jobs = parse_feed(xml_text)
    print(f"[info] feed items: {len(all_jobs)}")
    found: dict[str, dict] = {}
    for job in all_jobs:
        if not is_norcal(job["location"]):
            continue
        if not title_matches(job["title"]):
            continue
        found[job["id"]] = job
    print(f"[info] NorCal matches for {KEYWORDS}: {len(found)}")
    return found


# --------------------------------------------------------------------------- #
# History (seen_jobs.json)
# --------------------------------------------------------------------------- #

import json  # noqa: E402


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
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[error] Telegram send failed: {resp.status_code} {resp.text}")
    else:
        print("[info] Telegram message sent.")


def notify_new_jobs(new_jobs: list[dict]) -> None:
    header = (
        f"🏥 <b>{len(new_jobs)} new Kaiser NorCal posting"
        f"{'s' if len(new_jobs) != 1 else ''}</b>\n\n"
    )
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
    print(f"[info] keywords={KEYWORDS}")
    current = collect_jobs()

    seen = load_seen()
    first_run = len(seen) == 0
    new_ids = [jid for jid in current if jid not in seen]

    for jid, job in current.items():
        seen[jid] = job
    save_seen(seen)

    if first_run:
        send_telegram(
            "✅ <b>Kaiser NorCal alert is live.</b>\n\n"
            f"Baseline captured: {len(current)} current postings matching "
            f"{', '.join(KEYWORDS)}.\n"
            "From now on you'll only be pinged about <b>new</b> ones."
        )
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
