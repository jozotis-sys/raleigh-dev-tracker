"""
Stage 0: Discover new meetings across tracked committees, and merge any
newly-found development cases into the persistent case store.

A meeting is only marked "seen" (never looked at again) once it has
actually yielded cases. A meeting that comes back empty is usually one
whose agenda simply hasn't been published yet -- eSCRIBE lists meetings
well before their agendas go up -- so those go on a "pending" list and
are rechecked every few days for a couple of months before we give up
on them.

Merges into data/case_store.json rather than overwriting it. Also writes
data/new_this_run.json -- the list of case numbers that were newly
discovered in this run, which 03_send_digest.py uses to build the email.

Add more committees by adding entries to COMMITTEES below. Get a
committee's URL by picking it from the dropdown at
https://pub-raleighnc.escribemeetings.com/ and copying the address bar.
The "name" field is just a display label -- it doesn't need to match the
URL's "Expanded=" text, but it DOES need to match the keys in
COMMITTEE_COLORS in 02_build_map.py exactly (case-sensitive) for the
legend/pin colors to line up.

Usage:
    python3 00_discover_meetings.py
"""

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from common import (
    render_page,
    extract_meeting_links,
    extract_pdf_links,
    group_by_case,
    guess_address,
    best_document_text,
    extract_presented_date,
)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PDF_CACHE_DIR = DATA_DIR / "pdfs"
PDF_CACHE_DIR.mkdir(exist_ok=True)

SEEN_MEETINGS_PATH = DATA_DIR / "meetings_seen.json"
PENDING_MEETINGS_PATH = DATA_DIR / "meetings_pending.json"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
NEW_THIS_RUN_PATH = DATA_DIR / "new_this_run.json"
LOG_PATH = DATA_DIR / "pipeline.log"

# How long to keep rechecking a meeting that returned no cases, and how
# often. Rechecking every run would mean re-rendering dozens of empty
# agenda pages daily; every few days is plenty, since agendas are
# typically posted days to a couple of weeks before the meeting.
RECHECK_WINDOW_DAYS = 60
RECHECK_INTERVAL_DAYS = 3

