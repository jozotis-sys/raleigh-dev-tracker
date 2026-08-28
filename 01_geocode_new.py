"""
Stage 1: Geocode any cases in the store that don't have coordinates yet.

Requires GEOAPIFY_API_KEY to be set (as a GitHub Actions secret in CI,
or exported locally for manual runs).

Usage:
    python3 01_geocode_new.py
"""

import os
import json
import time
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
GEOCODE_CACHE_PATH = DATA_DIR / "geocode_cache.json"

API_KEY = os.environ.get("GEOAPIFY_API_KEY")
GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"

CITY_CONTEXT = "Raleigh, NC"
RALEIGH_BIAS = "-79.0,35.5,-78.3,36.1"  # min_lon,min_lat,max_lon,max_lat


def geocode(address: str):
    params = {
        "text": f"{address}, {CITY_CONTEXT}",
        "apiKey": API_KEY,
        "filter": f"rect:{RALEIGH_BIAS}",
        "limit": 1,
    }
    resp = requests.get(GEOCODE_URL, params=params, timeout=15)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        return None
    coords = features[0]["geometry"]["coordinates"]
    props = features[0].get("properties", {})
    return {"lat": coords[1], "lng": coords[0], "formatted": props.get("formatted")}


def main():
    if not API_KEY:
        print("ERROR: GEOAPIFY_API_KEY is not set.")
        sys.exit(1)

    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    cache = json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8")) if GEOCODE_CACHE_PATH.exists() else {}

    to_process = [
        (num, entry) for num, entry in store.items()
        if entry.get("address_guess") and entry.get("lat") is None
    ]
    print(f"{len(to_process)} case(s) need geocoding ({len(store) - len(to_process)} already done or address-less).")

    for case_number, entry in to_process:
        address = entry["address_guess"]

        if address in cache:
            result = cache[address]
            print(f"{case_number}: \"{address}\" -> (cached) {result}")
        else:
            print(f"{case_number}: geocoding \"{address}\" ...", end=" ")
            try:
                result = geocode(address)
            except Exception as e:
                print(f"ERROR ({e})")
                continue
            cache[address] = result
            GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
            print(result)
            time.sleep(0.5)

        if result:
            entry["lat"] = result["lat"]
            entry["lng"] = result["lng"]
            entry["formatted_address"] = result.get("formatted") or address
        else:
            entry["geocode_failed"] = True

    CASE_STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")
    pinned = sum(1 for e in store.values() if e.get("lat") is not None)
    print(f"\n{pinned}/{len(store)} case(s) in store now have coordinates.")


if __name__ == "__main__":
    main()
