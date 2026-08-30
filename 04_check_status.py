"""
Stage 4: Check official status for every case in the store against
Raleigh's own public "current cases" status pages -- separate from the
eSCRIBE meeting agendas used for discovery.

These are plain server-rendered HTML pages (Drupal), not JS-rendered like
eSCRIBE, so this uses plain requests + BeautifulSoup -- no browser
automation needed, much lighter and faster than the discovery stage.

For each of the 5 case types tracked, fetches Raleigh's status page and
builds a case_number -> status lookup. A case not found on its type's
current-cases table is flagged NOT_LISTED rather than guessed at --
Raleigh doesn't always explain why a case disappeared (this is exactly
the "case quietly stalled with no public update" scenario), so we
surface that honestly instead of inventing a reason.

Usage:
    python3 04_check_status.py
"""

import re
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from common import render_page, normalize_case_number

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CASE_NUMBER_RE = re.compile(r'\b([A-Z]{1,4}-\d{1,4}(?:-\d{2,4})?)\b')
DATE_RE = re.compile(r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b')

OUTCOME_KEYWORDS = {
    "APPROVED": ["approved", "adopted"],
    "DENIED": ["denied"],
    "WITHDRAWN": ["withdrawn"],
}


def classify_status(raw_text):
    """Given raw status text from the city's page, return (outcome, display_text)."""
    lower = raw_text.lower()
    for outcome, keywords in OUTCOME_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return outcome, raw_text
    if not raw_text.strip():
        return "ACTIVE", "Under review"
    return "ACTIVE", raw_text


def extract_date(text):
    m = DATE_RE.search(text)
    return m.group(1) if m else None


def fetch_soup(playwright, url):
    """Uses a real browser (via common.render_page) rather than plain
    requests -- Raleigh's Drupal site blocks non-browser HTTP requests
    with a 403, even for pages that don't need JS rendering."""
    html = render_page(playwright, url, wait_seconds=1)
    return BeautifulSoup(html, "html.parser")


def parse_embedded_status_table(soup):
    """Parser for Z/AX/CP-style tables: case number + status text are
    combined in one cell, separated by a line break."""
    results = {}
    for table in soup.find_all("table"):
        header_text = table.get_text(" ", strip=True).lower()
        if "case number" not in header_text and "case #" not in header_text:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            first_cell_text = cells[0].get_text("\n", strip=True)
            lines = [l.strip() for l in first_cell_text.split("\n") if l.strip()]
            if not lines:
                continue
            case_match = CASE_NUMBER_RE.search(lines[0])
            if not case_match:
                continue
            case_number = normalize_case_number(case_match.group(1))
            status_text = " ".join(lines[1:]) if len(lines) > 1 else ""
            results[case_number] = status_text
    return results


def parse_separate_status_table(soup):
    """Parser for TC/HLD-style tables: case number and status live in
    separate columns, identified by header text."""
    results = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers or not any("case" in h for h in headers) or "status" not in " ".join(headers):
            continue
        try:
            case_idx = next(i for i, h in enumerate(headers) if "case" in h)
            status_idx = next(i for i, h in enumerate(headers) if "status" in h)
        except StopIteration:
            continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(case_idx, status_idx):
                continue
            case_text = cells[case_idx].get_text(" ", strip=True)
            case_match = CASE_NUMBER_RE.search(case_text)
            if not case_match:
                continue
            case_number = normalize_case_number(case_match.group(1))
            status_text = cells[status_idx].get_text(" ", strip=True)
            results[case_number] = status_text
    return results


def parse_tc_adopted_table(soup):
    """TC's 'Recently Adopted Text Changes' table has different columns
    entirely (no shared 'Status' column) -- everything in it is
    definitionally APPROVED, with a real adopted date."""
    results = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers or "adopted" not in headers:
            continue
        try:
            case_idx = next(i for i, h in enumerate(headers) if "change #" in h or h == "case #")
            adopted_idx = headers.index("adopted")
        except StopIteration:
            continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(case_idx, adopted_idx):
                continue
            case_text = cells[case_idx].get_text(" ", strip=True)
            case_match = CASE_NUMBER_RE.search(case_text)
            if not case_match:
                continue
            case_number = normalize_case_number(case_match.group(1))
            adopted_date = cells[adopted_idx].get_text(" ", strip=True)
            results[case_number] = f"Approved {adopted_date}"
    return results


FINALIZED_Z_URL = "https://raleighnc.gov/planning/services/finalized-rezoning-cases"


def parse_finalized_z_table(soup):
    """Parses Raleigh's 'Finalized Rezoning Cases' archive (organized by
    year, going back decades). Columns are Case Number / Change Requested
    / Final Action / Adopted Ordinance -- "Final Action" is the status
    column here, e.g. "Denied 05-05-26" or "Approved 07-07-26". This is
    only relevant for Z-type cases; used as a fallback check for cases
    that came back NOT_LISTED from the live "current cases" page, since
    that page only shows a recent window, not full history."""
    results = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not headers or not any("case number" in h for h in headers) or not any("final action" in h for h in headers):
            continue
        case_idx = next(i for i, h in enumerate(headers) if "case number" in h)
        action_idx = next(i for i, h in enumerate(headers) if "final action" in h)
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(case_idx, action_idx):
                continue
            case_text = cells[case_idx].get_text(" ", strip=True)
            case_match = CASE_NUMBER_RE.search(case_text)
            if not case_match:
                continue
            case_number = normalize_case_number(case_match.group(1))
            status_text = cells[action_idx].get_text(" ", strip=True)
            results[case_number] = status_text
    return results


STATUS_SOURCES = [
    {"prefix": "Z", "url": "https://raleighnc.gov/planning/services/rezoning-process/rezoning-cases", "parser": "embedded"},
    {"prefix": "AX", "url": "https://raleighnc.gov/planning/services/current-development-activity/annexation-cases", "parser": "embedded"},
    {"prefix": "CP", "url": "https://raleighnc.gov/planning/services/rezoning-process/comprehensive-plan-amendments", "parser": "embedded"},
    {"prefix": "TC", "url": "https://raleighnc.gov/planning/services/text-changes/text-change-cases", "parser": "separate"},
    {"prefix": "HLD", "url": "https://raleighnc.gov/planning/services/raleigh-historic-landmarks-rhl", "parser": "separate"},
]


def build_status_lookup(playwright):
    lookup = {}
    for source in STATUS_SOURCES:
        print(f"Fetching {source['prefix']} status page: {source['url']}")
        try:
            soup = fetch_soup(playwright, source["url"])
        except Exception as e:
            print(f"  ERROR fetching: {e}")
            continue

        if source["parser"] == "embedded":
            found = parse_embedded_status_table(soup)
        else:
            found = parse_separate_status_table(soup)

        print(f"  Found {len(found)} case(s) on this page.")
        lookup.update(found)

        if source["prefix"] == "TC":
            adopted = parse_tc_adopted_table(soup)
            print(f"  Found {len(adopted)} additional adopted TC case(s).")
            lookup.update(adopted)

        time.sleep(1)  # polite pacing between requests

    return lookup


def migrate_case_keys(store):
    """One-time fix: re-key the store through normalize_case_number so
    cases like "Z-9-26" (from eSCRIBE) and "Z-09-26" (from Raleigh's own
    status pages) are recognized as the same case going forward. Merges
    documents if a collision surfaces two variants of the same case."""
    migrated = {}
    renamed = 0
    for case_number, entry in store.items():
        normalized = normalize_case_number(case_number)
        if normalized != case_number:
            renamed += 1
        if normalized in migrated:
            existing_hrefs = {d["href"] for d in migrated[normalized]["documents"]}
            for doc in entry.get("documents", []):
                if doc["href"] not in existing_hrefs:
                    migrated[normalized]["documents"].append(doc)
        else:
            entry["case_number"] = normalized
            migrated[normalized] = entry
    if renamed:
        print(f"Migrated {renamed} case number(s) to normalized form (e.g. Z-9-26 -> Z-09-26).\n")
    return migrated


def main():
    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    store = migrate_case_keys(store)
    print(f"Loaded {len(store)} case(s) from store.\n")

    with sync_playwright() as playwright:
        lookup = build_status_lookup(playwright)
    print(f"\nCombined status lookup has {len(lookup)} case(s) across all sources.\n")

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    counts = {"APPROVED": 0, "DENIED": 0, "WITHDRAWN": 0, "ACTIVE": 0, "NOT_LISTED": 0}

    for case_number, entry in store.items():
        raw_status = lookup.get(case_number)
        if raw_status is None:
            entry["official_status"] = "NOT_LISTED"
            entry["official_status_display"] = "No longer listed by the city — status unknown"
            entry["official_status_date"] = None
        else:
            outcome, display_text = classify_status(raw_status)
            entry["official_status"] = outcome
            entry["official_status_display"] = display_text
            entry["official_status_date"] = extract_date(raw_status)
        entry["status_checked_at"] = checked_at

    # Fallback: for Z-type cases still NOT_LISTED, check the historical
    # "Finalized Rezoning Cases" archive -- the live "current" page only
    # shows a recent window, so an older case can legitimately be decided
    # and simply have aged off it.
    still_unresolved_z = [
        c for c in store.values()
        if c["official_status"] == "NOT_LISTED" and c["case_number"].startswith("Z-")
    ]
    if still_unresolved_z:
        print(f"\n{len(still_unresolved_z)} Z-type case(s) still NOT_LISTED -- checking the finalized-cases archive...")
        with sync_playwright() as playwright:
            try:
                finalized_soup = fetch_soup(playwright, FINALIZED_Z_URL)
                finalized_lookup = parse_finalized_z_table(finalized_soup)
                print(f"  Found {len(finalized_lookup)} case(s) in the finalized archive.")
            except Exception as e:
                print(f"  ERROR fetching finalized archive: {e}")
                finalized_lookup = {}

        for entry in still_unresolved_z:
            raw_status = finalized_lookup.get(entry["case_number"])
            if raw_status:
                outcome, display_text = classify_status(raw_status)
                entry["official_status"] = outcome
                entry["official_status_display"] = display_text
                entry["official_status_date"] = extract_date(raw_status)

    for entry in store.values():
        counts[entry["official_status"]] += 1

    CASE_STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")

    print("Status breakdown:")
    for outcome, count in counts.items():
        print(f"  {outcome}: {count}")


if __name__ == "__main__":
    main()
