"""COMMUNICATION tools: email, chat apps, calendar.

No private APIs and no vision model: these open the service in the user's own app / browser profile (already
signed in), then read what is on screen by copying the text or, failing that, with local OCR.
Gmail search works straight from a URL; chat apps use their own search key.
"""

import time
import urllib.parse

import pyautogui
import pyperclip

from . import tool, remember
from .apps import _match_start_app, open_app
from .browser import open_url, _focus_browser
from .desktop import foreground, screenshot_image

# name -> (Start-menu app name or None, web address)
SERVICES = {
    "gmail": (None, "https://mail.google.com/mail/u/0/#inbox"),
    "outlook": ("Outlook", "https://outlook.office.com/mail/"),
    "whatsapp": ("WhatsApp", "https://web.whatsapp.com"),
    "instagram": (None, "https://www.instagram.com/direct/inbox/"),
    "google chat": (None, "https://chat.google.com"),
    "teams": ("Microsoft Teams", "https://teams.microsoft.com"),
    "slack": ("Slack", "https://app.slack.com/client"),
    "discord": ("Discord", "https://discord.com/app"),
    "telegram": ("Telegram", "https://web.telegram.org"),
    "messenger": (None, "https://www.messenger.com"),
    "google calendar": (None, "https://calendar.google.com/calendar/u/0/r/day"),
    "outlook calendar": (None, "https://outlook.office.com/calendar/"),
    "google meet": (None, "https://meet.google.com"),
    "google drive": (None, "https://drive.google.com"),
    "onedrive": (None, "https://onedrive.live.com"),
}
ALIASES = {"chat": "google chat", "gchat": "google chat", "google chat app": "google chat", "meet": "google meet",
           "calendar": "google calendar", "mail": "gmail", "email": "gmail", "emails": "gmail", "ms teams": "teams",
           "microsoft teams": "teams", "wa": "whatsapp", "whats app": "whatsapp", "insta": "instagram",
           "g mail": "gmail", "google mail": "gmail", "drive": "google drive", "one drive": "onedrive",
           "google meets": "google meet", "inbox": "gmail", "e mail": "gmail", "e mails": "gmail"}
# the key each app uses to jump to its search box
SEARCH_KEYS = {"whatsapp": "ctrl+f", "slack": "ctrl+k", "discord": "ctrl+k", "teams": "ctrl+e",
               "telegram": "ctrl+k", "outlook": "ctrl+e", "gmail": "/", "google chat": "/"}


def service_key(name):
    n = name.lower().strip().replace("-", " ")
    n = ALIASES.get(n, n)
    return n if n in SERVICES else None


def _clip():
    try:
        return pyperclip.paste()
    except Exception:
        return None


def screen_text(max_chars=12000):
    """All the text of the window in front: copy-everything first, local OCR if that gives nothing."""
    old = _clip()
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "a")
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.4)
    text = (_clip() or "").strip()
    if old is not None:
        pyperclip.copy(old)
    if len(text) < 60:
        from .ocr import ocr_image
        try:
            text = ocr_image(screenshot_image())
        except RuntimeError:
            pass
    return text[:max_chars]


@tool("comms")
def open_service(name: str, profile: str = "", prefer: str = "app") -> str:
    """Open an email / chat / calendar service: gmail, outlook, whatsapp, instagram, google chat, teams, slack,
    discord, telegram, messenger, google calendar, outlook calendar, google meet, google drive, onedrive.
    prefer='app' uses the installed desktop app when there is one, otherwise the web version in the
    browser / profile in use. profile: browser profile name for the web version (e.g. 'work')."""
    key = service_key(name)
    if not key:
        return "I don't know that service. Options: " + ", ".join(SERVICES)
    app, url = SERVICES[key]
    if prefer == "app" and app and _match_start_app(app):
        res = open_app(app)
    else:
        res = open_url(url, profile=profile)
    remember(last_service=key)
    time.sleep(2)
    return res


@tool("comms")
def gmail_search(query: str, profile: str = "") -> str:
    """Search Gmail using Gmail's own search words, e.g. 'from:john has:attachment newer_than:2d',
    'is:unread', 'subject:invoice filename:pdf'. Opens the results in the browser."""
    url = "https://mail.google.com/mail/u/0/#search/" + urllib.parse.quote(query)
    res = open_url(url, profile=profile)
    remember(last_service="gmail")
    time.sleep(3.5)
    return res


@tool("comms")
def search_in_app(query: str, app: str = "") -> str:
    """Use the search box of the chat / mail app in front (WhatsApp, Slack, Teams, Discord, Telegram, Outlook,
    Gmail, Google Chat) to look for a person, group or message. Types the query, waits, and opens the top result."""
    key = service_key(app) if app else None
    if not key:
        _, title, proc = foreground()
        probe = f"{title} {proc}".lower().replace(" ", "")
        key = next((k for k in SEARCH_KEYS if k.replace(" ", "") in probe), None)
    if not key or key not in SEARCH_KEYS:
        return "I'm not sure which app to search in. Open it first."
    combo = SEARCH_KEYS[key]
    pyautogui.hotkey(*combo.split("+")) if "+" in combo else pyautogui.press(combo)
    time.sleep(0.5)
    if query.isascii():
        pyautogui.write(query, interval=0.02)
    else:
        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
    time.sleep(1.2)
    pyautogui.press("down")                      # first suggestion
    pyautogui.press("enter")                     # open it (opening a chat sends nothing)
    time.sleep(1.0)
    return f"Searched {key} for '{query}' and opened the top result."


@tool("comms")
def open_first_result() -> str:
    """Open the first email / message / file in the list in front (found through Windows UI Automation)."""
    import uiautomation as auto
    top = auto.GetForegroundControl().GetTopLevelControl()
    for kind in ("DataItemControl", "ListItemControl", "TreeItemControl"):
        try:
            ctl = getattr(top, kind)(searchDepth=16)
            if ctl.Exists(0, 0) and ctl.Name.strip():
                ctl.Click(simulateMove=False)
                time.sleep(0.8)
                return f"Opened '{ctl.Name[:60]}'."
        except Exception:
            continue
    return "I couldn't find a list item to open."


@tool("comms")
def read_conversation() -> str:
    """The text of the chat / email / page in front, copied from the window (or read with OCR)."""
    return screen_text() or "I couldn't read anything from this window."
