"""PyInstaller recipe for the Tauri sidecar.

Optional plugin dependencies are imported lazily in Python, so their runtime
entry points must be declared explicitly. Only runtime modules and model data
are included: training, test, and server packages make the one-file sidecar
much larger without adding desktop functionality.
"""

from PyInstaller.utils.hooks import collect_data_files


hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "keyring.backends.Windows",
    "playwright.sync_api",
    "chromadb.api.rust",
    "chromadb.db.impl.sqlite",
    "tree_sitter_language_pack",
    "faster_whisper",
    "piper",
    "openwakeword.model",
    "cv2",
    "mss",
    "PIL.Image",
    "sounddevice",
    "soundfile",
    "win32com.client",
]
datas = []

for package in ("chromadb", "faster_whisper", "piper", "openwakeword"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

a = Analysis(
    ["aether/api.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "chromadb.test",
        "piper.train",
        "openwakeword.train",
        "torch",
        "tensorflow",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="aether-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
