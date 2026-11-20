#!/bin/bash
# Saves your ElevenLabs key into keys.json, then tests it.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo 'Run "1 - Install VoiceNoah.command" first.'; read -p "Press Enter to close"; exit 1; }
echo
KEY=""
while [ -z "$KEY" ]; do
  echo "Paste the ElevenLabs key here (Cmd+V), then press Enter:"
  read -r -p "> " KEY
  [ -z "$KEY" ] && echo "   (nothing was pasted, try again)" && echo
done
clear; echo "Got it, checking the key…"
NOAH_KEY="$KEY" .venv/bin/python -I - "$PWD/keys.json" <<'PY'
import json, sys, pathlib, io, wave
import re
import os; raw = os.environ.get("NOAH_KEY", "")
path = pathlib.Path(sys.argv[1])
print()
m = re.search(r"sk_[A-Za-z0-9]{20,}", raw)
key = m.group(0) if m else raw.strip()
if not raw.strip():
    print("❌ Nothing was pasted (the clipboard is empty).")
    print("   In ElevenLabs, click the copy icon next to the key that starts with sk_, then run this file again.")
    sys.exit(1)
if len(key) < 20 or any(c.isspace() for c in key):
    print(f"❌ What you pasted ({len(raw.strip())} characters) doesn't contain a key that starts with sk_.")
    print("   Copy it again from ElevenLabs and run this file again.")
    sys.exit(1)
import requests
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
print("Testing the key with ElevenLabs…")
r = requests.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key},
                  data={"model_id": "scribe_v2"}, files={"file": ("t.wav", buf.getvalue(), "audio/wav")}, timeout=60)
if not r.ok:
    print(f"❌ ElevenLabs rejected the key ({r.status_code}). It was NOT saved.")
    print("   Make sure you copied the full key. If it's lost, delete it in ElevenLabs and create a new one.")
    sys.exit(1)
data = json.loads(path.read_text()) if path.exists() else {}
data["elevenlabs"] = key
path.write_text(json.dumps(data, indent=2))
path.chmod(0o600)
print(f"✅ The key works and is saved (ends with …{key[-4:]}). VoiceNoah will use it next time you dictate.")
PY
echo
read -p "Press Enter to close"
