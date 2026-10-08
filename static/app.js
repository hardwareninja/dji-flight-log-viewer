/* DJI Flight Log Viewer — frontend */
"use strict";

const SERIES = [
  { key: "height",    label: "GPS Height MSL", color: "#4fc3f7", scale: 3, fmt: v => v.toFixed(1) + " m" },
  { key: "altitude",  label: "Altitude ASL",   color: "#81c784", scale: 3, fmt: v => v.toFixed(1) + " m" },
  { key: "vps",       label: "VPS Height",     color: "#ffb74d", scale: 3, fmt: v => v.toFixed(1) + " m" },
  { key: "vx",        label: "Speed X",        color: "#e57373", scale: 4, fmt: v => v.toFixed(1) + " m/s" },
  { key: "vy",        label: "Speed Y",        color: "#ba68c8", scale: 4, fmt: v => v.toFixed(1) + " m/s" },
  { key: "vz",        label: "Speed Z (Vert)", color: "#ffd54f", scale: 4, fmt: v => v.toFixed(1) + " m/s" },
  { key: "hspeed",    label: "H-Speed",        color: "#4dd0e1", scale: 4, fmt: v => v.toFixed(1) + " m/s" },
  { key: "battery",   label: "Battery %",      color: "#a5d6a7", scale: 5, fmt: v => v.toFixed(0) + " %" },
  { key: "voltage",   label: "Battery V",      color: "#f48fb1", scale: 5, fmt: v => v.toFixed(2) + " V" },
  { key: "current",   label: "Current A",      color: "#ce93d8", scale: 5, fmt: v => v.toFixed(1) + " A" },
  { key: "temperature", label: "Batt Temp",    color: "#ff8a65", scale: 5, fmt: v => v.toFixed(0) + " °C" },
  { key: "pitch",     label: "Pitch",          color: "#90caf9", scale: 6, fmt: v => v.toFixed(1) + "°" },
  { key: "roll",      label: "Roll",           color: "#b0bec5", scale: 6, fmt: v => v.toFixed(1) + "°" },
  { key: "yaw",       label: "Yaw",            color: "#fff59d", scale: 6, fmt: v => v.toFixed(1) + "°" },
  { key: "gps_num",   label: "Sats",           color: "#c5e1a5", scale: 7, fmt: v => v.toFixed(0) },
  { key: "aileron",   label: "RC Aileron",     color: "#ef9a9a", scale: 8, fmt: v => v.toFixed(0) },
  { key: "elevator",  label: "RC Elevator",    color: "#b39ddb", scale: 8, fmt: v => v.toFixed(0) },
  { key: "throttle",  label: "RC Throttle",    color: "#9fa8da", scale: 8, fmt: v => v.toFixed(0) },
  { key: "rudder",    label: "RC Rudder",      color: "#80cbc4", scale: 8, fmt: v => v.toFixed(0) },
];
const DEFAULT_ON = new Set(["height", "altitude", "vz", "hspeed", "battery", "voltage"]);

const state = {
  logs: [], current: null, data: null,
  sel: new Set(DEFAULT_ON),
  plot: null, uplot: null,
  map: null, track: null, marker: null, tiles: null,
  view: { i0: 0, i1: Infinity },
  playing: false, playTimer: null, simTime: 0,
};

const $ = id => document.getElementById(id);
const toast = (msg, isErr) => {
  const t = $("toast");
  t.textContent = msg;
  t.className = isErr ? "error" : "";
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 3200);
};
const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleString() : "—";
const fmtDur = s => {
  if (s == null) return "—";
  const m = Math.floor(s / 60), sec = (s % 60).toFixed(0).padStart(2, "0");
  return `${m}:${sec}`;
};

/* ================= file list ================= */
async function loadLogs() {
  const r = await fetch("/api/logs");
  state.logs = await r.json();
  renderFileList();
}

function renderFileList() {
  const ul = $("fileList");
  ul.innerHTML = "";
  $("fileCount").textContent = state.logs.length;
  for (const lg of state.logs) {
    const li = document.createElement("li");
    li.textContent = lg.name;
    li.title = `${lg.name} — ${lg.aircraft || "not parsed"}`;
    if (state.current === lg.name) li.classList.add("active");
    li.onclick = () => openLog(lg.name);
    ul.appendChild(li);
  }
}

