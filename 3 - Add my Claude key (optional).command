#!/bin/bash
# Saves your Anthropic (Claude) API key into keys.json, then tests it.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo 'Run "1 - Install VoiceNoah.command" first.'; read -p "Press Enter to close"; exit 1; }
echo
KEY=""
while [ -z "$KEY" ]; do
  echo "Paste the Claude API key here (Cmd+V), then press Enter:"
  read -r -p "> " KEY
  [ -z "$KEY" ] && echo "   (nothing was pasted, try again)" && echo
done
clear; echo "Got it, checking the key…"
NOAH_KEY="$KEY" .venv/bin/python -I - "$PWD/keys.json" <<'PY'
import json, os, re, sys, pathlib
raw = os.environ.get("NOAH_KEY", "")
path = pathlib.Path(sys.argv[1])
print()
m = re.search(r"sk-ant-[A-Za-z0-9_\-]{20,}", raw)
if not m:
    print(f"❌ What you pasted ({len(raw.strip())} characters) doesn't contain a key that starts with sk-ant-.")
    print("   Copy it again from the Claude Console and run this file again.")
    sys.exit(1)
key = m.group(0)
import anthropic
try:
    r = anthropic.Anthropic(api_key=key, timeout=20).messages.create(
        model="claude-haiku-5-5", max_tokens=16,
        messages=[{"role": "user", "content": "Reply with: ok"}])
except anthropic.APIConnectionError:
    print("❌ Couldn't reach Anthropic (check the internet), then run this file again.")
    sys.exit(1)
except anthropic.AuthenticationError:
    print("❌ Anthropic rejected the key. It was NOT saved. Make sure you copied the whole key.")
    sys.exit(1)
except anthropic.APIStatusError as e:
    msg = str(e)
    if "credit" in msg.lower() or "billing" in msg.lower():
        print("⚠️  The key is right, but the account has no credit yet.")
        print("   In the Claude Console: Settings → Billing → add credit (even $5 is enough for months).")
    else:
        print(f"⚠️  Test error ({e.status_code}): {msg[:200]}")
    print("   The key WAS saved; it will work once that is fixed.")
    data = json.loads(path.read_text()) if path.exists() else {}
    data["anthropic"] = key
    path.write_text(json.dumps(data, indent=2)); path.chmod(0o600)
    sys.exit(1)
data = json.loads(path.read_text()) if path.exists() else {}
data["anthropic"] = key
path.write_text(json.dumps(data, indent=2))
path.chmod(0o600)
print(f"✅ The Claude key works and is saved (ends with …{key[-4:]}). VoiceNoah will use it next time you dictate.")
PY
echo
read -p "Press Enter to close"
