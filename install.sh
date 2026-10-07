#!/usr/bin/env bash
# DJI Flight Log Viewer — one-shot installer for Linux VMs
# Usage:  sudo ./install.sh          (installs system deps, sets up venv, starts service)
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PY=python3

# 1) System packages (Debian/Ubuntu/Kali)
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
fi

# 2) Virtualenv + dependencies
cd "$APP_DIR"
[ -d .venv ] || $PY -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 3) Start server on all interfaces, port 8080 (survives shell exit)
pkill -f "app.py" 2>/dev/null || true
sleep 1
nohup env HOST=0.0.0.0 PORT=8080 DJI_API_KEY="${DJI_API_KEY:-}" \
  .venv/bin/python app.py > /var/log/dji-viewer.log 2>&1 &
sleep 2
echo "Started. Log: /var/log/dji-viewer.log"
curl -s -o /dev/null -w "Health: http://0.0.0.0:8080 → HTTP %{http_code}\n" http://127.0.0.1:8080/