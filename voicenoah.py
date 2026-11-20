#!/usr/bin/env python3
"""VoiceNoah - voice dictation for macOS (Egyptian Arabic + English).

Hold fn and speak; release and the text is typed into whatever app has focus.
Quick tap = hands-free mode (tap again to stop). Esc cancels.
Lives in the menu bar; a small pill appears at the bottom only while you talk,
showing your words as they are transcribed.
Speech is transcribed in chunks *while you talk* (split at natural pauses), so even
long dictations are typed 1-2 seconds after you let go.
Engine: ElevenLabs Scribe (cloud), local Whisper (MLX) fallback, optional Claude cleanup.
"""
import io
import json
import math
import plistlib
import random
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import objc
import Quartz
import sounddevice as sd
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSAttributedString,
    NSBackingStoreBuffered, NSBezierPath, NSColor, NSGraphicsContext, NSEvent, NSEventMaskFlagsChanged,
    NSEventMaskKeyDown, NSFont, NSFontAttributeName, NSForegroundColorAttributeName, NSImage,
    NSLineBreakByTruncatingHead, NSMakeRect, NSMenu, NSMenuItem, NSMutableParagraphStyle,
    NSGradient, NSShadow, NSPanel, NSParagraphStyleAttributeName, NSPasteboard, NSPasteboardItem,
    NSPasteboardTypeString, NSScreen, NSSound, NSStatusBar, NSTimer, NSView,
    NSVariableStatusItemLength, NSWindow, NSWorkspace,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
)
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from Foundation import NSObject
from PyObjCTools import AppHelper

from live import LiveSession, clean_live
from core import *  # noqa: F401,F403  (settings, engines, stats)
import core

LAUNCH_AGENT = Path.home() / "Library/LaunchAgents/com.voicenoah.app.plist"
APP_EXEC = Path.home() / "Applications/VoiceNoah.app/Contents/MacOS/VoiceNoah"

# hotkey name -> (macOS key code, device-specific modifier bit)
HOTKEYS = {
    "alt_r": (61, 0x40), "alt_l": (58, 0x20),
    "cmd_r": (54, 0x10), "ctrl_r": (62, 0x2000),
    "fn": (63, 1 << 23),
}
HOTKEY_NAMES = {"alt_r": "Right ⌥ Option", "alt_l": "Left ⌥ Option",
                "cmd_r": "Right ⌘ Command", "ctrl_r": "Right ⌃ Control", "fn": "fn"}
TAP_SECONDS = 0.35
ESC = 53

# chunking while you talk: cut at a pause once a chunk is long enough
CHUNK_MIN_SEC = 3.0       # don't cut before this much audio in the chunk
CHUNK_SOFT_SEC = 4.0      # after this, a shorter pause is enough
CHUNK_MAX_SEC = 6.5       # never longer: cut at the quietest moment of the last 2 s
PAUSE_SEC = 0.40          # quiet this long = pause
SHORT_PAUSE_SEC = 0.20    # enough once the chunk is long
BLOCK = 800               # 50 ms audio blocks
TYPING_WPM = 40           # for "time saved" (average typing speed)


# =================================================================== paste
_clip = {"saved": None, "gen": 0}
PASTE_GUARD = [0.0]       # ignore our own Cmd+V in the key monitor until this time


