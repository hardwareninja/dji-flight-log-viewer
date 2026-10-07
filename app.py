#!/usr/bin/env python3
"""DJI Flight Log Viewer — local web app.

Serves a single-page UI (like datfile.in / Betaflight log viewer) that loads
encrypted DJI flight logs (.txt), decrypts them via the DJI keychain API and
renders an interactive chart, map and data table.

Run:  .venv/bin/python app.py  →  http://127.0.0.1:8080
"""
import json
import math
import os
import re
import time

from flask import Flask, jsonify, request, send_from_directory

import dji_log_parser as dlp

API_KEY = os.environ.get("DJI_API_KEY", "5149f682627b9291e483afe66de6fd4")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")


def _cache_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return os.path.join(CACHE_DIR, safe + ".json")


def find_logs() -> list:
    logs = []
    for fn in sorted(os.listdir(BASE_DIR)):
        if not fn.lower().endswith(".txt"):
            continue
        if fn.startswith(".") or fn.startswith("requirements"):
            continue
        path = os.path.join(BASE_DIR, fn)
        try:
            with open(path, "rb") as f:
                head = f.read(11)
            version = head[10]
            if not (7 <= version <= 14):
                continue
        except OSError:
            continue
        st = os.stat(path)
        logs.append({"name": fn, "size": st.st_size, "version": version,
                     "mtime": st.st_mtime})
    return logs


def parse_cached(name: str) -> dict:
    """Parse a log, using/refreshing an on-disk cache (keychains included)."""
    path = os.path.join(BASE_DIR, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    st = os.stat(path)
    cpath = _cache_path(name)
    if os.path.isfile(cpath):
        try:
            with open(cpath) as f:
                cache = json.load(f)
            if cache.get("mtime") == st.st_mtime and cache.get("size") == st.st_size:
                return cache["data"]
        except (json.JSONDecodeError, KeyError):
            pass

    with open(path, "rb") as f:
        data = f.read()

    # reuse cached keychains (offline / faster) when available
    cached_chains = None
    if os.path.isfile(cpath):
        try:
            with open(cpath) as f:
                old = json.load(f)
            cached_chains = old.get("keychains")
        except (json.JSONDecodeError, KeyError):
            cached_chains = None

    log = dlp.parse_log(data, api_key=API_KEY, cached_keychains=cached_chains)
    built = dlp.build_frames(log)

    chains = [[[fp, _b64(iv), _b64(key)] for fp, (iv, key) in ch.items()]
              for ch in log.keychains]
    payload = {
        "name": name,
        "version": built["version"],
        "details": _clean(built["details"]),
        "recover": _clean(built["recover"]),
        "frames": built["frames"],
    }
    with open(cpath, "w") as f:
        json.dump({"mtime": st.st_mtime, "size": st.st_size,
                   "keychains": chains, "data": payload}, f)
    return payload


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode()


def _clean(d):
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items()}
    if isinstance(d, (bytes, bytearray)):
        return d.hex()
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    return d


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/logs")
def api_logs():
    logs = []
    for meta in find_logs():
        entry = dict(meta)
        cpath = _cache_path(meta["name"])
        if os.path.isfile(cpath):
            try:
                with open(cpath) as f:
                    data = json.load(f)["data"]
                rec = data.get("recover") or {}
                entry["aircraft"] = rec.get("aircraft_name") or "-"
                entry["frames"] = len(data.get("frames", []))
            except Exception:
                pass
        logs.append(entry)
    return jsonify(logs)


@app.route("/api/log")
def api_log():
    name = request.args.get("name", "")
    if "/" in name or ".." in name or not name.lower().endswith(".txt"):
        return jsonify({"error": "invalid file name"}), 400
    t0 = time.time()
    try:
        payload = parse_cached(name)
    except FileNotFoundError:
        return jsonify({"error": "file not found"}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    payload["parse_seconds"] = round(time.time() - t0, 2)
    return jsonify(payload)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".txt"):
        return jsonify({"error": "please upload a DJI flight record .txt file"}), 400
    name = os.path.basename(f.filename)
    if re.search(r"[\\/..]", name):
        return jsonify({"error": "invalid file name"}), 400
    dest = os.path.join(BASE_DIR, name)
    f.save(dest)
    try:
        parse_cached(name)
    except Exception as exc:  # noqa: BLE001
        os.remove(dest)
        return jsonify({"error": f"could not parse {name}: {exc}"}), 400
    return jsonify({"ok": True, "name": name})


if __name__ == "__main__":
    print("DJI Flight Log Viewer → http://127.0.0.1:8080")
    app.run(host="127.0.0.1", port=8080, debug=False)