/* ================= API key handling ================= */
function getApiKey() {
  return ($("apiKey").value || "").trim() || localStorage.getItem("djiApiKey") || "";
}
function setKeyStatus(msg, bad) {
  const el = $("keyStatus");
  el.textContent = msg;
  el.classList.toggle("key-bad", !!bad);
}
function initApiKeyUI() {
  const saved = localStorage.getItem("djiApiKey") || "";
  $("apiKey").value = saved;
  setKeyStatus(saved ? "Key saved in this browser." : "No key set — enter your DJI API key above.",
               !saved);
  $("btnSaveKey").onclick = () => {
    const k = $("apiKey").value.trim();
    if (!k) { setKeyStatus("Please paste a key first.", true); return; }
    localStorage.setItem("djiApiKey", k);
    setKeyStatus("Key saved in this browser.");
    toast("API key saved");
  };
  $("btnClearKey").onclick = () => {
    localStorage.removeItem("djiApiKey");
    $("apiKey").value = "";
    setKeyStatus("Key cleared.", true);
  };
}

async function openLog(name) {
  stopPlay();
  state.simTime = 0;
  state.current = name;
  renderFileList();
  $("logTitle").textContent = name;
  toast("Parsing " + name + " …");
  const r = await fetch("/api/log?name=" + encodeURIComponent(name),
                        { headers: { "X-DJI-Key": getApiKey() } });
  const j = await r.json();
  if (j.error) {
    if (j.need_key) {
      setKeyStatus("A DJI API key is required to decrypt this log. Paste yours above and retry.", true);
      $("apiKey").focus();
    }
    toast("Error: " + j.error, true);
    return;
  }
  state.data = j;
  $("frameStats").textContent =
    `${j.frames.length} frames · v${j.version} · parsed in ${j.parse_seconds}s`;
  buildInfo(j);
  buildTimeline(j);
  buildSeriesBoxes();
  buildPlot();
  buildMap();
  buildTable();
  buildForensics(j);
  $("btnExport").href = "data:application/json," +
    encodeURIComponent(JSON.stringify(j.frames));
  toast(`${j.frames.length} frames loaded`);
}

/* ================= forensics ================= */
function cleanStr(s) {
  if (!s) return "—";
  const t = String(s).replace(/[^\x20-\x7E]/g, "").trim();
  return t || "—";
}
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000, r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function analyzeFlight(j) {
  const f = j.frames, d = j.details || {}, rec = j.recover || {};
  const flags = [];
  const phases = [];

  // ---- GPS integrity & anomalies ----
  let gpsLossStart = null, gpsLossCount = 0;
  let maxSpeed = 0, maxAlt = -Infinity, minAlt = Infinity;
  let prev = null;
  for (let i = 0; i < f.length; i++) {
    const x = f[i];
    maxSpeed = Math.max(maxSpeed, x.hspeed || 0);
    if (x.height != null) { maxAlt = Math.max(maxAlt, x.height); minAlt = Math.min(minAlt, x.height); }
    if (!x.gps_valid && gpsLossStart === null) gpsLossStart = i;
    if (x.gps_valid && gpsLossStart !== null) {
      if (i - gpsLossStart >= 5) { gpsLossCount++; flags.push({ sev: "warn", t: f[gpsLossStart].fly_time, msg: `GPS fix lost for ${((x.fly_time - f[gpsLossStart].fly_time)).toFixed(1)} s (${i - gpsLossStart} frames)` }); }
      gpsLossStart = null;
    }
    if (prev && x.gps_valid && prev.gps_valid && x.lat && prev.lat) {
      const dt = x.fly_time - prev.fly_time;
      if (dt > 0 && dt < 5) {
        const dist = haversine(prev.lat, prev.lon, x.lat, x.lon);
        const spd = dist / dt;
        if (spd > 40) flags.push({ sev: "alert", t: x.fly_time, msg: `Position jump ${dist.toFixed(0)} m in ${dt.toFixed(1)} s (${spd.toFixed(0)} m/s) — possible GPS glitch/spoof` });
      }
    }
    if (prev && x.height != null && prev.height != null) {
      const dt = x.fly_time - prev.fly_time;
      if (dt > 0 && dt < 5 && Math.abs(x.height - prev.height) / dt > 15)
        flags.push({ sev: "warn", t: x.fly_time, msg: `Altitude jump ${(x.height - prev.height).toFixed(1)} m in ${dt.toFixed(1)} s` });
    }
    if (x.voltage && x.voltage > 0 && x.voltage < 6.4)
      flags.push({ sev: "alert", t: x.fly_time, msg: `Battery voltage critically low: ${x.voltage.toFixed(2)} V` });
    prev = x;
  }
  if (gpsLossStart !== null && f.length - gpsLossStart >= 5) gpsLossCount++;

  // dedupe repeated flags (keep first of each message prefix per 10s bucket)
  const seen = new Set();
  const uniqFlags = flags.filter(fl => {
    const k = fl.msg.slice(0, 24) + "|" + Math.floor((fl.t || 0) / 10);
    if (seen.has(k)) return false; seen.add(k); return true;
  }).slice(0, 40);

  // ---- flight phases ----
  const airborne = x => x.motor_on && !x.on_ground;
  let cur = null;
  for (const x of f) {
    const phase = !airborne(x) ? "Ground" : (x.mode || "").includes("Go Home") ? "Return-to-Home" : "Airborne";
    if (!cur || cur.name !== phase) {
      if (cur) { cur.end = x.fly_time; phases.push(cur); }
      cur = { name: phase, start: x.fly_time, end: x.fly_time };
    } else cur.end = x.fly_time;
  }
  if (cur) phases.push(cur);

  // ---- summary stats ----
  const gpsFrames = f.filter(x => x.gps_valid && x.lat);
  const bats = f.map(x => x.battery).filter(v => v != null && v > 0 && v <= 100);
  const temps = f.map(x => x.temperature).filter(v => v && v > 0);
  const summary = {
    frames: f.length,
    duration: f.length ? f[f.length - 1].fly_time : 0,
    maxSpeed, maxAlt: isFinite(maxAlt) ? maxAlt : 0, minAlt: isFinite(minAlt) ? minAlt : 0,
    gpsCoverage: f.length ? (gpsFrames.length / f.length * 100) : 0,
    gpsLossEvents: gpsLossCount,
    batteryStart: bats[0] ?? null, batteryEnd: bats[bats.length - 1] ?? null,
    tempMax: temps.length ? Math.max(...temps) : null,
    distance: d.total_distance ?? null,
    flagCount: uniqFlags.length,
  };
  const identity = {
    aircraft: cleanStr(rec.aircraft_name || d.aircraft_name),
    aircraftSn: cleanStr(rec.aircraft_sn || d.aircraft_sn),
    cameraSn: cleanStr(rec.camera_sn || d.camera_sn),
    rcSn: cleanStr(rec.rc_sn || d.rc_sn),
    appVersion: cleanStr(rec.app_version || d.app_version),
    platform: cleanStr(rec.platform),
  };
  return { identity, summary, flags: uniqFlags, phases };
}