def paste_text(text):
    """Paste via Cmd+V. Several pastes in a row keep the user's clipboard safe:
    it is saved before the first one and restored after the last one."""
    pb = NSPasteboard.generalPasteboard()
    if CFG.get("restore_clipboard", True) and _clip["saved"] is None:
        _clip["saved"] = [{t: item.dataForType_(t) for t in item.types()}
                          for item in pb.pasteboardItems() or []]
    PASTE_GUARD[0] = time.time() + 0.3
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    for down in (True, False):          # Cmd+V (key code 9 = V)
        ev = Quartz.CGEventCreateKeyboardEvent(src, 9, down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    _clip["gen"] += 1
    gen = _clip["gen"]

    def restore():
        if gen != _clip["gen"] or _clip["saved"] is None:
            return                     # another paste came after this one
        saved, _clip["saved"] = _clip["saved"], None
        if not saved:
            return
        pb.clearContents()
        items = []
        for d in saved:
            it = NSPasteboardItem.alloc().init()
            for t, data in d.items():
                if data is not None:
                    it.setData_forType_(data, t)
            items.append(it)
        pb.writeObjects_(items)
    AppHelper.callLater(0.8, restore)


class Typer:
    """Types text into the focused app as you speak, fixing only what changed.

    The current (unfinished) sentence may still change, so we keep what we typed
    for it and send backspaces + the new ending when the transcript updates.
    """
    BACKSPACE = 51

    def __init__(self):
        self.src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        self.done = ""        # finished segments typed so far (this dictation)
        self.seg = ""         # what is typed for the current segment
        self.active = True    # False after Esc: late results are ignored

    @staticmethod
    def _units(text):
        import unicodedata
        return sum(1 for ch in text if not unicodedata.combining(ch))

    def _post(self, down, code=0, text=None):
        ev = Quartz.CGEventCreateKeyboardEvent(self.src, code, down)
        if text is not None:
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
        Quartz.CGEventSetFlags(ev, 0)          # never combine with fn / other keys
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def type(self, text):
        PASTE_GUARD[0] = time.time() + 0.5
        for i in range(0, len(text), 16):
            part = text[i:i + 16]
            self._post(True, text=part)
            self._post(False, text=part)

    def backspace(self, n):
        PASTE_GUARD[0] = time.time() + 0.5
        for _ in range(n):
            self._post(True, self.BACKSPACE)
            self._post(False, self.BACKSPACE)

    def _show(self, new):
        """Make the current segment read `new`, with minimal edits."""
        old = self.seg
        k = 0
        while k < min(len(old), len(new)) and old[k] == new[k]:
            k += 1
        if k < len(old):
            self.backspace(self._units(old[k:]))
        if k < len(new):
            self.type(new[k:])
        self.seg = new

    def partial(self, text):
        if not self.active or not CFG.get("live_partials", False):
            return                       # default: type each sentence once, when it is final
        text = clean_live(text)
        cut = text.rfind(" ")                  # only finished words; the last one may change
        self._show(text[:cut + 1] if cut > 0 else "")

    def commit(self, text):
        if not self.active:
            return
        text = clean_live(text)
        self._show(text + " " if text else "")
        self.done += self.seg
        self.seg = ""

    def erase_all(self):
        self.backspace(self._units(self.done + self.seg))
        self.done = self.seg = ""

    def typed(self):
        return (self.done + self.seg).strip()


def play(name):
    if CFG.get("sounds", True):
        s = NSSound.soundNamed_(name)
        if s:
            s.play()


# =================================================================== one dictation
class Dictation:
    """Audio of one dictation, cut into chunks at pauses and transcribed while you talk."""

    def __init__(self, pool, on_live, on_chunk=None):
        self.pool, self.on_live, self.on_chunk = pool, on_live, on_chunk
        self.frames, self.n = [], 0
        self.chunk_start, self.quiet = 0, 0
        self.dbs = []                 # level of each block, for the noise floor and cuts
        self.done, self.next_out = {}, 0
        self.futures, self.live = [], {}
        self.cancelled = False
        self.app = ""
        self.lock = threading.Lock()

    def silence_db(self):
        """Quiet = a little above this room's noise floor (adapts to the mic and the room)."""
        recent = self.dbs[-400:]
        floor = float(np.percentile(recent, 10)) if len(recent) >= 10 else -55.0
        return min(-30.0, max(-50.0, floor + 9.0))

    def add(self, block, db):
        with self.lock:
            self.frames.append(block)
            self.dbs.append(db)
            self.n += len(block)
            self.quiet = self.quiet + len(block) if db < self.silence_db() else 0
            length = (self.n - self.chunk_start) / SAMPLE_RATE
            quiet = self.quiet / SAMPLE_RATE
            cut = None
            if (length >= CHUNK_MIN_SEC and quiet >= PAUSE_SEC) or \
               (length >= CHUNK_SOFT_SEC and quiet >= SHORT_PAUSE_SEC):
                cut = self.n - self.quiet // 2
            elif length >= CHUNK_MAX_SEC:      # no pause: cut at the quietest 50 ms of the last 2 s
                k = int(2.0 * SAMPLE_RATE / BLOCK)
                tail = self.dbs[-k:]
                i = int(np.argmin(tail))
                cut = self.n - (len(tail) - i) * BLOCK + BLOCK // 2
            if cut is not None and cut > self.chunk_start:
                self._submit(self.chunk_start, cut)
                self.chunk_start = cut

    def finish(self):
        with self.lock:
            if self.n - self.chunk_start > 0:
                self._submit(self.chunk_start, self.n)
                self.chunk_start = self.n
            return list(self.futures)

    def audio(self, start, end):
        with self.lock:
            frames = list(self.frames)
        return np.concatenate(frames).flatten()[start:end] if frames else np.zeros(0, np.float32)

    def _submit(self, start, end):
        idx = len(self.futures)
        self.futures.append(self.pool.submit(self._process, idx, start, end))

    def _process(self, idx, start, end):
        if self.cancelled:
            self._deliver(idx, "")
            return ""
        audio = self.audio(start, end)
        if len(audio) < CFG.get("min_seconds", 0.4) * SAMPLE_RATE or not audio.size \
                or np.abs(audio).max() < 0.01:
            self._deliver(idx, "")
            return ""
        raw = transcribe(audio)
        if self.cancelled or not raw:
            self._deliver(idx, "")
            return ""
        self.live[idx] = raw
        self.on_live(self.live_text())
        text = cleanup(raw)
        self._deliver(idx, text)
        return text

    def _deliver(self, idx, text):
        """Hand chunks to on_chunk strictly in order, as soon as each is ready."""
        if self.on_chunk is None:
            return
        out = []
        with self.lock:
            self.done[idx] = text
            while self.next_out in self.done:
                out.append(self.done.pop(self.next_out))
                self.next_out += 1
        for t in out:
            if t and not self.cancelled:
                self.on_chunk(t)

    def live_text(self):
        return " ".join(self.live[i] for i in sorted(self.live))

    @property
    def seconds(self):
        return self.n / SAMPLE_RATE


# =================================================================== widget
PANEL_W, PANEL_H = 520, 60
SIZES = {"idle": (30, 6), "recording": (88, 24), "locked": (112, 24),
         "processing": (60, 24), "loading": (60, 24)}
PILL_Y = 12               # pill bottom inside the panel (room for the glow below)
MAX_PILL_W = 460
BAR_SHAPE = [0.35, 0.55, 0.8, 1.0, 0.8, 0.55, 0.35, 0.55, 0.8]


def text_attrs():
    ps = NSMutableParagraphStyle.alloc().init()
    ps.setLineBreakMode_(NSLineBreakByTruncatingHead)
    return {NSFontAttributeName: NSFont.systemFontOfSize_weight_(13, 0.2),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1, 0.92),
            NSParagraphStyleAttributeName: ps}


