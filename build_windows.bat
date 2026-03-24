@echo off
setlocal
cd /d %~dp0

python tools\generate_icon_assets.py
python -m PyInstaller ^
  --noconfirm ^
  --windowed ^
  --clean ^
  --name OpenClawOps ^
  --icon assets\openclaw.ico ^
  --add-data "assets;assets" ^
  app.py
