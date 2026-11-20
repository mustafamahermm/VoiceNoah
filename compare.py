#!/usr/bin/env python3
"""VoiceNoah engine comparison.

  python compare.py record   -> record a few sentences with your voice (recordings/)
  python compare.py run      -> run every engine on every recording, write compare-report.md

Cloud engines run only if their key is in keys.json:
  {"elevenlabs": "...", "openai": "..."}
"""
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REC_DIR = HERE / "recordings"
KEYS_PATH = HERE / "keys.json"
REPORT = HERE / "compare-report.md"
SR = 16000
PROMPT = ("أنا بتكلم عربي مصري و English مع بعض، يعني ممكن أقول meeting أو project "
          "أو deadline في نص الكلام. Notion, Claude, ChatGPT.")


def load_keys():
    if KEYS_PATH.exists():
        return {k: v for k, v in json.loads(KEYS_PATH.read_text()).items() if v}
    return {}


# ------------------------------------------------------------------ record
def record():
    import sounddevice as sd
    REC_DIR.mkdir(exist_ok=True)
    n = len(list(REC_DIR.glob("*.wav")))
    print("Speak the way you normally do: Egyptian Arabic mixed with English.\n"
          "Press Enter to start each sentence, and Enter again when you finish. Type q to quit.\n")
    while True:
        if input(f"[sentence {n + 1}] Enter to start (q to quit): ").strip().lower() == "q":
            break
        frames = []
        st = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                            callback=lambda d, f, t, s: frames.append(d.copy()))
        st.start()
        input("  ● recording… press Enter to stop ")
        st.stop(); st.close()
        audio = np.concatenate(frames).flatten() if frames else np.zeros(0)
        path = REC_DIR / f"sample_{n + 1:02d}.wav"
        save_wav(path, audio)
        ref = input("  (optional) type what you actually said, or Enter to skip: ").strip()
        if ref:
            path.with_suffix(".txt").write_text(ref, encoding="utf-8")
        print(f"  saved {path.name} ({len(audio) / SR:.1f}s)\n")
        n += 1


def save_wav(path, audio):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ engines
def local(model, language):
    def run(path):
        import mlx_whisper
        r = mlx_whisper.transcribe(str(path), path_or_hf_repo=model, language=language,
                                   initial_prompt=PROMPT, condition_on_previous_text=False,
                                   temperature=0.0, verbose=None)
        return r["text"].strip()
    return run


def elevenlabs(key, model_id):
    def run(path):
        import requests
        with open(path, "rb") as f:
            r = requests.post("https://api.elevenlabs.io/v1/speech-to-text",
                              headers={"xi-api-key": key},
                              data={"model_id": model_id, "tag_audio_events": "false"},
                              files={"file": (path.name, f, "audio/wav")}, timeout=60)
        r.raise_for_status()
        return r.json().get("text", "").strip()
    return run


def openai(key, model):
    def run(path):
        import requests
        with open(path, "rb") as f:
            r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                              headers={"Authorization": f"Bearer {key}"},
                              data={"model": model, "prompt": PROMPT},
                              files={"file": (path.name, f, "audio/wav")}, timeout=60)
        r.raise_for_status()
        return r.json().get("text", "").strip()
    return run


def engines():
    e = {
        "Local · Whisper turbo (ar)": local("mlx-community/whisper-large-v3-turbo", "ar"),
        "Local · Whisper large-v3 (ar)": local("mlx-community/whisper-large-v3-mlx", "ar"),
    }
    keys = load_keys()
    if "elevenlabs" in keys:
        e["Cloud · ElevenLabs Scribe v2"] = elevenlabs(keys["elevenlabs"], "scribe_v2")
    if "openai" in keys:
        e["Cloud · OpenAI gpt-4o-transcribe"] = openai(keys["openai"], "gpt-4o-transcribe")
    return e


# ------------------------------------------------------------------ run
def run_all(files=None):
    files = [Path(f) for f in files] if files else sorted(REC_DIR.glob("*.wav"))
    if not files:
        print("No recordings yet. Run:  .venv/bin/python compare.py record")
        return
    eng = engines()
    missing = [k for k in ("elevenlabs", "openai") if k not in load_keys()]
    if missing:
        print(f"(no key for: {', '.join(missing)}, those engines are skipped)")
    for name, fn in eng.items():          # warm up local models so timing is fair
        if name.startswith("Local"):
            try:
                fn(files[0])
            except Exception:
                pass
    out = ["# VoiceNoah engine comparison\n"]
    totals = {n: 0.0 for n in eng}
    for path in files:
        secs = wave.open(str(path)).getnframes() / SR
        out.append(f"\n## {path.name} ({secs:.1f}s of audio)\n")
        ref = path.with_suffix(".txt")
        if ref.exists():
            out.append(f"**What you said:** {ref.read_text(encoding='utf-8')}\n")
        out.append("| Engine | Time | Text |\n|---|---|---|")
        print(f"\n{path.name} ({secs:.1f}s)")
        for name, fn in eng.items():
            t = time.time()
            try:
                text = fn(path)
            except Exception as ex:
                text = f"ERROR: {str(ex)[:150]}"
            dt = time.time() - t
            totals[name] += dt
            print(f"  {name:36s} {dt:5.1f}s  {text}")
            out.append(f"| {name} | {dt:.1f}s | {text.replace('|', '/')} |")
    out.append("\n## Total time\n")
    out += [f"- {n}: {t:.1f}s" for n, t in totals.items()]
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nReport written to {REPORT.name}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "record":
        record()
    else:
        run_all(sys.argv[2:])
