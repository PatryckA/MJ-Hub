# On the Floor: International Modern Jive & Ceroc Events

A single-page site listing multi-day Modern Jive / Ceroc events (weekenders,
championships, dance holidays) that people might reasonably travel internationally
for. Built to run on GitHub Pages with no server required.

## Structure

```
index.html              the whole site (HTML/CSS/JS in one file)
data/events.json         the event data the site reads at load time
data/sources.json        organiser domains the scraper checks (auto-created on first run)
data/rejected.json       candidates you've dismissed, so the scraper stops re-suggesting them
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

Only multi-day events (`end_date` later than `start_date`) show on the site.
That's the objective bar for "big event" agreed on for this list.

`types` is a fixed vocabulary and an array, since one event can be more than one
kind (e.g. a competition weekend that also runs workshops):
**Competition, Workshops, Dance Holiday, Cruise, Other**. Each type has a fixed
colour used consistently across the card tags and the map pins. `region` is used
for the region filter. Current values in use: Europe, Oceania, Asia, Americas,
Other. Extend `country_region` in `convert.py` (or add new entries directly to
`events.json`) as new countries show up.

## The scraper (automated pulling)

`scripts/scrape_events.py` is a starting point, not a finished pipeline. Organiser
sites range from clean event pages with structured data down to Facebook-group-only
listings that can't be scraped at all. It looks for, in order of reliability:

1. An `.ics` calendar feed
2. Schema.org `Event` JSON-LD embedded in the page (most reliable HTML case)
3. A conservative regex pass for date ranges near words like "weekender" or
   "championship" (weakest signal, always low-confidence)

Anything it finds is appended to `events.json` with `ai_sourced: true` and
`needs_review: true`, and the GitHub Action opens a **pull request** rather than
committing straight to `main`, so nothing reaches the live site without a human
looking at the `source_url` first. On the site itself, any event still flagged
`ai_sourced` shows a caution banner telling visitors to confirm details with the
organiser before booking.

Expect to add per-domain handling over time as you see which organiser sites the
generic scraper handles well and which need a small custom rule. The script is
structured so each source is tried independently and failures on one domain don't
block the others.

### Rejecting a candidate for good

If the scraper keeps re-suggesting something you don't want (a duplicate, a wrong
match, or just not worth tracking), add it to `data/rejected.json` so it stops
coming back. This is a plain array matching on `name` + `start_date`:

```json
[
  { "name": "Some Event Name", "start_date": "2026-05-01" }
]
```

Works the same way whether you're closing a scraper PR without merging it, or
deleting something you'd already merged in and later decided against. Either way,
add its name and start date here once, and it's permanently excluded from future
scraper runs. `events.json` alone only prevents exact re-adds of things already
published, it doesn't remember what you've actively said no to.

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
country, start date, end date, website, event type, submitter contact, matching
the `events.json` schema makes copying accepted submissions across faster.

Google Forms can also be configured to email you or log to a Sheet on every
response, so review stays a simple "check the sheet, copy the good ones into
`events.json`" habit rather than needing any new infrastructure.

## Deploying to GitHub Pages

1. Push this folder to a repo.
2. Repo Settings → Pages → Deploy from branch → `main` / root.
3. The site will be live at `https://<username>.github.io/<repo>/`.

No build step. `index.html` fetches `data/events.json` directly at runtime.
