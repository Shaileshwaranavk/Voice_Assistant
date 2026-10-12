"""BROWSER tools: which browser / profile, tabs, navigation, page reading, downloads."""

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
import winreg
from pathlib import Path

import pyautogui
import pyperclip

from . import tool, remember
from .desktop import all_windows, focus, foreground
from .files import known_folder

_PF = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
       os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
       os.environ.get("LOCALAPPDATA", "")]
_LOCAL = os.environ.get("LOCALAPPDATA", "")

BROWSERS = {
    "chrome": {"proc": "chrome", "rel": [r"Google\Chrome\Application\chrome.exe"],
               "data": Path(_LOCAL) / "Google/Chrome/User Data"},
    "edge": {"proc": "msedge", "rel": [r"Microsoft\Edge\Application\msedge.exe"],
             "data": Path(_LOCAL) / "Microsoft/Edge/User Data"},
    "brave": {"proc": "brave", "rel": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
              "data": Path(_LOCAL) / "BraveSoftware/Brave-Browser/User Data"},
    "firefox": {"proc": "firefox", "rel": [r"Mozilla Firefox\firefox.exe"], "data": None},
}
_PROG_IDS = {"chromehtml": "chrome", "msedgehtm": "edge", "firefoxurl": "firefox", "bravehtml": "brave"}

ACTIONS = {
    "new_tab": "ctrl+t", "close_tab": "ctrl+w", "reopen_tab": "ctrl+shift+t", "next_tab": "ctrl+tab",
    "previous_tab": "ctrl+shift+tab", "back": "alt+left", "forward": "alt+right", "refresh": "f5",
    "bookmarks": "ctrl+shift+o", "history": "ctrl+h", "downloads": "ctrl+j", "fullscreen": "f11",
    "zoom_in": "ctrl+=", "zoom_out": "ctrl+-", "zoom_reset": "ctrl+0", "address_bar": "ctrl+l",
    "new_window": "ctrl+n", "incognito": "ctrl+shift+n", "find": "ctrl+f", "scroll_top": "ctrl+home",
    "scroll_bottom": "ctrl+end",
}


def _exe(browser):
    for rel in BROWSERS[browser]["rel"]:
        for base in _PF:
            p = Path(base) / rel if base else None
            if p and p.exists():
                return str(p)
    return None


def default_browser():
    try:
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
        for pid, name in _PROG_IDS.items():
            if prog.startswith(pid):
                return name
    except OSError:
        pass
    return "edge"


def _browser_windows(browser=None):
    procs = {b["proc"]: n for n, b in BROWSERS.items()}
    out = []
    for hwnd, title, proc in all_windows():
        name = procs.get(proc.lower().replace(".exe", ""))
        if name and (browser is None or name == browser):
            out.append((hwnd, title, name))
    return out


def current_browser():
    """The browser the user is using: the one in front, else any that's open, else the default."""
    _, _, proc = foreground()
    for n, b in BROWSERS.items():
        if proc.lower().replace(".exe", "") == b["proc"]:
            return n
    wins = _browser_windows()
    return wins[0][2] if wins else default_browser()


def _pick(browser):
    b = (browser or "").lower().replace("google ", "").replace("microsoft ", "").strip()
    return b if b in BROWSERS else current_browser()


