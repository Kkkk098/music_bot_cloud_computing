#!/bin/bash
set -euo pipefail

APP_DIR=/opt/app

echo "[app.sh] cd ${APP_DIR}"
cd "${APP_DIR}"

echo "[app.sh] create venv"
python3 -m venv .venv

echo "[app.sh] install requirements"
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "[app.sh] setup complete (systemd will be handled by Ansible)"
