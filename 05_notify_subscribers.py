"""
Stage 5: Check public subscriber signups (from a Google Form -> Sheet)
against new cases, and email each subscriber individually about
anything within their chosen radius.

This is separate from 03_send_digest.py, which sends your own full
digest -- this stage only emails people who signed up via the public
form, and only sends them cases near THEIR address, never the full list.

Setup:
    1. Create a Google Form with an email field and an address field
       (optionally a radius field too), linked to a response Sheet.
    2. Share that Sheet as "Anyone with the link" -> Viewer.
    3. Paste the Sheet's ID (the long string between /d/ and /edit in
       its URL) into GOOGLE_SHEET_ID below.

Requires these environment variables:
    RESEND_API_KEY
    GEOAPIFY_API_KEY
    DIGEST_FROM_EMAIL  -- e.g. alerts@raleighdevelopmenttracker.com
                          (requires a verified domain in Resend -- the
                          sandbox sender can only email your own address)

Usage:
    python3 05_notify_subscribers.py
"""

import os
import csv
import io
import json
import math
import time
from pathlib import Path

import requests

# ---- Fill this in once you've created the Form + Sheet ----
GOOGLE_SHEET_ID = "1uAxlCupgZ4KD96obRUrEeTNF5mAppI3Y72uRzOQKZZs"
# -------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
NEW_THIS_RUN_PATH = DATA_DIR / "new_this_run.json"
SUBSCRIBERS_PATH = DATA_DIR / "subscribers.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
GEOAPIFY_API_KEY = os.environ.get("GEOAPIFY_API_KEY")
FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "onboarding@resend.dev")

RESEND_URL = "https://api.resend.com/emails"
GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
RALEIGH_BIAS = "-79.0,35.5,-78.3,36.1"

DEFAULT_RADIUS_METERS = 1000
RADIUS_LABELS = {
    "250m": 250, "500m": 500, "1km": 1000, "2km": 2000,
}


