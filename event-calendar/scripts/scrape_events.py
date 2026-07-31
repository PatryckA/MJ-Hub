"""
Scrape organiser websites for candidate multi-day events and merge them into
data/events.json as review-pending entries (ai_sourced: true, needs_review: true).

This is a STARTING POINT, not a finished pipeline. Organiser sites vary wildly in
structure (some are Facebook groups, some are Instagram, some are plain HTML with
no structured data at all) so this script focuses on the sites that expose enough
structure to be worth automating, and leaves the rest for manual/Google-Form entry.

How it works
------------
1. Reads `sources.json` — a list of organiser domains/URLs to check (seeded from
   the websites already in data/events.json, deduplicated to their root domain).
2. For each source, tries in order:
     a. An iCal/.ics feed if the site exposes one (best case - structured & reliable)
     b. Schema.org "Event" JSON-LD embedded in the page HTML (common on modern sites)
     c. A conservative regex pass over the page text for date-range patterns near
        the words that matter (weekender, champs, festival, etc.) — this is the
        weakest signal and always gets needs_review: true
3. Any candidate event that doesn't already exist (matched by name + start_date)
   is appended to events.json with ai_sourced: true, needs_review: true, and a
   `source_url` pointing at exactly where it was found, so a human can verify fast.

This script deliberately does NOT auto-publish anything. Nothing produced here
should reach the live site until a human has looked at it (see needs_review).

Run locally:
    pip install requests beautifulsoup4 python-dateutil
    python scripts/scrape_events.py

Intended to run on a schedule via .github/workflows/scrape.yml, which opens a
pull request with any new candidate events rather than pushing straight to main —
so the review step is enforced by the PR, not just the data flag.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

ROOT = Path(__file__).parent.parent
EVENTS_PATH = ROOT / "data" / "events.json"
SOURCES_PATH = ROOT / "data" / "sources.json"

HEADERS = {"User-Agent": "OnTheFloorEventBot/0.1 (+https://github.com/) - community calendar, contact organiser to opt out"}

DATE_RANGE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s*[-–—to]{1,3}\s*(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)

KEYWORDS = ["weekender", "championship", "champs", "festival", "dance holiday", "cruise", "escape"]


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def root_domain(url):
    try:
        netloc = urlparse(url).netloc
        return netloc.replace("www.", "")
    except Exception:
        return url


def build_sources_from_events(events):
    """Seed sources.json from the domains already present in events.json."""
    domains = sorted({root_domain(e["website"]) for e in events if e.get("website", "").startswith("http")})
    return [{"domain": d, "notes": "seeded from existing event list"} for d in domains]


def try_ical(base_url):
    for path in ["/events.ics", "/calendar.ics", "/feed/events.ics"]:
        try:
            r = requests.get(urljoin(base_url, path), headers=HEADERS, timeout=10)
            if r.ok and "BEGIN:VCALENDAR" in r.text:
                return r.text
        except requests.RequestException:
            continue
    return None


def try_jsonld_events(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("Event", "Festival"):
                candidates.append({
                    "name": item.get("name"),
                    "start_date": item.get("startDate", "")[:10],
                    "end_date": item.get("endDate", item.get("startDate", ""))[:10],
                    "website": item.get("url", page_url),
                    "source_url": page_url,
                    "confidence": "high",
                })
    return candidates


def try_regex_dates(html, page_url):
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if not any(k in text.lower() for k in KEYWORDS):
        return []
    candidates = []
    for m in DATE_RANGE_RE.finditer(text):
        try:
            start_day, end_day, month, year = m.groups()
            start = dateparser.parse(f"{start_day} {month} {year}").date().isoformat()
            end = dateparser.parse(f"{end_day} {month} {year}").date().isoformat()
            candidates.append({
                "name": None,  # low confidence - needs a human to name it
                "start_date": start,
                "end_date": end,
                "website": page_url,
                "source_url": page_url,
                "confidence": "low",
            })
        except (ValueError, OverflowError):
            continue
    return candidates


def scan_source(domain):
    base_url = f"https://{domain}"
    found = []
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  skip {domain}: {e}")
        return found

    ical = try_ical(base_url)
    if ical:
        print(f"  {domain}: found an .ics feed (parse with icalendar lib - TODO wire up)")

    found += try_jsonld_events(r.text, base_url)
    found += try_regex_dates(r.text, base_url)
    return found


def merge_candidates(events, candidates):
    existing_keys = {(e["name"], e["start_date"]) for e in events}
    new_events = []
    for c in candidates:
        if not c.get("start_date"):
            continue
        key = (c.get("name"), c["start_date"])
        if key in existing_keys or not c.get("name"):
            continue
        new_events.append({
            "id": re.sub(r"[^a-z0-9]+", "-", f"{c.get('name','unknown')}-{c['start_date']}".lower()).strip("-"),
            "name": c.get("name") or "Unnamed event (needs review)",
            "city": "",
            "country": "",
            "region": "Other",
            "types": ["Other"],
            "status": "tbc",
            "start_date": c["start_date"],
            "end_date": c.get("end_date", c["start_date"]),
            "multiday": c.get("end_date", c["start_date"]) > c["start_date"],
            "website": c.get("website", ""),
            "ai_sourced": True,
            "needs_review": True,
            "source": "auto-scraped",
            "source_url": c.get("source_url", ""),
            "confidence": c.get("confidence", "low"),
        })
    return new_events


def main():
    events = load_json(EVENTS_PATH, [])
    sources = load_json(SOURCES_PATH, None)
    if sources is None:
        sources = build_sources_from_events(events)
        SOURCES_PATH.write_text(json.dumps(sources, indent=2))
        print(f"Seeded {SOURCES_PATH} with {len(sources)} domains from existing events.")

    all_candidates = []
    for s in sources:
        print(f"Scanning {s['domain']}...")
        all_candidates += scan_source(s["domain"])

    new_events = merge_candidates(events, all_candidates)
    if not new_events:
        print("No new candidate events found.")
        return

    events.extend(new_events)
    events.sort(key=lambda e: e["start_date"])
    EVENTS_PATH.write_text(json.dumps(events, indent=2))
    print(f"Added {len(new_events)} candidate event(s), all flagged needs_review: true.")


if __name__ == "__main__":
    sys.exit(main())
