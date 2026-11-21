#!/bin/bash
# Builds dist/VoiceNoah-<version>-mac.dmg (Apple Silicon) from this project folder.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(.venv/bin/python -c "from core import VERSION; print(VERSION)" 2>/dev/null | tail -1)

# app icon
.venv/bin/python app/make_icon.py app/build/VoiceNoah.iconset >/dev/null
iconutil -c icns app/build/VoiceNoah.iconset -o app/build/VoiceNoah.icns

# standalone app
.venv/bin/pyinstaller packaging/voicenoah_mac.spec --noconfirm --distpath dist --workpath build/pyi >/dev/null
codesign --force --deep --sign - dist/VoiceNoah.app

# disk image with a shortcut to Applications
STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R dist/VoiceNoah.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/First time - read me.txt" <<'TXT'
VoiceNoah: first time on this Mac

1. Drag VoiceNoah into the Applications folder.
2. Open Applications, RIGHT-CLICK VoiceNoah and choose Open, then click Open.
   (macOS asks this once because the app is free and not from the App Store.
    If it still refuses: System Settings > Privacy & Security > "Open Anyway".)
3. In the VoiceNoah window: Settings > paste your ElevenLabs key > Save & test.
4. Allow Accessibility and Microphone for VoiceNoah when macOS asks.
5. Hold fn and speak in any app.
TXT
DMG="dist/VoiceNoah-${VERSION}-mac.dmg"
rm -f "$DMG"
hdiutil create -volname "VoiceNoah" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"

# keep only the DMG: leftover app copies confuse Spotlight, Launchpad and the permission list
rm -rf build/dmg build/pyi dist/VoiceNoah.app dist/VoiceNoah
