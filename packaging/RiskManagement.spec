# PyInstaller spec for the desktop (pywebview) Dock app.
#
# Build:  uv run pyinstaller packaging/RiskManagement.spec \
#             --distpath packaging/dist --workpath packaging/build --noconfirm
# Output: packaging/dist/Risk Management.app
#
# A windowed (console=False) bundle. To debug a freeze, run the inner Mach-O
# directly from a terminal so stderr is visible:
#   "packaging/dist/Risk Management.app/Contents/MacOS/risk-management"

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Ship the SQL migrations as data (load_schema reads them via sys._MEIPASS when
# frozen) and the built SPA where the launcher's _bundled_static_dir() expects it.
datas = [
    (os.path.join(ROOT, "src", "risk", "db", "migrations"), "risk/db/migrations"),
    (os.path.join(ROOT, "web", "dist"), "web_dist"),
]
binaries = []
# uvicorn resolves its loop/protocol impls by string at runtime — PyInstaller
# can't see those, so collect the whole package. email_validator is imported
# lazily by pydantic's EmailStr path.
hiddenimports = collect_submodules("uvicorn") + ["email_validator"]

# pywebview pulls in its JS bridge assets + the platform (Cocoa) backend.
for _pkg in ("webview",):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [os.path.join(ROOT, "src", "risk", "desktop", "__main__.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="risk-management",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed — no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # current arch only (arm64); no cross-compile
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="risk-management",
)

app = BUNDLE(
    coll,
    name="Risk Management.app",
    icon=None,
    bundle_identifier="com.colinguenther.risk-management",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "CFBundleShortVersionString": "0.1.0",
        # Single-window utility; no need to show in the app-switcher as a doc app.
        "LSMinimumSystemVersion": "12.0",
    },
)
