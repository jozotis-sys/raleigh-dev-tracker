"""
Stage 2: Build docs/index.html from the accumulated case store.

Now a small multi-section site rather than just a map: a top nav bar
switches between Map, Table, Stats, and About views, all sharing one
embedded copy of the case data (no page reloads, no duplicated payload).

Usage:
    python3 02_build_map.py
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CASE_STORE_PATH = DATA_DIR / "case_store.json"
DOCS_DIR = Path(__file__).parent / "docs"
DOCS_DIR.mkdir(exist_ok=True)
OUT_PATH = DOCS_DIR / "index.html"

COMMITTEE_COLORS = {
    "City Council": "#d9720a",              # vivid orange-amber
    "Planning Commission": "#f2c200",       # saturated gold (was too pale to see before)
    "Design Review Commission": "#1f9e4e",  # richer green
    "Board of Adjustment": "#d62839",       # vivid red
    "Raleigh Transit Authority": "#8639e0", # vivid purple
}
DEFAULT_COLOR = "#5b6472"

STATUS_META = {
    "APPROVED":   {"label": "Approved",   "color": "#1a9850"},
    "DENIED":     {"label": "Denied",     "color": "#d62839"},
    "WITHDRAWN":  {"label": "Withdrawn",  "color": "#8a9ba8"},
    "ACTIVE":     {"label": "Active",     "color": "#5b6472"},
    "NOT_LISTED": {"label": "Not Listed", "color": "#e0850a"},
}

OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Raleigh — Development Tracker</title>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.css" />
<style>
  :root{
    --ink: #f4f5f7; --panel: #ffffff; --panel-border: #d7dbe0;
    --amber: #8f5c0c; --amber-dim: #d9b478; --teal: #0a6b61;
    --text: #1a1f26; --text-muted: #5b6472;
    --nav-h: 52px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; background: var(--ink); font-family: 'Courier New', monospace; overflow: hidden; }

  /* ---- Nav bar ---- */
  #navbar {
    position: fixed; top: 0; left: 0; right: 0; height: var(--nav-h); z-index: 3000;
    background: rgba(255,255,255,0.96); border-bottom: 1px solid var(--panel-border);
    display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
  }
  .nav-title { font-weight: 700; font-size: 15px; letter-spacing: 0.12em; color: var(--text); display: flex; align-items: baseline; gap: 8px; }
  .nav-title .unit { font-weight: 400; font-size: 9px; letter-spacing: 0.16em; color: var(--amber); border: 1px solid var(--amber-dim); padding: 1px 6px; border-radius: 2px; }
  .nav-tabs { display: flex; gap: 4px; }
  .nav-tab {
    background: transparent; border: 1px solid transparent; color: var(--text-muted);
    font-family: 'Courier New', monospace; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 7px 14px; cursor: pointer; border-radius: 3px; transition: color 0.15s, border-color 0.15s;
  }
  .nav-tab:hover { color: var(--text); }
  .nav-tab.active { color: var(--amber); border-color: var(--amber-dim); }

  /* ---- Views ---- */
  .view { position: fixed; top: var(--nav-h); left: 0; right: 0; bottom: 0; display: none; overflow: auto; }
  .view.active { display: block; }

  /* ---- Map view ---- */
  #view-map { overflow: hidden; }
  #map { position: absolute; inset: 0; background: var(--ink); z-index: 1; }
  .maplibregl-ctrl-attrib { background: rgba(255,255,255,0.85) !important; color: var(--text-muted) !important; font-family: 'Courier New', monospace; font-size: 10px; }
  .maplibregl-ctrl-attrib a { color: var(--amber-dim) !important; }
  .maplibregl-popup-content { background: var(--panel); color: var(--text); border: 1px solid var(--panel-border); border-radius: 4px; font-family: 'Courier New', monospace; padding: 12px; }
  .maplibregl-popup-tip { border-top-color: var(--panel) !important; border-bottom-color: var(--panel) !important; }
  .maplibregl-popup-close-button { color: var(--text-muted); font-size: 16px; }
  .popup-case-type { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; }
  .popup-case-number { font-weight: bold; font-size: 15px; margin: 4px 0; }
  .popup-address { color: var(--text-muted); font-size: 12px; margin-bottom: 8px; }
  .popup-meta { color: var(--text-muted); font-size: 10px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
  .popup-meta .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .popup-docs a { color: var(--teal); font-size: 11px; display: block; text-decoration: none; margin: 2px 0; }
  .popup-docs a:hover { text-decoration: underline; }
  .pin-marker { width: 17px; height: 17px; border-radius: 50%; border: 2.5px solid #1a1f26; cursor: pointer; box-shadow: 0 2px 5px rgba(0,0,0,0.4); }
  .pin-marker.not-listed { animation: pulse-ring 1.8s infinite; }
  @keyframes pulse-ring {
    0%   { box-shadow: 0 2px 5px rgba(0,0,0,0.4), 0 0 0 0 rgba(224,133,10,0.6); }
    70%  { box-shadow: 0 2px 5px rgba(0,0,0,0.4), 0 0 0 9px rgba(224,133,10,0); }
    100% { box-shadow: 0 2px 5px rgba(0,0,0,0.4), 0 0 0 0 rgba(224,133,10,0); }
  }

  .status-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; padding: 2px 8px; border-radius: 10px; border: 1px solid; }
  .status-badge .dot { width: 6px; height: 6px; border-radius: 50%; }

  .resolved-toggle {
    position: absolute; top: 16px; left: 20px; z-index: 1000;
    background: rgba(255,255,255,0.92); border: 1px solid var(--panel-border); border-radius: 3px;
    padding: 6px 12px; font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--text-muted); cursor: pointer; user-select: none;
  }
  .resolved-toggle.on { color: var(--amber); border-color: var(--amber-dim); }

  .table-status { white-space: nowrap; }

  .map-meta { position: absolute; top: 16px; right: 20px; z-index: 1000; text-align: right; font-size: 10px; color: var(--text-muted); letter-spacing: 0.08em; line-height: 1.6; background: rgba(255,255,255,0.9); padding: 8px 12px; border-radius: 3px; border: 1px solid var(--panel-border); }
  .map-meta span { color: var(--teal); }

  .zoom-ctrl { position: absolute; bottom: 20px; right: 20px; z-index: 1000; display: flex; flex-direction: column; background: rgba(255,255,255,0.92); border: 1px solid var(--panel-border); border-radius: 3px; overflow: hidden; }
  .zoom-ctrl button { width: 34px; height: 34px; background: transparent; border: none; color: var(--text); font-size: 16px; font-family: 'Courier New', monospace; cursor: pointer; }
  .zoom-ctrl button:first-child { border-bottom: 1px solid var(--panel-border); }
  .zoom-ctrl button:hover { background: rgba(226,165,69,0.12); color: var(--amber); }
  .locate-pill { position: absolute; bottom: 20px; left: 20px; z-index: 1000; background: rgba(255,255,255,0.92); border: 1px solid var(--panel-border); border-radius: 3px; padding: 0 14px; height: 40px; display: flex; align-items: center; gap: 8px; color: var(--text-muted); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; cursor: pointer; }
  .locate-pill:hover { color: var(--teal); border-color: var(--teal); }
  .locate-pill svg { width: 14px; height: 14px; }

  .legend { position: absolute; bottom: 20px; left: 190px; z-index: 1000; background: rgba(255,255,255,0.92); border: 1px solid var(--panel-border); border-radius: 3px; padding: 10px 14px; font-size: 10px; color: var(--text); letter-spacing: 0.05em; }
  .legend-row { display: flex; align-items: center; gap: 8px; padding: 2px 0; cursor: pointer; user-select: none; }
  .legend-row.disabled { opacity: 0.35; }
  .legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }

  .sidebar { position: absolute; top: 0; right: 0; height: 100%; width: 320px; z-index: 1500; background: rgba(255,255,255,0.97); border-left: 1px solid var(--panel-border); backdrop-filter: blur(8px); transform: translateX(100%); transition: transform 0.25s ease; display: flex; flex-direction: column; }
  .sidebar.open { transform: translateX(0); }
  .sidebar-header { padding: 20px; border-bottom: 1px solid var(--panel-border); display: flex; align-items: center; justify-content: space-between; }
  .sidebar-title { font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text); }
  .sidebar-title .count { color: var(--amber); }
  .sidebar-close { color: var(--text-muted); cursor: pointer; font-size: 18px; background: none; border: none; }
  .sidebar-close:hover { color: var(--teal); }
  .sidebar-body { overflow-y: auto; flex: 1; padding: 8px 0; }
  .sidebar-item { padding: 12px 20px; border-bottom: 1px solid rgba(35,44,56,0.5); }
  .sidebar-case-type { font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; display: flex; align-items: center; gap: 6px; }
  .sidebar-case-type .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .sidebar-case-number { font-size: 13px; font-weight: bold; color: var(--text); margin: 2px 0; }
  .sidebar-reason { font-size: 10px; color: var(--text-muted); }
  .sidebar-docs a { color: var(--teal); font-size: 10px; display: block; text-decoration: none; margin-top: 4px; }
  .sidebar-docs a:hover { text-decoration: underline; }
  .sidebar-toggle { position: absolute; top: 16px; right: 20px; z-index: 1000; background: rgba(255,255,255,0.92); border: 1px solid var(--panel-border); border-radius: 3px; color: var(--text); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; padding: 10px 14px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .sidebar-toggle:hover { color: var(--amber); border-color: var(--amber-dim); }
  .sidebar-toggle .badge { background: var(--amber); color: var(--ink); border-radius: 10px; padding: 1px 7px; font-weight: bold; }
  #status { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 2000; color: var(--text-muted); font-size: 12px; letter-spacing: 0.1em; pointer-events: none; }

  /* ---- Table view ---- */
  .table-wrap { padding: 24px; max-width: 1100px; margin: 0 auto; }
  #tableSearch {
    width: 100%; background: var(--panel); border: 1px solid var(--panel-border); color: var(--text);
    font-family: 'Courier New', monospace; font-size: 13px; padding: 10px 14px; border-radius: 3px; margin-bottom: 16px;
  }
  #tableSearch:focus { outline: none; border-color: var(--amber-dim); }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; padding: 10px 12px; color: var(--text-muted); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; border-bottom: 1px solid var(--panel-border); cursor: pointer; user-select: none; }
  th:hover { color: var(--amber); }
  th.sorted::after { content: ' ▾'; color: var(--amber); }
  th.sorted.asc::after { content: ' ▴'; }
  td { padding: 10px 12px; border-bottom: 1px solid rgba(35,44,56,0.5); color: var(--text); vertical-align: top; }
  .table-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .table-committee { white-space: nowrap; }
  .table-docs a { color: var(--teal); text-decoration: none; margin-right: 8px; font-size: 11px; }
  .table-docs a:hover { text-decoration: underline; }
  .table-unmapped { color: var(--text-muted); font-style: italic; }

  /* ---- Stats view ---- */
  .stats-wrap { padding: 24px; max-width: 700px; margin: 0 auto; }
  .stats-wrap h2 { font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text); margin: 28px 0 14px; }
  .stats-wrap h2:first-child { margin-top: 0; }
  .stat-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 12px; }
  .stat-label { width: 190px; flex-shrink: 0; color: var(--text-muted); }
  .stat-bar-track { flex: 1; background: rgba(0,0,0,0.07); border-radius: 2px; height: 16px; overflow: hidden; }
  .stat-bar-fill { height: 100%; border-radius: 2px; }
  .stat-count { width: 34px; text-align: right; color: var(--text); flex-shrink: 0; }

  /* ---- About view ---- */
  .about-wrap { padding: 24px; max-width: 640px; margin: 0 auto; color: var(--text); font-size: 13px; line-height: 1.7; }
  .about-wrap h2 { font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; margin: 28px 0 10px; }
  .about-wrap h2:first-child { margin-top: 0; }
  .about-wrap p { color: var(--text-muted); margin: 0 0 12px; }
  .about-wrap ul { margin: 0 0 12px; padding-left: 20px; color: var(--text-muted); }
  .about-wrap li { margin-bottom: 4px; }
  .about-wrap li .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
  .about-wrap a { color: var(--teal); }

  @media (max-width: 640px) {
    .nav-title { font-size: 12px; }
    .nav-tab { padding: 7px 9px; font-size: 10px; }
    .legend { left: 20px; bottom: 70px; }
    .sidebar { width: 100vw; }
    .stat-label { width: 110px; }
  }
</style>
</head>
<body>

<div id="navbar">
  <div class="nav-title">RALEIGH <span class="unit">DEV TRACKER</span></div>
  <div class="nav-tabs">
    <button class="nav-tab active" data-view="map">Map</button>
    <button class="nav-tab" data-view="table">Table</button>
    <button class="nav-tab" data-view="stats">Stats</button>
    <button class="nav-tab" data-view="about">About</button>
  </div>
</div>

<div id="view-map" class="view active">
  <div id="status">LOADING MAP…</div>
  <div id="map"></div>
  <div class="map-meta">ZOOM <span id="zoomVal">12</span> · PINNED <span id="pinnedCount">0</span></div>
  <button class="sidebar-toggle" id="sidebarToggle">Unmapped cases <span class="badge" id="unpinnedBadge">0</span></button>
  <div class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">No location found <span class="count" id="sidebarCount"></span></div>
      <button class="sidebar-close" id="sidebarClose">&times;</button>
    </div>
    <div class="sidebar-body" id="sidebarBody"></div>
  </div>
  <div class="legend" id="legend"></div>
<div class="resolved-toggle" id="resolvedToggle">Show resolved cases (<span id="resolvedCount">0</span>)</div>
  <div class="locate-pill" id="locateBtn">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/></svg>
    <span>Recenter</span>
  </div>
  <div class="zoom-ctrl">
    <button id="zoomIn">+</button>
    <button id="zoomOut">–</button>
  </div>
</div>

<div id="view-table" class="view">
  <div class="table-wrap">
    <input id="tableSearch" placeholder="Search case number, address, committee..." />
    <table>
      <thead>
        <tr>
          <th data-key="committee">Committee</th>
          <th data-key="case_number">Case #</th>
          <th data-key="case_type">Type</th>
          <th data-key="address">Address</th>
          <th data-key="official_status">Status</th>
          <th data-key="first_seen_at">First Seen</th>
          <th>Docs</th>
        </tr>
      </thead>
      <tbody id="casesTableBody"></tbody>
    </table>
  </div>
</div>

<div id="view-stats" class="view">
  <div class="stats-wrap">
    <h2>Cases by status</h2>
    <div id="statsByStatus"></div>
    <h2>Cases by committee</h2>
    <div id="statsByCommittee"></div>
    <h2>Cases discovered by month</h2>
    <div id="statsByMonth"></div>
  </div>
</div>

<div id="view-about" class="view">
  <div class="about-wrap">
    <h2>What this is</h2>
    <p>An unofficial, automated tracker of real-estate development activity in Raleigh, NC. Every day, a background job checks Raleigh's public meeting records for new rezoning, annexation, and other development-related cases, plots them on the map, and emails a digest of anything new.</p>

    <h2>Committees tracked</h2>
    <ul id="aboutCommitteeList"></ul>

    <h2>How it works</h2>
    <p>Meeting agendas are pulled from the City of Raleigh's public eSCRIBE meeting portal. Case documents are parsed to extract project addresses, which are geocoded via <a href="https://www.geoapify.com/" target="_blank" rel="noopener">Geoapify</a>. The map itself uses <a href="https://openfreemap.org/" target="_blank" rel="noopener">OpenFreeMap</a> tiles built on <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> data.</p>

    <h2>Accuracy note</h2>
    <p>Addresses are extracted automatically from PDF documents and may occasionally be wrong or incomplete -- always confirm details against the linked source documents before relying on this for anything official. Cases are not currently removed or flagged when resolved (approved/denied/withdrawn), so the map reflects everything ever discovered, not just active cases.</p>
  </div>
</div>

<script src="https://unpkg.com/maplibre-gl@5.10.0/dist/maplibre-gl.js"></script>
<script>
  var CASES = __CASES_JSON__;
  var COMMITTEE_COLORS = __COMMITTEE_COLORS_JSON__;
  var DEFAULT_COLOR = __DEFAULT_COLOR_JSON__;
  var STYLE_URL = __STYLE_URL_JSON__;

  var STATUS_META = __STATUS_META_JSON__;
  var RESOLVED_STATUSES = ['APPROVED', 'DENIED', 'WITHDRAWN'];

  function colorFor(committee) { return COMMITTEE_COLORS[committee] || DEFAULT_COLOR; }
  function escapeHtml(s) { return (s || '').replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

  function statusMeta(c) {
    return STATUS_META[c.official_status] || STATUS_META['ACTIVE'];
  }
  function isResolved(c) {
    return RESOLVED_STATUSES.indexOf(c.official_status) !== -1;
  }
  function statusBadgeHtml(c) {
    var meta = statusMeta(c);
    var dateStr = c.official_status_date ? ' \u2014 ' + escapeHtml(c.official_status_date) : '';
    return '<span class="status-badge" style="color:' + meta.color + ';border-color:' + meta.color + ';">' +
      '<span class="dot" style="background:' + meta.color + ';"></span>' + escapeHtml(meta.label) + dateStr +
      '</span>';
  }

  // =========================================================================
  // Related cases: group cases whose geocoded coordinates fall within
  // RELATED_DISTANCE_METERS of each other. Coordinates (not raw address
  // strings) are used as the join key -- geocoding already normalizes
  // formatting differences between sources, which a plain string match
  // would not. Computed once at the top level so both the Map view and
  // the Table view can use it regardless of which loads first.
  // =========================================================================
  var RELATED_DISTANCE_METERS = 75;

  function haversineMeters(lat1, lng1, lat2, lng2) {
    var R = 6371000;
    var toRad = function (d) { return d * Math.PI / 180; };
    var dLat = toRad(lat2 - lat1);
    var dLng = toRad(lng2 - lng1);
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
            Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  var caseByNumber = {};
  CASES.forEach(function (c) { caseByNumber[c.case_number] = c; });

  var geocodedCases = CASES.filter(function (c) { return c.lat != null && c.lng != null; });
  var relatedFor = {}; // case_number -> [{case_number, case_type, committee, official_status}, ...]
  geocodedCases.forEach(function (c) { relatedFor[c.case_number] = []; });

  for (var i = 0; i < geocodedCases.length; i++) {
    for (var j = i + 1; j < geocodedCases.length; j++) {
      var a = geocodedCases[i], b = geocodedCases[j];
      if (a.case_number === b.case_number) continue;
      var dist = haversineMeters(a.lat, a.lng, b.lat, b.lng);
      if (dist <= RELATED_DISTANCE_METERS) {
        relatedFor[a.case_number].push({ case_number: b.case_number, case_type: b.case_type, committee: b.committee, official_status: b.official_status });
        relatedFor[b.case_number].push({ case_number: a.case_number, case_type: a.case_type, committee: a.committee, official_status: a.official_status });
      }
    }
  }

  function relatedCasesHtml(c) {
    var related = relatedFor[c.case_number];
    if (!related || !related.length) return '';
    // case_number is always alphanumeric+hyphens (from our own regex),
    // so it's safe to embed directly in the onclick attribute with no
    // quote-escaping needed.
    var rows = related.map(function (r) {
      var color = colorFor(r.committee);
      return '<div style="margin-top:3px;">' +
        '<a href="#" onclick="window.jumpToCase(&quot;' + r.case_number + '&quot;); return false;" style="color:' + color + ';text-decoration:none;font-weight:bold;">' +
        escapeHtml(r.case_number) + '</a> <span style="color:var(--text-muted);">(' + escapeHtml(r.case_type) + ')</span>' +
        '</div>';
    }).join('');
    return '<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--panel-border);">' +
      '<div style="font-size:9px;letter-spacing:0.08em;text-transform:uppercase;color:var(--text-muted);">Related cases at this site</div>' +
      rows + '</div>';
  }

  // =========================================================================
  // Nav / view switching
  // =========================================================================
  var mapInitialized = false;

  document.querySelectorAll('.nav-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.nav-tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
      tab.classList.add('active');
      var viewName = tab.getAttribute('data-view');
      document.getElementById('view-' + viewName).classList.add('active');

      if (viewName === 'map') {
        if (!mapInitialized) { initMap(); mapInitialized = true; }
        setTimeout(function () { if (window._map) window._map.resize(); }, 50);
      }
    });
  });

  // =========================================================================
  // Map view
  // =========================================================================
  function initMap() {
    var statusEl = document.getElementById('status');
    try {
      if (typeof maplibregl === 'undefined') throw new Error('MapLibre GL JS failed to load');

      var RALEIGH = [-78.6382, 35.7796];
      var map = new maplibregl.Map({
        container: 'map', style: STYLE_URL, center: RALEIGH, zoom: 12, attributionControl: true
      });
      window._map = map;

      map.on('load', function () { statusEl.style.display = 'none'; });
      map.on('error', function (e) { statusEl.textContent = 'MAP LOAD ERROR — CHECK NETWORK ACCESS'; console.error(e); });
      setTimeout(function () { if (statusEl) statusEl.style.display = 'none'; }, 5000);

      var pinned = CASES.filter(function (c) { return c.lat != null && c.lng != null; });
      var unpinned = CASES.filter(function (c) { return c.lat == null || c.lng == null; });

      document.getElementById('unpinnedBadge').textContent = unpinned.length;
      document.getElementById('sidebarCount').textContent = '(' + unpinned.length + ')';

      var committeesPresent = Array.from(new Set(pinned.map(function (c) { return c.committee; }))).sort();
      var committeeVisible = {};
      committeesPresent.forEach(function (committee) { committeeVisible[committee] = true; });
      var showResolved = false;

      // markerEntries tracks every marker plus the metadata needed to
      // decide visibility from two independent toggles: the per-committee
      // legend, and the global "show resolved" switch.
      var markerEntries = [];
      var markerByCaseNumber = {};

      pinned.forEach(function (c) {
        var color = colorFor(c.committee);
        var resolved = isResolved(c);
        var docsHtml = (c.documents || []).slice(0, 4).map(function (d) {
          return '<a href="' + d.href + '" target="_blank" rel="noopener">' + escapeHtml(d.text) + '</a>';
        }).join('');
        var popupHtml =
          '<div class="popup-case-type" style="color:' + color + ';">' + escapeHtml(c.case_type) + '</div>' +
          '<div class="popup-case-number">' + escapeHtml(c.case_number) + '</div>' +
          '<div class="popup-address">' + escapeHtml(c.formatted_address || c.address_guess || '') + '</div>' +
          '<div style="margin:4px 0;">' + statusBadgeHtml(c) + '</div>' +
          '<div class="popup-meta"><span class="dot" style="background:' + color + ';"></span>' + escapeHtml(c.committee) + ' — first seen ' + (c.first_seen_at || '').slice(0, 10) + '</div>' +
          '<div class="popup-docs">' + docsHtml + '</div>' +
          relatedCasesHtml(c);

        var el = document.createElement('div');
        el.className = 'pin-marker' + (c.official_status === 'NOT_LISTED' ? ' not-listed' : '');
        el.style.background = color;
        el.style.boxShadow = '0 0 6px ' + color + 'cc';

        var popup = new maplibregl.Popup({ offset: 12 }).setHTML(popupHtml);
        var marker = new maplibregl.Marker({ element: el }).setLngLat([c.lng, c.lat]).setPopup(popup);
        markerEntries.push({ marker: marker, committee: c.committee, resolved: resolved });
        markerByCaseNumber[c.case_number] = { marker: marker, committee: c.committee };
      });

      // Lets a related-case link (from any popup) jump straight to another
      // case's marker: makes sure it's visible (bypassing the resolved-hide
      // filter if needed) then opens its popup.
      window.jumpToCase = function (caseNumber) {
        var target = markerByCaseNumber[caseNumber];
        if (!target) return;
        if (isResolved(caseByNumber[caseNumber]) && !showResolved) {
          showResolved = true;
          document.getElementById('resolvedToggle').classList.add('on');
          refreshMarkerVisibility();
        }
        if (!committeeVisible[target.committee]) {
          committeeVisible[target.committee] = true;
          refreshMarkerVisibility();
          document.querySelectorAll('.legend-row').forEach(function (row) {
            if (row.textContent.indexOf(target.committee) !== -1) row.classList.remove('disabled');
          });
        }
        map.flyTo({ center: target.marker.getLngLat(), zoom: Math.max(map.getZoom(), 16), duration: 600 });
        setTimeout(function () { target.marker.togglePopup(); }, 650);
      };

      function refreshMarkerVisibility() {
        var visibleCount = 0;
        markerEntries.forEach(function (entry) {
          var shouldShow = committeeVisible[entry.committee] && (!entry.resolved || showResolved);
          if (shouldShow) {
            entry.marker.addTo(map);
            visibleCount++;
          } else {
            entry.marker.remove();
          }
        });
        document.getElementById('pinnedCount').textContent = visibleCount;
      }

      var resolvedTotal = markerEntries.filter(function (e) { return e.resolved; }).length;
      document.getElementById('resolvedCount').textContent = resolvedTotal;
      document.getElementById('resolvedToggle').addEventListener('click', function () {
        showResolved = !showResolved;
        this.classList.toggle('on', showResolved);
        refreshMarkerVisibility();
      });

      var legendEl = document.getElementById('legend');
      committeesPresent.forEach(function (committee) {
        var color = colorFor(committee);
        var count = pinned.filter(function (c) { return c.committee === committee; }).length;
        var row = document.createElement('div');
        row.className = 'legend-row';
        row.innerHTML = '<span class="legend-dot" style="background:' + color + ';"></span>' + escapeHtml(committee) + ' (' + count + ')';
        row.addEventListener('click', function () {
          committeeVisible[committee] = !committeeVisible[committee];
          row.classList.toggle('disabled', !committeeVisible[committee]);
          refreshMarkerVisibility();
        });
        legendEl.appendChild(row);
      });

      refreshMarkerVisibility(); // establishes correct initial state (resolved cases hidden by default)

      var sidebarBody = document.getElementById('sidebarBody');
      unpinned.forEach(function (c) {
        var color = colorFor(c.committee);
        var docsHtml = (c.documents || []).slice(0, 3).map(function (d) {
          return '<a href="' + d.href + '" target="_blank" rel="noopener">' + escapeHtml(d.text) + '</a>';
        }).join('');
        var reason = c.address_guess ? ('Address "' + escapeHtml(c.address_guess) + '" did not geocode.') : 'No address could be extracted from the case documents.';
        var item = document.createElement('div');
        item.className = 'sidebar-item';
        item.innerHTML =
          '<div class="sidebar-case-type"><span class="dot" style="background:' + color + ';"></span>' + escapeHtml(c.case_type) + '</div>' +
          '<div class="sidebar-case-number">' + escapeHtml(c.case_number) + '</div>' +
          '<div style="margin:4px 0;">' + statusBadgeHtml(c) + '</div>' +
          '<div class="sidebar-reason">' + reason + '</div>' +
          '<div class="sidebar-docs">' + docsHtml + '</div>';
        sidebarBody.appendChild(item);
      });

      var sidebar = document.getElementById('sidebar');
      document.getElementById('sidebarToggle').addEventListener('click', function () { sidebar.classList.add('open'); });
      document.getElementById('sidebarClose').addEventListener('click', function () { sidebar.classList.remove('open'); });

      map.on('zoom', function () { document.getElementById('zoomVal').textContent = Math.round(map.getZoom()); });
      document.getElementById('zoomIn').addEventListener('click', function () { map.zoomIn(); });
      document.getElementById('zoomOut').addEventListener('click', function () { map.zoomOut(); });
      document.getElementById('locateBtn').addEventListener('click', function () { map.flyTo({ center: RALEIGH, zoom: 12, duration: 1100 }); });

    } catch (err) {
      statusEl.textContent = 'MAP FAILED TO INITIALIZE: ' + err.message;
      statusEl.style.color = '#e2a545';
    }
  }

  // Map is the default active tab, so initialize it right away.
  initMap();
  mapInitialized = true;

  // =========================================================================
  // Table view
  // =========================================================================
  var tableRows = CASES.map(function (c) {
    return {
      committee: c.committee || '',
      case_number: c.case_number || '',
      case_type: c.case_type || '',
      address: c.formatted_address || c.address_guess || '',
      mapped: c.lat != null,
      official_status: c.official_status || 'ACTIVE',
      official_status_display: c.official_status_display || '',
      first_seen_at: c.first_seen_at || '',
      documents: c.documents || []
    };
  });

  var sortKey = 'first_seen_at';
  var sortAsc = false;

  function renderTable() {
    var query = document.getElementById('tableSearch').value.toLowerCase();
    var rows = tableRows.filter(function (r) {
      if (!query) return true;
      return (r.committee + ' ' + r.case_number + ' ' + r.case_type + ' ' + r.address + ' ' + r.official_status_display).toLowerCase().indexOf(query) !== -1;
    });

    rows.sort(function (a, b) {
      var av = a[sortKey], bv = b[sortKey];
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

    var tbody = document.getElementById('casesTableBody');
    tbody.innerHTML = rows.map(function (r) {
      var color = colorFor(r.committee);
      var meta = STATUS_META[r.official_status] || STATUS_META['ACTIVE'];
      var docsHtml = r.documents.slice(0, 2).map(function (d) {
        return '<a href="' + d.href + '" target="_blank" rel="noopener">' + escapeHtml(d.text.slice(0, 24)) + (d.text.length > 24 ? '…' : '') + '</a>';
      }).join('');
      var addressHtml = r.address
        ? escapeHtml(r.address)
        : '<span class="table-unmapped">not found</span>';
      var statusHtml = '<span class="status-badge" style="color:' + meta.color + ';border-color:' + meta.color + ';">' +
        '<span class="dot" style="background:' + meta.color + ';"></span>' + escapeHtml(meta.label) + '</span>';
      var related = relatedFor[r.case_number] || [];
      var relatedHtml = related.length
        ? ' <span title="' + related.map(function (x) { return x.case_number; }).join(', ') + '" style="color:var(--teal);font-size:10px;">(+' + related.length + ' related)</span>'
        : '';
      return '<tr>' +
        '<td class="table-committee"><span class="table-dot" style="background:' + color + ';"></span>' + escapeHtml(r.committee) + '</td>' +
        '<td>' + escapeHtml(r.case_number) + '</td>' +
        '<td>' + escapeHtml(r.case_type) + '</td>' +
        '<td>' + addressHtml + relatedHtml + '</td>' +
        '<td class="table-status">' + statusHtml + '</td>' +
        '<td>' + escapeHtml(r.first_seen_at.slice(0, 10)) + '</td>' +
        '<td class="table-docs">' + docsHtml + '</td>' +
        '</tr>';
    }).join('');
  }

  document.getElementById('tableSearch').addEventListener('input', renderTable);

  document.querySelectorAll('th[data-key]').forEach(function (th) {
    th.addEventListener('click', function () {
      var key = th.getAttribute('data-key');
      if (sortKey === key) { sortAsc = !sortAsc; } else { sortKey = key; sortAsc = true; }
      document.querySelectorAll('th[data-key]').forEach(function (t) { t.classList.remove('sorted', 'asc'); });
      th.classList.add('sorted');
      if (sortAsc) th.classList.add('asc');
      renderTable();
    });
  });

  renderTable();

  // =========================================================================
  // Stats view
  // =========================================================================
  (function renderStats() {
    var byStatus = {};
    CASES.forEach(function (c) { var s = c.official_status || 'ACTIVE'; byStatus[s] = (byStatus[s] || 0) + 1; });
    var statusOrder = ['ACTIVE', 'NOT_LISTED', 'APPROVED', 'DENIED', 'WITHDRAWN'];
    var maxStatusCount = Math.max.apply(null, Object.keys(byStatus).map(function (k) { return byStatus[k]; }).concat([1]));

    var statusHtml = statusOrder.filter(function (s) { return byStatus[s]; }).map(function (s) {
      var count = byStatus[s];
      var meta = STATUS_META[s] || STATUS_META['ACTIVE'];
      var pct = Math.round((count / maxStatusCount) * 100);
      return '<div class="stat-row">' +
        '<div class="stat-label">' + escapeHtml(meta.label) + '</div>' +
        '<div class="stat-bar-track"><div class="stat-bar-fill" style="width:' + pct + '%;background:' + meta.color + ';"></div></div>' +
        '<div class="stat-count">' + count + '</div>' +
        '</div>';
    }).join('');
    document.getElementById('statsByStatus').innerHTML = statusHtml || '<p style="color:var(--text-muted)">No data yet.</p>';

    var byCommittee = {};
    CASES.forEach(function (c) { byCommittee[c.committee] = (byCommittee[c.committee] || 0) + 1; });
    var committeeEntries = Object.keys(byCommittee).map(function (k) { return [k, byCommittee[k]]; }).sort(function (a, b) { return b[1] - a[1]; });
    var maxCommitteeCount = Math.max.apply(null, committeeEntries.map(function (e) { return e[1]; }).concat([1]));

    var committeeHtml = committeeEntries.map(function (entry) {
      var name = entry[0], count = entry[1];
      var color = colorFor(name);
      var pct = Math.round((count / maxCommitteeCount) * 100);
      return '<div class="stat-row">' +
        '<div class="stat-label">' + escapeHtml(name) + '</div>' +
        '<div class="stat-bar-track"><div class="stat-bar-fill" style="width:' + pct + '%;background:' + color + ';"></div></div>' +
        '<div class="stat-count">' + count + '</div>' +
        '</div>';
    }).join('');
    document.getElementById('statsByCommittee').innerHTML = committeeHtml || '<p style="color:var(--text-muted)">No data yet.</p>';

    var byMonth = {};
    CASES.forEach(function (c) {
      var month = (c.first_seen_at || '').slice(0, 7);
      if (!month) return;
      byMonth[month] = (byMonth[month] || 0) + 1;
    });
    var monthEntries = Object.keys(byMonth).map(function (k) { return [k, byMonth[k]]; }).sort(function (a, b) { return a[0] < b[0] ? -1 : 1; });
    var maxMonthCount = Math.max.apply(null, monthEntries.map(function (e) { return e[1]; }).concat([1]));

    var monthHtml = monthEntries.map(function (entry) {
      var month = entry[0], count = entry[1];
      var pct = Math.round((count / maxMonthCount) * 100);
      return '<div class="stat-row">' +
        '<div class="stat-label">' + month + '</div>' +
        '<div class="stat-bar-track"><div class="stat-bar-fill" style="width:' + pct + '%;background:var(--teal);"></div></div>' +
        '<div class="stat-count">' + count + '</div>' +
        '</div>';
    }).join('');
    document.getElementById('statsByMonth').innerHTML = monthHtml || '<p style="color:var(--text-muted)">No data yet.</p>';
  })();

  // =========================================================================
  // About view
  // =========================================================================
  (function renderAbout() {
    var list = document.getElementById('aboutCommitteeList');
    Object.keys(COMMITTEE_COLORS).forEach(function (name) {
      var li = document.createElement('li');
      li.innerHTML = '<span class="dot" style="background:' + COMMITTEE_COLORS[name] + ';"></span>' + escapeHtml(name);
      list.appendChild(li);
    });
  })();
</script>

</body>
</html>
"""


def main():
    store = json.loads(CASE_STORE_PATH.read_text(encoding="utf-8")) if CASE_STORE_PATH.exists() else {}
    cases = list(store.values())
    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = (
        TEMPLATE
        .replace("__CASES_JSON__", json.dumps(cases))
        .replace("__COMMITTEE_COLORS_JSON__", json.dumps(COMMITTEE_COLORS))
        .replace("__DEFAULT_COLOR_JSON__", json.dumps(DEFAULT_COLOR))
        .replace("__STYLE_URL_JSON__", json.dumps(OPENFREEMAP_STYLE_URL))
        .replace("__STATUS_META_JSON__", json.dumps(STATUS_META))
    )
    OUT_PATH.write_text(html, encoding="utf-8")

    pinned = sum(1 for c in cases if c.get("lat") is not None)
    committees = Counter(c.get("committee", "Unknown") for c in cases)
    print(f"Wrote {OUT_PATH}  ({pinned} pinned, {len(cases) - pinned} sidebar-only, {len(cases)} total)")
    print(f"Committees: {dict(committees)}")


if __name__ == "__main__":
    main()
