"""
Stage 3: Email a digest of newly-discovered cases, if any.

Reads data/new_this_run.json (written by 00_discover_meetings.py) and, if
it's non-empty, looks up the full details for each case in
data/case_store.json and sends a summary email via the Resend API.

If there are no new cases this run, this script does nothing and exits
cleanly -- it's meant to be run every time, not conditionally.

Requires these environment variables (set as GitHub Actions secrets):
    RESEND_API_KEY   -- your Resend API key
    DIGEST_TO_EMAIL  -- where to send the digest (must be the email
                        address you signed up to Resend with, unless
                        you've verified your own sending domain)

Usage:
    python3 03_send_digest.py
"""

import os
import sys
import json
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
NEW_THIS_RUN_PATH = DATA_DIR / "new_this_run.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL")
FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "onboarding@resend.dev")

RESEND_URL = "https://api.resend.com/emails"


def build_email_html(cases):
    rows = []
    for c in cases:
        address = c.get("formatted_address") or c.get("address_guess") or "No address found"
        docs_html = "".join(
            f'<div><a href="{d["href"]}">{d["text"]}</a></div>'
            for d in c.get("documents", [])[:3]
        )
        rows.append(f"""
        <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #ddd;">
          <div style="font-size:11px;text-transform:uppercase;color:#888;letter-spacing:0.05em;">{c['case_type']}</div>
          <div style="font-size:16px;font-weight:bold;">{c['case_number']}</div>
          <div style="color:#555;margin:4px 0;">{address}</div>
          <div style="font-size:12px;">{docs_html}</div>
        </div>
        """)
    return f"""
    <div style="font-family:sans-serif;max-width:600px;">
      <h2>{len(cases)} new Raleigh development case(s)</h2>
      {''.join(rows)}
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

    html = build_email_html(cases)

    resp = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [DIGEST_TO_EMAIL],
            "subject": f"Raleigh Dev Tracker: {len(cases)} new case(s)",
            "html": html,
        },
        timeout=15,
    )

    if resp.ok:
        print(f"Sent digest email for {len(cases)} case(s) to {DIGEST_TO_EMAIL}.")
    else:
        print(f"Failed to send digest email: {resp.status_code} {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
