"""
Scrape organiser websites (and discover new ones) for candidate multi-day and
one-day-championship Ceroc/Modern Jive events, and write each genuinely new
find as its own file under data/candidates/ for individual PR review.

This is v2 of the original scraper. Key differences from v1:

- Candidates are written one-per-file to data/candidates/<id>.json instead of
  being appended directly to data/events.json. This lets the GitHub Action
  open one PR per event, so you can approve/deny them individually without
  merge conflicts between PRs.
- Source discovery is no longer limited to a fixed domain list: a DuckDuckGo
  search pass (no API key required) looks for new organiser domains each run,
  filtered by a keyword check (the page must prominently mention "ceroc" or
  "modern jive") before being trusted.
- Sources that repeatedly return nothing get a "strike"; after enough
  consecutive strikes they're dropped from sources.json automatically.
- Geocoding (lat/lon) happens here via Nominatim (OpenStreetMap, free, no
  key), rate-limited to respect their usage policy. A geocoding failure does
  NOT block the event from being added — it's added with lat/lon as null.
- A rejected.json denylist is checked before proposing any candidate, so
  events you've explicitly denied don't keep coming back.
- One-day events (e.g. the International Open Modern Jive Championships) are
  allowed through with multiday: false — the site's display logic is a
  separate concern from data collection.

Run locally:
    pip install requests beautifulsoup4 python-dateutil duckduckgo-search

Intended to run on a schedule via .github/workflows/scrape.yml.
"""

import json
import re
import sys
import time
import difflib
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None  # search step degrades gracefully if the package isn't installed

ROOT = Path(__file__).parent.parent
EVENTS_PATH = ROOT / "data" / "events.json"
SOURCES_PATH = ROOT / "data" / "sources.json"
REJECTED_PATH = ROOT / "data" / "rejected.json"
CANDIDATES_DIR = ROOT / "data" / "candidates"

HEADERS = {
    "User-Agent": "OnTheFloorEventBot/0.2 (+https://github.com/PatryckA/MJ-Hub) "
                  "- community calendar, contact organiser to opt out"
}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "OnTheFloorEventBot/0.2 (contact via GitHub repo issues)"}
NOMINATIM_DELAY_SECONDS = 1.1  # Nominatim usage policy: max 1 req/sec, be polite

SEARCH_QUERIES = [
    "Ceroc Escape",
    "Ceroc Weekender",
    "Modern Jive Weekender",
    "Ceroc Championships",
    "Modern Jive Championships",
    "Ceroc Dance Holiday",
    "Modern Jive Dance Holiday",
    "Ceroc Cruise",
    "Modern Jive Cruise",
]
KEYWORD_CHECK = ["ceroc", "modern jive"]

STRIKE_LIMIT = 104  # ~24 months of consecutive weekly zero-result runs

DATE_RANGE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s*[-–—to]{1,3}\s*(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)
SINGLE_DATE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
    re.IGNORECASE,
)

KEYWORDS = [
    "weekender", "championship", "champs", "festival", "dance holiday",
    "cruise", "escape", "open", "international",
]

FUZZY_NAME_THRESHOLD = 0.7
DATE_WINDOW_DAYS = 5


# ---------------------------------------------------------------- utilities

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def slugify(*parts):
    text = "-".join(str(p) for p in parts if p)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text


def root_domain(url):
    try:
        netloc = urlparse(url).netloc
        return netloc.replace("www.", "")
    except Exception:
        return url


def parse_date_safe(value):
    try:
        return dateparser.parse(value).date()
    except (ValueError, OverflowError, TypeError):
        return None


# ------------------------------------------------------------- fuzzy match

def _normalize_name(name):
    name = name.lower()
    name = re.sub(r"\([^)]*\)", " ", name)  # drop parenthetical abbreviations like "(WMJC)"
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def name_similarity(a, b):
    if not a or not b:
        return 0.0
    na, nb = _normalize_name(a), _normalize_name(b)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()

    # word-containment check: catches cases like "World Modern Jive Champs"
    # vs "World Modern Jive Championships" where whole-string ratio is
    # dragged down by suffix/length differences but the words themselves
    # substantially overlap.
    words_a, words_b = set(na.split()), set(nb.split())
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    containment = len(shorter & longer) / len(shorter) if shorter else 0.0

    return max(ratio, containment)


