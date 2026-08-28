"""
Stage 2: Build raleigh-map.html from the accumulated case store.

Usage:
    python3 02_build_map.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
OUT_PATH = Path(__file__).parent / "raleigh-map.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Raleigh — Development Tracker</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
<style>
  :root{
    --ink: #0a0d12; --panel: #12171f; --panel-border: #232c38;
    --amber: #e2a545; --amber-dim: #8a672f; --teal: #4fd6c4;
    --text: #dfe4ea; --text-muted: #64707e;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: var(--ink); font-family: 'Courier New', monospace; overflow: hidden; }
  #map { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #0a0d12; z-index: 1; }
  .leaflet-tile-pane { filter: saturate(0.85) brightness(0.92); }
  .leaflet-control-zoom { display: none; }
  .leaflet-control-attribution { background: rgba(10,13,18,0.75) !important; color: var(--text-muted) !important; font-family: 'Courier New', monospace; font-size: 10px; }
  .leaflet-control-attribution a { color: var(--amber-dim) !important; }
  .leaflet-popup-content-wrapper { background: var(--panel); color: var(--text); border: 1px solid var(--panel-border); border-radius: 4px; font-family: 'Courier New', monospace; }
  .leaflet-popup-tip { background: var(--panel); }
  .popup-case-type { color: var(--amber); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; }
  .popup-case-number { font-weight: bold; font-size: 15px; margin: 4px 0; }
  .popup-address { color: var(--text-muted); font-size: 12px; margin-bottom: 8px; }
  .popup-meta { color: var(--text-muted); font-size: 10px; margin-bottom: 6px; }
  .popup-docs a { color: var(--teal); font-size: 11px; display: block; text-decoration: none; margin: 2px 0; }
  .popup-docs a:hover { text-decoration: underline; }
  .hud-top { position: fixed; top: 0; left: 0; right: 0; z-index: 1000; display: flex; align-items: center; justify-content: space-between; padding: 18px 26px; pointer-events: none; background: linear-gradient(to bottom, rgba(10,13,18,0.85) 0%, rgba(10,13,18,0.0) 100%); }
  .hud-title { font-weight: 700; font-size: 22px; letter-spacing: 0.14em; color: var(--text); display: flex; align-items: baseline; gap: 10px; }
  .hud-title .unit { font-weight: 400; font-size: 10px; letter-spacing: 0.18em; color: var(--amber); border: 1px solid var(--amber-dim); padding: 2px 7px; border-radius: 2px; }
  .hud-subtitle { font-size: 10px; letter-spacing: 0.16em; color: var(--text-muted); margin-top: 3px; text-transform: uppercase; }
  .hud-meta { text-align: right; font-size: 10px; color: var(--text-muted); letter-spacing: 0.08em; line-height: 1.6; }
  .hud-meta span { color: var(--teal); }
  .zoom-ctrl { position: fixed; bottom: 20px; right: 20px; z-index: 1000; display: flex; flex-direction: column; background: rgba(18,23,31,0.82); border: 1px solid var(--panel-border); border-radius: 3px; overflow: hidden; }
  .zoom-ctrl button { width: 34px; height: 34px; background: transparent; border: none; color: var(--text); font-size: 16px; font-family: 'Courier New', monospace; cursor: pointer; }
  .zoom-ctrl button:first-child { border-bottom: 1px solid var(--panel-border); }
  .zoom-ctrl button:hover { background: rgba(226,165,69,0.12); color: var(--amber); }
  .locate-pill { position: fixed; bottom: 20px; left: 20px; z-index: 1000; background: rgba(18,23,31,0.82); border: 1px solid var(--panel-border); border-radius: 3px; padding: 0 14px; height: 40px; display: flex; align-items: center; gap: 8px; color: var(--text-muted); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; cursor: pointer; }
  .locate-pill:hover { color: var(--teal); border-color: var(--teal); }
  .locate-pill svg { width: 14px; height: 14px; }
  .vignette { position: fixed; inset: 0; z-index: 500; pointer-events: none; box-shadow: inset 0 0 160px 40px rgba(0,0,0,0.55); }
  .sidebar { position: fixed; top: 0; right: 0; height: 100vh; width: 320px; z-index: 1500; background: rgba(10,13,18,0.94); border-left: 1px solid var(--panel-border); backdrop-filter: blur(8px); transform: translateX(100%); transition: transform 0.25s ease; display: flex; flex-direction: column; }
  .sidebar.open { transform: translateX(0); }
  .sidebar-header { padding: 20px; border-bottom: 1px solid var(--panel-border); display: flex; align-items: center; justify-content: space-between; }
  .sidebar-title { font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text); }
  .sidebar-title .count { color: var(--amber); }
  .sidebar-close { color: var(--text-muted); cursor: pointer; font-size: 18px; background: none; border: none; }
  .sidebar-close:hover { color: var(--teal); }
  .sidebar-body { overflow-y: auto; flex: 1; padding: 8px 0; }
  .sidebar-item { padding: 12px 20px; border-bottom: 1px solid rgba(35,44,56,0.5); }
  .sidebar-case-type { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--amber-dim); }
  .sidebar-case-number { font-size: 13px; font-weight: bold; color: var(--text); margin: 2px 0; }
  .sidebar-reason { font-size: 10px; color: var(--text-muted); }
  .sidebar-docs a { color: var(--teal); font-size: 10px; display: block; text-decoration: none; margin-top: 4px; }
  .sidebar-docs a:hover { text-decoration: underline; }
  .sidebar-toggle { position: fixed; top: 20px; right: 20px; z-index: 1000; background: rgba(18,23,31,0.82); border: 1px solid var(--panel-border); border-radius: 3px; color: var(--text); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; padding: 10px 14px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .sidebar-toggle:hover { color: var(--amber); border-color: var(--amber-dim); }
  .sidebar-toggle .badge { background: var(--amber); color: var(--ink); border-radius: 10px; padding: 1px 7px; font-weight: bold; }
  #status { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 2000; color: var(--text-muted); font-size: 12px; letter-spacing: 0.1em; pointer-events: none; }
  @media (max-width: 640px) { .hud-title { font-size: 16px; } .hud-meta { display: none; } .sidebar { width: 100vw; } }
</style>
</head>
<body>

<div id="status">LOADING MAP…</div>
<div id="map"></div>
<div class="vignette"></div>

<div class="hud-top">
  <div>
    <div class="hud-title">RALEIGH <span class="unit">DEV TRACKER</span></div>
    <div class="hud-subtitle">Auto-updated __BUILD_DATE__</div>
  </div>
  <div class="hud-meta">
    ZOOM <span id="zoomVal">12</span><br>
    PINNED <span id="pinnedCount">0</span>
  </div>
</div>

<button class="sidebar-toggle" id="sidebarToggle">Unmapped cases <span class="badge" id="unpinnedBadge">0</span></button>

<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <div class="sidebar-title">No location found <span class="count" id="sidebarCount"></span></div>
    <button class="sidebar-close" id="sidebarClose">&times;</button>
  </div>
  <div class="sidebar-body" id="sidebarBody"></div>
</div>

<div class="locate-pill" id="locateBtn">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/></svg>
  <span>Recenter</span>
</div>

<div class="zoom-ctrl">
  <button id="zoomIn">+</button>
  <button id="zoomOut">–</button>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
  var CASES = __CASES_JSON__;

  var statusEl = document.getElementById('status');

  try {
    if (typeof L === 'undefined') throw new Error('Leaflet library failed to load');

    var RALEIGH = [35.7796, -78.6382];
    var map = L.map('map', { center: RALEIGH, zoom: 12, zoomControl: false, attributionControl: true });

    var tileLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      subdomains: 'abcd',
      maxZoom: 20
    });
    tileLayer.on('load', function () { statusEl.style.display = 'none'; });
    tileLayer.on('tileerror', function () { statusEl.textContent = 'TILE LOAD ERROR — CHECK NETWORK ACCESS'; });
    tileLayer.addTo(map);
    setTimeout(function () { if (statusEl) statusEl.style.display = 'none'; }, 4000);

    var pinned = CASES.filter(function (c) { return c.lat != null && c.lng != null; });
    var unpinned = CASES.filter(function (c) { return c.lat == null || c.lng == null; });

    document.getElementById('pinnedCount').textContent = pinned.length;
    document.getElementById('unpinnedBadge').textContent = unpinned.length;
    document.getElementById('sidebarCount').textContent = '(' + unpinned.length + ')';

    var markerIcon = L.divIcon({
      className: '',
      html: '<div style="width:14px;height:14px;border-radius:50%;background:#e2a545;border:2px solid #0a0d12;box-shadow:0 0 6px rgba(226,165,69,0.8);"></div>',
      iconSize: [14, 14], iconAnchor: [7, 7]
    });

    pinned.forEach(function (c) {
      var docsHtml = (c.documents || []).slice(0, 4).map(function (d) {
        return '<a href="' + d.href + '" target="_blank" rel="noopener">' + d.text + '</a>';
      }).join('');
      var popupHtml =
        '<div class="popup-case-type">' + c.case_type + '</div>' +
        '<div class="popup-case-number">' + c.case_number + '</div>' +
        '<div class="popup-address">' + (c.formatted_address || c.address_guess || '') + '</div>' +
        '<div class="popup-meta">' + c.committee + ' — first seen ' + (c.first_seen_at || '').slice(0, 10) + '</div>' +
        '<div class="popup-docs">' + docsHtml + '</div>';
      L.marker([c.lat, c.lng], { icon: markerIcon }).addTo(map).bindPopup(popupHtml);
    });

    var sidebarBody = document.getElementById('sidebarBody');
    unpinned.forEach(function (c) {
      var docsHtml = (c.documents || []).slice(0, 3).map(function (d) {
        return '<a href="' + d.href + '" target="_blank" rel="noopener">' + d.text + '</a>';
      }).join('');
      var reason = c.address_guess
        ? ('Address "' + c.address_guess + '" did not geocode.')
        : 'No address could be extracted from the case documents.';
      var item = document.createElement('div');
      item.className = 'sidebar-item';
      item.innerHTML =
        '<div class="sidebar-case-type">' + c.case_type + '</div>' +
        '<div class="sidebar-case-number">' + c.case_number + '</div>' +
        '<div class="sidebar-reason">' + reason + '</div>' +
        '<div class="sidebar-docs">' + docsHtml + '</div>';
      sidebarBody.appendChild(item);
    });

    var sidebar = document.getElementById('sidebar');
    document.getElementById('sidebarToggle').addEventListener('click', function () { sidebar.classList.add('open'); });
    document.getElementById('sidebarClose').addEventListener('click', function () { sidebar.classList.remove('open'); });

    map.on('zoomend', function () { document.getElementById('zoomVal').textContent = map.getZoom(); });
    document.getElementById('zoomIn').addEventListener('click', function () { map.zoomIn(); });
    document.getElementById('zoomOut').addEventListener('click', function () { map.zoomOut(); });
    document.getElementById('locateBtn').addEventListener('click', function () { map.flyTo(RALEIGH, 12, { duration: 1.1 }); });

    setTimeout(function () { map.invalidateSize(); }, 200);

  } catch (err) {
    statusEl.textContent = 'MAP FAILED TO INITIALIZE: ' + err.message;
    statusEl.style.color = '#e2a545';
  }
</script>

</body>
</html>
"""


def main():
    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    cases = list(store.values())
    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = TEMPLATE.replace("__CASES_JSON__", json.dumps(cases)).replace("__BUILD_DATE__", build_date)
    OUT_PATH.write_text(html, encoding="utf-8")

    pinned = sum(1 for c in cases if c.get("lat") is not None)
    print(f"Wrote {OUT_PATH}  ({pinned} pinned, {len(cases) - pinned} sidebar-only, {len(cases)} total)")


if __name__ == "__main__":
    main()
