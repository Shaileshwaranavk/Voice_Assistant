"""
Veera - a desktop pet (an eagle mascot in a suit) that is also your voice assistant (Windows).

* The eagle hovers in the bottom-right corner of your screen, gently bobbing in place.
* Say "Hey Veera ..." -> he perks up, says "Yeah, sure!" and starts listening.
* Ask for things (date, time, play, search, screenshot, edit, read, ...).
* Say "goodbye" / "stop" / "that's all" -> he goes back to idle hovering.
* Click him to wake / sleep manually (lie down <-> fly), right-click for the Quit menu.
* Hover over him while he's lying on the taskbar and he'll slide aside so you can
  still click whatever he's sitting on top of.
* When some other app goes fullscreen (a video, a game, a fullscreen website),
  he hides automatically - the voice assistant keeps listening in the background.

Install:
    pip install -r requirements.txt

Run:
    python veera.py
"""

import ctypes
import datetime
import difflib
import json
import math
import os
import queue
import re
import threading
import time
import traceback
import tkinter as tk
from ctypes import wintypes

import sys

try:
    import pyaudio  # noqa: F401
except ImportError:
    # PyAudio has no wheel for newer Pythons; PyAudioWPatch is a drop-in fork.
    import pyaudiowpatch
    sys.modules["pyaudio"] = pyaudiowpatch

import pyautogui
import system_control
import pywhatkit
import speech_recognition as sr
import wikipedia
from brain import Brain
from greeting import Greeter
from PIL import Image, ImageTk

try:                       # accurate offline speech recognition (Whisper) - the main recogniser
    import numpy as np
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

try:                       # offline speech recognition - fast, works with no internet
    import json
    from vosk import KaldiRecognizer, Model as VoskModel, SetLogLevel
    SetLogLevel(-1)
except ImportError:
    VoskModel = None

try:
    from pygoogle_image import image as google_image
except ImportError:  # optional feature
    google_image = None


def load_env():
    """Read KEY=value lines from a .env file next to this script into os.environ
    (real environment variables win)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()


# --------------------------------------------------------------------------
# Look & feel settings
# --------------------------------------------------------------------------
TRANSPARENT = "#010101"      # this colour becomes see-through (and click-through)

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
IDLE_FRAME_FILES = ["eagle_idle/idle_1.png", "eagle_idle/idle_2.png", "eagle_idle/idle_3.png"]
LIE_FRAME_FILE = "lying-down/lie.png"     # used while perched (lying) on the taskbar
LIE_DISPLAY_W = 160                       # on-screen width of the reclining pose (wide image)
KEYFRAME_ORDER = [0]         # ping-pong: 1-2-3-2-1-2-3-2-... (no jump-cut at the loop point)
HOVER_CYCLE_MS = 1800                 # one full bob+wing cycle - frame swaps sync to this, not their own clock

EAGLE_DISPLAY_W = 150                 # on-screen width of the mascot (source art is ~1231px wide)
ALPHA_THRESHOLD = 120                 # binarize edge alpha - kills the colour-key halo (see _load_frames)
BOB_AMPLITUDE = 8                     # px of vertical hover motion
DRIFT_AMPLITUDE = 3                   # px of horizontal drift - traces a small hover ellipse, not a straight line
CORNER_MARGIN_X = 20                  # gap from the right edge of the screen
CORNER_MARGIN_TOP = 0                # gap from the top edge of the screen, while flying
BUBBLE_MAX_W = 260                    # width budget for the speech bubble, to the left of him

FALLBACK_TASKBAR_H = 48               # used only if the real taskbar height can't be read
PEEK_DELAY_S = 1.0                    # pointer must rest on him this long before he slides aside
PEEK_AWAY_DX = 300                    # how far he slides aside when you hover him while perched
POS_EASE = 0.22                       # how quickly the window eases toward a new resting spot
TASKBAR_POLL_TICKS = 150              # re-check the real taskbar height every ~2.4s (16ms * 150)
FULLSCREEN_POLL_TICKS = 15            # re-check the foreground window every ~240ms

FILE_NAME = "example.txt"
BARGE_IN = os.environ.get("BARGE_IN", "on").strip().lower() not in ("off", "no", "false", "0")   # talk over him to stop him
WHISPER_MODEL = os.environ.get("VEERA_WHISPER_MODEL", "base.en")       # base.en = fast (about 1s); small.en = about 3x slower, not clearly better here


def get_taskbar_height(screen_h):
    """Real height of the Windows taskbar (assuming it's docked at the bottom,
    the default/common case), via the actual work-area the OS reports - not a
    guessed constant. Falls back to FALLBACK_TASKBAR_H if the API call fails,
    or if the taskbar is auto-hidden/docked elsewhere (work area == full screen)."""
    try:
        SPI_GETWORKAREA = 0x0030
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            h = screen_h - rect.bottom
            if h > 0:
                return h
    except Exception:
        pass
    return FALLBACK_TASKBAR_H


_DESKTOP_CLASSES = {"Progman", "WorkerW"}   # the desktop/wallpaper host - not a "fullscreen app"


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def is_fullscreen_app_active(own_hwnd):
    """True when the foreground window exactly covers its monitor - the same
    heuristic Windows itself uses to decide whether to auto-hide the taskbar
    for a fullscreen video/game/website. Ignores our own window and the desktop."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == own_hwnd:
            return False

        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in _DESKTOP_CLASSES:
            return False

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        MONITOR_DEFAULTTONEAREST = 2
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False

        mon = info.rcMonitor
        return (rect.left <= mon.left and rect.top <= mon.top
                and rect.right >= mon.right and rect.bottom >= mon.bottom)
    except Exception:
        return False