def _profiles(browser):
    data = BROWSERS[browser]["data"]
    state = data / "Local State" if data else None
    if not state or not state.exists():
        return []
    try:
        cache = json.loads(state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
    except Exception:
        return []
    return [{"directory": d, "name": v.get("name", d), "email": v.get("user_name", "")} for d, v in cache.items()]


@tool("browser")
def list_browser_profiles(browser: str = "") -> list:
    """Profiles of a browser (default: the one in use): directory, name, and signed-in email."""
    b = _pick(browser)
    return _profiles(b) or [f"No profiles found for {b}."]


@tool("browser")
def active_browser() -> str:
    """Which browser the user is currently using (or their default)."""
    return current_browser()


def _normalise_url(text):
    t = text.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return t
    if " " not in t and "." in t:
        return "https://" + t
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(t)


def _launch(browser, url, profile=""):
    exe = _exe(browser)
    if not exe:
        webbrowser.open(url)
        return "default browser"
    args = [exe]
    if profile and BROWSERS[browser]["data"]:
        want = profile.lower()
        for p in _profiles(browser):
            if want in (p["name"].lower(), p["directory"].lower()) or want in p["name"].lower() \
                    or (p["email"] and want in p["email"].lower()):
                args.append(f"--profile-directory={p['directory']}")
                break
        else:
            return None
    if url:
        args.append(url)
    subprocess.Popen(args)
    return browser


@tool("browser")
def open_browser(browser: str = "", profile: str = "") -> str:
    """Open a browser: chrome | edge | brave | firefox (empty = the one in use). If it is already open it is
    brought to the front; with a profile name, a window for that profile is opened."""
    b = _pick(browser)
    if not profile:
        wins = _browser_windows(b)
        if wins:
            focus(wins[0][0])
            return f"{b} was already open; brought it to the front."
    used = _launch(b, "", profile)
    if used is None:
        return f"I couldn't find a {b} profile called '{profile}'."
    time.sleep(1.5)
    return f"Opened {b}" + (f" with the {profile} profile." if profile else ".")


@tool("browser")
def open_url(url: str, browser: str = "", profile: str = "") -> str:
    """Open a web address in a browser. browser: chrome | edge | firefox | brave (empty = the one already in use).
    profile: a browser profile name / email, e.g. 'work' (empty = whichever is current).
    Reuses an already-open browser, opening a new tab in it."""
    b = _pick(browser)
    target = _normalise_url(url)
    used = _launch(b, target, profile)
    if used is None:
        return f"I couldn't find a {b} profile called '{profile}'."
    remember(last_url=target)
    time.sleep(1.5)
    return f"Opened {target} in {used}."


@tool("browser")
def web_search(query: str, engine: str = "google", browser: str = "", profile: str = "") -> str:
    """Search the web in the user's browser. engine: google | bing | duckduckgo | youtube."""
    q = urllib.parse.quote_plus(query)
    base = {"google": "https://www.google.com/search?q=", "bing": "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=", "youtube": "https://www.youtube.com/results?search_query="}
    return open_url(base.get(engine.lower(), base["google"]) + q, browser, profile)


@tool("browser")
def browser_action(action: str, browser: str = "") -> str:
    """Do a browser action in the front browser window.
    action: new_tab | close_tab | reopen_tab | next_tab | previous_tab | back | forward | refresh | bookmarks |
    history | downloads | fullscreen | zoom_in | zoom_out | zoom_reset | address_bar | new_window | incognito |
    find | scroll_top | scroll_bottom."""
    keys = ACTIONS.get(action.lower())
    if not keys:
        return "Unknown browser action. Options: " + ", ".join(ACTIONS)
    if not _focus_browser(browser):
        return "No browser window is open."
    pyautogui.hotkey(*keys.split("+"))
    time.sleep(0.3)
    return f"{action} done. In front now: {foreground()[1]}"


def _focus_browser(browser=""):
    wins = _browser_windows(_pick(browser) if browser else None)
    if not wins:
        return False
    _, _, proc = foreground()
    if any(proc.lower().replace(".exe", "") == b["proc"] for b in BROWSERS.values()):
        return True                                       # already in a browser: leave it alone
    return focus(wins[0][0])


@tool("browser")
def list_open_pages() -> list:
    """Titles of the browser windows that are open (each shows its current tab)."""
    return [f"{t}  [{n}]" for _, t, n in _browser_windows()] or ["No browser is open."]


@tool("browser")
def switch_browser_tab(title_contains: str, max_tabs: int = 30) -> str:
    """Go to the open tab whose title contains some text, by cycling through the tabs."""
    if not _focus_browser():
        return "No browser window is open."
    want = title_contains.lower()
    for _ in range(max_tabs):
        if want in foreground()[1].lower():
            return f"On the tab: {foreground()[1]}"
        pyautogui.hotkey("ctrl", "tab")
        time.sleep(0.25)
    return f"I couldn't find a tab with '{title_contains}'."


@tool("browser")
def current_page_url() -> str:
    """The web address of the page in front."""
    if not _focus_browser():
        return "No browser window is open."
    old = _clip()
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "l")
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.25)
    url = _clip()
    pyautogui.press("escape")
    if old is not None:
        pyperclip.copy(old)
    remember(last_url=url)
    return url or "I couldn't read the address."


def _clip():
    try:
        return pyperclip.paste()
    except Exception:
        return None


@tool("browser")
def read_current_page(max_chars: int = 8000) -> str:
    """Read the text of the web page in front (what a person would see on it).
    Falls back to reading the screen with OCR when the page text can't be copied."""
    if not _focus_browser():
        return "No browser window is open."
    old = _clip()
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "a")         # no clicking: a click could press a link or button
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.4)
    text = (_clip() or "").strip()
    if old is not None:
        pyperclip.copy(old)
    if len(text) < 150:
        from .ocr import ocr_image
        from .desktop import screenshot_image
        try:
            text = "(read from the screen) " + ocr_image(screenshot_image())    # local OCR fallback
        except RuntimeError:
            pass
    return text[:max_chars]


def _unique(path):
    if not path.exists():
        return path
    for i in range(2, 200):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path


@tool("browser")
def download_file(url: str, folder: str = "downloads", filename: str = "") -> str:
    """Download a file from a web address into a folder (default Downloads). Returns the saved path."""
    from .files import resolve
    dest_dir = resolve(folder) or known_folder("downloads")
    dest_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            name = filename
            if not name:
                cd = r.headers.get("Content-Disposition", "")
                m = re.search(r'filename="?([^";]+)', cd)
                name = m.group(1) if m else (Path(urllib.parse.urlparse(url).path).name or "download")
            path = _unique(dest_dir / name)
            total = 0
            with open(path, "wb") as f:
                while chunk := r.read(1 << 16):
                    total += len(chunk)
                    if total > 300 * 1024 * 1024:
                        f.close()
                        path.unlink(missing_ok=True)
                        return "That file is bigger than 300 MB, so I stopped."
                    f.write(chunk)
    except Exception as e:
        return f"The download failed: {e}"
    remember(last_file=str(path))
    return f"Downloaded to {path}"


@tool("browser")
def list_downloads(limit: int = 10) -> list:
    """The most recent files in the Downloads folder."""
    from .files import recent_files
    return recent_files("downloads", hours=24 * 30, limit=limit)
