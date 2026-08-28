"""
Shared helpers for the Raleigh development-tracker pipeline.

Used by 00_discover_meetings.py, 01_geocode_new.py, and 02_build_map.py.
"""

import re
import time
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

CASE_PATTERN = re.compile(r'\b(Z|AX|CP|TC|HLD)-\d+-\d+\b', re.IGNORECASE)

CASE_TYPE_NAMES = {
    "Z": "Rezoning",
    "AX": "Annexation",
    "CP": "Comprehensive Plan Amendment",
    "TC": "Text Change",
    "HLD": "Historic Landmark Designation",
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


# ---------------------------------------------------------------------------
# Browser fetching (used for both the meeting-list pages and individual
# meeting agenda pages, both of which are JS-rendered by eSCRIBE)
# ---------------------------------------------------------------------------

def render_page(playwright, url, wait_seconds=3):
    """Load a URL with a real browser and return the fully-rendered HTML.

    Tries Playwright's own bundled Chromium first (this is what GitHub
    Actions' fresh Ubuntu runners use, and it's the officially-supported
    path). Falls back to the locally-installed Google Chrome via
    channel="chrome" if the bundled browser reports the host OS is
    unsupported -- this is the case on older macOS versions, where
    Playwright's own Chromium build refuses to run but a real Chrome
    install works fine.
    """
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as e:
        if "does not support chromium" in str(e).lower():
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        else:
            raise

    context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
    page = context.new_page()
    page.goto(url, wait_until="networkidle", timeout=45000)
    time.sleep(wait_seconds)
    html = page.content()
    browser.close()
    return html


# ---------------------------------------------------------------------------
# Parsing a rendered meeting-list page -> individual meeting URLs
# ---------------------------------------------------------------------------

def extract_meeting_links(html):
    """Find individual meetings on a rendered committee listing page.

    eSCRIBE listing pages contain multiple link variants per meeting --
    some with "&Agenda=Agenda" (the actual agenda view, with case PDFs)
    and some without (a bare "meeting details" page with no documents).
    Rather than trust whichever variant happens to appear, we pull out
    just the meeting's Id and build the canonical agenda URL ourselves,
    deduping by Id so each real meeting is only processed once."""
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


# ---------------------------------------------------------------------------
# Parsing a rendered meeting agenda page -> case documents
# ---------------------------------------------------------------------------

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
        case_number = match.group(0).upper()
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