class PillView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.ctrl = None
        self.w, self.h = SIZES["idle"]
        self.bars = [0.0] * len(BAR_SHAPE)
        self.phase = 0.0
        self.smooth = 0.0
        self.attrs = text_attrs()
        self.text = ""
        self.text_w = 0.0
        return self

    def acceptsFirstMouse_(self, e):
        return True

    def mouseDown_(self, e):
        if self.ctrl:
            self.ctrl.pill_clicked()

    @objc.python_method
    def set_text(self, text):
        text = text[-120:]                      # only the latest words matter
        if text != self.text:
            self.text = text
            self.text_w = NSAttributedString.alloc().initWithString_attributes_(
                text, self.attrs).size().width if text else 0.0

    @objc.python_method
    def target_size(self, st):
        w, h = SIZES["idle" if st == "loading" else st]
        if st in ("recording", "locked") and self.text:
            w = min(MAX_PILL_W, w + 16 + self.text_w)
        return w, h

    def drawRect_(self, rect):
        st = self.ctrl.state if self.ctrl else "idle"
        W = self.bounds().size.width
        pw, ph = self.w, self.h
        x, y = (W - pw) / 2.0, float(PILL_Y)
        active = st != "idle" and pw > 40
        energy = min(1.0, self.smooth * 1.4) if st in ("recording", "locked") else 0.35
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, pw, ph), ph / 2, ph / 2)
        dark = NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.92)
        if active:
            # soft white glow behind a black pill (no colors), stronger when you speak
            sh = NSShadow.alloc().init()
            sh.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10 + 0.22 * energy))
            sh.setShadowBlurRadius_(6 + 8 * energy)
            sh.setShadowOffset_((0, 0))
            NSGraphicsContext.saveGraphicsState()
            sh.set()
            dark.setFill()
            path.fill()
            NSGraphicsContext.restoreGraphicsState()
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).setStroke()
            path.setLineWidth_(1.0)
            path.stroke()
        else:
            NSColor.colorWithCalibratedWhite_alpha_(0.07, 0.6).setFill()
            path.fill()
            return
        cy = y + ph / 2
        if st in ("recording", "locked"):
            n, bw, gap = len(self.bars), 2.0, 2.6
            total = n * bw + (n - 1) * gap
            bx = x + (pw - total) / 2
            if st == "locked":   # small red dot = hands-free, stop square on the right
                bx -= 4
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1, 0.3, 0.35, 1).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x + 10, cy - 3, 6, 6)).fill()
                NSColor.colorWithCalibratedWhite_alpha_(1, 0.8).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(x + pw - 17, cy - 3.5, 7, 7), 1.5, 1.5).fill()
            NSColor.colorWithCalibratedWhite_alpha_(1, 0.95).setFill()
            for i, v in enumerate(self.bars):
                bh = 2.5 + (ph - 10) * v
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(bx + i * (bw + gap), cy - bh / 2, bw, bh), 1.0, 1.0).fill()
        else:                    # processing / loading: three pulsing dots
            for i in range(3):
                a = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(self.phase * 6 - i * 0.9))
                NSColor.colorWithCalibratedWhite_alpha_(1, a).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(x + pw / 2 - 12 + i * 10 - 2.5, cy - 2.5, 5, 5)).fill()


