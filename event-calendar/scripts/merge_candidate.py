"""
Runs after a candidate PR (data/candidates/<id>.json) is merged. Takes that
one candidate file, does a hard id-collision check against the current
events.json (the fuzzy check at scrape time doesn't protect against
collisions with events added *after* the candidate was created, since a PR
can sit unreviewed for a while), and either:

- appends it to events.json and deletes the candidate file (clean merge), or
- leaves the candidate file in place and exits non-zero, so the workflow can
  open an issue instead of silently overwriting or renaming anything.

Usage:
    python scripts/merge_candidate.py data/candidates/some-event-id.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVENTS_PATH = ROOT / "data" / "events.json"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/merge_candidate.py <path-to-candidate.json>")
        return 1

    candidate_path = Path(sys.argv[1])
    if not candidate_path.exists():
        print(f"Candidate file not found: {candidate_path} (already merged?)")
        return 0

    candidate = load_json(candidate_path, None)
    if candidate is None:
        print(f"Could not read {candidate_path}")
        return 1

    events = load_json(EVENTS_PATH, [])
    existing_ids = {e["id"] for e in events}

    if candidate["id"] in existing_ids:
        print(f"ID COLLISION: '{candidate['id']}' already exists in events.json.")
        print("Not merging automatically — leaving the candidate file in place for manual review.")
        return 1

    events.append(candidate)
    events.sort(key=lambda e: e.get("start_date", ""))
    save_json(EVENTS_PATH, events)
    candidate_path.unlink()
    print(f"Merged '{candidate['id']}' into events.json and removed the candidate file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