COMMITTEES = [
    {
        "name": "City Council",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2026&Expanded=City%20Council%20Meeting%20-%20First%20Tuesday%20-%20Afternoon%20&%20Evening%20Sessions",
    },
    {
        "name": "City Council",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2025&Expanded=City%20Council%20Meeting%20-%20First%20Tuesday%20-%20Afternoon%20&%20Evening%20Sessions",
    },
    {
        "name": "Planning Commission",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2026&Expanded=Planning%20Commission%20Regular%20Meeting",
    },
    {
        "name": "Planning Commission",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2025&Expanded=Planning%20Commission%20Regular%20Meeting",
    },
    {
        "name": "Board of Adjustment",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2026&Expanded=Board%20of%20Adjustment",
    },
    {
        "name": "Board of Adjustment",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2025&Expanded=Board%20of%20Adjustment",
    },
    {
        "name": "Design Review Commission",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2026&Expanded=Design%20Review%20Commission",
    },
    {
        "name": "Design Review Commission",
        "url": "https://pub-raleighnc.escribemeetings.com/?Year=2025&Expanded=Design%20Review%20Commission",
    },
    # Raleigh Transit Authority was tracked briefly but removed -- its
    # agendas cover transit operations/budget, not development cases, and
    # consistently found zero matches across every run.
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def utc_today():
    return datetime.now(timezone.utc).date()


def should_check(href, seen_meetings, pending, today):
    """True if this meeting needs to be (re)fetched this run."""
    if href in seen_meetings:
        return False
    info = pending.get(href)
    if info is None:
        return True  # never looked at it before
    try:
        first = date.fromisoformat(info["first_checked"])
        last = date.fromisoformat(info["last_checked"])
    except (KeyError, ValueError):
        return True  # malformed entry -- safest to just look again
    if (today - first).days > RECHECK_WINDOW_DAYS:
        return False  # gave it a fair chance; stop spending time on it
    return (today - last).days >= RECHECK_INTERVAL_DAYS


def merge_case(store, case_number, case_type, documents, committee, meeting_url, run_time, new_case_numbers):
    entry = store.get(case_number)
    if entry is None:
        entry = {
            "case_number": case_number,
            "case_type": case_type,
            "documents": [],
            "committee": committee,
            "first_seen_meeting": meeting_url,
            "first_seen_at": run_time,
            "address_guess": None,
            "lat": None,
            "lng": None,
        }
        store[case_number] = entry
        new_case_numbers.add(case_number)

    existing_hrefs = {d["href"] for d in entry["documents"]}
    for doc in documents:
        if doc["href"] not in existing_hrefs:
            entry["documents"].append(doc)
            existing_hrefs.add(doc["href"])

    entry["last_seen_meeting"] = meeting_url
    entry["last_seen_at"] = run_time
    return entry


def process_meeting(playwright, committee_name, meeting_url, store, run_time, new_case_numbers):
    """Fetches one meeting's agenda and merges any cases into the store.
    Returns the number of cases found (0 if the agenda has none yet)."""
    log(f"  Fetching meeting: {meeting_url}")
    html = render_page(playwright, meeting_url)
    links = extract_pdf_links(html)
    cases = group_by_case(links)

    if not cases:
        log(f"    No development cases found on this agenda (yet).")
        return 0

    for case_number, data in cases.items():
        entry = merge_case(
            store, case_number, data["case_type"], data["documents"],
            committee_name, meeting_url, run_time, new_case_numbers,
        )
        if not entry.get("address_guess"):
            best_text, best_doc = best_document_text(entry["documents"], PDF_CACHE_DIR)
            entry["address_guess"] = guess_address(best_text) if best_text else None
            entry["best_document"] = best_doc
        log(f"    {case_number}: address_guess = {entry.get('address_guess')!r}")

    return len(cases)


def main():
    seen_meetings = set(load_json(SEEN_MEETINGS_PATH, []))
    pending = load_json(PENDING_MEETINGS_PATH, {})
    store = load_json(CASE_STORE_PATH, {})
    run_time = datetime.now(timezone.utc).isoformat()
    today = utc_today()
    new_case_numbers = set()

    log(f"=== Run started. {len(seen_meetings)} meeting(s) already processed, "
        f"{len(pending)} awaiting an agenda, {len(store)} case(s) in store. ===")

    with sync_playwright() as playwright:
        for committee in COMMITTEES:
            log(f"Checking committee: {committee['name']}")
            try:
                listing_html = render_page(playwright, committee["url"])
            except Exception as e:
                log(f"  ERROR loading listing page: {e}")
                continue

            meeting_links = extract_meeting_links(listing_html)
            to_check = [m for m in meeting_links if should_check(m["href"], seen_meetings, pending, today)]
            rechecks = sum(1 for m in to_check if m["href"] in pending)
            log(f"  Found {len(meeting_links)} meeting link(s) total, "
                f"{len(to_check) - rechecks} new, {rechecks} recheck(s).")

            for meeting in to_check:
                href = meeting["href"]
                try:
                    case_count = process_meeting(playwright, committee["name"], href, store, run_time, new_case_numbers)
                except Exception as e:
                    log(f"  ERROR processing {href}: {e}")
                    continue  # leave state untouched -- retry next run

                if case_count > 0:
                    seen_meetings.add(href)
                    pending.pop(href, None)
                else:
                    info = pending.get(href) or {"first_checked": today.isoformat()}
                    info["last_checked"] = today.isoformat()
                    pending[href] = info

                save_json(SEEN_MEETINGS_PATH, sorted(seen_meetings))
                save_json(PENDING_MEETINGS_PATH, pending)
                save_json(CASE_STORE_PATH, store)
                time.sleep(2)

    # Backfill/refresh the real "presented" date for every case using
    # documents already on file -- cheap (no network calls), and catches
    # both existing cases and anything new from this run in one pass.
    for entry in store.values():
        presented = extract_presented_date(entry.get("documents", []))
        if presented:
            entry["presented_date"] = presented

    save_json(SEEN_MEETINGS_PATH, sorted(seen_meetings))
    save_json(PENDING_MEETINGS_PATH, pending)
    save_json(CASE_STORE_PATH, store)
    save_json(NEW_THIS_RUN_PATH, sorted(new_case_numbers))

    log(f"=== Run finished. {len(seen_meetings)} meeting(s) processed total, "
        f"{len(pending)} awaiting an agenda, {len(store)} case(s) in store, "
        f"{len(new_case_numbers)} new this run. ===\n")


if __name__ == "__main__":
    main()
