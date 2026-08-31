"""
One-off: backfill presented_date for every case already in the store,
using documents already on file. No network calls -- runs in seconds.

(This same logic also runs automatically at the end of every
00_discover_meetings.py run going forward, so this script only needs to
be run once to catch up the existing 143 cases immediately.)

Usage:
    python3 backfill_presented_dates.py
"""

import json
from pathlib import Path

from common import extract_presented_date

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"


def main():
    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8"))
    found, missing = 0, 0

    for entry in store.values():
        presented = extract_presented_date(entry.get("documents", []))
        if presented:
            entry["presented_date"] = presented
            found += 1
        else:
            missing += 1

    CASE_STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")
    print(f"Backfilled presented_date for {found} case(s); {missing} case(s) had no dated document to extract from.")


if __name__ == "__main__":
    main()
