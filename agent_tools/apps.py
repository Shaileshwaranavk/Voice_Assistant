"""APPLICATIONS tools: launch, close, switch, inspect."""

import difflib
import json
import subprocess
import time

from . import tool, remember
from .desktop import all_windows, find_window, focus, user32, WM_CLOSE

# friendly name -> process name (without .exe). Used to recognise an app's window reliably.
PROCESS = {
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "onenote": "onenote", "teams": "ms-teams", "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "firefox": "firefox", "notepad": "notepad", "paint": "mspaint",
    "vlc": "vlc", "spotify": "spotify", "vs code": "code", "vscode": "code", "visual studio code": "code",
    "file explorer": "explorer", "explorer": "explorer", "whatsapp": "whatsapp", "discord": "discord",
    "slack": "slack", "zoom": "zoom", "calculator": "calculatorapp",
}
# fallbacks for starting things Windows doesn't list in the Start menu by that name
EXE = {"notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe",
       "file explorer": "explorer.exe", "explorer": "explorer.exe", "command prompt": "cmd.exe",
       "task manager": "taskmgr.exe", "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
       "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
       "vlc": "vlc", "spotify": "spotify", "vs code": "code", "vscode": "code"}

_start_apps = None


def start_apps():
    """[(name, app id)] of everything in the Start menu (cached)."""
    global _start_apps
    if _start_apps is None:
        _start_apps = []
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-StartApps | ConvertTo-Json -Compress"],
                               capture_output=True, text=True, timeout=40, creationflags=0x08000000)
            data = json.loads(r.stdout or "[]")
            data = [data] if isinstance(data, dict) else data
            _start_apps = [(d["Name"], d["AppID"]) for d in data if d.get("Name")]
        except Exception as e:
            print("[apps] couldn't read the Start menu:", e)
    return _start_apps


def _match_start_app(name):
    n = name.lower().strip()
    apps = start_apps()
    for nm, aid in apps:
        if nm.lower() == n:
            return nm, aid
    for nm, aid in apps:
        if n in nm.lower():
            return nm, aid
    names = [nm.lower() for nm, _ in apps]
    close = difflib.get_close_matches(n, names, n=1, cutoff=0.75)
    return next(((nm, aid) for nm, aid in apps if nm.lower() == close[0]), None) if close else None


def app_window(name):
    """The window belonging to an app, matched by process when we know it, else by title."""
    n = name.lower().strip()
    proc = PROCESS.get(n)
    if proc:
        for hwnd, title, p in all_windows():
            if p.lower().replace(".exe", "") == proc:
                return hwnd, title, p
        return None
    return find_window(n)


@tool("apps")
def open_app(name: str) -> str:
    """Open an application by name (Chrome, Word, Excel, Spotify, WhatsApp, VLC, Notepad, ...).
    If it is already open it is brought to the front instead of starting a second copy."""
    existing = app_window(name)
    if existing:
        focus(existing[0])
        remember(last_app=name)
        return f"{name} was already open; brought it to the front."
    hit = _match_start_app(name)
    try:
        if hit:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{hit[1]}"])
        else:
            exe = EXE.get(name.lower().strip(), name)
            subprocess.Popen(["cmd", "/c", "start", "", exe], creationflags=0x08000000)
    except Exception as e:
        return f"I couldn't start {name}: {e}"
    for _ in range(16):                                   # wait up to ~8 s for its window to appear
        time.sleep(0.5)
        w = app_window(name) or (find_window(hit[0]) if hit else None)
        if w:
            focus(w[0])
            remember(last_app=name)
            return f"Opened {hit[0] if hit else name}."
    return f"I started {name} but no window has appeared yet."


@tool("apps")
def switch_to_app(name: str) -> str:
    """Bring an already-open application to the front."""
    w = app_window(name)
    if not w:
        return f"{name} isn't open."
    ok = focus(w[0])
    remember(last_app=name)
    return f"{w[1]} is now in front." if ok else f"Found {name}, but Windows wouldn't bring it forward."


@tool("apps", risk="confirm")
def close_app(name: str) -> str:
    """Close every window of an application, politely (unsaved-work prompts still appear)."""
    wins = [w for w in all_windows() if app_window(name) and
            (w[2].lower() == app_window(name)[2].lower())]
    if not wins:
        return f"{name} isn't open."
    for hwnd, _, _ in wins:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.2)
    left = app_window(name)
    return f"Closed {name}." if not left else f"Asked {name} to close, but it's still open (it may be asking to save)."


@tool("apps")
def list_running_apps() -> list:
    """Applications that currently have a window open."""
    seen = []
    for _, title, proc in all_windows():
        label = proc.replace(".exe", "")
        if label and label not in seen:
            seen.append(label)
    return seen


@tool("apps")
def is_app_installed(name: str) -> str:
    """Check whether an app is installed (it appears in the Start menu)."""
    hit = _match_start_app(name)
    return f"Yes: {hit[0]}" if hit else f"I can't find {name} in the Start menu."


@tool("apps")
def open_with(path: str, app: str) -> str:
    """Open a file with a specific application, e.g. a PDF in Word, a picture in Paint."""
    import os
    from .files import resolve
    p = resolve(path)
    if not p or not p.exists():
        return "I can't find that file."
    key = app.lower().replace("microsoft ", "").strip()
    exe = EXE.get(key) or {"paint": "mspaint.exe", "wordpad": "write.exe", "photos": "ms-photos:", "media player": "wmplayer.exe",
                           "acrobat": "acrord32.exe", "adobe": "acrord32.exe", "visual studio code": "code"}.get(key)
    if not exe:
        os.startfile(str(p))
        return f"Opened {p.name} with its default app."
    try:
        subprocess.Popen(["cmd", "/c", "start", "", exe, str(p)], creationflags=0x08000000)
    except Exception as e:
        return f"I couldn't open it in {app}: {e}"
    remember(last_file=str(p), last_app=app)
    return f"Opening {p.name} in {app}."
