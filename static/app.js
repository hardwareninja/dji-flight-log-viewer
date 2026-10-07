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
  $("btnExport").href = "data:application/json," +
    encodeURIComponent(JSON.stringify(j.frames));
  toast(`${j.frames.length} frames loaded`);
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
}
function startPlus(ft) {
  const st = (state.data.details || {}).start_time;
  return st ? fmtTime(st + (ft || 0)) : "—";
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
window.addEventListener("resize", () => state.data && buildPlot());
loadLogs();