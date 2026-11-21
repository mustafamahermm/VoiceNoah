"""VoiceNoah for Windows.

Hold the shortcut (default: Right Ctrl), speak, let go: your words are typed into
whatever app has focus. Quick tap = hands-free (tap again to stop). Esc cancels.
A small pill at the bottom of the screen shows while you talk; the tray icon
opens the VoiceNoah window (stats, history, dictionary, settings).

Run with --window to open only the window (used internally), --background at login.
"""
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

import numpy as np

import core
from core import (APP_NAME, CFG, DASHBOARD_HTML, RES_DIR, SAMPLE_RATE, VERSION, cleanup,
                  load_keys, load_stats, log, record_stat, save_config, save_keys,
                  save_recording, test_key, transcribe, words_today)
from live import LiveSession, clean_live

# shortcut = list of key codes (same names the window's recorder sends) held together
CODE_TO_NAMES = {"ControlLeft": {"ctrl", "left ctrl"}, "ControlRight": {"right ctrl"},
                 "AltLeft": {"alt", "left alt"}, "AltRight": {"right alt", "alt gr"},
                 "ShiftLeft": {"shift", "left shift"}, "ShiftRight": {"right shift"},
                 "MetaLeft": {"left windows", "windows"}, "MetaRight": {"right windows"},
                 "Space": {"space"}, "Backquote": {"`"}, "IntlBackslash": {"<", "\\"},
                 "ContextMenu": {"menu", "apps"}, "CapsLock": {"caps lock"},
                 **{f"F{i}": {f"f{i}"} for i in range(1, 25)},
                 **{f"Key{c}": {c.lower()} for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
                 **{f"Digit{d}": {str(d)} for d in range(10)}}
LABELS = {"ControlLeft": "Left Ctrl", "ControlRight": "Right Ctrl", "AltLeft": "Left Alt",
          "AltRight": "Right Alt", "ShiftLeft": "Left Shift", "ShiftRight": "Right Shift",
          "MetaLeft": "Left Win", "MetaRight": "Right Win"}
PRESETS = [("Right Ctrl", ["ControlRight"]),
           ("Right Ctrl + Right Shift", ["ControlRight", "ShiftRight"]),
           ("Ctrl + Win", ["ControlLeft", "MetaLeft"]),
           ("Right Alt", ["AltRight"]), ("F8", ["F8"])]
LEGACY = {"right ctrl": ["ControlRight"], "right alt": ["AltRight"], "right shift": ["ShiftRight"],
          "f8": ["F8"], "f9": ["F9"]}


def shortcut_codes(value):
    if isinstance(value, str):
        return LEGACY.get(value, ["ControlRight"])
    codes = [c for c in (value or []) if c in CODE_TO_NAMES]
    return codes or ["ControlRight"]


def shortcut_list(value):
    """Config value -> list of shortcuts; older configs hold a single one."""
    if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
        out = []
        for v in value:
            c = shortcut_codes(v)
            if c not in out:
                out.append(c)
        return out or [["ControlRight"]]
    return [shortcut_codes(value)]


def shortcut_label(codes):
    return " + ".join(LABELS.get(c, c) for c in codes)


def shortcuts_label(lst):
    return " or ".join(shortcut_label(c) for c in lst)
TAP_SECONDS = 0.35
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def self_command(*args):
    """How to start this program again (works both from source and as the .exe)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, os.path.abspath(__file__), *args]


# =================================================================== login item
def login_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_login(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            cmd = " ".join(f'"{a}"' for a in self_command("--background"))
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# =================================================================== window process (pywebview)
class WindowApi:
    """Called from the page: window.pywebview.api.call({...}). Returns replies for vnReceive."""

    def state(self):
        keys = load_keys()
        cfg = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))
        st = {**CFG, **cfg}
        return {
            "type": "state", "platform": "windows", "version": VERSION, "stats": load_stats(),
            "keys": {k: (keys[k][-4:] if keys.get(k) else "") for k in ("elevenlabs", "anthropic")},
            "settings": {
                "hotkey": shortcut_list(st.get("win_hotkey")),
                "hotkey_label": shortcuts_label(shortcut_list(st.get("win_hotkey"))),
                "hotkey_labels": [shortcut_label(c) for c in shortcut_list(st.get("win_hotkey"))],
                "live": st.get("live", True),
                "cleanup": st.get("cleanup", True), "polish_after_live": st.get("polish_after_live", False),
                "sounds": st.get("sounds", True), "login": login_enabled(),
                "vocabulary": st.get("vocabulary") or [], "replacements": st.get("replacements") or {},
            },
            "hotkeys": [[codes, label] for label, codes in PRESETS],
            "permissions": {"accessibility": True},
        }

    def call(self, msg):
        kind = (msg or {}).get("type")
        if kind == "getState":
            return [self.state()]
        if kind == "saveKey":
            name, value = msg.get("name"), (msg.get("value") or "").strip()
            ok, note = test_key(name, value)
            if ok:
                save_keys(**{name: value})
            return [{"type": "keyResult", "name": name, "ok": ok, "note": note}, self.state()]
        if kind == "saveSettings":
            st = dict(msg.get("settings") or {})
            login = st.pop("login", None)
            if "hotkey" in st:
                st["win_hotkey"] = shortcut_list(st.pop("hotkey"))
            save_config(**st)
            if login is not None:
                set_login(bool(login))
            return [{"type": "saved"}]
        if kind == "openURL":
            import webbrowser
            webbrowser.open(msg.get("url", ""))
        return []


def run_window():
    import webview
    html = DASHBOARD_HTML.read_text(encoding="utf-8").replace(
        "/*__STATE__*/null", json.dumps(WindowApi().state(), ensure_ascii=False))
    webview.create_window(APP_NAME, html=html, js_api=WindowApi(), width=1040, height=720,
                          min_size=(720, 520))
    webview.start()


def open_window():
    flags = 0x08000000 if getattr(sys, "frozen", False) else 0   # CREATE_NO_WINDOW
    subprocess.Popen(self_command("--window"), creationflags=flags)


# =================================================================== pasting
def paste_text(text):
    """Paste through the clipboard (Ctrl+V), then put the user's clipboard back."""
    import keyboard
    import pyperclip
    try:
        saved = pyperclip.paste()
    except Exception:
        saved = None
    pyperclip.copy(text)
    time.sleep(0.03)
    keyboard.send("ctrl+v")
    if saved is not None and CFG.get("restore_clipboard", True):
        def restore():
            time.sleep(0.5)
            try:
                if pyperclip.paste() == text:
                    pyperclip.copy(saved)
            except Exception:
                pass
        threading.Thread(target=restore, daemon=True).start()


def beep(kind):
    if not CFG.get("sounds", True):
        return
    try:
        import winsound
        winsound.MessageBeep({"start": 0x00000040, "stop": 0x00000000, "error": 0x00000010}.get(kind, 0))
    except Exception:
        pass


# =================================================================== pill (tkinter)
class Pill:
    W, H = 220, 60
    KEY = "#ff00fe"                        # transparent color

    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.KEY)
        try:
            self.win.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.win.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{sh - self.H - 2}")
        self.c = tk.Canvas(self.win, width=self.W, height=self.H, bg=self.KEY, highlightthickness=0)
        self.c.pack()
        self.win.withdraw()
        self.shown = False
        self.state = "idle"
        self.level = 0.0
        self.smooth = 0.0
        self.phase = 0.0
        self.w = 30.0
        self.bars = [0.0] * 9
        self.shape = [0.35, 0.55, 0.8, 1.0, 0.8, 0.55, 0.35, 0.55, 0.8]
        self.tick()

    def rounded(self, x, y, w, h, fill, outline):
        r = h / 2
        c = self.c
        c.create_oval(x, y, x + h, y + h, fill=fill, outline=outline)
        c.create_oval(x + w - h, y, x + w, y + h, fill=fill, outline=outline)
        c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline=fill)
        c.create_line(x + r, y, x + w - r, y, fill=outline)
        c.create_line(x + r, y + h, x + w - r, y + h, fill=outline)

    def tick(self):
        st = self.state
        visible = st in ("recording", "locked", "processing")
        target = {"recording": 88, "locked": 112, "processing": 60}.get(st, 30)
        if visible and not self.shown:
            self.shown = True
            self.w = 30
            self.win.deiconify()
            self.win.attributes("-topmost", True)
        if self.shown:
            self.w += (target - self.w) * 0.22
            if not visible and self.w < 34:
                self.shown = False
                self.win.withdraw()
        if self.shown:
            self.phase += 1 / 60
            self.smooth += (self.level - self.smooth) * (0.30 if self.level > self.smooth else 0.10)
            c = self.c
            c.delete("all")
            h = 24
            x, y = (self.W - self.w) / 2, self.H - h - 10
            self.rounded(x, y, self.w, h, "#0b0b0d", "#4a4a52")
            cy = y + h / 2
            if st in ("recording", "locked"):
                for i, s in enumerate(self.shape):
                    wave = 0.72 + 0.28 * math.sin(self.phase * 7 + i * 0.9)
                    idle = 0.06 * (0.5 + 0.5 * math.sin(self.phase * 3 + i * 0.6))
                    tgt = max(idle, self.smooth * s * wave)
                    self.bars[i] += (tgt - self.bars[i]) * 0.35
                total = 9 * 2 + 8 * 2.6
                bx = x + (self.w - total) / 2 - (4 if st == "locked" else 0)
                for i, v in enumerate(self.bars):
                    bh = 2.5 + (h - 10) * v
                    c.create_rectangle(bx + i * 4.6, cy - bh / 2, bx + i * 4.6 + 2, cy + bh / 2,
                                       fill="#f2f2f2", outline="")
                if st == "locked":
                    c.create_oval(x + 10, cy - 3, x + 16, cy + 3, fill="#ff4d5a", outline="")
            elif st == "processing":
                for i in range(3):
                    a = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(self.phase * 6 - i * 0.9))
                    g = int(60 + 190 * a)
                    cx = x + self.w / 2 - 10 + i * 10
                    c.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, fill=f"#{g:02x}{g:02x}{g:02x}", outline="")
        self.root.after(16, self.tick)


