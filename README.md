# DJI Flight Log Viewer

A local web application that decrypts and visualizes **DJI v14 encrypted flight logs** (`.txt` flight records from DJI Fly), with a datfile.in-style dark UI.

![UI](https://img.shields.io/badge/python-3.10%2B-blue) ![UI](https://img.shields.io/badge/flask-3.x-green)

## Features

- **Encrypted log parsing** — DJI Fly v14 logs are encrypted per-record. This parser implements the full pipeline: CRC-64 framing, XOR seed derivation, AES-128-CBC decryption with per-feature-point IV chains, and keychain retrieval via the DJI Developer API (`https://developer.dji.com/api/openapi/v1/appapi/component/info/keychain`).
- **DATA ANALYZER** — multi-series uPlot chart (height, altitude, speeds, battery, attitude, RC sticks) with multi-Y axes, brush zoom, and hover crosshair.
- **MAP** — Leaflet map with Esri satellite imagery / OpenStreetMap / OpenTopoMap, flight track polyline, clickable points, and a Point Details panel synced with the chart.
- **DATA VIEWER** — sortable data table with row count selector and CSV export.
- **Flight info & timeline** — aircraft model, SN, app version, duration, distance, max height/speed, battery consumption, and flight event timeline.
- **Upload & cache** — upload new logs in the UI; parsed results and DJI keychains are cached on disk so repeat loads are instant.

## Supported data

| Data | Source |
|---|---|
| GPS position, height, velocity, sats | OSD records (feature point 0) |
| Battery %, voltage, current, temperature, cells | SmartBattery records (type 22) |
| Pitch / roll / yaw | Gimbal/attitude records |
| RC sticks, switches | RC records |
| Flight events (power on, motors, take-off, landing) | Details/Aux records |

## Setup

```bash
python -m venv .venv
.venv/bin/pip install flask cryptography
.venv/bin/python app.py
```

Then open <http://127.0.0.1:8080>.

## DJI API key

v14 logs are encrypted; decryption requires a **personal DJI Developer API key** (free at the [DJI Developer portal](https://developer.dji.com/doc/open-api-tutorial/en/)).

- **In the app**: paste your key into the **DJI API KEY** field in the sidebar and click *Save Key* — it is stored in your browser (`localStorage`) and sent only with parse requests.
- **On the server** (optional): `DJI_API_KEY=your_key .venv/bin/python app.py`

The key is never embedded in the code. Log files are decrypted locally and are **not uploaded** anywhere; only the key request goes to DJI.

## Files

```
app.py               Flask server + on-disk cache
dji_log_parser.py    Core parser (CRC-64, XOR/AES, keychain API, record decoding)
static/              UI (index.html, style.css, app.js)
```

## Log compatibility

Tested with v14 logs from **DJI Mini 3 Pro** and **DJI Neo**. The Neo has no GPS, so map view is gracefully skipped for such logs.