function buildForensics(j) {
  const a = analyzeFlight(j);
  state.analysis = a;
  const id = a.identity, s = a.summary;
  const row = (k, v) => `<div class="row"><span>${k}</span><span>${v}</span></div>`;
  $("forensicBody").innerHTML = `
    <h5>Device Identity</h5>
    ${row("Aircraft", id.aircraft)}
    ${row("Aircraft SN", id.aircraftSn)}
    ${row("Camera SN", id.cameraSn)}
    ${row("RC SN", id.rcSn)}
    ${row("App version", id.appVersion)}
    <h5>Flight Summary</h5>
    ${row("Duration", (s.duration / 60).toFixed(1) + " min")}
    ${row("Distance", s.distance != null ? s.distance.toFixed(0) + " m" : "—")}
    ${row("Max speed", s.maxSpeed.toFixed(1) + " m/s")}
    ${row("Max height", s.maxAlt.toFixed(1) + " m")}
    ${row("GPS coverage", s.gpsCoverage.toFixed(0) + " %")}
    ${row("GPS loss events", s.gpsLossEvents)}
    ${row("Battery", (s.batteryStart ?? "—") + " % → " + (s.batteryEnd ?? "—") + " %")}
    ${row("Max batt temp", s.tempMax != null ? s.tempMax.toFixed(1) + " °C" : "—")}
    <h5>Flight Phases</h5>
    ${a.phases.map(p => row(p.name, (p.end - p.start).toFixed(0) + " s")).join("") || "<div class='muted'>No phases</div>"}
    <h5>Anomaly Flags (${a.flags.length})</h5>
    ${a.flags.length ? a.flags.map(fl =>
      `<div class="flag flag-${fl.sev}"><b>${fl.t != null ? fl.t.toFixed(1) + "s" : ""}</b> ${fl.msg}</div>`).join("")
      : "<div class='muted'>No anomalies detected.</div>"}`;
}

