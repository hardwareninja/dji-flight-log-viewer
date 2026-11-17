#!/usr/bin/env python3
"""DJI Flight Log Viewer — local web app.

Serves a single-page UI (like datfile.in / Betaflight log viewer) that loads
encrypted DJI flight logs (.txt), decrypts them via the DJI keychain API and
renders an interactive chart, map and data table.

Run:  .venv/bin/python app.py  →  http://127.0.0.1:8080
"""
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time

from flask import Flask, jsonify, request, send_from_directory

import dji_log_parser as dlp

API_KEY = os.environ.get("DJI_API_KEY", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)
MAX_UPLOAD_BYTES = 256 * 1024 * 1024

# Device names Windows treats as files no matter which directory they use.
_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _is_safe_log_name(name: str) -> bool:
    """True when `name` is one flight-log file name, not a path."""
    if not isinstance(name, str) or not name or len(name) > 240:
        return False
    if name != name.strip() or name.endswith(".") or name.endswith(" "):
        return False
    if any(ord(c) < 32 or c in "/\\:\x00" for c in name):
        return False
    if ".." in name or name.startswith("."):
        return False
    if os.path.basename(name) != name:
        return False
    lower = name.lower()
    if not lower.endswith(".txt") or lower.startswith("requirements"):
        return False
    if name.split(".", 1)[0].upper() in _RESERVED_STEMS:
        return False
    return True


def _directory(path: str) -> str:
    return os.path.realpath(path)


def _contained(path: str, root: str) -> bool:
    root = _directory(root)
    try:
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:
        return False


def _resolve_log_path(name: str) -> str:
    """Absolute path of a log that stays directly inside BASE_DIR.

    Symlinks are followed only when the target is also a file in that directory.
    """
    if not _is_safe_log_name(name):
        raise ValueError("invalid file name")
    root = _directory(BASE_DIR)
    direct = os.path.join(root, name)
    real = os.path.realpath(direct)
    if os.path.dirname(real) != root or not _contained(real, root):
        raise ValueError("invalid file name")
    return real


def _cache_filename(name: str) -> str:
    """Cache file name that cannot collide when unsafe characters are folded."""
    raw = name if isinstance(name, str) else ""
    digest = hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")[:80] or "log"
    return f"{safe}.{digest}.json"


def _legacy_cache_filename(name: str) -> str:
    raw = name if isinstance(name, str) else ""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw) + ".json"


def _cache_file(filename: str) -> str:
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError("invalid cache name")
    root = _directory(CACHE_DIR)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, filename)
    if os.path.islink(path) or not _contained(path, root):
        raise ValueError("invalid cache name")
    if os.path.dirname(os.path.realpath(path)) != root:
        raise ValueError("invalid cache name")
    return path


def _cache_path(name: str) -> str:
    return _cache_file(_cache_filename(name))


def _cache_belongs_to(cache: dict, name: str) -> bool:
    data = cache.get("data")
    if isinstance(data, dict) and "name" in data:
        return data.get("name") == name
    # Caches written by this app always store the file name. A name-less file
    # is trusted only when its name needed no character folding, so two
    # different logs cannot share it.
    raw = name if isinstance(name, str) else ""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw) == raw


def _read_cache(path: str, name: str) -> dict | None:
    if not path or os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(cache, dict) or not _cache_belongs_to(cache, name):
        return None
    return cache


def _cached_log(name: str) -> dict | None:
    """Hashed cache for this log, or a legacy cache that still names it.

    Older builds stored ``<sanitized name>.json``. Sanitizing folds ``a b.txt``
    and ``a_b.txt`` onto one file, so a legacy file is used only when the
    ``name`` it recorded is the log being opened.
    """
    for filename in (_cache_filename(name), _legacy_cache_filename(name)):
        try:
            path = _cache_file(filename)
        except ValueError:
            continue
        cache = _read_cache(path, name)
        if cache is not None:
            return cache
    return None


def _flight_version(path: str) -> int | None:
    try:
        with open(path, "rb") as f:
            head = f.read(11)
    except OSError:
        return None
    if len(head) < 11:
        return None
    version = head[10]
    if not 7 <= version <= 14:
        return None
    return version


def find_logs() -> list:
    logs = []
    root = _directory(BASE_DIR)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return logs
    for fn in names:
        if not _is_safe_log_name(fn):
            continue
        direct = os.path.join(root, fn)
        try:
            if os.path.islink(direct):
                real = os.path.realpath(direct)
                if os.path.dirname(real) != root or not os.path.isfile(real):
                    continue
                path = real
            elif os.path.isfile(direct):
                path = direct
            else:
                continue
            version = _flight_version(path)
            if version is None:
                continue
            st = os.stat(path)
        except OSError:
            continue
        logs.append({"name": fn, "size": st.st_size, "version": version,
                     "mtime": st.st_mtime})
    return logs