# =================================================================== app
class Controller(NSObject):
    def init(self):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.state = "loading"
        self.press_t = 0.0
        self.key_down = False
        self.level = 0.0
        self.stream = None
        self.dict = None
        self.live = None
        self.typer = None
        self.pending = 0
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.monitors = []
        self.trusted = False
        self.dash = None
        name = CFG.get("hotkey", "fn")
        self.keycode, self.keymask = HOTKEYS.get(name, HOTKEYS["fn"])
        self.keyname = HOTKEY_NAMES.get(name, name)
        return self

    # ---------------------------------------------------------- setup
    @objc.python_method
    def setup(self):
        migrate_old_history()
        self.make_panel()
        self.make_menu()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 60.0, self, "tick:", None, True)
        self.check_trust(prompt=True)
        threading.Thread(target=self.load_model, daemon=True).start()

    @objc.python_method
    def make_panel(self):
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(False)
        p.setLevel_(Quartz.kCGStatusWindowLevel + 1)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | NSWindowCollectionBehaviorStationary
                                 | NSWindowCollectionBehaviorFullScreenAuxiliary)
        p.setHidesOnDeactivate_(False)
        p.setFloatingPanel_(True)
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setIgnoresMouseEvents_(False)
        v = PillView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
        v.ctrl = self
        p.setContentView_(v)
        self.panel, self.view = p, v
        self.shown = False
        self.place_panel()

    @objc.python_method
    def place_panel(self):
        mouse = NSEvent.mouseLocation()
        screen = NSScreen.mainScreen()
        for s in NSScreen.screens():
            f = s.frame()
            if f.origin.x <= mouse.x <= f.origin.x + f.size.width and \
               f.origin.y <= mouse.y <= f.origin.y + f.size.height:
                screen = s
        sf = screen.frame()               # the real bottom edge, like Wispr
        self.panel.setFrameOrigin_((sf.origin.x + (sf.size.width - PANEL_W) / 2,
                                    sf.origin.y - PILL_Y + CFG.get("pill_bottom_margin", 3)))

    @objc.python_method
    def make_menu(self):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.icon = None
        m = NSMenu.alloc().init()
        self.status_mi = m.addItemWithTitle_action_keyEquivalent_("Starting…", None, "")
        self.today_mi = m.addItemWithTitle_action_keyEquivalent_("", None, "")
        self.update_today()
        m.addItem_(NSMenuItem.separatorItem())
        dash = m.addItemWithTitle_action_keyEquivalent_("Open VoiceNoah", "openDashboard:", "o")
        dash.setTarget_(self)
        m.addItem_(NSMenuItem.separatorItem())
        m.addItemWithTitle_action_keyEquivalent_(f"Hold {self.keyname} to talk", None, "")
        m.addItemWithTitle_action_keyEquivalent_("Tap it once for hands-free · Esc cancels", None, "")
        m.addItem_(NSMenuItem.separatorItem())
        self.clean_mi = m.addItemWithTitle_action_keyEquivalent_("Clean up text (Claude)", "toggleCleanup:", "")
        self.clean_mi.setTarget_(self)
        self.clean_mi.setState_(1 if CFG.get("cleanup", True) else 0)
        self.login_mi = m.addItemWithTitle_action_keyEquivalent_("Open at login", "toggleLogin:", "")
        self.login_mi.setTarget_(self)
        self.login_mi.setState_(1 if LAUNCH_AGENT.exists() else 0)
        for title, sel in (("Settings…", "openSettings:"), (f"Quit {APP_NAME}", "quit:")):
            mi = m.addItemWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
        self.item.setMenu_(m)
        self.set_icon("waveform")

    @objc.python_method
    def update_today(self):
        self.today_mi.setTitle_(f"Today: {words_today():,} words")

    @objc.python_method
    def set_icon(self, symbol):
        if symbol == self.icon:
            return
        self.icon = symbol
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, APP_NAME)
        b = self.item.button()
        if img is not None:
            img.setTemplate_(True)
            b.setImage_(img)
            b.setTitle_("")
        else:
            b.setTitle_("🎙")

    @objc.python_method
    def set_status(self, symbol, text):
        self.set_icon(symbol)
        self.status_mi.setTitle_(text)

    @objc.python_method
    def check_trust(self, prompt=False):
        opts = {kAXTrustedCheckOptionPrompt: bool(prompt)}
        ok = bool(AXIsProcessTrustedWithOptions(opts))
        if ok and not self.trusted:
            self.trusted = True
            self.install_monitors()
            log("✓ Accessibility permission OK: hotkey is active.")
            if self.state != "loading":
                self.set_status("waveform", "Ready")
        elif not ok and not self.monitors:
            if prompt:
                log(f"\n⚠️  {APP_NAME} needs Accessibility permission:\n"
                    f"   System Settings → Privacy & Security → Accessibility → turn ON {APP_NAME}.\n"
                    "   It starts working by itself once you allow it.\n")
            self.set_status("exclamationmark.triangle", "Needs Accessibility permission")
            AppHelper.callLater(2.0, self.check_trust)

    @objc.python_method
    def install_monitors(self):
        self.monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, self.on_flags))
        self.monitors.append(NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self.on_key))

    @objc.python_method
    def load_model(self):
        t = time.time()
        if CFG.get("engine", "elevenlabs") == "elevenlabs" and load_keys().get("elevenlabs"):
            try:      # open the HTTPS connection now so the first dictation is fast
                http().get("https://api.elevenlabs.io/v1/models", timeout=10)
            except Exception:
                pass
            log("✓ Cloud engine ready (ElevenLabs Scribe)")
            if CFG.get("cleanup", True) and load_keys().get("anthropic"):
                t2 = time.time()
                cleanup("warm up the connection one two three")   # loads the SDK + opens TLS
                log(f"✓ Cleanup ready ({time.time() - t2:.1f}s)")
        else:
            log("Loading local Whisper model…")
            transcribe_local(np.zeros(SAMPLE_RATE, dtype=np.float32))
            log(f"✓ Model ready in {time.time() - t:.1f}s")
        AppHelper.callAfter(self.model_ready)

    @objc.python_method
    def model_ready(self):
        if self.state == "loading":
            self.state = "idle"
        if self.trusted:
            self.set_status("waveform", "Ready")
        log(f"\n{APP_NAME} is running. Hold {self.keyname} and speak. "
            "Tap once for hands-free. Esc cancels.\n")

    # ---------------------------------------------------------- input
    @objc.python_method
    def on_flags(self, ev):
        if ev.keyCode() != self.keycode:
            return
        down = bool(ev.modifierFlags() & self.keymask)
        if down and not self.key_down:
            self.key_down = True
            self.hotkey_down()
        elif not down and self.key_down:
            self.key_down = False
            self.hotkey_up()

    @objc.python_method
    def on_key(self, ev):
        if self.state not in ("recording", "locked") or time.time() < PASTE_GUARD[0]:
            return
        if ev.keyCode() == ESC:
            self.cancel()
        elif self.state == "recording" and time.time() - self.press_t < 0.6:
            self.cancel(silent=True)    # hotkey used as a normal modifier (fn+key)

    @objc.python_method
    def hotkey_down(self):
        if self.state == "locked":
            self.stop()
        elif self.state != "recording":
            self.press_t = time.time()
            self.begin()

    @objc.python_method
    def hotkey_up(self):
        if self.state != "recording":
            return
        if time.time() - self.press_t < TAP_SECONDS:
            self.state = "locked"       # quick tap -> hands-free
            log("● hands-free: tap again to stop")
        else:
            self.stop()

    @objc.python_method
    def pill_clicked(self):
        if self.state in ("recording", "locked"):
            self.stop()
        elif self.state in ("idle", "processing"):
            self.press_t = time.time()
            self.begin()
            self.state = "locked"

    # ---------------------------------------------------------- audio
    @objc.python_method
    def _audio_cb(self, indata, frames, t, status):
        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block ** 2)) + 1e-7)
        db = 20 * math.log10(rms)
        self.level = min(1.0, max(0.0, (db + 55) / 40))
        d = self.dict
        if d is not None:
            d.add(block, db)
        ls = self.live
        if ls is not None:
            ls.add(block)

    @objc.python_method
    def on_live(self, text):
        if CFG.get("pill_text", False):
            AppHelper.callAfter(self.view.set_text, text)

    @objc.python_method
    def on_chunk(self, d, text):
        d.typed.append(text)
        AppHelper.callAfter(paste_text, text + " ")

    @objc.python_method
    def begin(self):
        if self.state == "loading":
            play("Basso")
            return
        try:
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            app_name = front.localizedName() if front else ""
        except Exception:
            app_name = ""
        key = load_keys().get("elevenlabs")
        if CFG.get("live", True) and key and CFG.get("engine", "elevenlabs") == "elevenlabs":
            typer = Typer()
            self.typer = typer
            self.live = LiveSession(
                key,
                on_partial=lambda t: AppHelper.callAfter(typer.partial, t),
                on_commit=lambda t: AppHelper.callAfter(typer.commit, t),
                model=CFG.get("live_model", "scribe_v3_live"),
                keyterms=[t for t in (CFG.get("vocabulary") or []) if t],
                language=CFG.get("language"),
                silence_secs=CFG.get("live_pause_secs", 0.6), log=log)
            self.live.app = app_name
        else:
            d = Dictation(self.pool, self.on_live)
            if CFG.get("type_while_speaking", True):
                d.on_chunk = lambda t, d=d: self.on_chunk(d, t)
            d.app = app_name
            self.dict = d
        self.view.set_text("")
        try:
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                         blocksize=800, callback=self._audio_cb)
            self.stream.start()
        except Exception as e:
            log(f"Microphone error: {e}")
            self.dict = None
            if self.live:
                self.live.close()
                self.live = None
            play("Basso")
            return
        self.place_panel()
        self.state = "recording"
        play("Tink")
        log("● recording…")

    @objc.python_method
    def _close_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        d, self.dict = self.dict, None
        self.level = 0.0
        return d

    @objc.python_method
    def stop_live(self):
        ls, typer = self.live, self.typer
        self.live = None
        self.pending += 1
        t = time.time()

        def wait():
            full = ls.finish()
            if ls.failed:
                log(f"  live failed ({ls.failed}); falling back to normal transcription")
                if ls.seconds >= 0.4:
                    try:
                        full = transcribe(ls.audio())
                    except Exception as e:
                        log(f"ERROR: {e}")
                AppHelper.callAfter(self.finish_live, typer, ls, full, t, True)
            else:
                AppHelper.callAfter(self.finish_live, typer, ls, full, t, False)
        threading.Thread(target=wait, daemon=True).start()

    @objc.python_method
    def finish_live(self, typer, ls, full, t, fallback):
        self.pending = max(0, self.pending - 1)
        if fallback:
            typer.erase_all()
            text = cleanup(full) if full else ""
            if text:
                paste_text(text + " ")
        else:
            if typer.seg and not full:
                typer.commit(typer.seg)
            text = typer.typed()
            if text and CFG.get("cleanup", True) and CFG.get("polish_after_live", False):
                polished = cleanup(text)
                if polished and polished != text:
                    typer.erase_all()
                    paste_text(polished + " ")
                    text = polished
        if text:
            log(f"  [{ls.seconds:.1f}s audio, live → done {time.time() - t:.2f}s after release]\n"
                f"     heard: {full}\n     typed: {text}")
            record_stat(text, ls.seconds, getattr(ls, "app", ""))
            if CFG.get("save_recordings", True):
                threading.Thread(target=save_recording, args=(ls.audio(), full, text), daemon=True).start()
            self.update_today()
            if self.dash is not None and self.dash.isVisible():
                self.reload_dashboard()
        else:
            log("  (no speech)")
        if self.state == "processing" and not self.pending:
            self.state = "idle"

    @objc.python_method
    def stop(self):
        if self.live is not None:
            self._close_stream()
            play("Pop")
            self.state = "processing"
            self.stop_live()
            return
        d = self._close_stream()
        play("Pop")
        self.state = "processing"
        if d is None:
            self.state = "idle"
            return
        self.pending += 1
        futures = d.finish()
        t = time.time()

        def wait():
            parts = []
            for f in futures:
                try:
                    parts.append(f.result())
                except Exception as e:
                    log(f"ERROR: {e}")
            text = " ".join(p for p in parts if p).strip()
            d.already_typed = bool(getattr(d, "on_chunk", None))
            if text:
                raw = d.live_text()
                log(f"  [{d.seconds:.1f}s audio, {len(futures)} chunk(s) → "
                    f"{time.time() - t:.1f}s after release · {LAST_ENGINE[0]}]\n"
                    f"     heard: {raw}\n     typed: {text}")
                record_stat(text, d.seconds, d.app)
                if CFG.get("save_recordings", True):
                    save_recording(d.audio(0, d.n), raw, text)
            else:
                log("  (no speech)")
            AppHelper.callAfter(self.finished, text, d.already_typed)
        threading.Thread(target=wait, daemon=True).start()

    @objc.python_method
    def cancel(self, silent=False):
        d = self._close_stream()
        if d:
            d.cancelled = True
        if self.live is not None:
            self.live.close()
            self.live = None
            if self.typer:
                self.typer.erase_all()   # Esc removes what was typed live
                self.typer.active = False
        self.state = "processing" if self.pending else "idle"
        if not silent:
            play("Funk")
            log("✕ cancelled")

    @objc.python_method
    def finished(self, text, already_typed=False):
        self.pending = max(0, self.pending - 1)
        if text:
            if not already_typed:
                paste_text(text + " ")
            self.update_today()
            if self.dash is not None and self.dash.isVisible():
                self.reload_dashboard()
        if self.state == "processing" and not self.pending:
            self.state = "idle"

    # ---------------------------------------------------------- animation
    def tick_(self, timer):
        v = self.view
        st = self.state
        visible = st in ("recording", "locked", "processing")
        if visible and not self.shown:
            self.shown = True
            v.w, v.h = SIZES["idle"]
            self.panel.orderFrontRegardless()
        elif not visible and self.shown and v.w < SIZES["idle"][0] + 4:
            self.shown = False
            self.panel.orderOut_(None)
            v.set_text("")
        if not self.shown:
            return
        self.set_icon("waveform.circle.fill" if st in ("recording", "locked") else "waveform")
        tw, th = v.target_size(st)
        k = 0.22                                   # smooth spring-like easing
        v.w += (tw - v.w) * k
        v.h += (th - v.h) * k
        v.phase += 1 / 60.0
        # level eased: quick to rise, slow to fall (no flicker)
        lvl = self.level
        v.smooth += (lvl - v.smooth) * (0.30 if lvl > v.smooth else 0.10)
        for i, shape in enumerate(BAR_SHAPE):
            wave_ = 0.72 + 0.28 * math.sin(v.phase * 7.0 + i * 0.9)       # gentle travelling wave
            idle = 0.06 * (0.5 + 0.5 * math.sin(v.phase * 3.0 + i * 0.6))  # soft breathing when quiet
            target = max(idle, v.smooth * shape * wave_)
            v.bars[i] += (target - v.bars[i]) * 0.35
        v.setNeedsDisplay_(True)

    # ---------------------------------------------------------- main window (dashboard + settings)
    @objc.python_method
    def app_state(self):
        keys = load_keys()
        return {
            "type": "state", "platform": "mac", "version": VERSION,
            "stats": load_stats(),
            "keys": {k: (keys[k][-4:] if keys.get(k) else "") for k in ("elevenlabs", "anthropic")},
            "settings": {
                "hotkey": CFG.get("hotkey", "fn"), "live": CFG.get("live", True),
                "cleanup": CFG.get("cleanup", True), "polish_after_live": CFG.get("polish_after_live", False),
                "sounds": CFG.get("sounds", True), "login": LAUNCH_AGENT.exists(),
                "vocabulary": CFG.get("vocabulary") or [], "replacements": CFG.get("replacements") or {},
            },
            "hotkeys": [[k, HOTKEY_NAMES[k]] for k in ("fn", "alt_r", "cmd_r", "ctrl_r")],
            "permissions": {"accessibility": bool(self.trusted)},
        }

    @objc.python_method
    def send_web(self, obj):
        if self.dash is None:
            return
        js = "window.vnReceive && window.vnReceive(%s)" % json.dumps(obj, ensure_ascii=False)
        self.webview.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def reload_dashboard(self):
        self.send_web(self.app_state())

    @objc.python_method
    def handle_web(self, msg):
        kind = msg.get("type")
        if kind == "getState":
            self.send_web(self.app_state())
        elif kind == "saveKey":
            name, value = msg.get("name"), (msg.get("value") or "").strip()
            def work():
                ok, note = test_key(name, value)
                if ok:
                    save_keys(**{name: value})
                AppHelper.callAfter(self.send_web, {"type": "keyResult", "name": name, "ok": ok, "note": note})
                AppHelper.callAfter(self.reload_dashboard)
            threading.Thread(target=work, daemon=True).start()
        elif kind == "saveSettings":
            st = msg.get("settings") or {}
            login = st.pop("login", None)
            if "vocabulary" in st:
                st["vocabulary"] = [str(v).strip() for v in st["vocabulary"] if str(v).strip()]
            save_config(**st)
            if "hotkey" in st:
                self.keycode, self.keymask = HOTKEYS.get(st["hotkey"], HOTKEYS["fn"])
                self.keyname = HOTKEY_NAMES.get(st["hotkey"], st["hotkey"])
            if login is not None and login != LAUNCH_AGENT.exists():
                self.toggleLogin_(None)
            if "cleanup" in st:
                self.clean_mi.setState_(1 if CFG["cleanup"] else 0)
            self.send_web({"type": "saved"})
        elif kind == "openURL":
            from Foundation import NSURL
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(msg.get("url", "")))
        elif kind == "openPermissions":
            from Foundation import NSURL
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})

    def openDashboard_(self, s):
        self.show_window()

    @objc.python_method
    def show_window(self):
        from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
        app = NSApplication.sharedApplication()
        if self.dash is None:
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                     | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
            w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 1040, 720), style, NSBackingStoreBuffered, False)
            w.setTitle_(APP_NAME)
            w.setReleasedWhenClosed_(False)
            w.setMinSize_((720, 520))
            w.setDelegate_(self)
            cfg = WKWebViewConfiguration.alloc().init()
            ucc = WKUserContentController.alloc().init()
            self.bridge = WebBridge.alloc().init()
            self.bridge.ctrl = self
            ucc.addScriptMessageHandler_name_(self.bridge, "vn")
            cfg.setUserContentController_(ucc)
            wv = WKWebView.alloc().initWithFrame_configuration_(w.contentView().bounds(), cfg)
            wv.setAutoresizingMask_(18)        # width + height sizable
            w.contentView().addSubview_(wv)
            w.center()
            self.dash, self.webview = w, wv
            html = DASHBOARD_HTML.read_text(encoding="utf-8").replace(
                "/*__STATE__*/null", json.dumps(self.app_state(), ensure_ascii=False))
            wv.loadHTMLString_baseURL_(html, None)
        else:
            self.reload_dashboard()
        app.setActivationPolicy_(0)          # regular app with a Dock icon while the window is open
        app.activateIgnoringOtherApps_(True)
        self.dash.makeKeyAndOrderFront_(None)

    def windowWillClose_(self, note):
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
        self.show_window()                    # clicking the app again opens its window
        return True

    def showFromOtherLaunch_(self, note):
        self.show_window()

    # ---------------------------------------------------------- menu
    def openSettings_(self, s):
        self.show_window()
        self.send_web({"type": "goto", "page": "settings"})

    def toggleCleanup_(self, s):
        save_config(cleanup=not CFG.get("cleanup", True))
        self.clean_mi.setState_(1 if CFG["cleanup"] else 0)

    def toggleLogin_(self, s):
        if LAUNCH_AGENT.exists():
            LAUNCH_AGENT.unlink()
            self.login_mi.setState_(0)
            return
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--background"]
        elif APP_EXEC.exists():
            args = [str(APP_EXEC), "--background"]
        else:
            args = [sys.executable, str(Path(__file__).resolve()), "--background"]
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_bytes(plistlib.dumps(
            {"Label": "com.voicenoah.app", "ProgramArguments": args, "RunAtLoad": True,
             "ProcessType": "Interactive"}))
        self.login_mi.setState_(1)

    def quit_(self, s):
        NSApplication.sharedApplication().terminate_(None)


class WebBridge(NSObject):
    """Receives messages from the window's page (window.webkit.messageHandlers.vn)."""

    def userContentController_didReceiveScriptMessage_(self, ucc, message):
        body = message.body()
        try:
            msg = json.loads(body) if isinstance(body, str) else dict(body)
        except Exception:
            return
        self.ctrl.handle_web(msg)


# =================================================================== entry
def main():
    from Foundation import NSDistributedNotificationCenter
    import fcntl
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # only one copy at a time
    except OSError:
        # already running: ask the running copy to show its window, then leave
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
            "com.voicenoah.show", None, None, True)
        log(f"{APP_NAME} is already running: showing its window.")
        return
    globals()["_LOCK"] = lock
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    ctrl = Controller.alloc().init()
    app.setDelegate_(ctrl)
    ctrl.setup()
    NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        ctrl, "showFromOtherLaunch:", "com.voicenoah.show", None)
    if "--background" not in sys.argv:
        AppHelper.callLater(0.3, ctrl.show_window)
    globals()["_CTRL"] = ctrl   # keep a reference alive
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