def dates_close(date_a, date_b, window_days=DATE_WINDOW_DAYS):
    da, db = parse_date_safe(date_a), parse_date_safe(date_b)
    if not da or not db:
        return False
    return abs((da - db).days) <= window_days


def fuzzy_matches(name_a, date_a, name_b, date_b):
    return name_similarity(name_a, name_b) >= FUZZY_NAME_THRESHOLD and dates_close(date_a, date_b)


def is_known_event(candidate_name, candidate_start, existing_events, existing_candidate_files, rejected):
    """Check against confirmed events, in-flight candidate files, and the rejected denylist."""
    for e in existing_events:
        if fuzzy_matches(candidate_name, candidate_start, e.get("name", ""), e.get("start_date", "")):
            return True
    for c in existing_candidate_files:
        if fuzzy_matches(candidate_name, candidate_start, c.get("name", ""), c.get("start_date", "")):
            return True
    for r in rejected:
        if fuzzy_matches(candidate_name, candidate_start, r.get("name", ""), r.get("start_date", "")):
            return True
    return False


# --------------------------------------------------------------- geocoding

def geocode(city, country):
    """Return (lat, lon) or (None, None). Never raises — a geocoding failure
    should never block an event from being added."""
    if not city and not country:
        return None, None
    try:
        params = {
            "city": city or "",
            "country": country or "",
            "format": "json",
            "limit": 1,
        }
        r = requests.get(NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS, timeout=10)
        time.sleep(NOMINATIM_DELAY_SECONDS)  # respect Nominatim's 1 req/sec policy
        r.raise_for_status()
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"  geocoding failed for {city}, {country}: {e}")
    return None, None


# ---------------------------------------------------------- source scraping

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
                start = (item.get("startDate") or "")[:10]
                end = (item.get("endDate") or item.get("startDate") or "")[:10]
                candidates.append({
                    "name": item.get("name"),
                    "start_date": start,
                    "end_date": end or start,
                    "website": item.get("url", page_url),
                    "source_url": page_url,
                    "confidence": "high",
                })
    return candidates


def _clean_name_candidate(text):
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip(" -|:")
    return text if text else None


def _choose_name(last_heading, page_title):
    """Prefer a heading/title that actually mentions a relevant keyword
    (weekender, championship, etc.) since that's a strong signal it's the
    event name and not just a generic nav label. Fall back to whichever is a
    plausible short name. Return None only if nothing usable at all."""
    last_heading = _clean_name_candidate(last_heading)
    page_title = _clean_name_candidate(page_title)

    for candidate in (last_heading, page_title):
        if candidate and any(k in candidate.lower() for k in KEYWORDS) and len(candidate) < 100:
            return candidate

    # Many organiser sites are single-event domains (e.g. cerocescape.com),
    # so a short, sane-looking title/heading is very likely the event name
    # even without an exact keyword match.
    for candidate in (last_heading, page_title):
        if candidate and 1 <= len(candidate.split()) <= 12 and len(candidate) < 100:
            return candidate

    return None