def parse_cached(name: str, api_key: str = "") -> dict:
    """Parse a log, using/refreshing an on-disk cache (keychains included)."""
    path = _resolve_log_path(name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    st = os.stat(path)
    cache = _cached_log(name)
    fresh_meta = bool(
        cache
        and cache.get("mtime") == st.st_mtime
        and cache.get("size") == st.st_size
        and isinstance(cache.get("data"), dict)
    )
    # Caches written before content hashes existed stay valid on size and mtime.
    if fresh_meta and cache.get("sha256") is None:
        return cache["data"]

    with open(path, "rb") as f:
        data = f.read()
    file_hash = hashlib.sha256(data).hexdigest()
    if fresh_meta and cache.get("sha256") == file_hash:
        return cache["data"]

    cached_chains = cache.get("keychains") if cache else None
    log = dlp.parse_log(data, api_key=api_key or API_KEY, cached_keychains=cached_chains)
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
    cpath = _cache_path(name)
    root = _directory(CACHE_DIR)
    os.makedirs(root, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="cache-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mtime": st.st_mtime, "size": st.st_size, "sha256": file_hash,
                       "keychains": chains, "data": payload}, f)
        os.replace(tmp, cpath)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
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


def _atomic_write(dest: str, data: bytes) -> None:
    root = _directory(CACHE_DIR)
    os.makedirs(root, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="upload-", suffix=".bin", dir=root)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, dest)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _backup_file(dest: str) -> str:
    root = _directory(CACHE_DIR)
    os.makedirs(root, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="backup-", suffix=".bak", dir=root)
    os.close(fd)
    try:
        shutil.copy2(dest, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return path


def _discard(path: str | None) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.remove(path)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/logs")
def api_logs():
    logs = []
    for meta in find_logs():
        entry = dict(meta)
        try:
            cache = _cached_log(meta["name"])
            data = cache.get("data") if cache else None
            if isinstance(data, dict):
                rec = data.get("recover") or {}
                entry["aircraft"] = rec.get("aircraft_name") or "-"
                entry["frames"] = len(data.get("frames", []))
        except Exception:
            pass
        logs.append(entry)
    return jsonify(logs)


def _need_key_response(exc: Exception):
    """Turn a missing-key error into a 400 the UI can act on."""
    msg = str(exc)
    if "API key" in msg:
        return jsonify({"error": msg, "need_key": True}), 400
    return None


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "file is too large"}), 413


@app.route("/api/log")
def api_log():
    name = request.args.get("name", "")
    if not _is_safe_log_name(name):
        return jsonify({"error": "invalid file name"}), 400
    # The key is a secret. Accept it from a header, not the query string,
    # so it is not written into access logs or the browser history.
    api_key = request.headers.get("X-DJI-Key", "")
    t0 = time.time()
    try:
        payload = parse_cached(name, api_key=api_key)
    except ValueError:
        return jsonify({"error": "invalid file name"}), 400
    except FileNotFoundError:
        return jsonify({"error": "file not found"}), 404
    except Exception as exc:  # noqa: BLE001
        resp = _need_key_response(exc)
        if resp:
            return resp
        return jsonify({"error": str(exc)}), 500
    payload["parse_seconds"] = round(time.time() - t0, 2)
    return jsonify(payload)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    raw = f.filename if f is not None else ""
    if not raw or not _is_safe_log_name(raw):
        return jsonify({"error": "invalid file name"}), 400
    try:
        root = _directory(BASE_DIR)
        direct = os.path.join(root, raw)
        _resolve_log_path(raw)
    except ValueError:
        return jsonify({"error": "invalid file name"}), 400
    # Never follow a link, and never replace a text file that is not a log.
    if os.path.lexists(direct) and (os.path.islink(direct) or not os.path.isfile(direct)):
        return jsonify({"error": "invalid file name"}), 400
    if os.path.isfile(direct) and _flight_version(direct) is None:
        return jsonify({"error": "refusing to overwrite a file that is not a flight log"}), 400

    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file is too large"}), 413

    api_key = request.headers.get("X-DJI-Key") or request.form.get("api_key", "")
    backup = None
    wrote = False
    try:
        if os.path.isfile(direct):
            backup = _backup_file(direct)
        _atomic_write(direct, data)
        wrote = True
        parse_cached(raw, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        need = _need_key_response(exc)
        if wrote and need is not None:
            # The log itself is valid; keep it so the UI can retry after a key is saved.
            _discard(backup)
        elif wrote and backup:
            os.replace(backup, direct)
            backup = None
        elif wrote:
            _discard(direct)
        else:
            _discard(backup)
        if need is not None:
            return need
        print(f"upload failed for {raw}: {exc}", flush=True)
        return jsonify({"error": f"could not parse {raw}: {exc}"}), 400
    _discard(backup)
    return jsonify({"ok": True, "name": raw})


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"DJI Flight Log Viewer → http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
