# Packaging — native macOS Dock app

The chair app ships as a single native `.app` (no terminal, no browser chrome)
using **pywebview** (a Cocoa WKWebView window) wrapping the FastAPI backend, all
frozen into one bundle with **PyInstaller**. Chosen over Tauri to avoid a Rust
toolchain and a sidecar process for what is a single-user, single-machine tool.

## How it fits together
- **Same-origin serving** (`risk.api.create_app`): when a built SPA is present,
  FastAPI serves it and the API on one port, so there is no CORS/proxy in the
  bundle. See `src/risk/api/app.py`.
- **Launcher** (`risk.desktop`, `python -m risk.desktop`): picks a free localhost
  port, runs uvicorn on a daemon thread, health-gates on `/api/health`, then opens
  the native window. Closing the window exits the process. See
  `src/risk/desktop/__main__.py`.
- **DB**: `~/Library/Application Support/risk-management/risk.db` (via `RISK_DB_PATH`).
  First run bootstraps the schema from the bundled migrations.

## Rebuild after code changes
```sh
# 1. Build the frontend bundle (FastAPI serves web/dist)
cd web && npm run build && cd ..

# 2. Freeze the app
uv run pyinstaller packaging/RiskManagement.spec \
    --distpath packaging/dist --workpath packaging/build --noconfirm

# 3. Install + re-sign (ad-hoc; no paid cert needed for local use)
rm -rf "/Applications/Risk Management.app"
cp -R "packaging/dist/Risk Management.app" "/Applications/Risk Management.app"
codesign --force --deep --sign - "/Applications/Risk Management.app"
```
The Dock entry points at `/Applications/Risk Management.app`, so replacing it in
place keeps the Dock icon valid. A locally-built bundle carries no
`com.apple.quarantine` flag, so it launches with no Gatekeeper prompt.

## Icon
`icon/icon.svg` is the source; rebuild `icon/RiskManagement.icns` with:
```sh
cd packaging/icon
rsvg-convert -w 1024 -h 1024 icon.svg -o icon-1024.png
mkdir -p icon.iconset
for sz in 16 32 128 256 512; do
  sips -z $sz $sz icon-1024.png --out icon.iconset/icon_${sz}x${sz}.png
  sips -z $((sz*2)) $((sz*2)) icon-1024.png --out icon.iconset/icon_${sz}x${sz}@2x.png
done
iconutil -c icns icon.iconset -o RiskManagement.icns
```
