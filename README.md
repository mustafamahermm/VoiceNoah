<p align="center"><img src="assets/VoiceNoah-icon.png" width="128" alt="VoiceNoah icon"></p>

<h1 align="center">VoiceNoah</h1>
<p align="center"><b>Voice dictation for macOS that actually understands Egyptian Arabic mixed with English.</b><br>
Hold <kbd>fn</kbd>, speak, let go. The text is typed into any app.</p>

---

## The problem

Most dictation tools are good at English, or at Modern Standard Arabic, but not at the way many of us actually talk:
**Egyptian Arabic with English words in the middle of the sentence** ("عايز أعمل meeting بكرة عشان نراجع الـ deadline").
They write the English words in Arabic letters, mix up the dialect, and have monthly word limits.

## What VoiceNoah does

- **Hold `fn` and talk** in any app: Notion, Chrome, Slack, WhatsApp, ChatGPT, Claude, Word...
- **Egyptian Arabic + English in the same sentence.** English words stay in English, Arabic stays Egyptian.
- **Cleans up the text**: filler sounds ("umm", "آآ") are removed while typing; optional Claude polish removes "umm" and "آآ", fixes punctuation, and applies self-corrections ("الساعة تلاتة… لأ قصدي أربعة" → "الساعة أربعة").
- **Types while you talk.** Audio streams live to ElevenLabs Scribe v3 Live; each sentence is typed into your app as soon as you pause, and the last one appears about half a second after you let go.
- **Dashboard** with your stats: words today/this week, speaking speed, time saved vs. typing, streak, which apps you dictate in, and a searchable history.
- **Quick tap on `fn`** = hands-free mode for long dictation; **Esc** cancels.
- Lives in the **menu bar**; a small black pill peeks from the bottom edge of the screen only while you speak.
- **No subscription.** You use your own API keys and pay only for what you use (free tiers are usually enough).

