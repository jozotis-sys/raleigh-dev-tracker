"""
Shared helpers for the Raleigh development-tracker pipeline.

Used by 00_discover_meetings.py, 01_geocode_new.py, 02_build_map.py, and
04_check_status.py.
"""

import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
import pdfplumber
from bs4 import BeautifulSoup

BASE_URL = "https://pub-raleighnc.escribemeetings.com/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# No leading \b: Raleigh's dated document filenames often run a label
# directly into the case number with no separator (e.g.
# "...CommissionReportZ-17-26.pdf"), which a leading word-boundary would
# reject since both the preceding letter and the case prefix are \w
# characters. The trailing \b is kept to avoid grabbing a partial number.
CASE_PATTERN = re.compile(r'(Z|AX|CP|TC|HLD|DA|BOA)-\d+-\d+\b', re.IGNORECASE)


def normalize_case_number(case_number):
    """Normalize a case number for consistent matching across sources.
    eSCRIBE sometimes renders a case as "Z-9-26" while Raleigh's own
    status pages render the same case as "Z-09-26" -- zero-padding each
    numeric segment to at least 2 digits makes both forms produce the
    same key (harmless for segments already padded further, like HLD's
    4-digit year)."""
    parts = case_number.upper().split("-")
    if len(parts) < 2:
        return case_number.upper()
    prefix = parts[0]
    padded = [p.zfill(2) if p.isdigit() else p for p in parts[1:]]
    return "-".join([prefix] + padded)

CASE_TYPE_NAMES = {
    "Z": "Rezoning",
    "AX": "Annexation",
    "CP": "Comprehensive Plan Amendment",
    "TC": "Text Change",
    "HLD": "Historic Landmark Designation",
    "DA": "Design Alternate",
    "BOA": "Variance / Special Use Permit / Appeal",
}

PRIORITY_KEYWORDS = ["staffreport", "publichearing", "commissionreport", "specialitem"]

STREET_SUFFIX = (
    r"(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Boulevard|Blvd|Lane|Ln|"
    r"Way|Circle|Cir|Court|Ct|Place|Pl|Trail|Trl|Parkway|Pkwy)"
)
LABEL_RE = re.compile(r"(?:Location|Address|Site\s*Address)\s*[:\-]\s*(.*)", re.IGNORECASE)
LINE_ADDR_RE = re.compile(
    r"(?<!-)(?<!\d)(\d{3,6}\s+[A-Za-z0-9.#]+(?:\s+[A-Za-z0-9.#]+){0,5}\s+" + STREET_SUFFIX + r")\b",
    re.IGNORECASE,
)


DATE_PREFIX_RE = re.compile(r'(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)')


def extract_presented_date(documents):
    """Raleigh's PDF filenames often start with an 8-digit YYYYMMDD date
    (e.g. "20260818PLANDEVCommissionReportZ-17-26.pdf") -- that's the
    actual date the document was presented at a meeting, which is a much
    more meaningful date than when our scraper happened to run. Not every
    document has this prefix (cover-sheet PDFs often don't), so we scan
    all of a case's documents and take the earliest date found."""
    dates = []
    for doc in documents:
        m = DATE_PREFIX_RE.search(doc.get("text", ""))
        if not m:
            continue
        try:
            dates.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    return min(dates).strftime("%Y-%m-%d") if dates else None


def render_page(playwright, url, wait_seconds=3):
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception:
        browser = playwright.chromium.launch(channel="chrome", headless=True)

    context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
    page = context.new_page()
    page.goto(url, wait_until="networkidle", timeout=45000)
    time.sleep(wait_seconds)
    html = page.content()
    browser.close()
    return html


def extract_meeting_links(html):
    soup = BeautifulSoup(html, "html.parser")
    seen_ids = set()
    meetings = []
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE_URL, a["href"])
        if "Meeting.aspx" not in href:
            continue
        qs = parse_qs(urlparse(href).query)
        meeting_id = qs.get("Id", [None])[0]
        if not meeting_id or meeting_id in seen_ids:
            continue
        seen_ids.add(meeting_id)
        canonical_url = f"{BASE_URL}Meeting.aspx?Id={meeting_id}&Agenda=Agenda&lang=English"
        meetings.append({"href": canonical_url, "text": a.get_text(strip=True)})
    return meetings


def extract_pdf_links(html):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE_URL, a["href"])
        text = a.get_text(strip=True)
        if "FileStream.ashx" not in href:
            continue
        key = (href, text)
        if key in seen:
            continue
        seen.add(key)
        links.append({"href": href, "text": text})
    return links


def group_by_case(links):
    cases = {}
    for link in links:
        match = CASE_PATTERN.search(link["text"])
        if not match:
            continue
        case_number = normalize_case_number(match.group(0))
        prefix = case_number.split("-")[0]
        cases.setdefault(case_number, {
            "case_number": case_number,
            "case_type": CASE_TYPE_NAMES.get(prefix, prefix),
            "documents": [],
        })
        cases[case_number]["documents"].append(link)
    return cases


def download_pdf(url, dest_path):
    if dest_path.exists():
        return dest_path
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    dest_path.write_bytes(resp.content)
    return dest_path


def extract_pdf_text(pdf_path, max_pages=5):
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:max_pages]:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def guess_address(text):
    if not text:
        return None
    lines = [l.strip() for l in text.split("\n")]

    for i, line in enumerate(lines):
        m = LABEL_RE.search(line)
        if not m:
            continue
        candidate = m.group(1).strip()
        j = i
        while (not candidate or not re.search(STREET_SUFFIX, candidate, re.IGNORECASE)) and j + 1 < len(lines) and j < i + 3:
            j += 1
            nxt = lines[j].strip()
            if nxt:
                candidate = (candidate + " " + nxt).strip()
        if candidate:
            return candidate

    for line in lines:
        m = LINE_ADDR_RE.search(line)
        if m:
            return m.group(1).strip()

    return None


def best_document_text(documents, pdf_cache_dir: Path):
    docs_sorted = sorted(
        documents,
        key=lambda d: not any(k in d["text"].lower().replace(" ", "") for k in PRIORITY_KEYWORDS),
    )
    best_text, best_doc = "", None
    for doc in docs_sorted:
        m = re.search(r"DocumentId=(\d+)", doc["href"])
        doc_id = m.group(1) if m else re.sub(r"\W+", "_", doc["href"])[-40:]
        pdf_path = pdf_cache_dir / f"{doc_id}.pdf"
        try:
            download_pdf(doc["href"], pdf_path)
            text = extract_pdf_text(pdf_path)
        except Exception:
            continue
        if len(text) > len(best_text):
            best_text, best_doc = text, doc
        time.sleep(1)
    return best_text, best_doc