# "hey veera", "hi vera", "veera" ...
WAKE_RE = re.compile(
    r"\b(?:(?:hey|hi|hello|ok|okay)\s+)?(?:veera|veerah|veeraa|vera|veer|weera|vira|viera|beera|bheera|veda)\b"
)


def is_wake(text):
    """'Hey Veera' - exact variants, or any word that merely SOUNDS close to 'veera'
    (offline recognition rarely spells a made-up name right)."""
    if not text:
        return False
    if WAKE_RE.search(text):
        return True
    return any(difflib.SequenceMatcher(None, w, t).ratio() >= 0.75
               for w in re.findall(r"[a-z]+", text) for t in ("veera",))


# --------------------------------------------------------------------------
# Shared state (voice thread writes, GUI thread reads)
# --------------------------------------------------------------------------
class Speaker(threading.Thread):
    """Owns the text-to-speech voice on its own thread, so any thread (the GUI click
    handler included) can make him talk instantly, without waiting for the
    microphone thread. Uses Windows SAPI directly: no per-line engine start-up."""

    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.q = queue.Queue()
        self.seq = 0                 # id of the newest line
        self.floor = 0               # lines with an id below this are cancelled

    def say(self, text, wait=False, interrupt=False):
        """interrupt=True cuts off whatever is being said (or queued) and speaks this now."""
        done = threading.Event()
        self.seq += 1
        if interrupt:
            self.floor = self.seq
        self.q.put((self.seq, text, done))
        if wait:
            done.wait()
        return done

    def stop(self):
        """Cut off whatever is being said right now (and anything queued)."""
        self.seq += 1
        self.floor = self.seq

    def run(self):
        voice = None
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            voice.Rate = 2                              # ~185 wpm
        except Exception as e:
            print("SAPI unavailable, using pyttsx3:", e)
        while True:
            n, text, done = self.q.get()
            if n < self.floor:                          # superseded before it started
                done.set()
                continue
            self.state.speaking = True
            try:
                if voice is not None:
                    voice.Speak(text, 1)                # async, so we can cut it off
                    while not voice.WaitUntilDone(30):
                        if n < self.floor:
                            voice.Speak("", 3)          # async + purge = stop talking now
                            break
                else:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 185)
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                print("Text-to-speech error:", e)
            finally:
                if self.q.empty():
                    self.state.speaking = False
                done.set()


class State:
    def __init__(self):
        self.speaker = Speaker(self)
        self.speaker.start()
        self.awake = False           # True = flying / in conversation; False = perched on the taskbar
        self.listening = False       # True while the mic is open for a command
        self.speaking = False        # True while text-to-speech is talking
        self.hidden = False          # hide the pet (used for screenshots)
        self.quit = False
        self.toggle_request = False  # set by double-click
        self.bubble = ""
        self.bubble_until = 0.0

    def set_bubble(self, text, seconds):
        if len(text) > 240:
            text = text[:237].rstrip() + "..."
        self.bubble = text
        self.bubble_until = time.time() + seconds


