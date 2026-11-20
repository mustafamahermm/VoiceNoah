#!/bin/bash
# Installs VoiceNoah: Python libraries + ~/Applications/VoiceNoah.app, then opens it.
cd "$(dirname "$0")"
fail() { echo; echo "❌ $1"; echo; read -p "Press Enter to close"; exit 1; }

echo "Installing VoiceNoah…"
[ "$(uname -m)" = "arm64" ] || fail "VoiceNoah needs a Mac with Apple Silicon (M1/M2/M3/M4)."

# Python 3.10 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  p="$(command -v "$c" 2>/dev/null)" || continue
  "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { PY="$p"; break; }
done
[ -n "$PY" ] || fail "You need Python 3.10 or newer. Install it from https://www.python.org/downloads/macos/ and run this file again."

# Apple's command line tools (to build the app)
xcode-select -p >/dev/null 2>&1 || { xcode-select --install; fail "Install the Command Line Tools in the window that just opened, then run this file again."; }

echo "• Python: $("$PY" --version)"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || fail "Could not create the Python environment."
fi
echo "• Installing libraries (takes a few minutes the first time)…"
.venv/bin/pip install -q --upgrade pip >/dev/null 2>&1
.venv/bin/pip install -q -r requirements.txt || fail "Installing the libraries failed (check your internet)."

echo "• Building VoiceNoah.app…"
./app/build_app.sh >/dev/null || fail "Building the app failed."

echo
echo "✅ VoiceNoah is installed in your Applications folder (Home → Applications)."
echo "   Next: in the VoiceNoah window, open Settings and paste your ElevenLabs key."
open "$HOME/Applications/VoiceNoah.app"
echo
read -p "Press Enter to close"
