"""
Runs after a candidate PR (data/candidates/<id>.json) is closed WITHOUT being
merged (i.e. denied). Records the event in data/rejected.json so the scraper
doesn't propose the same event again next run, then deletes the candidate
file since it's no longer needed.

Usage:
    python scripts/reject_candidate.py data/candidates/some-event-id.json
"""

import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
REJECTED_PATH = ROOT / "data" / "rejected.json"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/reject_candidate.py <path-to-candidate.json>")
        return 1

    candidate_path = Path(sys.argv[1])
    if not candidate_path.exists():
        print(f"Candidate file not found: {candidate_path} (already handled?)")
        return 0

    candidate = load_json(candidate_path, None)
    if candidate is None:
        print(f"Could not read {candidate_path}")
        return 1

    rejected = load_json(REJECTED_PATH, [])
    rejected.append({
        "id": candidate["id"],
        "name": candidate.get("name", ""),
        "start_date": candidate.get("start_date", ""),
        "rejected_date": date.today().isoformat(),
    })
    save_json(REJECTED_PATH, rejected)
    candidate_path.unlink()
    print(f"Recorded '{candidate['id']}' as rejected and removed the candidate file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
