# PyInstaller spec: builds dist/VoiceNoah.app (standalone, no Python needed).
# Run from the project folder:  .venv/bin/pyinstaller packaging/voicenoah_mac.spec --noconfirm
import os
import sys

sys.path.insert(0, os.path.abspath("."))
from core import VERSION  # noqa: E402

ROOT = os.path.abspath(".")

a = Analysis(
    [os.path.join(ROOT, "voicenoah.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "dashboard.html"), "."),
        (os.path.join(ROOT, "config.default.json"), "."),
    ],
    hiddenimports=[
        "AppKit", "Foundation", "Quartz", "WebKit", "ApplicationServices", "objc",
        "PyObjCTools.AppHelper", "certifi", "websocket", "anthropic", "requests",
        "sounddevice", "_sounddevice_data",
    ],
    excludes=["mlx", "mlx_whisper", "torch", "numba", "scipy", "tiktoken", "tkinter",
              "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="VoiceNoah",
    console=False,
    target_arch="arm64",
    codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="VoiceNoah")
app = BUNDLE(
    coll,
    name="VoiceNoah.app",
    icon=os.path.join(ROOT, "app", "build", "VoiceNoah.icns"),
    bundle_identifier="com.voicenoah.app",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "VoiceNoah",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "VoiceNoah listens while you hold the shortcut, to turn your speech into text.",
    },
)