def try_regex_dates(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(strip=True) if soup.title and soup.title.string else None

    full_text = soup.get_text(" ", strip=True)
    if not any(k in full_text.lower() for k in KEYWORDS):
        return []

    candidates = []
    seen_keys = set()
    last_heading = None

    # Walk headings and text-bearing leaf elements in document order so a
    # date mentioned under "Ceroc Escape 2027" gets that as its name, rather
    # than every date on the page being lumped together as unnamed.
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "span", "div", "section", "article"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue

        if el.name in ("h1", "h2", "h3", "h4"):
            last_heading = text
            continue

        for m in DATE_RANGE_RE.finditer(text):
            try:
                start_day, end_day, month, year = m.groups()
                start = dateparser.parse(f"{start_day} {month} {year}").date().isoformat()
                end = dateparser.parse(f"{end_day} {month} {year}").date().isoformat()
            except (ValueError, OverflowError):
                continue
            key = (start, end)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidates.append({
                "name": _choose_name(last_heading, page_title),
                "start_date": start, "end_date": end,
                "website": page_url, "source_url": page_url, "confidence": "low",
            })

        # one-day events (e.g. championships) — single date near a champs keyword
        nearby = f"{last_heading or ''} {text}".lower()
        if any(k in nearby for k in ["championship", "champs", "open"]):
            for m in SINGLE_DATE_RE.finditer(text):
                try:
                    day, month, year = m.groups()
                    d = dateparser.parse(f"{day} {month} {year}").date().isoformat()
                except (ValueError, OverflowError):
                    continue
                key = (d, d)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                candidates.append({
                    "name": _choose_name(last_heading, page_title),
                    "start_date": d, "end_date": d,
                    "website": page_url, "source_url": page_url, "confidence": "low",
                })

    return candidates



def scan_source(domain):
    """Returns (candidates, ok). ok=False means the fetch itself failed
    (network/blocked), distinct from ok=True with zero candidates."""
    base_url = f"https://{domain}"
    found = []
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  skip {domain}: {e}")
        return found, False

    ical = try_ical(base_url)
    if ical:
        print(f"  {domain}: found an .ics feed (parse with icalendar lib - TODO wire up)")

    found += try_jsonld_events(r.text, base_url)
    found += try_regex_dates(r.text, base_url)
    return found, True


def passes_keyword_filter(html_text):
    """Require 'ceroc' or 'modern jive' to appear prominently — in the title,
    an h1/h2, or the first ~500 characters of body text — before we trust a
    newly-discovered domain enough to add it to sources.json."""
    soup = BeautifulSoup(html_text, "html.parser")
    title = (soup.title.string if soup.title and soup.title.string else "").lower()
    headings = " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])).lower()
    body_start = soup.get_text(" ", strip=True)[:500].lower()
    haystack = " ".join([title, headings, body_start])
    return any(k in haystack for k in KEYWORD_CHECK)


# -------------------------------------------------------- source discovery

def discover_new_sources(existing_domains):
    """Search DuckDuckGo for each query term, return newly-seen domains that
    pass the keyword filter. Degrades gracefully (returns []) if the search
    package is missing or DDG blocks/rate-limits the runner — this must never
    crash the whole run."""
    if DDGS is None:
        print("duckduckgo_search not installed — skipping discovery pass")
        return []

    new_domains = set()
    try:
        with DDGS() as ddgs:
            for query in SEARCH_QUERIES:
                try:
                    results = list(ddgs.text(query, max_results=10))
                except Exception as e:
                    print(f"  search failed for '{query}': {e}")
                    continue
                for result in results:
                    url = result.get("href") or result.get("link") or ""
                    if not url:
                        continue
                    domain = root_domain(url)
                    if not domain or domain in existing_domains or domain in new_domains:
                        continue
                    try:
                        r = requests.get(f"https://{domain}", headers=HEADERS, timeout=10)
                        r.raise_for_status()
                        if passes_keyword_filter(r.text):
                            new_domains.add(domain)
                        else:
                            print(f"  discovered {domain} but it failed the keyword check, skipping")
                    except requests.RequestException as e:
                        print(f"  couldn't verify discovered domain {domain}: {e}")
    except Exception as e:
        print(f"Search discovery pass failed entirely: {e} (continuing with existing sources only)")
    return sorted(new_domains)


# --------------------------------------------------------------- candidates

def write_candidate_file(event):
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATES_DIR / f"{event['id']}.json"
    save_json(path, event)
    return path


def build_candidate(name, start_date, end_date, website, source_url):
    city, country = "", ""  # left for human review to fill in / geocoding may still work off blank strings
    lat, lon = geocode(city, country)
    multiday = bool(end_date and end_date > start_date)
    event_id = slugify(country or "unknown", city or "unknown", name or "unnamed-event", start_date[:4] if start_date else "")
    return {
        "id": event_id,
        "name": name or "Unnamed event (needs review)",
        "city": city,
        "country": country,
        "region": "Other",
        "lat": lat,
        "lon": lon,
        "types": ["Other"],
        "status": "tbc",
        "start_date": start_date,
        "end_date": end_date or start_date,
        "multiday": multiday,
        "website": website or "",
        "ai_sourced": True,
        "needs_review": True,
        "source": "auto-scraped",
        "source_url": source_url or "",
    }


# --------------------------------------------------------------------- main

def main():
    events = load_json(EVENTS_PATH, [])
    sources = load_json(SOURCES_PATH, [])
    rejected = load_json(REJECTED_PATH, [])
    existing_candidate_files = [load_json(p, {}) for p in CANDIDATES_DIR.glob("*.json")] if CANDIDATES_DIR.exists() else []

    today = date.today().isoformat()

    # seed from existing events if sources.json is completely empty (first run
    # only). This must happen BEFORE search discovery runs — otherwise a
    # single lucky/unlucky search result can make `sources` non-empty and
    # silently skip seeding on what should still count as a first run.
    if not sources:
        seeded = sorted({root_domain(e["website"]) for e in events if e.get("website", "").startswith("http")})
        sources = [{"domain": d, "notes": "seeded from existing event list", "strikes": 0,
                     "discovered_via": "seeded", "added_date": today} for d in seeded]
        print(f"sources.json was empty — seeded {len(sources)} domain(s) from events.json")

    existing_domains = {s["domain"] for s in sources}

    # 1. Discover new sources via search, tag and add them
    print("Discovering new sources via search...")
    new_domains = discover_new_sources(existing_domains)
    for d in new_domains:
        sources.append({"domain": d, "notes": "found via search", "strikes": 0,
                         "discovered_via": "search", "added_date": today})
        print(f"  new source added: {d}")

    # 2. Scan every source, track strikes
    all_raw_candidates = []
    for s in sources:
        print(f"Scanning {s['domain']}...")
        found, ok = scan_source(s["domain"])
        if not ok:
            # a fetch failure isn't the same as "genuinely nothing here" — don't strike it
            print(f"  {s['domain']}: fetch failed this run, not counted as a strike")
            continue
        if found:
            s["strikes"] = 0
            print(f"  found {len(found)} raw candidate(s) on {s['domain']}")
            all_raw_candidates += [(c, s["domain"]) for c in found]
        else:
            s["strikes"] = s.get("strikes", 0) + 1

    # 3. Prune sources that have struck out
    before = len(sources)
    sources = [s for s in sources if s.get("strikes", 0) < STRIKE_LIMIT]
    pruned_count = before - len(sources)
    if pruned_count:
        print(f"Pruned {pruned_count} source(s) with {STRIKE_LIMIT}+ consecutive zero-result runs.")

    save_json(SOURCES_PATH, sources)

    # 4. Build de-duplicated candidate events
    new_candidate_paths = []
    for raw, domain in all_raw_candidates:
        name, start = raw.get("name"), raw.get("start_date")
        if not start:
            continue
        if not name:
            print(f"  skipping unnamed low-confidence candidate from {domain} on {start} — needs a human to name it")
            continue
        if is_known_event(name, start, events, existing_candidate_files, rejected):
            print(f"  '{name}' ({start}) from {domain} matches an existing/known/rejected event — skipping as duplicate")
            continue

        candidate = build_candidate(name, start, raw.get("end_date", start), raw.get("website"), raw.get("source_url"))

        # also guard against two raw hits this same run producing the same candidate twice
        if any(fuzzy_matches(name, start, c.get("name", ""), c.get("start_date", "")) for c in existing_candidate_files):
            continue

        path = write_candidate_file(candidate)
        existing_candidate_files.append(candidate)
        new_candidate_paths.append(str(path))
        print(f"  new candidate written: {path.name}")

    if not new_candidate_paths:
        print("No new candidate events found this run.")
    else:
        print(f"Wrote {len(new_candidate_paths)} new candidate event file(s).")

    # Emit a simple newline-separated list on stdout the workflow can capture,
    # in addition to the files themselves (git status also finds these).
    for p in new_candidate_paths:
        print(f"CANDIDATE_FILE::{p}")


if __name__ == "__main__":
    sys.exit(main())