| Part | What it uses |
|---|---|
| Speech → text | [ElevenLabs Scribe v3 Live](https://elevenlabs.io/speech-to-text) (realtime; best we tested on Egyptian + English) |
| Fallback with no internet | Whisper large-v3-turbo running locally on your Mac (MLX) |
| Text cleanup (optional) | Claude Haiku 5.5 by Anthropic |

## Requirements

- A Mac with **Apple Silicon** (M1 or newer)
- **Python 3.10+** ([python.org](https://www.python.org/downloads/macos/))
- An **ElevenLabs** account (free plan: about 2.5 hours of live dictation a month; Starter, $6/month, about 27 hours)
- Optional: an **Anthropic (Claude)** API key, for text cleanup

## Install (about 5 minutes)

1. **Download** this project: green **Code** button → **Download ZIP**, then unzip it. Or:
   ```bash
   git clone https://github.com/mustafamahermm/VoiceNoah.git
   ```
2. Double-click **`1 - Install VoiceNoah.command`**.
   If macOS says it can't be opened: right-click the file → **Open** → **Open**.
   It installs everything and opens VoiceNoah (a 〰️ icon appears in the menu bar).
3. Allow **Accessibility** when asked: System Settings → Privacy & Security → Accessibility → turn on **VoiceNoah**.
4. System Settings → Keyboard → **"Press 🌐 key to"** → **Do Nothing** (otherwise `fn` opens the emoji picker).
5. Add your ElevenLabs key (below), then hold `fn` and speak. Allow the **Microphone** the first time.
6. From the 〰️ menu, turn on **Open at login**.

## Get your ElevenLabs API key

1. Sign up at [elevenlabs.io](https://elevenlabs.io) (the free plan is fine).
2. Go to **Developers → API keys** ([direct link](https://elevenlabs.io/app/developers/api-keys)) → **Create Key**.
3. Name it `VoiceNoah`. Under permissions, set **Speech to Text → Access** and leave everything else on **No Access**.
4. Click **Create Key** and **copy the key right away** (it starts with `sk_`, and is shown only once).
5. Double-click **`2 - Add my ElevenLabs key.command`**, paste with **Cmd+V**, press **Enter**. You should see ✅.

## Optional: text cleanup with Claude

1. Sign in at [platform.claude.com](https://platform.claude.com) → **Settings → API keys → Create key**.
2. Copy the key (it starts with `sk-ant-`). Make sure your account has some credit.
3. Double-click **`3 - Add my Claude key (optional).command`**, paste, press **Enter**.

Cleanup costs a tiny fraction of a cent per dictation. You can turn it on or off from the menu bar.

## Settings

Menu bar 〰️ → **Edit settings** opens `config.json`. Quit and reopen VoiceNoah after changes.

| Setting | What it does |
|---|---|
| `engine` | `elevenlabs` (cloud, most accurate) or `local` (Whisper on your Mac, no internet) |
| `cloud_model` | `scribe_v3` or `scribe_v2` |
| `live` | `true`: stream and type sentence by sentence while you talk; `false`: transcribe in chunks |
| `cleanup` / `polish_after_live` | Claude text cleanup (used in chunk mode; in live mode only if `polish_after_live` is `true`) |
| `hotkey` | `fn` · `alt_r` (Right Option) · `cmd_r` · `ctrl_r` |
| `vocabulary` | Names and terms you say often, so they're spelled right |
| `replacements` | Fix a word that keeps coming out wrong: `{"wrong": "right"}` |

Everything you dictate is saved locally in `history.jsonl` and shown in the dashboard (menu 〰️ → **Open dashboard**).

## Privacy

- Your API keys stay on your Mac in `keys.json` (readable only by your user account, and never committed to git).
- Audio is sent to ElevenLabs only while you hold the key; text is sent to Anthropic only if cleanup is on.
- With `"engine": "local"`, nothing leaves your Mac.

## Troubleshooting

| Problem | Fix |
|---|---|
| `fn` does nothing | VoiceNoah must be on under Accessibility, and "Press 🌐 key to" = Do Nothing |
| Nothing gets typed | Privacy & Security → Microphone → turn on VoiceNoah |
| Anything else | Check the log: `~/Library/Logs/VoiceNoah.log` |

## How it works (for developers)

- `voicenoah.py`: the whole app (PyObjC): global `fn` monitor, recording with `sounddevice`, pause detection that cuts the audio into chunks transcribed in parallel while you talk, the floating pill with live text, the menu bar item, cleanup, and pasting with Cmd+V (your clipboard is restored afterwards).
- `dashboard.html`: the stats window (rendered in a WebKit view from `history.jsonl`).
- `app/`: a tiny C launcher plus `build_app.sh`, which builds `~/Applications/VoiceNoah.app` so macOS permissions belong to "VoiceNoah" and not to Terminal.
- `compare.py`: records your voice and compares the engines side by side (`.venv/bin/python compare.py record`, then `run`).

---

## بالعربي: باختصار

**VoiceNoah** بيخليك تمسك زرار `fn` وتتكلم مصري وإنجليزي مع بعض، وأول ما تسيبه الكلام يتكتب في أي برنامج. بيحوّل كلامك وإنت بتتكلم، فحتى الكلام الطويل بيتكتب في ثانية أو اتنين، وفيه داشبورد بإحصائياتك.
خطوات التسطيب:
1. نزّل المشروع (Code ← Download ZIP).
2. دبل كليك على `1 - Install VoiceNoah.command`.
3. اعمل مفتاح من ElevenLabs:
   - Developers ← API keys ← Create Key.
   - خلي **Speech to Text = Access** بس.
   - انسخ المفتاح فوراً.
4. دبل كليك على `2 - Add my ElevenLabs key.command`، والصق المفتاح، واضغط Enter.
5. فعّل Accessibility و Microphone لـ VoiceNoah.
6. خلي "Press 🌐 key to" = Do Nothing.

---

Made by [Mustafa Maher](https://github.com/mustafamahermm). MIT License.
