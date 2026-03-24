#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

python3 tools/generate_icon_assets.py

python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --clean \
  --name OpenClawOps \
  --icon assets/openclaw.icns \
  --add-data "assets:assets" \
  app.py