function exportForensicReport() {
  if (!state.analysis) return;
  const a = state.analysis, id = a.identity, s = a.summary;
  const lines = [
    ["DJI Flight Log — Forensic Report"],
    ["File", state.current || ""],
    ["Generated", new Date().toISOString()],
    [],
    ["DEVICE IDENTITY"],
    ["Aircraft", id.aircraft], ["Aircraft SN", id.aircraftSn],
    ["Camera SN", id.cameraSn], ["RC SN", id.rcSn],
    ["App version", id.appVersion],
    [],
    ["FLIGHT SUMMARY"],
    ["Frames", s.frames], ["Duration s", s.duration.toFixed(1)],
    ["Distance m", s.distance != null ? s.distance.toFixed(1) : ""],
    ["Max speed m/s", s.maxSpeed.toFixed(2)], ["Max height m", s.maxAlt.toFixed(1)],
    ["GPS coverage %", s.gpsCoverage.toFixed(1)], ["GPS loss events", s.gpsLossEvents],
    ["Battery start %", s.batteryStart ?? ""], ["Battery end %", s.batteryEnd ?? ""],
    ["Max battery temp C", s.tempMax != null ? s.tempMax.toFixed(1) : ""],
    [],
    ["FLIGHT PHASES"],
    ["Phase", "Start s", "End s", "Duration s"],
    ...a.phases.map(p => [p.name, p.start.toFixed(1), p.end.toFixed(1), (p.end - p.start).toFixed(1)]),
    [],
    ["ANOMALY FLAGS"],
    ["Severity", "Time s", "Description"],
    ...a.flags.map(fl => [fl.sev, fl.t != null ? fl.t.toFixed(1) : "", '"' + fl.msg.replaceAll('"', "'") + '"']),
  ];
  const csv = lines.map(r => r.join(",")).join("\n");
  const el = document.createElement("a");
  el.href = "data:text/csv," + encodeURIComponent(csv);
  el.download = (state.current || "flight").replace(/\.txt$/, "") + "_forensic_report.csv";
  el.click();
}

/* ================= sidebar info ================= */
function buildInfo(j) {
  const d = j.details || {}, rec = j.recover || {};
  const rows = [
    ["Aircraft", rec.aircraft_name || d.aircraft_name || "—"],
    ["SN", rec.aircraft_sn || d.aircraft_sn || "—"],
    ["App version", rec.app_version || d.app_version || "—"],
    ["Start", fmtTime(d.start_time)],
    ["Duration", fmtDur(d.total_time)],
    ["Distance", d.total_distance != null ? d.total_distance.toFixed(0) + " m" : "—"],
    ["Max height", d.max_height != null ? d.max_height.toFixed(1) + " m" : "—"],
    ["Max h-speed", d.max_h_speed != null ? d.max_h_speed.toFixed(1) + " m/s" : "—"],
    ["Max v-speed", d.max_v_speed != null ? d.max_v_speed.toFixed(1) + " m/s" : "—"],
    ["Log version", "v" + j.version],
    ["Home", [d.area_code, d.city, d.area].filter(Boolean).join(", ") || "—"],
  ];
  $("flightInfo").innerHTML = rows.map(
    ([k, v]) => `<span class="k">${k}</span><span class="v">${esc(v)}</span>`).join("");
}

const esc = v => String(v ?? "—").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function buildTimeline(j) {
  const f = j.frames;
  const ev = [];
  const firstTs = (f.find(x => x.ts) || {}).ts || (j.details || {}).start_time;
  ev.push(["Power On / App Start", firstTs]);
  const motorIdx = f.findIndex(x => x.motor_on);
  if (motorIdx >= 0) {
    ev.push(["First Motors", frameTs(f, motorIdx, firstTs)]);
    const takeoff = f.findIndex((x, i) => i >= motorIdx && x.height > 0.5);
    if (takeoff >= 0) ev.push(["Take-off", frameTs(f, takeoff, firstTs)]);
    for (let i = f.length - 1; i >= 0; i--) {
      if (f[i].motor_on) { ev.push(["Flight End", frameTs(f, i, firstTs)]); break; }
    }
  }
  ev.push(["Log End", frameTs(f, f.length - 1, firstTs)]);
  $("timeline").innerHTML = ev.map(([k, ts]) =>
    `<li>◆ <b>${esc(k)}</b><br><span style="margin-left:14px">${fmtTime(ts)}</span></li>`).join("");
}
const frameTs = (f, i, fallback) =>
  (f.slice(0, i + 1).findLast?.(x => x.ts))?.ts ||
  (fallback != null ? fallback + (f[i].fly_time || 0) : null);

