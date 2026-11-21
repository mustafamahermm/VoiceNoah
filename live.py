"""Live transcription with ElevenLabs Scribe (realtime WebSocket).

Audio is streamed while you talk; partial text comes back within a fraction of a
second and each segment is committed by the server when you pause.
"""
import base64
import json
import re
import threading
import time
from urllib.parse import urlencode

import numpy as np

SAMPLE_RATE = 16000
URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

# filler sounds removed while typing live (Claude cleanup is optional at the end)
_L = r"A-Za-z0-9\u0621-\u064A\u0660-\u0669"
_FILLERS = re.compile(
    rf"(?<![{_L}])(?:آ{{2,}}|اا+|إ+م+|ا+م{{2,}}|u+m+|u+h+|e+r+m+|hmm+|mm+)(?![{_L}])[-–—]*[,،.]?",
    re.IGNORECASE)


def clean_live(text):
    text = _FILLERS.sub("", text)
    text = re.sub(r"\s+([,،.؟?!])", r"\1", text)
    text = re.sub(r"([,،])\s*([,،])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip().lstrip("،, ")
    return text.rstrip(" ،,-–—")


class LiveSession:
    """One dictation streamed to Scribe realtime.

    on_partial(text): text of the current, not yet final segment (may change).
    on_commit(text):  final text of a finished segment.
    """

    def __init__(self, key, on_partial, on_commit, model="scribe_v3_live", keyterms=None,
                 language=None, silence_secs=0.6, log=print, extra=None):
        self.key, self.on_partial, self.on_commit, self.log = key, on_partial, on_commit, log
        params = {"model_id": model, "audio_format": "pcm_16000", "commit_strategy": "vad",
                  "vad_silence_threshold_secs": silence_secs}
        if language:
            params["language_code"] = language
        params.update(extra or {})
        q = urlencode(params, doseq=True)
        for t in (keyterms or [])[:50]:
            q += "&" + urlencode({"keyterms": t})
        self.url = f"{URL}?{q}"
        self.ws = None
        self.pending = []            # audio sent before the socket is open
        self.lock = threading.Lock()
        self.frames = []
        self.n = 0
        self.segments = []           # committed texts
        self.partial = ""
        self.closed = False
        self.failed = None
        self.first_chunk = True
        self.committed_evt = threading.Event()
        self.open_evt = threading.Event()
        self.buf = np.zeros(0, np.int16)
        threading.Thread(target=self._run, daemon=True).start()

    # ------------------------------------------------------------ socket
    def _run(self):
        import websocket
        try:
            import certifi
            self.ws = websocket.create_connection(self.url, header=[f"xi-api-key: {self.key}"],
                                                  timeout=10, enable_multithread=True,
                                                  sslopt={"ca_certs": certifi.where()})
        except Exception as e:
            self.failed = f"connect: {e}"
            self.open_evt.set()
            return
        self.open_evt.set()
        with self.lock:
            pending, self.pending = self.pending, None
        for msg in pending:
            self._send(msg)
        self.ws.settimeout(None)
        while not self.closed:
            try:
                raw = self.ws.recv()
            except Exception as e:
                if not self.closed:
                    self.failed = f"recv: {e}"
                break
            if not raw:
                break
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            kind = m.get("message_type", "")
            if kind == "partial_transcript":
                text = (m.get("text") or "").strip()
                if text and text != self.partial:
                    self.partial = text
                    self.on_partial(text)
            elif kind in ("committed_transcript", "committed_transcript_with_timestamps"):
                text = (m.get("text") or "").strip()
                self.partial = ""
                if text:
                    self.segments.append(text)
                    self.on_commit(text)
                self.committed_evt.set()
            elif kind == "session_started":
                pass
            elif kind in ("warning",):
                self.log(f"  live warning: {m.get('warning')}")
            elif "error" in m or kind.endswith("error") or kind in (
                    "quota_exceeded", "rate_limited", "invalid_request", "input_error",
                    "insufficient_audio_activity", "session_time_limit_exceeded"):
                if kind == "insufficient_audio_activity":
                    self.committed_evt.set()
                    continue
                self.failed = f"{kind}: {m.get('error')}"
                self.log(f"  live error: {self.failed}")
                break

    def _send(self, msg):
        try:
            self.ws.send(json.dumps(msg))
        except Exception as e:
            self.failed = f"send: {e}"

    def _queue(self, msg):
        with self.lock:
            if self.pending is not None:
                self.pending.append(msg)
                return
        if self.ws is not None and not self.failed:
            self._send(msg)

    # ------------------------------------------------------------ audio in
    def add(self, block_float):
        """Feed float32 mono audio (any block size); sent in 100 ms chunks."""
        self.frames.append(block_float)
        self.n += len(block_float)
        pcm = (np.clip(block_float, -1, 1) * 32767).astype(np.int16)
        self.buf = np.concatenate([self.buf, pcm])
        while len(self.buf) >= 1600:
            chunk, self.buf = self.buf[:1600], self.buf[1600:]
            self._chunk(chunk, commit=False)

    def _chunk(self, pcm, commit):
        msg = {"message_type": "input_audio_chunk",
               "audio_base_64": base64.b64encode(pcm.tobytes()).decode(),
               "commit": commit, "sample_rate": SAMPLE_RATE}
        self._queue(msg)

    def finish(self, timeout=3.0):
        """Commit what's left, wait for the final text, close. Returns the full text."""
        self.committed_evt.clear()
        tail = self.buf if len(self.buf) else np.zeros(320, np.int16)
        self.buf = np.zeros(0, np.int16)
        self._chunk(tail, commit=True)
        self.open_evt.wait(5)
        if not self.failed:
            self.committed_evt.wait(timeout)
        self.close()
        return " ".join(self.segments).strip()

    def close(self):
        self.closed = True
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass

    def audio(self):
        return np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)

    @property
    def seconds(self):
        return self.n / SAMPLE_RATE
