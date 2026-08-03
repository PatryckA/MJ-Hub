# On the Floor — International Modern Jive & Ceroc Events

A single-page site listing multi-day Modern Jive / Ceroc events (weekenders,
championships, dance holidays) that people might reasonably travel internationally
for. Built to run on GitHub Pages with no server required.

## Structure

```
index.html              the whole site (HTML/CSS/JS in one file)
data/events.json         the event data the site reads at load time
data/sources.json        organiser domains the scraper checks (auto-created on first run)
scripts/scrape_events.py scheduled scraper that proposes new candidate events
.github/workflows/scrape.yml   runs the scraper weekly, opens a PR with findings
```

## Updating events by hand

Edit `data/events.json` directly, or open a pull request. Each event looks like:

```json
{
  "id": "united-kingdom-blackpool-world-modern-jive-championships-wmjc",
  "name": "World Modern Jive Championships (WMJC)",
  "city": "Blackpool",
  "country": "United Kingdom",
  "region": "Europe",
  "lat": 53.8175,
  "lon": -3.0357,
  "types": ["Competition"],
  "status": "confirmed",
  "start_date": "2026-03-06",
  "end_date": "2026-03-08",
  "multiday": true,
  "website": "https://wmjc-blackpool.com",
  "ai_sourced": false,
  "needs_review": false,
  "source": "organiser-submitted"
}
```

Only multi-day events (`end_date` later than `start_date`) show on the site —
that's the objective bar for "big event" agreed on for this list.

`types` is a fixed vocabulary and an array, since one event can be more than one
kind (e.g. a competition weekend that also runs workshops):
**Competition, Workshops, Dance Holiday, Cruise, Other**. Each type has a fixed
colour used consistently across the card tags and the map pins. `region` is used
for the region filter. Current values in use: Europe, Oceania, Asia, Americas,
Other — extend `country_region` in `convert.py` (or add new entries directly to
`events.json`) as new countries show up.

The scraper (v2 — automated pulling with per-event review)
`scripts/scrape_events.py` runs weekly via `.github/workflows/scrape.yml` and:
Discovers new organiser domains via a no-key DuckDuckGo search pass
(search terms: Ceroc Escape, Ceroc Weekender, Modern Jive Weekender, Ceroc
Championships, Modern Jive Championships, Ceroc Dance Holiday, Modern Jive
Dance Holiday, Ceroc Cruise, Modern Jive Cruise). A discovered domain is
only trusted if "ceroc" or "modern jive" appears prominently on its page
(title, headings, or early body text).
Scans every known source (the fixed list in `data/sources.json` plus
anything newly discovered) for structured event data — an `.ics` feed,
Schema.org `Event` JSON-LD, or a conservative regex date-range pass as a
last resort. One-day events (e.g. championships) are picked up via a
single-date pattern near words like "championship" or "open".
Geocodes each candidate's city/country via Nominatim (OpenStreetMap,
free, no API key), rate-limited to 1 request/second. If geocoding fails,
the event is still added with `lat`/`lon` as `null` — geocoding never
blocks an event.
De-duplicates using fuzzy name matching (not exact string match) plus
a nearby-date window, checked against `data/events.json`, any in-flight
candidate files, and `data/rejected.json`.
Writes one file per new event to `data/candidates/<event-id>.json`
rather than appending directly to `events.json`. This is deliberate: it
means the GitHub Action can open one PR per event, so approving one
candidate never conflicts with another sitting in review.
Prunes sources that return zero candidates for 104 consecutive weekly
runs (~24 months). A source's strike count resets to zero the moment it
produces a hit.
Reviewing candidates
Each new candidate event gets its own pull request, and you get an email
(via Gmail SMTP) summarising every PR from that run: event name, dates,
source link, and a link straight to the PR.
Approve an event: open its PR, check the source, click Merge.
A follow-up workflow (`merge-candidate.yml`) then appends it into
`events.json` and removes the candidate file automatically.
Deny an event: open its PR, click Close (don't merge). The same
follow-up workflow records it in `data/rejected.json` so it won't be
proposed again, and cleans up the branch.
If an approved candidate's id happens to collide with something already in
`events.json` (rare — can happen if the PR sat unreviewed while something
else was added by hand), the merge step does not auto-resolve it. It
opens a GitHub issue instead and leaves the candidate file in place for you
to sort out manually.
Required repo settings
Settings → Actions → General → Workflow permissions →
"Allow GitHub Actions to create and approve pull requests" must be enabled,
or PR creation will fail silently.
Two repository secrets are required for the email steps:
`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` (a Gmail app password, not your
normal password — requires 2-Step Verification on the sending account).
Files involved
```
scripts/scrape_events.py     the scraper itself (run weekly)
scripts/merge_candidate.py   runs on PR merge — moves a candidate into events.json
scripts/reject_candidate.py  runs on PR close-without-merge — records a denial
data/events.json             the live, approved event list (unchanged in shape)
data/candidates/*.json        events awaiting review, one file per event
data/rejected.json            denylist of denied events, checked before re-proposing
data/sources.json             known organiser domains, with strike/discovery tracking
.github/workflows/scrape.yml         the weekly scrape + PR + email job
.github/workflows/merge-candidate.yml the approve/deny handler
```

### Adding sources manually

`data/sources.json` is checked into the repo after the first run. Add a domain any
time you hear about a new organiser:

```json
{ "domain": "someneweventsite.com", "notes": "added after Discord mention, 2026-08" }
```

## Organiser submissions

The "Submit your event" button on the site links to a Google Form. **Replace the
placeholder URL in `index.html`** (search for `REPLACE-WITH-YOUR-FORM-ID`) with
your real form link once it's set up. Recommended form fields: event name, city,
country, start date, end date, website, event type, submitter contact — matching
the `events.json` schema makes copying accepted submissions across faster.

Google Forms can also be configured to email you or log to a Sheet on every
response, so review stays a simple "check the sheet, copy the good ones into
`events.json`" habit rather than needing any new infrastructure.

## Deploying to GitHub Pages

1. Push this folder to a repo.
2. Repo Settings → Pages → Deploy from branch → `main` / root.
3. The site will be live at `https://<username>.github.io/<repo>/`.

No build step — `index.html` fetches `data/events.json` directly at runtime.