# --------------------------------------------------------------------------
# The Veera desktop pet (tkinter, drawn with shapes - no image files)
# --------------------------------------------------------------------------
class VeeraPet:
    """Loads the eagle sprite frames once, then hovers + animates them in place."""

    def __init__(self, root, state):
        self.root = root
        self.state = state

        self.sw = root.winfo_screenwidth()
        self.sh = root.winfo_screenheight()

        self.frames = self._load_frames()
        self.eagle_w, self.eagle_h = self.frames[0].width(), self.frames[0].height()
        self.lie_frame, self.lie_w, self.lie_h = self._load_lie_frame()

        # window hugs the sprite: just enough room for the hover bob/drift
        self.win_w = max(self.eagle_w, self.lie_w) + 2 * DRIFT_AMPLITUDE + 12
        self.win_h = max(self.eagle_h, self.lie_h) + 2 * BOB_AMPLITUDE + 12

        root.overrideredirect(True)                # no title bar / borders
        root.attributes("-topmost", True)          # always on top
        root.attributes("-transparentcolor", TRANSPARENT)  # Windows only
        root.config(bg=TRANSPARENT)

        # anchor point for the eagle's centre inside the canvas (window-relative, fixed -
        # only the WINDOW's screen position moves between perched/flying, this stays put)
        self.ax = self.win_w // 2
        self.ay = self.win_h // 2

        self.peek_away = False
        self._hover_since = None
        self.taskbar_h = get_taskbar_height(self.sh)

        # current (eased) and target window top-left, in screen coords
        self.cur_x, self.cur_y = self._target_pos()
        self._applied_x, self._applied_y = None, None
        root.geometry(f"{self.win_w}x{self.win_h}+{int(self.cur_x)}+{int(self.cur_y)}")

        self.canvas = tk.Canvas(root, width=self.win_w, height=self.win_h,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._on_click)
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Quit Veera",
                         command=lambda: setattr(state, "quit", True))
        self.canvas.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        self.own_hwnd = root.winfo_id()
        self.fullscreen_active = False

        self.t = 0.0
        self.frame = 0
        self._hidden = False
        self.tick()

    def _on_click(self, _event):
        # flip the pose right away on the GUI thread; the voice thread may be blocked
        # listening for seconds, so it only gets told to say the matching line
        st = self.state
        st.awake = not st.awake
        st.toggle_request = True
        line = "Yeah, sure! What do you need?" if st.awake else "Okay, swinging back up!"
        print("Veera:", line)
        st.speaker.say(line, interrupt=True)   # cuts off whatever he was saying

    # ---- positioning -----------------------------------------------------
    def _target_pos(self):
        """Where the window's top-left should be for the current mode. Only x/y
        move between perched (lying on the taskbar, sliding aside on hover) and
        flying (top-right hover) - the window size and the eagle's anchor inside
        it never change."""
        rest_x, y = self._rest_pos()

        # like Chrome's status bubble: while the pointer is over his RESTING spot he
        # stays slid aside (even though the pointer is no longer over the window
        # itself), and only comes back once the pointer leaves that spot
        px, py = self.root.winfo_pointerxy()
        over = rest_x <= px < rest_x + self.win_w and y <= py < y + self.win_h
        if not over:
            self._hover_since = None
            self.peek_away = False
        elif self._hover_since is None:
            self._hover_since = time.time()              # pointer just arrived
        elif time.time() - self._hover_since >= PEEK_DELAY_S:
            self.peek_away = True                        # only after resting there a while
        x = rest_x - PEEK_AWAY_DX if self.peek_away else rest_x
        return (x, y)

    def _rest_pos(self):
        x = self.sw - self.win_w - CORNER_MARGIN_X
        if self.state.awake:                                            # flying, top-right
            return (x, CORNER_MARGIN_TOP)
        return (x, self.sh - self.taskbar_h - self.ay - self.lie_h // 2)

    # ---- asset loading -----------------------------------------------------
    def _bake(self, rel_path, display_w):
        """Resize a PNG to display_w, then bake its alpha onto TRANSPARENT so
        Windows' colour-key transparency trick treats the see-through pixels
        correctly. Returns (PhotoImage, width, height).

        Note: deliberately NOT cross-dissolving between frames with Image.blend
        - blending raw RGBA pixels before compositing lets the leftover (often
        black) RGB data under fully-transparent pixels leak into the visible
        image, producing a black flash/glow at the edges during every
        transition. Plain frame swaps, held long enough, read as calm idle
        motion without that artifact."""
        bg_rgb = tuple(int(TRANSPARENT[i:i + 2], 16) for i in (1, 3, 5))
        img = Image.open(os.path.join(ASSET_DIR, rel_path)).convert("RGBA")
        w = display_w
        h = round(w * img.height / img.width)
        img = img.resize((w, h), Image.LANCZOS)

        # binarize alpha: the colour-key trick can only key out pixels that are
        # EXACTLY the key colour. Antialiased edge pixels blend PART-way toward
        # it and stay just off-key, which renders as a faint grey/box halo around
        # the whole sprite. Snapping alpha to fully-opaque-or-fully-transparent
        # removes that in-between band entirely (edges get a touch more jagged,
        # but no more halo).
        r, g, b, a = img.split()
        a = a.point(lambda v: 255 if v >= ALPHA_THRESHOLD else 0)
        img = Image.merge("RGBA", (r, g, b, a))

        backing = Image.new("RGBA", img.size, bg_rgb + (255,))
        composited = Image.alpha_composite(backing, img).convert("RGB")
        return ImageTk.PhotoImage(composited), w, h

    def _load_frames(self):
        baked = {}
        for idx in set(KEYFRAME_ORDER):
            baked[idx], _, _ = self._bake(IDLE_FRAME_FILES[idx], EAGLE_DISPLAY_W)
        return [baked[idx] for idx in KEYFRAME_ORDER]

    def _load_lie_frame(self):
        photo, w, h = self._bake(LIE_FRAME_FILE, LIE_DISPLAY_W)
        return photo, w, h

    # ---- main loop -------------------------------------------------------
    def tick(self):
        st = self.state
        if st.quit:
            self.root.destroy()
            return

        # hide when a screenshot is being taken (st.hidden) OR some other app has
        # gone fullscreen (self.fullscreen_active) - voice listening is untouched,
        # it lives entirely in the Assistant thread and doesn't look at this at all
        effective_hidden = st.hidden or self.fullscreen_active
        if effective_hidden != self._hidden:
            self._hidden = effective_hidden
            if effective_hidden:
                self.root.withdraw()
            else:
                self.root.deiconify()
                self.root.attributes("-topmost", True)

        self.frame += 1
        if self.frame % 300 == 0:
            self.root.attributes("-topmost", True)
        if self.frame % TASKBAR_POLL_TICKS == 0:
            self.taskbar_h = get_taskbar_height(self.sh)   # picks up a resized/moved taskbar
        if self.frame % FULLSCREEN_POLL_TICKS == 0:
            self.fullscreen_active = is_fullscreen_app_active(self.own_hwnd)

        self.t += 0.033

        # ease the window toward wherever the current mode (perched/flying, plus
        # hover-away) wants it, and only touch the actual OS window when it's
        # moved enough to matter
        tx, ty = self._target_pos()
        self.cur_x += (tx - self.cur_x) * POS_EASE
        self.cur_y += (ty - self.cur_y) * POS_EASE
        rx, ry = round(self.cur_x), round(self.cur_y)
        if (rx, ry) != (self._applied_x, self._applied_y):
            self.root.geometry(f"+{rx}+{ry}")
            self._applied_x, self._applied_y = rx, ry

        self.canvas.delete("all")
        self.draw()
        self.root.after(16, self.tick)

    # ---- drawing ---------------------------------------------------------
    def draw(self):
        c, st, t = self.canvas, self.state, self.t

        if st.awake:
            # one shared phase drives both the body motion and the wing-frame swap, so
            # the pose always changes at a consistent point in the rise/fall - not on
            # its own clock - which is what makes it read as one coordinated hover
            # instead of two unrelated animations layered on top of each other.
            cycle_ms = HOVER_CYCLE_MS * (0.85 if st.speaking else 1.0)   # livelier while talking
            phase = (self.frame * 16 % cycle_ms) / cycle_ms * (2 * math.pi)
            bob = math.sin(phase) * BOB_AMPLITUDE
            drift = math.cos(phase) * DRIFT_AMPLITUDE      # small elliptical hover path
            quarter = int(phase / (math.pi / 2)) % len(KEYFRAME_ORDER)
            frame_img = self.frames[quarter]
        else:
            # perched: lying down resting on the taskbar (slides aside on hover)
            bob = drift = 0.0
            frame_img = self.lie_frame

        cx, cy = self.ax + drift, self.ay + bob

        c.create_image(cx, cy, image=frame_img)


# --------------------------------------------------------------------------
# Your original helper functions
# --------------------------------------------------------------------------
def write_to_file(content):
    with open(FILE_NAME, "w") as file:
        file.write(content)


def read_from_file():
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r") as file:
            return file.read()
    return "The file does not exist."


def download_images(query):
    google_image.download(keywords=query, limit=30)


def shutdown():
    os.system("shutdown /s /t 10")     # 10 s grace period; run "shutdown /a" to abort


def get_date():
    return datetime.datetime.now().strftime("%A, %B %d, %Y")


def get_time():
    return datetime.datetime.now().strftime("%I:%M %p")


def has(text, *words):
    """True if any of the words appears as a whole word in text."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


# --------------------------------------------------------------------------
# Command table: every command is a function with the regex(es) that trigger it.
# They are tried in the order they are written below; the first match wins, and a
# command can `return False` to say "not mine after all" so the next one gets a turn.
# --------------------------------------------------------------------------
_COMMANDS = []


def command(*patterns):
    """@command(r"\\bplay\\s+(?P<q>.+)") on a method registers it. (?P<q>...) is the argument."""
    regs = [re.compile(p) for p in patterns]

    def register(fn):
        _COMMANDS.append((regs, fn))
        return fn
    return register


def reply(pattern, template, max_words=None):
    """One-liner for commands that only speak. {date} {time} {q} are filled in.
    max_words: only fires on short sentences - 'what time is the meeting' is for the brain, not a clock."""
    @command(pattern)
    def say(self, m, text, source):
        if max_words and len(text.split()) > max_words:
            return False
        q = (m.groupdict().get("q") or "").strip()
        self.speak(template.format(date=get_date(), time=get_time(), q=q))


# --------------------------------------------------------------------------
# Voice assistant (runs in a background thread)
# --------------------------------------------------------------------------
class Assistant:
    def __init__(self, state):
        self.state = state
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.75         # wait a little longer so a sentence isn't cut in half
        self.recognizer.non_speaking_duration = 0.4
        self.recognizer.phrase_threshold = 0.2
        self.recognizer.operation_timeout = 4          # don't hang on a weak connection
        self._engine = None
        self.vosk = None
        self._cmd_rec, self._grammar_at = None, 0.0
        self.whisper, self.prompt, self.last_conf = None, "", 0.0
        self.names, self.apps = [], []
        self._said = []
        self.ready = False                              # True once the microphone is calibrated
        self._source = None                             # the open microphone (set in _run)
        self._voice_thread = None
        self._barge_pre = b""
        self.brain = Brain(self)                        # understands requests and runs them (all local)
        model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "models", "vosk-model-small-en-us-0.15")
        if VoskModel is not None and os.path.isdir(model_dir):
            try:
                self.vosk = VoskModel(model_dir)
            except Exception as e:
                print("Offline model failed to load:", e)
        else:
            print("Offline recognition off (pip install vosk + models/vosk-model-small-en-us-0.15).")
        self.alternatives = []                         # every transcript Google guessed, best first

    # ---- speech in / out -------------------------------------------------
    def speak(self, text):
        self._said.append(text)
        print("Veera:", text)
        done = self.state.speaker.say(text)
        if self._source is not None and BARGE_IN and threading.current_thread() is self._voice_thread:
            self._wait_or_interrupt(done)
        else:
            done.wait()

    def _wait_or_interrupt(self, done):
        """Wait for him to finish talking - but if YOU start talking, stop him at once and keep your words.
        His own voice is measured during the first moments (nobody has had time to answer yet); you
        interrupt when the microphone gets clearly louder than that, for a moment."""
        src, rec = self._source, self.recognizer
        rate, width, chunk = src.SAMPLE_RATE, src.SAMPLE_WIDTH, src.CHUNK
        recent, base, loud, t0 = [], 0.0, 0, time.time()
        try:
            while not done.is_set():
                buf = src.stream.read(chunk)
                level = float(np.sqrt(np.mean(np.frombuffer(buf, dtype=np.int16).astype(np.float32) ** 2))) if buf else 0.0
                recent.append(buf)
                recent[:] = recent[-10:]
                if time.time() - t0 < 0.7:                 # learn how loud his own voice is on these speakers
                    base = max(base, level)
                    continue
                if level > max(base * 2.4, rec.energy_threshold * 1.8, 700):
                    loud += 1
                else:
                    loud = 0
                if loud >= 3:                               # ~0.2 s of clearly-louder sound
                    self.state.speaker.stop()
                    self._barge_pre = b"".join(recent[-8:])  # keep the start of what you said
                    print("[you interrupted]")
                    done.wait(1.0)
                    return
        except Exception as e:                              # the mic hiccuped: just fall back to waiting
            print("[barge-in off:", e, "]")
            done.wait()

    def listen(self, source, timeout=8, limit=15, wake=False):
        # listen in short slices so a click (toggle_request) is noticed within ~0.5s
        # instead of waiting out the whole timeout
        deadline = time.time() + timeout
        audio = None
        while time.time() < deadline:
            if self.state.toggle_request:
                return None
            try:
                audio = self.recognizer.listen(source, timeout=0.5, phrase_time_limit=limit)
                break
            except sr.WaitTimeoutError:
                continue
        if audio is None:
            return None
        if self._barge_pre:                                 # you talked over him: don't lose your first words
            audio = sr.AudioData(self._barge_pre + audio.frame_data, audio.sample_rate, audio.sample_width)
            self._barge_pre = b""
        return self.transcribe(audio, wake)

    def _vosk_text(self, audio, commands=False):
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        if commands:
            # open dictation is shaky offline, so first try the known commands only
            now = time.time()
            if self._cmd_rec is None or now - self._grammar_at > 600:   # pick up new devices / networks
                grammar = json.dumps(system_control.offline_phrases() + ["[unk]"])
                err = os.dup(2)                          # Vosk prints one warning per unknown word - hide them
                null = os.open(os.devnull, os.O_WRONLY)
                os.dup2(null, 2)
                try:
                    self._cmd_rec = KaldiRecognizer(self.vosk, 16000, grammar)
                finally:
                    os.dup2(err, 2)
                    os.close(err)
                    os.close(null)
                self._grammar_at = now
            rec = self._cmd_rec                          # built once, reused (rebuilding cost a second each time)
            rec.Reset()
            rec.AcceptWaveform(raw)
            text = json.loads(rec.FinalResult()).get("text", "").strip()
            if text and "[unk]" not in text:
                return text
        rec = KaldiRecognizer(self.vosk, 16000)
        rec.AcceptWaveform(raw)
        return json.loads(rec.FinalResult()).get("text", "")

    # ---- Whisper: the main recogniser (offline, accurate, no internet round-trip) ----
    def _load_whisper(self):
        if WhisperModel is None:
            print("Whisper not installed (pip install faster-whisper) - using Google/Vosk instead.")
            return
        print(f"Loading speech model {WHISPER_MODEL} (the first run downloads it)...")
        try:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        except Exception as e:
            print("Whisper failed to load:", e)
            return
        # Whisper spells things better when it has seen them: hand it our vocabulary,
        # including the real device and network names on this PC
        names = []
        try:
            names += list(system_control._bt_devices()) + system_control._wifi_profiles()
        except Exception:
            pass
        self.names = [n for n in names if n]
        threading.Thread(target=self._learn_apps, daemon=True).start()
        self.prompt = ("Veera. Hey Veera. Turn on Wi-Fi. Turn off Bluetooth. Reduce the volume by "
                       "twenty percent. Connect Bluetooth to " + ", ".join(names[:12]) +
                       ". Open Chrome, Brave, Spotify, WhatsApp, Gmail, Word, Excel, Downloads. Summarize it. "
                       "Create a folder called Projects. Find the PDF. Read the document. Minimize everything. "
                       "Switch to Word. Close this tab. Open File Explorer. Create a Python file. Close Claude Code. "
                       "Connect to the Wi-Fi. Play, search, screenshot, date, time.")

    def _learn_apps(self):
        """Start-menu app names (Claude Code, Spotify ...) so what you say can be matched to them."""
        try:
            from agent_tools.apps import start_apps
            self.apps = [n for n, _ in start_apps()]
        except Exception:
            self.apps = []

    def snap_names(self, text):
        """Whisper often spells a name almost right ('cloud code', 'vijaya lakshmi'). Replace a word or two
        that sound like a name this PC actually has (Wi-Fi network, Bluetooth device, app, window)."""
        known = list(self.names) + list(self.apps)
        try:
            from agent_tools.desktop import all_windows
            known += [p.replace(".exe", "") for _, _, p in all_windows() if p]
        except Exception:
            pass
        flat = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in known if len(re.sub(r"[^a-z0-9]", "", k.lower())) >= 5}
        words = text.split()
        i, out = 0, []
        while i < len(words):
            done = False
            for n in (1, 2, 3):                              # shortest match first, so 'to' isn't swallowed
                if i + n > len(words):
                    continue
                gram = " ".join(words[i:i + n])
                key = re.sub(r"[^a-z0-9]", "", gram.lower())
                if len(key) < 5:
                    continue
                if key in flat:
                    break                                          # already exact
                best = difflib.get_close_matches(key, list(flat), n=1, cutoff=0.82)
                if best:
                    out.append(flat[best[0]].lower())
                    i += n
                    done = True
                    break
            if not done:
                out.append(words[i])
                i += 1
        fixed = " ".join(out)
        if fixed != text:
            print(f"(heard '{text}' -> using '{fixed}')")
        return fixed

    def _whisper_text(self, audio, wake=False):
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(
            pcm, language="en", beam_size=1 if wake else 5, temperature=0.0,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False, initial_prompt=self.prompt,
            hotwords=" ".join(self.names[:10]) or None)
        segs = list(segments)
        if not segs:
            return None
        text = " ".join(sg.text for sg in segs).strip().lower().strip(" .,!?")
        conf = sum(sg.avg_logprob for sg in segs) / len(segs)        # 0 = sure, more negative = guessing
        if not text or max(sg.no_speech_prob for sg in segs) > 0.6 or conf < -1.3:
            return None                                              # noise / Whisper's made-up filler
        self.last_conf = conf
        if wake:
            if not is_wake(text):
                print("(heard, not the wake word:", text + ")")
                return None
            self.alternatives = [text]
            print("You:", text)
            return text
        text = self.snap_names(text)
        self.alternatives = [text]
        print(f"You: {text}   (confidence {conf:.2f})")
        return text

    def _confirm(self, text, source):
        """For commands that change the system: if the recogniser wasn't sure what it heard,
        read it back and wait for a yes before running anything."""
        if self.whisper is None or self.last_conf > -0.5:
            return True
        answer = self.ask(source, f"Did you say: {text}? Say yes to confirm.", timeout=5, limit=3)
        return bool(answer and has(answer, "yes", "yeah", "yep", "correct", "sure", "confirm"))

    def transcribe(self, audio, wake=False):
        if self.whisper:
            return self._whisper_text(audio, wake)
        self.alternatives = []
        if wake and self.vosk:
            # offline first: instant, and no network round-trip just to hear "hey veera"
            text = self._vosk_text(audio)
            if not text:
                return None
            if is_wake(text):
                self.alternatives = [text]
                print("You:", text)
                return text
            print("(heard, not the wake word:", text + ")")
            # no match offline - ask Google too, it spells names better; if there is no
            # internet this just fails fast and we carry on
        try:
            result = self.recognizer.recognize_google(audio, show_all=True)
            alts = [a["transcript"].lower() for a in (result.get("alternative", []) if result else [])
                    if a.get("transcript")]
            if alts:
                self.alternatives = alts
                print("You:", alts[0])
                return alts[0]
            return None
        except sr.UnknownValueError:
            return None
        except Exception as e:                       # no / weak internet -> offline fallback
            print("Online recognition failed:", e)
            if self.vosk:
                text = self._vosk_text(audio, commands=True)
                if text:
                    self.alternatives = [text]
                    print("You (offline):", text)
                    return text
            return None

    def ask(self, source, prompt, timeout=8, limit=15):
        """Say a question and return the spoken answer (or None)."""
        self.speak(prompt)
        self.state.listening = True
        try:
            return self.listen(source, timeout, limit)
        finally:
            self.state.listening = False

    # ---- swing down / up -------------------------------------------------
    def wake_up(self, has_command):
        self.state.awake = True                           # swing down while he talks
        self.speak("Yeah, sure!" if has_command else "Yeah, sure! What do you need?")

    def go_to_sleep(self, message):
        self.state.awake = False                          # swing back up while talking
        self.speak(message)

    # ---- commands: add a new one by adding a decorated method ----------------
    def handle(self, text, source):
        """Run what was said. The brain (brain.py) understands it first; the built-in commands below are the
        fallback, so everything that worked before still works."""
        out = self.brain.run(text, source, legacy=self._dispatch)
        if out is None:
            self.speak("Sorry, I didn't understand that.")
        elif out:
            self.speak(out)

    def _dispatch(self, text, source):
        """Run the first matching command. False = nothing handled it."""
        for regs, fn in _COMMANDS:
            m = next((r.search(text) for r in regs if r.search(text)), None)
            if m and fn(self, m, text, source) is not False:
                return True
        return False

    @command(r"\b(quit|exit)\b")
    def _quit(self, m, text, source):
        self.speak("See you later!")
        self.state.quit = True

    @command(r"\bshutdown\b", r"shut down")
    def _shutdown(self, m, text, source):
        answer = self.ask(source, "Are you sure you want to shut down the computer? "
                                  "Say yes to confirm.", timeout=6, limit=4)
        if answer and has(answer, "yes", "yeah", "sure", "confirm"):
            self.speak("Okay, shutting down in ten seconds.")
            shutdown()
            self.state.quit = True
        else:
            self.speak("Okay, cancelled.")

    @command(r".")                                    # volume / Wi-Fi / Bluetooth (see system_control.py)
    def _system(self, m, text, source):
        reply_text = system_control.try_handle(text, self.speak, confirm=lambda: self._confirm(text, source))
        if not reply_text:
            return False
        self.speak(reply_text)

    @command(r"\b(goodbye|bye|stop)\b", r"go back", r"that's all")
    def _sleep(self, m, text, source):
        self.go_to_sleep("Goodbye! Call me if you need me.")

    @command(r"\bdownload\s+(?:the\s+|some\s+|a few\s+)?(?:images?|pictures?|photos?)(?:\s+(?:of|for|about)\s+(?P<q>.+))?\s*$")
    def _images(self, m, text, source):
        query = (m.groupdict().get("q") or "").strip()
        if not query:                                       # "download the images" - ask what of
            query = (self.ask(source, "Images of what?", timeout=7, limit=5) or "").strip()
            if not query:
                self.speak("I didn't hear what to download, so I didn't download anything.")
                return
        if google_image is None:
            self.speak("The pygoogle image package is not installed.")
            return
        self.speak(f"Downloading images of {query}")
        try:
            download_images(query)
            self.speak("Done! The images are in your images folder.")
        except Exception as e:
            print("Image download error:", e)
            self.speak("Sorry, the image download failed.")

    @command(r"\bscreenshot\b")
    def _screenshot(self, m, text, source):
        st = self.state
        self.speak("Say cheese!")
        st.hidden = True                     # hide Veera so he isn't in the picture
        time.sleep(0.7)
        path = os.path.abspath(datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png"))
        pyautogui.screenshot(path)
        st.hidden = False
        self.speak("Got it! Screenshot saved.")
        try:
            os.startfile(path)
        except Exception:
            pass

    @command(r"^(?!.*\b(spotify|playlist|vlc|workout)\b)(?!\W*(?:(?:can|could|will) you |please )*play (?:the |some )?(?:music|song|songs|it)?\W*$).*?\bplay\s+(?P<q>.+)")
    def _play(self, m, text, source):
        query = m.group("q").strip()
        self.speak(f"Searching for {query} on YouTube...")
        pywhatkit.playonyt(query)

    @command(r"^(?!.*\b(web|google|internet|online|browser|youtube|bing|error|gmail|email|mail|chat|whatsapp)\b).*?\bsearch(?:\s+(?:for|about|up))?\s+(?P<q>.+)")
    def _search(self, m, text, source):
        query = m.group("q").strip()
        self.speak(f"Looking up {query}...")
        result = self.wiki(query)
        self.speak(f"According to Wikipedia: {result}" if result
                   else "Sorry, I couldn't find anything about that.")

    @command(r"^\W*(?:(?:can|could|will) you |please )*(?:edit|write)(?: (?:the|a|my))?(?: (?:file|note|notes))?\W*$")
    def _write(self, m, text, source):
        content = self.ask(source, "What would you like to write in the file?")
        if content:
            write_to_file(content)
            self.speak("Content has been written to the file.")
        else:
            self.speak("Sorry, I couldn't understand you.")

    @command(r"^\W*(?:(?:can|could|will) you |please )*read(?: (?:the|my))?(?: (?:file|note|notes))?(?: out)?\W*$")
    def _read(self, m, text, source):
        self.speak("Reading content from the file.")
        self.speak(read_from_file())

    reply(r"\bdate\b", "Today is {date}", max_words=5)
    reply(r"\btime\b", "The time is {time}", max_words=5)
    reply(r"\b(thanks|thank)\b", "Anytime!", max_words=5)
    reply(r"\b(hello|hi|hey)\b", "Hey there! What can I do for you?", max_words=4)

    @staticmethod
    def wiki(query):
        try:
            return wikipedia.summary(query, sentences=2)
        except wikipedia.DisambiguationError as e:
            try:
                return wikipedia.summary(e.options[0], sentences=2)
            except Exception:
                return None
        except Exception:
            return None

    # ---- main voice loop -------------------------------------------------
    def run(self):
        # a transient audio-driver error (device switch, Bluetooth route change...) used to
        # kill the voice thread for good; now the microphone is simply reopened
        while not self.state.quit:
            try:
                self._run()
                return
            except Exception:
                traceback.print_exc()
                print("Microphone problem - reopening it in 2 seconds...")
                time.sleep(2)

    def _run(self):
        # pyttsx3 (SAPI5) needs COM initialised in this thread on Windows
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

        st = self.state
        self._load_whisper()
        self._voice_thread = threading.current_thread()
        with sr.Microphone() as source:
            self._source = source
            print("Calibrating microphone... stay quiet for a second.")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            st.set_bubble("Say 'Hey Veera'!", 6)
            print("Ready! Say 'Hey Veera'.")
            self.ready = True

            misses = 0
            while not st.quit:
                if st.toggle_request:                       # click on Veera (pose + voice already done)
                    st.toggle_request = False
                    misses = 0
                    continue

                if not st.awake:
                    text = self.listen(source, timeout=3, limit=6, wake=True)
                    # Google's top guess often mangles "veera" - check its other guesses too
                    heard = next((a for a in [text] + self.alternatives
                                  if a and is_wake(a)), None) if text else None
                    if heard:
                        text = heard
                        rest = WAKE_RE.sub("", text, count=1).strip(" ,.!?")
                        self.wake_up(has_command=bool(rest))
                        misses = 0
                        if rest:                            # "hey veera, what time is it"
                            self.handle(rest, source)
                else:
                    st.listening = True
                    text = self.listen(source, timeout=8, limit=15)
                    st.listening = False
                    if text:
                        misses = 0
                        self.handle(text, source)
                    else:
                        misses += 1
                        if misses >= 3:
                            self.go_to_sleep("Nothing? Okay, I'll hang out up here. Just call me!")
                            misses = 0


# --------------------------------------------------------------------------
def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    state = State()
    root = tk.Tk()
    VeeraPet(root, state)
    assistant = Assistant(state)
    threading.Thread(target=assistant.run, daemon=True).start()
    Greeter(state, brain_ready=lambda: assistant.ready).start()      # hello on start-up / unlock
    root.mainloop()


if __name__ == "__main__":
    main()