def haversine_meters(lat1, lng1, lat2, lng2):
    R = 6371000
    to_rad = math.radians
    dlat = to_rad(lat2 - lat1)
    dlng = to_rad(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode(address):
    params = {
        "text": address,
        "apiKey": GEOAPIFY_API_KEY,
        "filter": f"rect:{RALEIGH_BIAS}",
        "limit": 1,
    }
    resp = requests.get(GEOCODE_URL, params=params, timeout=15)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        return None
    coords = features[0]["geometry"]["coordinates"]
    return coords[1], coords[0]


def fetch_form_responses():
    """Reads the linked Sheet's first page as CSV -- works for a Sheet
    shared as "Anyone with the link -> Viewer", no Google API credentials
    needed."""
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=csv"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


def find_column(fieldnames, keyword, exclude=None):
    """Google Forms names columns after your exact question text, so we
    match loosely (case-insensitive substring) rather than hardcoding an
    exact header string. `exclude` skips columns already claimed by
    another field -- needed because a header like "What is your email
    address?" contains the substring "address" too, and would otherwise
    be wrongly matched when searching for the address column."""
    exclude = exclude or set()
    for name in fieldnames:
        if name in exclude:
            continue
        if keyword.lower() in name.lower():
            return name
    return None


def parse_radius(raw_value):
    if not raw_value:
        return DEFAULT_RADIUS_METERS
    return RADIUS_LABELS.get(raw_value.strip(), DEFAULT_RADIUS_METERS)


def load_subscribers_cache():
    if SUBSCRIBERS_PATH.exists():
        return json.loads(SUBSCRIBERS_PATH.read_text(encoding="utf-8"))
    return {}


def sync_subscribers():
    """Pulls current form responses, geocodes any email we haven't seen
    before (or whose address changed), and returns the up-to-date
    subscriber dict keyed by email."""
    if GOOGLE_SHEET_ID == "PASTE_YOUR_SHEET_ID_HERE":
        print("ERROR: GOOGLE_SHEET_ID is still a placeholder -- edit this script and paste in the real Sheet ID.")
        return {}

    rows = fetch_form_responses()
    if not rows:
        print("No form responses found (or sheet is empty).")
        return {}

    email_col = find_column(rows[0].keys(), "email")
    address_col = find_column(rows[0].keys(), "address", exclude={email_col})
    radius_col = find_column(rows[0].keys(), "radius", exclude={email_col, address_col})

    if not email_col or not address_col:
        print(f"ERROR: could not find email/address columns. Columns found: {list(rows[0].keys())}")
        return {}

    subscribers = load_subscribers_cache()
    changed = False

    for row in rows:
        email = (row.get(email_col) or "").strip().lower()
        address = (row.get(address_col) or "").strip()
        if not email or not address:
            continue

        radius = parse_radius(row.get(radius_col) if radius_col else None)
        existing = subscribers.get(email)

        if existing and existing.get("address") == address and existing.get("lat") is not None:
            if existing.get("radius_meters") != radius:
                existing["radius_meters"] = radius
                changed = True
            continue  # already geocoded, address unchanged

        print(f"Geocoding new/updated subscriber: {email} -> {address}")
        try:
            result = geocode(address)
        except Exception as e:
            print(f"  ERROR geocoding: {e}")
            continue
        if not result:
            print(f"  No geocode match for '{address}'")
            continue

        subscribers[email] = {
            "address": address,
            "lat": result[0],
            "lng": result[1],
            "radius_meters": radius,
        }
        changed = True
        time.sleep(0.5)

    if changed:
        SUBSCRIBERS_PATH.write_text(json.dumps(subscribers, indent=2), encoding="utf-8")

    return subscribers


def build_email_html(email, nearby_cases):
    def case_block(c, dist_m):
        address = c.get("formatted_address") or c.get("address_guess") or "No address found"
        docs_html = "".join(
            f'<div><a href="{d["href"]}">{d["text"]}</a></div>'
            for d in c.get("documents", [])[:3]
        )
        return f"""
        <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #ddd;">
          <div style="color:#b8720c;font-weight:bold;font-size:11px;margin-bottom:4px;">📍 {dist_m}m away</div>
          <div style="font-size:11px;text-transform:uppercase;color:#888;letter-spacing:0.05em;">{c['case_type']}</div>
          <div style="font-size:16px;font-weight:bold;">{c['case_number']}</div>
          <div style="color:#555;margin:4px 0;">{address}</div>
          <div style="font-size:12px;">{docs_html}</div>
        </div>
        """

    blocks = "".join(case_block(c, d) for c, d in nearby_cases)
    return f"""
    <div style="font-family:sans-serif;max-width:600px;">
      <h2>{len(nearby_cases)} new development case(s) near you</h2>
      {blocks}
      <p style="color:#999;font-size:11px;margin-top:24px;">
        You're receiving this because you signed up for alerts on the Raleigh Development Tracker.
      </p>
    </div>
    """


def send_email(to_email, html, case_count):
    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": f"{case_count} new development case(s) near you",
            "html": html,
        },
        timeout=15,
    )
    return resp


def main():
    new_case_numbers = json.loads(NEW_THIS_RUN_PATH.read_text(encoding="utf-8")) if NEW_THIS_RUN_PATH.exists() else []
    if not new_case_numbers:
        print("No new cases this run -- nothing to notify subscribers about.")
        return

    if not RESEND_API_KEY or not GEOAPIFY_API_KEY:
        print("RESEND_API_KEY or GEOAPIFY_API_KEY not set -- skipping subscriber notifications.")
        return

    print("Syncing subscribers from Google Form responses...")
    subscribers = sync_subscribers()
    print(f"{len(subscribers)} subscriber(s) loaded.\n")

    if not subscribers:
        return

    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    new_cases = [store[num] for num in new_case_numbers if num in store and store[num].get("lat") is not None]

    sent_count = 0
    for email, sub in subscribers.items():
        nearby = []
        for c in new_cases:
            dist = haversine_meters(c["lat"], c["lng"], sub["lat"], sub["lng"])
            if dist <= sub.get("radius_meters", DEFAULT_RADIUS_METERS):
                nearby.append((c, round(dist)))

        if not nearby:
            continue

        html = build_email_html(email, nearby)
        resp = send_email(email, html, len(nearby))
        if resp.ok:
            print(f"Sent {len(nearby)} nearby case(s) to {email}")
            sent_count += 1
        else:
            print(f"FAILED to email {email}: {resp.status_code} {resp.text}")
        time.sleep(0.5)  # stay well under Resend's rate limit

    print(f"\nDone. {sent_count} subscriber(s) notified.")


if __name__ == "__main__":
    main()