# =================================================================== app
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.pill = Pill(self.root)
        self.state = "idle"
        self.press_t = 0.0
        self.key_down = False
        self.stream = None
        self.live = None
        self.pending = 0
        self.cfg_mtime = 0.0
        self.hotkeys = shortcut_list(CFG.get("win_hotkey"))
        self.pressed = set()
        self.typed = []

    # ---------------------------------------------------------- settings changes from the window
    def watch_config(self):
        try:
            m = core.CONFIG_PATH.stat().st_mtime
            if m != self.cfg_mtime:
                self.cfg_mtime = m
                CFG.update(json.loads(core.CONFIG_PATH.read_text(encoding="utf-8")))
                self.hotkeys = shortcut_list(CFG.get("win_hotkey"))
        except Exception:
            pass
        self.root.after(1500, self.watch_config)

    # ---------------------------------------------------------- keyboard
    def chord_held(self):
        return any(all(self.pressed & CODE_TO_NAMES[c] for c in hk) for hk in self.hotkeys)

    def on_key(self, ev):
        name = (ev.name or "").lower()
        if ev.event_type == "down":
            self.pressed.add(name)
        else:
            self.pressed.discard(name)
        part_of_chord = any(name in CODE_TO_NAMES[c] for hk in self.hotkeys for c in hk)
        held = self.chord_held()
        if held and not self.key_down:
            self.key_down = True
            self.root.after(0, self.hotkey_down)
            return
        if not held and self.key_down:
            self.key_down = False
            self.root.after(0, self.hotkey_up)
            return
        if part_of_chord:
            return
        if ev.event_type == "down" and self.state in ("recording", "locked"):
            if name == "esc":
                self.root.after(0, self.cancel)
            elif self.state == "recording" and time.time() - self.press_t < 0.6 and name not in ("v",):
                self.root.after(0, lambda: self.cancel(silent=True))   # shortcut used with another key

    def hotkey_down(self):
        if self.state == "locked":
            self.stop()
        elif self.state != "recording":
            self.press_t = time.time()
            self.begin()

    def hotkey_up(self):
        if self.state != "recording":
            return
        if time.time() - self.press_t < TAP_SECONDS:
            self.state = self.pill.state = "locked"
        else:
            self.stop()

    # ---------------------------------------------------------- audio
    def audio_cb(self, indata, frames, t, status):
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block ** 2)) + 1e-7)
        self.pill.level = min(1.0, max(0.0, (20 * math.log10(rms) + 55) / 40))
        if self.live is not None:
            self.live.add(block)

    def begin(self):
        import sounddevice as sd
        key = load_keys().get("elevenlabs")
        if not key:
            beep("error")
            open_window()
            return
        typed = []
        self.typed = typed

        def on_commit(text):
            text = core.apply_replacements(clean_live(text))
            if text and CFG.get("live", True):
                typed.append(text)
                paste_text(text + " ")
        try:
            app = ""
            try:
                import win32gui  # optional, for "Where you dictate"
                app = win32gui.GetWindowText(win32gui.GetForegroundWindow())[-40:]
            except Exception:
                pass
            self.live = LiveSession(key, on_partial=lambda t: None, on_commit=on_commit,
                                    model=CFG.get("live_model", "scribe_v3_live"),
                                    keyterms=[t for t in (CFG.get("vocabulary") or []) if t],
                                    language=CFG.get("language"),
                                    silence_secs=CFG.get("live_pause_secs", 0.6), log=log,
                extra={"multilang_mode": "true"} if CFG.get("live_multilang", True) else None)
            self.live.app = app
            try:
                self.open_mic()
            except Exception as e:     # the microphone changed since start: refresh the list, try again
                log(f"Microphone error ({e}); trying again")
                sd._terminate()
                sd._initialize()
                self.open_mic()
        except Exception as e:
            log(f"Microphone error: {e}")
            beep("error")
            self.live = None
            return
        self.state = self.pill.state = "recording"
        beep("start")

    def open_mic(self):
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     blocksize=800, callback=self.audio_cb)
        self.stream.start()

    def close_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.pill.level = 0.0

    def stop(self):
        self.close_stream()
        ls, typed = self.live, self.typed
        self.live = None
        self.state = self.pill.state = "processing"
        beep("stop")
        if ls is None:
            self.state = self.pill.state = "idle"
            return
        self.pending += 1

        def wait():
            full = ls.finish()
            if ls.failed:
                log(f"live failed ({ls.failed}); using normal transcription")
                full = transcribe(ls.audio()) if ls.seconds >= 0.4 else ""
                text = cleanup(clean_live(full)) if full else ""
                if text:
                    paste_text(text + " ")
            elif not CFG.get("live", True):
                text = clean_live(full)
                if CFG.get("cleanup", True):
                    text = cleanup(text)
                if text:
                    paste_text(text + " ")
            else:
                text = " ".join(typed).strip()
            if text:
                record_stat(text, ls.seconds, getattr(ls, "app", ""))
                if CFG.get("save_recordings", True):
                    save_recording(ls.audio(), full, text)
            self.root.after(0, self.finished)
        threading.Thread(target=wait, daemon=True).start()

    def finished(self):
        self.pending = max(0, self.pending - 1)
        if self.state == "processing" and not self.pending:
            self.state = self.pill.state = "idle"

    def cancel(self, silent=False):
        self.close_stream()
        if self.live is not None:
            self.live.close()
            self.live = None
        self.state = self.pill.state = "processing" if self.pending else "idle"
        if not silent:
            beep("error")

    # ---------------------------------------------------------- tray
    def start_tray(self):
        import pystray
        from PIL import Image
        icon_path = RES_DIR / "VoiceNoah-icon.png"
        if not icon_path.exists():
            icon_path = RES_DIR / "assets" / "VoiceNoah-icon.png"
        img = Image.open(icon_path)

        def today_label(item):
            return f"Today: {words_today():,} words"
        menu = pystray.Menu(
            pystray.MenuItem("Open VoiceNoah", lambda: open_window(), default=True),
            pystray.MenuItem(today_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Quit {APP_NAME}", lambda icon, item: self.quit(icon)),
        )
        self.icon = pystray.Icon(APP_NAME, img, APP_NAME, menu)
        self.icon.run_detached()

    def quit(self, icon):
        icon.stop()
        self.root.after(0, self.root.destroy)

    def run(self):
        import keyboard
        keyboard.hook(self.on_key)
        self.start_tray()
        self.watch_config()
        log(f"{APP_NAME} is running. Hold {shortcuts_label(self.hotkeys)} and speak.")
        self.root.mainloop()
        os._exit(0)


def already_running():
    """Single instance via a named Windows mutex."""
    ctypes.windll.kernel32.CreateMutexW(None, False, "VoiceNoahSingleInstance")
    return ctypes.windll.kernel32.GetLastError() == 183      # ERROR_ALREADY_EXISTS


def main():
    if "--window" in sys.argv:
        run_window()
        return
    if already_running():
        open_window()          # opening the app again shows its window
        return
    app = App()
    if "--background" not in sys.argv:
        open_window()
    app.run()


if __name__ == "__main__":
    main()
