#!/bin/bash
# Builds ~/Applications/VoiceNoah.app from this project folder.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/VoiceNoah.app"
BUILD="$DIR/app/build"
rm -rf "$BUILD" && mkdir -p "$BUILD"

# icon
"$DIR/.venv/bin/python" "$DIR/app/make_icon.py" "$BUILD/VoiceNoah.iconset"
iconutil -c icns "$BUILD/VoiceNoah.iconset" -o "$BUILD/VoiceNoah.icns"

# launcher
clang -O2 -arch arm64 -mmacosx-version-min=12.0 -DVOICENOAH_DIR="\"$DIR\"" \
      -o "$BUILD/VoiceNoah" "$DIR/app/launcher.c"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/VoiceNoah" "$APP/Contents/MacOS/VoiceNoah"
cp "$BUILD/VoiceNoah.icns" "$APP/Contents/Resources/VoiceNoah.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>VoiceNoah</string>
  <key>CFBundleDisplayName</key><string>VoiceNoah</string>
  <key>CFBundleIdentifier</key><string>com.voicenoah.app</string>
  <key>CFBundleExecutable</key><string>VoiceNoah</string>
  <key>CFBundleIconFile</key><string>VoiceNoah</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.3</string>
  <key>CFBundleVersion</key><string>3</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>VoiceNoah listens while you hold the fn key, to turn your speech into text.</string>
</dict></plist>
PLIST
codesign --force --deep --sign - --identifier com.voicenoah.app "$APP"
echo "Built $APP"
