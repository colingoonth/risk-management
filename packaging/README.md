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

# 4. Delete the build copy — DO NOT SKIP. See below.
rm -rf "packaging/dist/Risk Management.app"
```
The Dock entry points at `/Applications/Risk Management.app`, so replacing it in
place keeps the Dock icon valid. A locally-built bundle carries no
`com.apple.quarantine` flag, so it launches with no Gatekeeper prompt.

### Why step 4 matters

PyInstaller writes a **fully launchable** `.app` into `packaging/dist`, and
Spotlight indexes it like any other application. Searching "risk" then returns
**two** identical-looking results with no way to tell them apart, and the repo one
is whatever you last froze — which is *not* what step 3 installed if you ever
build without installing. That is how a two-month-old bundle stayed in use: the
stale copy still launched, still opened the real database, and still looked right.
Its migrations stopped at `0011`, so it silently re-created the index that `0012`
replaced.

Two defences, both cheap:

- **Step 4 removes the duplicate** as soon as it has been installed. The bundle is
  a build artifact — `packaging/dist` is gitignored and every build regenerates it.
- **`packaging/dist/.metadata_never_index`** tells Spotlight to skip that whole
  tree, so a build you have not installed yet never shows up in search. PyInstaller
  removes only the `.app` on `--noconfirm`, so the marker survives rebuilds. It is
  inside a gitignored directory and therefore not tracked — recreate it with
  `touch packaging/dist/.metadata_never_index` on a fresh clone.

To check what is actually installed at any time:

```sh
ls "/Applications/Risk Management.app/Contents/Resources/risk/db/migrations" | tail -1
```
If that is not the highest-numbered file in `src/risk/db/migrations`, the Dock app
is behind the source and needs a rebuild.

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
