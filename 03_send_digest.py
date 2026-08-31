"""
Stage 3: Email a digest of newly-discovered cases, if any.

Now also checks each new case's distance against a list of "watch
addresses" (data/watch_addresses.json) and highlights any that fall
within that address's radius at the top of the email, separate from the
general list of everything new.

Requires these environment variables (set as GitHub Actions secrets):
    RESEND_API_KEY   -- your Resend API key
    DIGEST_TO_EMAIL  -- where to send the digest
    GEOAPIFY_API_KEY -- used to geocode watch addresses (cached after first run)

Usage:
    python3 03_send_digest.py
"""

import os
import sys
import json
import math
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
NEW_THIS_RUN_PATH = DATA_DIR / "new_this_run.json"
WATCH_ADDRESSES_PATH = Path(__file__).parent / "watch_addresses.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL")
FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "onboarding@resend.dev")
GEOAPIFY_API_KEY = os.environ.get("GEOAPIFY_API_KEY")

RESEND_URL = "https://api.resend.com/emails"
GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
RALEIGH_BIAS = "-79.0,35.5,-78.3,36.1"


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
    return coords[1], coords[0]  # lat, lng


def load_watch_addresses():
    """Loads watch addresses and geocodes any that don't have lat/lng
    cached yet, writing the result back to the same file so we don't
    re-geocode on every run."""
    if not WATCH_ADDRESSES_PATH.exists():
        return []

    addresses = json.loads(WATCH_ADDRESSES_PATH.read_text(encoding="utf-8"))
    changed = False

    for entry in addresses:
        if entry.get("lat") is not None:
            continue
        if not GEOAPIFY_API_KEY:
            print(f"  WARNING: no GEOAPIFY_API_KEY, cannot geocode watch address '{entry['label']}'")
            continue
        try:
            result = geocode(entry["address"])
        except Exception as e:
            print(f"  WARNING: failed to geocode watch address '{entry['label']}': {e}")
            continue
        if result:
            entry["lat"], entry["lng"] = result
            changed = True
            print(f"  Geocoded watch address '{entry['label']}' -> {result}")
        else:
            print(f"  WARNING: watch address '{entry['label']}' did not geocode")

    if changed:
        WATCH_ADDRESSES_PATH.write_text(json.dumps(addresses, indent=2), encoding="utf-8")

    return [a for a in addresses if a.get("lat") is not None]


def find_nearby_watches(case, watch_addresses):
    if case.get("lat") is None:
        return []
    nearby = []
    for w in watch_addresses:
        dist = haversine_meters(case["lat"], case["lng"], w["lat"], w["lng"])
        if dist <= w.get("radius_meters", 1000):
            nearby.append({"label": w["label"], "distance_m": round(dist)})
    return nearby


def build_email_html(near_cases, other_cases):
    def case_block(c, nearby_info=None):
        address = c.get("formatted_address") or c.get("address_guess") or "No address found"
        docs_html = "".join(
            f'<div><a href="{d["href"]}">{d["text"]}</a></div>'
            for d in c.get("documents", [])[:3]
        )
        badge = ""
        if nearby_info:
            labels = ", ".join(f'{n["label"]} ({n["distance_m"]}m)' for n in nearby_info)
            badge = f'<div style="color:#b8720c;font-weight:bold;font-size:11px;margin-bottom:4px;">📍 NEAR {labels}</div>'
        return f"""
        <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #ddd;">
          {badge}
          <div style="font-size:11px;text-transform:uppercase;color:#888;letter-spacing:0.05em;">{c['case_type']}</div>
          <div style="font-size:16px;font-weight:bold;">{c['case_number']}</div>
          <div style="color:#555;margin:4px 0;">{address}</div>
          <div style="font-size:12px;">{docs_html}</div>
        </div>
        """

    near_html = "".join(case_block(c, n) for c, n in near_cases)
    other_html = "".join(case_block(c) for c in other_cases)

    near_section = f"<h3>Near your watched addresses</h3>{near_html}" if near_cases else ""
    other_section = f"<h3>All new cases</h3>{other_html}" if other_cases else ""

    total = len(near_cases) + len(other_cases)
    return f"""
    <div style="font-family:sans-serif;max-width:600px;">
      <h2>{total} new Raleigh development case(s)</h2>
      {near_section}
      {other_section}
    </div>
    """


def main():
    new_case_numbers = json.loads(NEW_THIS_RUN_PATH.read_text(encoding="utf-8")) if NEW_THIS_RUN_PATH.exists() else []

    if not new_case_numbers:
        print("No new cases this run -- skipping email.")
        return

    if not RESEND_API_KEY or not DIGEST_TO_EMAIL:
        print("RESEND_API_KEY or DIGEST_TO_EMAIL not set -- skipping email (but there ARE new cases to report):")
        for num in new_case_numbers:
            print(f"  {num}")
        return

    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    cases = [store[num] for num in new_case_numbers if num in store]

    print("Checking watch addresses...")
    watch_addresses = load_watch_addresses()
    print(f"  {len(watch_addresses)} watch address(es) ready.")

    near_cases, other_cases = [], []
    for c in cases:
        nearby = find_nearby_watches(c, watch_addresses)
        if nearby:
            near_cases.append((c, nearby))
            print(f"  {c['case_number']} is near: {[n['label'] for n in nearby]}")
        else:
            other_cases.append(c)

    html = build_email_html(near_cases, other_cases)

    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": FROM_EMAIL,
            "to": [DIGEST_TO_EMAIL],
            "subject": f"Raleigh Dev Tracker: {len(cases)} new case(s)" + (f" ({len(near_cases)} near you)" if near_cases else ""),
            "html": html,
        },
        timeout=15,
    )

    if resp.ok:
        print(f"Sent digest email for {len(cases)} case(s) to {DIGEST_TO_EMAIL} ({len(near_cases)} flagged as nearby).")
    else:
        print(f"Failed to send digest email: {resp.status_code} {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