/* ================= chart ================= */
function buildSeriesBoxes() {
  const f = state.data.frames;
  const has = k => f.some(x => x[k] != null);
  const wrap = $("seriesBoxes");
  wrap.innerHTML = "";
  const filt = $("colFilter").value.toLowerCase();
  for (const s of SERIES) {
    if (s.key === "hspeed") continue; // derived, always computed
    if (!has(s.key)) continue;
    if (filt && !s.label.toLowerCase().includes(filt)) continue;
    const id = "chk_" + s.key;
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" id="${id}"
      ${state.sel.has(s.key) ? "checked" : ""}
      style="accent-color:${s.color}"> <span style="color:${s.color}">●</span> ${s.label}`;
    wrap.appendChild(lab);
    lab.querySelector("input").onchange = e => {
      e.target.checked ? state.sel.add(s.key) : state.sel.delete(s.key);
      buildPlot();
    };
  }
  if (!state.sel.has("hspeed")) state.sel.add("hspeed");
}
$("colFilter").oninput = () => state.data && buildSeriesBoxes();

function visibleFrames() {
  const step = +$("sampleSel").value;
  const f = state.data.frames;
  if (step <= 1) return f;
  return f.filter((_, i) => i % step === 0);
}

function buildPlot() {
  if (!state.data) return;
  const f = visibleFrames();
  const xs = f.map(x => x.fly_time ?? 0);
  const active = SERIES.filter(s => state.sel.has(s.key) &&
    f.some(x => x[s.key] != null) &&
    !f.every(x => x[s.key] != null && x[s.key] === 0));
  const data = [xs, ...active.map(s => f.map(x => x[s.key] == null ? null : x[s.key]))];

  const legend = $("chartLegend");
  legend.innerHTML = "";
  active.forEach((s, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = s.color;
    chip.textContent = s.label;
    chip.onclick = () => {
      state.sel.has(s.key) ? state.sel.delete(s.key) : state.sel.add(s.key);
      buildSeriesBoxes(); buildPlot();
    };
    legend.appendChild(chip);
  });

  const multiY = $("multiY").checked;
  const n = Math.max(active.length, 1);
  const width = $("chart").clientWidth || 900;
  const height = Math.max(240, Math.min(430, 200 + n * 26));

  const fmtElapsed = s => {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};
const opts = {
    width, height,
    cursor: { sync: false, datapoints: { prox: 12 } },
    legend: { show: false },
    scales: multiY ? {} : { y: { auto: true } },
    axes: [
      { values: (u, ticks) => ticks.map(t => fmtElapsed(t)) },
      ...active.map(() => ({ grid: { show: false } })),
    ].slice(0, multiY ? active.length + 1 : 2),
    series: [
      { label: "t" },
      ...active.map(s => ({
        label: s.label, stroke: s.color,
        width: 1.4,
        points: $("showPoints").checked ? { show: true, size: 3 } : { show: false },
        scale: multiY ? s.scale : "y",
        value: s.fmt,
      })),
    ],
    hooks: {
      setCursor: [u => {
        if (u.cursor.idx != null) hoverFrame(u.cursor.idx);
      }],
      setSelect: [u => {
        if (u.select.width > 5) zoomToTimeRange(u.select.min, u.select.max);
      }],
    },
  };
  if (state.uplot) state.uplot.destroy();
  state.uplot = new uPlot(opts, data, $("chart"));
}

$("sampleSel").onchange = buildPlot;
$("multiY").onchange = buildPlot;
$("showPoints").onchange = buildPlot;
$("btnFit").onclick = () => {
  state.view = { i0: 0, i1: Infinity };
  buildPlot(); fitMap();
};

function zoomToTimeRange(t0, t1) {
  const f = state.data.frames;
  const i0 = f.findIndex(x => (x.fly_time ?? 0) >= t0);
  let i1 = f.findIndex(x => (x.fly_time ?? 0) >= t1);
  if (i1 === -1) i1 = f.length;
  state.view = { i0: Math.max(i0, 0), i1 };
  buildPlot();
  if ($("syncRange").checked) fitMap(state.view.i0, state.view.i1);
}

/* ================= map ================= */
function buildMap() {
  if (state.map) { state.map.remove(); state.map = null; state.track = null; state.marker = null; }
  state.map = L.map("map", { preferCanvas: true }).setView([22, 114], 13);
  setTiles("sat");
  const f = state.data.frames;
  const pts = f.filter(x => x.lat != null && (x.lat !== 0 || x.lon !== 0))
    .map(x => [x.lat, x.lon]);
  if (!pts.length) {
    $("pointDetails").classList.remove("hidden");
    $("pointDetails").innerHTML =
      "<h4>Point Details</h4><div class='row'><span>No GPS data in this log</span></div>";
    toast("No GPS positions in this log", true);
    return;
  }
  state.track = L.polyline(pts, { color: "#2f81f7", weight: 3 }).addTo(state.map);
  state.map.fitBounds(state.track.getBounds().pad(0.15));
  state.marker = L.circleMarker(pts[0], {
    radius: 7, color: "#fff", weight: 2, fillColor: "#3fb950", fillOpacity: 1,
  }).addTo(state.map);
  $("pointDetails").classList.remove("hidden");
  showPoint(state.data.frames.findIndex(x => x.lat != null && (x.lat !== 0 || x.lon !== 0)));
}

function setTiles(style) {
  if (state.tiles) state.map.removeLayer(state.tiles);
  const url = {
    sat: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    osm: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    topo: "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
  }[style];
  state.tiles = L.tileLayer(url, { maxZoom: 19, attribution: "© Esri / OSM" })
    .addTo(state.map);
}
$("mapStyle").onchange = e => setTiles(e.target.value);
$("btnFitMap").onclick = () => fitMap();

function fitMap(i0, i1) {
  if (!state.track) return;
  const f = state.data.frames;
  const pts = (i0 != null
    ? f.slice(i0, i1 === undefined ? undefined : i1)
    : f).filter(x => x.lat != null && (x.lat !== 0 || x.lon !== 0))
    .map(x => [x.lat, x.lon]);
  if (!pts.length) { state.map.fitBounds(state.track.getBounds().pad(0.15)); return; }
  state.map.fitBounds(L.latLngBounds(pts).pad(0.2));
}

function hoverFrame(idx) {
  // map real frame index from possibly downsampled chart
  const step = +$("sampleSel").value;
  const realIdx = step <= 1 ? idx : idx * step;
  showPoint(realIdx);
  markTableRow(realIdx);
}

function showPoint(i) {
  const f = state.data.frames;
  if (!f || i < 0 || i >= f.length) return;
  const x = f[i];
  const d = $("pointDetails");
  d.innerHTML = `<h4>Point Details</h4>` + [
    ["Time", x.ts ? fmtTime(x.ts) : (startPlus(x.fly_time))],
    ["Fly time", (x.fly_time ?? 0).toFixed(1) + " s"],
    ["Latitude", x.lat ? x.lat.toFixed(7) : "0 (no fix)"],
    ["Longitude", x.lon ? x.lon.toFixed(7) : "0 (no fix)"],
    ["Height", x.height != null ? x.height.toFixed(1) + " m" : "—"],
    ["H-Speed", x.hspeed != null ? x.hspeed.toFixed(1) + " m/s" : "—"],
    ["V-Speed", x.vz != null ? x.vz.toFixed(1) + " m/s" : "—"],
    ["Sats", x.gps_num ?? "—"],
    ["Battery", x.battery != null ? x.battery + " %" : "—"],
    ["Mode", x.mode || "—"],
  ].map(([k, v]) => `<div class="row"><span>${k}</span><span>${esc(v)}</span></div>`).join("");
  if (state.marker && x.lat != null && (x.lat !== 0 || x.lon !== 0)) {
    state.marker.setLatLng([x.lat, x.lon]);
  }
  drawSimulation(x, i);
}
function startPlus(ft) {
  const st = (state.data.details || {}).start_time;
  return st ? fmtTime(st + (ft || 0)) : "—";
}

/* ================= stick + attitude simulation ================= */
function stickNorm(v) {
  if (v == null) return 0;
  return Math.max(-1, Math.min(1, (v - 1024) / 660));
}
function fitCanvas(c) {
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth || 480;
  const h = c.clientHeight || 280;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}
function drawStick(ctx, cx, cy, r, nx, ny, title, hLabel, vLabel) {
  ctx.fillStyle = "#2a3140";
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#4a5568";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy);
  ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r);
  ctx.stroke();
  const x = cx + nx * r * 0.82;
  const y = cy - ny * r * 0.82;
  ctx.strokeStyle = "#58a6ff";
  ctx.beginPath();
  ctx.moveTo(cx, cy); ctx.lineTo(x, y);
  ctx.stroke();
  ctx.fillStyle = "#d6dade";
  ctx.beginPath();
  ctx.arc(x, y, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#8b929b";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(title, cx, cy + r + 16);
  ctx.font = "10px sans-serif";
  ctx.fillText(hLabel, cx, cy + r + 30);
  ctx.fillText(vLabel, cx, cy - r - 8);
}
function drawSticks(x) {
  const c = $("stickCanvas");
  if (!c) return;
  const { ctx, w, h } = fitCanvas(c);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#1a1e24";
  ctx.fillRect(0, 0, w, h);
  const r = Math.min(w * 0.16, h * 0.32);
  const cy = h * 0.46;
  drawStick(ctx, w * 0.28, cy, r, stickNorm(x.rudder), stickNorm(x.throttle),
            "Left stick", "Rudder " + (x.rudder ?? "—"), "Throttle " + (x.throttle ?? "—"));
  drawStick(ctx, w * 0.72, cy, r, stickNorm(x.aileron), stickNorm(x.elevator),
            "Right stick", "Aileron " + (x.aileron ?? "—"), "Elevator " + (x.elevator ?? "—"));
}
function drawAttitude(x) {
  const c = $("attitudeCanvas");
  if (!c) return;
  const { ctx, w, h } = fitCanvas(c);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#1a1e24";
  ctx.fillRect(0, 0, w, h);
  const pitch = x.pitch || 0, roll = x.roll || 0, yaw = x.yaw || 0;
  const cx = w * 0.30, cy = h * 0.46;
  const R = Math.min(w * 0.22, h * 0.34);
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.clip();
  ctx.translate(cx, cy);
  ctx.rotate(-roll * Math.PI / 180);
  ctx.translate(0, Math.max(-R, Math.min(R, pitch * 2.4)));
  ctx.fillStyle = "#3d7ea6";
  ctx.fillRect(-R * 3, -R * 4, R * 6, R * 4);
  ctx.fillStyle = "#6b5332";
  ctx.fillRect(-R * 3, 0, R * 6, R * 4);
  ctx.strokeStyle = "#e8eef4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(-R * 3, 0); ctx.lineTo(R * 3, 0);
  ctx.stroke();
  ctx.restore();
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.strokeStyle = "#8b929b";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.strokeStyle = "#f5d76e";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx - R * 0.72, cy); ctx.lineTo(cx - R * 0.22, cy);
  ctx.moveTo(cx + R * 0.22, cy); ctx.lineTo(cx + R * 0.72, cy);
  ctx.moveTo(cx, cy - 4); ctx.lineTo(cx, cy + R * 0.28);
  ctx.stroke();

  const hx = w * 0.68, hy = h * 0.42, Hr = Math.min(w * 0.13, h * 0.28);
  ctx.beginPath();
  ctx.arc(hx, hy, Hr, 0, Math.PI * 2);
  ctx.strokeStyle = "#4a5568";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = "#8b929b";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("N", hx, hy - Hr - 4);
  ctx.save();
  ctx.translate(hx, hy);
  ctx.rotate(yaw * Math.PI / 180);
  ctx.fillStyle = "#58a6ff";
  ctx.beginPath();
  ctx.moveTo(0, -Hr * 0.72);
  ctx.lineTo(Hr * 0.28, Hr * 0.45);
  ctx.lineTo(0, Hr * 0.22);
  ctx.lineTo(-Hr * 0.28, Hr * 0.45);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  const hs = x.hspeed || 0;
  const track = Math.atan2(x.vy || 0, x.vx || 0);
  if (hs > 0.2) {
    const len = Math.min(Hr * 0.95, (hs / 12) * Hr);
    ctx.strokeStyle = "#3fb950";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(hx + Math.sin(track) * len, hy - Math.cos(track) * len);
    ctx.stroke();
  }
  ctx.fillStyle = "#d6dade";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(
    `Pitch ${pitch.toFixed(1)}°   Roll ${roll.toFixed(1)}°   Yaw ${yaw.toFixed(1)}°`,
    8, h - 28);
  ctx.fillText(
    `H-speed ${(x.hspeed || 0).toFixed(1)} m/s   V-speed ${(x.vz || 0).toFixed(1)} m/s`,
    8, h - 12);
}
function drawSimulation(x, i) {
  if (!x) return;
  drawSticks(x);
  drawAttitude(x);
  const scrub = $("playScrub");
  const n = state.data && state.data.frames ? state.data.frames.length : 0;
  if (scrub && n) {
    scrub.max = String(n - 1);
    if (document.activeElement !== scrub) scrub.value = String(i);
  }
  const ft = x.fly_time || 0;
  const m = Math.floor(ft / 60), s = Math.floor(ft % 60);
  if ($("playTime")) $("playTime").textContent = `${m}:${String(s).padStart(2, "0")}`;
  state.simTime = ft;
}
function stopPlay() {
  state.playing = false;
  if (state.playTimer) cancelAnimationFrame(state.playTimer);
  state.playTimer = null;
  const b = $("btnPlay");
  if (b) b.textContent = "▶ Play";
}
function playStep(ts) {
  if (!state.playing || !state.data) return;
  if (!state.playLast) state.playLast = ts;
  const dt = Math.min(0.1, (ts - state.playLast) / 1000);
  state.playLast = ts;
  const speed = +$("playSpeed").value || 1;
  const frames = state.data.frames;
  const end = frames[frames.length - 1].fly_time || 0;
  state.simTime += dt * speed;
  if (state.simTime > end) state.simTime = 0;
  let i = 0;
  while (i < frames.length - 1 && (frames[i].fly_time || 0) < state.simTime) i++;
  showPoint(i);
  state.playTimer = requestAnimationFrame(playStep);
}
function togglePlay() {
  if (!state.data) return;
  if (state.playing) { stopPlay(); return; }
  state.playing = true;
  state.playLast = 0;
  $("btnPlay").textContent = "⏸ Pause";
  state.playTimer = requestAnimationFrame(playStep);
}

/* ================= table ================= */
const TABLE_COLS = [
  ["fly_time", "FlyTime s"], ["ts", "Time"], ["mode", "Mode"], ["lat", "GPS sLat"],
  ["lon", "GPS sLon"], ["height", "Height m"], ["altitude", "Alt m"],
  ["hspeed", "H-Speed"], ["vz", "V-Speed"], ["battery", "Batt %"],
  ["voltage", "Batt V"], ["current", "Amps"], ["gps_num", "Sats"],
  ["pitch", "Pitch"], ["roll", "Roll"], ["yaw", "Yaw"],
];
function buildTable() {
  const f = state.data.frames;
  const cols = TABLE_COLS.filter(([k]) => f.some(x => x[k] != null));
  state.tableCols = cols;
  const thead = $("dataTable").querySelector("thead");
  thead.innerHTML = "<tr>" + cols.map(([, l]) => `<th>${l}</th>`).join("") + "</tr>";
  thead.querySelectorAll("th").forEach((th, i) => th.onclick = () => sortTable(i));
  renderTable();
}
function renderTable(offset = 0) {
  const f = state.data.frames;
  const n = +$("rowsSel").value || f.length;
  const cols = state.tableCols;
  const rows = f.slice(offset, offset + n);
  $("tableStats").textContent =
    `Showing ${offset + 1}–${offset + rows.length} of ${f.length} frames`;
  const tb = $("dataTable").querySelector("tbody");
  tb.innerHTML = rows.map((x, ri) => `<tr data-i="${offset + ri}">` + cols.map(([k]) => {
    let v = x[k];
    if (k === "ts") v = v ? new Date(v * 1000).toLocaleTimeString() : "";
    else if (typeof v === "number") v = Math.abs(v) < 1e-4 ? (v === 0 ? "0" : v.toExponential(1)) :
      (+v.toFixed(v > 100 ? 1 : 4)).toString();
    return `<td>${v ?? ""}</td>`;
  }).join("") + "</tr>").join("");
  tb.querySelectorAll("tr").forEach(tr =>
    tr.onclick = () => showPoint(+tr.dataset.i));
  state.tableOffset = offset;
}
$("rowsSel").onchange = () => renderTable(0);

function markTableRow(i) {
  document.querySelectorAll("#dataTable tbody tr").forEach(tr =>
    tr.classList.toggle("sel", +tr.dataset.i === i));
}
function sortTable(ci) {
  const k = state.tableCols[ci][0];
  const f = state.data.frames.slice().sort((a, b) => (a[k] ?? -1e18) - (b[k] ?? -1e18));
  state.data.frames = f;
  buildPlot(); buildTable(); fitMap();
  toast("Sorted by " + k);
}

$("btnCsv").onclick = () => {
  const f = state.data.frames, cols = state.tableCols;
  const csv = [cols.map(([, l]) => l).join(","),
    ...f.map(x => cols.map(([k]) => x[k] ?? "").join(","))].join("\n");
  const a = document.createElement("a");
  a.href = "data:text/csv," + encodeURIComponent(csv);
  a.download = (state.current || "frames") + ".csv";
  a.click();
};

/* local files (drag/drop → server parse) */
$("btnLoadLog").onclick = () => $("filePick").click();
$("filePick").onchange = async e => {
  const files = [...e.target.files];
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("api_key", getApiKey());
    toast("Uploading " + file.name + " …");
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (j.error) {
      if (j.need_key) {
        setKeyStatus("A DJI API key is required to decrypt this log. Paste yours above and retry.", true);
        $("apiKey").focus();
      }
      toast(j.error, true);
    }
  }
  e.target.value = "";
  await loadLogs();
};

/* boot */
initApiKeyUI();
$("btnForensic").onclick = exportForensicReport;
$("btnPlay").onclick = togglePlay;
$("playScrub").oninput = () => {
  if (!state.data) return;
  stopPlay();
  showPoint(+$("playScrub").value);
};
window.addEventListener("resize", () => {
  if (!state.data) return;
  buildPlot();
  const i = +$("playScrub").value || 0;
  drawSimulation(state.data.frames[i], i);
});
loadLogs();