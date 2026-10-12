"""
Greets you when the PC starts or is unlocked - by time of day, with a quote in the morning and
"how was your day?" in the evening. Everything here is local (no Gemini, no internet).

    Morning   (05:00-11:59)  Good morning + a quote
    Afternoon (12:00-16:59)  Good afternoon
    Evening   (17:00-20:59)  Good evening + "how was your day?"
    Night     (21:00-04:59)  Good evening / it's late

The first greeting of the day is the full one. After that he only says a short hello when you were away for
GREET_AWAY_MINUTES or more, so a quick lock/unlock doesn't make him chatter. Settings in .env (all optional):
    GREETING=off              turn greetings off
    GREET_NAME=Shaileshwaran  what he calls you
    GREET_AWAY_MINUTES=20
"""

import ctypes
import datetime
import json
import os
import random
import threading
import time

from agent_tools import CTX

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "greeting_state.json")

QUOTES = [
    "The secret of getting ahead is getting started.",
    "Well begun is half done.",
    "Small steps every day add up to big results.",
    "Don't watch the clock; do what it does. Keep going.",
    "You don't have to be great to start, but you have to start to be great.",
    "The best way to predict the future is to create it.",
    "Today is a good day to learn something new.",
    "Discipline is choosing between what you want now and what you want most.",
    "Do something today that your future self will thank you for.",
    "Believe you can, and you're halfway there.",
    "It always seems impossible until it's done.",
    "Start where you are. Use what you have. Do what you can.",
    "Energy and persistence conquer all things.",
    "Make today so good that yesterday gets jealous.",
    "The harder you work for something, the greater you'll feel when you achieve it.",
    "Focus on progress, not perfection.",
    "A little progress each day adds up to big results.",
    "Dream big, start small, but most of all, start.",
]
DAY_QUESTIONS = ["How was your day?", "How did your day go?", "Did you have a good day?"]
WELCOME_BACK = ["Welcome back!", "Hey, you're back.", "Good to see you again."]


def part_of_day(now=None):
    h = (now or datetime.datetime.now()).hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "night"


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def build_greeting(first_today, now=None, name=""):
    """-> (what to say, whether it asked how the day was). Pure function, easy to test."""
    now = now or datetime.datetime.now()
    part = part_of_day(now)
    who = f", {name}" if name else ""
    if part == "morning":
        hello = f"Good morning{who}!"
        if first_today:
            return f"{hello} Here's a thought for today: {random.choice(QUOTES)}", False
        return hello, False
    if part == "afternoon":
        return f"Good afternoon{who}!", False
    if part == "evening":
        hello = f"Good evening{who}!"
        return (f"{hello} {random.choice(DAY_QUESTIONS)}", True) if first_today else (hello, False)
    hello = f"Good evening{who}." if now.hour >= 21 else f"You're up late{who}."
    if now.hour >= 23 or now.hour < 5:
        hello += " It's quite late, don't forget to rest."
    return hello, False


# --------------------------------------------------------------------------
# lock detection: the lock screen runs on a different desktop than "Default"
# --------------------------------------------------------------------------
def is_locked():
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    hdesk = user32.OpenInputDesktop(0, False, 0x0100)         # DESKTOP_SWITCHDESKTOP
    if not hdesk:
        return True                                           # can't open the input desktop = secure desktop (locked)
    user32.CloseDesktop(ctypes.c_void_p(hdesk))
    return False


def seconds_since_boot():
    return ctypes.windll.kernel32.GetTickCount64() / 1000.0


class Greeter(threading.Thread):
    def __init__(self, state, brain_ready=lambda: True):
        super().__init__(daemon=True)
        self.state = state
        self.brain_ready = brain_ready
        self.name = os.environ.get("GREET_NAME", "").strip()
        self.away_min = float(os.environ.get("GREET_AWAY_MINUTES", "20") or 20)
        self.enabled = os.environ.get("GREETING", "on").strip().lower() not in ("off", "no", "false", "0")

    def greet(self, why):
        data = _load()
        today = datetime.date.today().isoformat()
        first_today = data.get("last_full") != today
        text, asked = build_greeting(first_today, name=self.name)
        if not first_today and why == "unlock":
            text = f"{random.choice(WELCOME_BACK)} {text}" if part_of_day() != "night" else text
        data["last_full" if first_today else "last_short"] = today
        data["last_time"] = time.time()
        _save(data)
        print(f"[greeting:{why}] {text}")
        st = self.state
        st.awake = True                                      # fly up while talking
        st.speaker.say(text, wait=True)
        if asked:
            CTX["asked_day_t"] = time.time()                 # the brain turns "it was great" into a reply
        else:
            time.sleep(2.5)
            if not CTX.get("asked_day_t") or time.time() - CTX["asked_day_t"] > 1:
                st.awake = False

    def run(self):
        if not self.enabled:
            return
        while not self.brain_ready():                        # wait until the assistant has finished loading
            time.sleep(1)
        data = _load()
        # 1) just started: greet if the PC itself booted recently or we haven't greeted yet today
        booted_recently = seconds_since_boot() < 15 * 60
        if booted_recently or data.get("last_full") != datetime.date.today().isoformat():
            if not is_locked():
                self.greet("start")
        locked_at = None
        while not self.state.quit:
            time.sleep(2)
            try:
                locked = is_locked()
            except Exception:
                continue
            if locked and locked_at is None:
                locked_at = time.time()
            elif not locked and locked_at is not None:
                away = (time.time() - locked_at) / 60
                locked_at = None
                new_day = _load().get("last_full") != datetime.date.today().isoformat()
                if away >= self.away_min or new_day:
                    time.sleep(2.0)                          # let the desktop finish appearing
                    self.greet("unlock")


# --------------------------------------------------------------------------
# answers to "how was your day?"  (called from brain.py)
# --------------------------------------------------------------------------
GOOD = ("good", "great", "fine", "nice", "awesome", "amazing", "wonderful", "fantastic", "productive", "happy", "well", "okay", "ok", "alright", "excellent")
BAD = ("bad", "terrible", "awful", "rough", "tough", "stressful", "tiring", "exhausting", "tired", "horrible", "hectic", "busy", "long", "difficult", "sad", "worst")


def day_reply(text):
    """A short, kind reply to how the user says their day went, or None if it doesn't sound like an answer."""
    t = text.lower()
    negated = any(n in t for n in ("not ", "n't", "never"))
    if (negated and any(w in t for w in GOOD)) or any(w in t for w in BAD):
        return random.choice(["I'm sorry to hear that. Take it easy tonight, you've earned a rest.",
                              "That sounds tough. Tomorrow is a fresh start.",
                              "Hang in there. A little rest will help."])
    if any(w in t for w in GOOD):
        return random.choice(["That's great to hear!", "Glad it went well. You deserve it.",
                              "Wonderful. Let's keep that going."])
    return None
