"""DESKTOP tools: screen, windows, mouse, keyboard, clipboard."""

import ctypes
import json
import re
import subprocess
import time
from ctypes import wintypes

import psutil
import pyautogui
import pyperclip

from . import tool, remember

pyautogui.FAILSAFE = True          # slam the mouse into a screen corner to abort any automation
pyautogui.PAUSE = 0.05

user32 = ctypes.windll.user32
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
WM_CLOSE = 0x0010

# words that mean "this click / keystroke has an effect on other people or on data"
SENSITIVE = re.compile(r"\b(send|submit|post|publish|pay|buy|purchase|order|checkout|delete|remove|"
                       r"share|sign out|log ?out|unsubscribe|transfer|confirm purchase)\b", re.I)


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------
def all_windows():
    """[(hwnd, title, process_name)] for visible, titled windows, front-most first."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value).name()
                except Exception:
                    proc = ""
                if buf.value not in ("Program Manager", "Windows Input Experience"):
                    found.append((hwnd, buf.value, proc))
        return True

    user32.EnumWindows(callback, 0)
    return found


def find_window(query):
    """First window whose title or process name contains the query (case-insensitive)."""
    q = (query or "").lower().replace(".exe", "").strip()
    if not q:
        return None
    for hwnd, title, proc in all_windows():
        if q in title.lower() or q in proc.lower().replace(".exe", ""):
            return hwnd, title, proc
    return None


def focus(hwnd):
    """Bring a window to the front. Returns True if it really is in front afterwards."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.keybd_event(0x12, 0, 0, 0)                  # tap Alt: lets us take the foreground
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == hwnd


def foreground():
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value).name()
    except Exception:
        proc = ""
    return hwnd, buf.value, proc


def _powershell(script):
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                   timeout=15, creationflags=0x08000000)


@tool("desktop")
def list_windows(limit: int = 25) -> list:
    """List the open windows (title and application), front-most first."""
    return [f"{t}  [{p}]" for _, t, p in all_windows()[:limit]]


@tool("desktop")
def active_window() -> str:
    """Title and application of the window the user is looking at right now."""
    _, title, proc = foreground()
    return f"{title}  [{proc}]"


@tool("desktop")
def switch_to_window(name: str) -> str:
    """Bring a window to the front. name = part of its title or application name (e.g. 'word', 'chrome')."""
    hit = find_window(name)
    if not hit:
        return f"No open window matches '{name}'."
    ok = focus(hit[0])
    remember(last_app=hit[2])
    return f"Now in front: {hit[1]}" if ok else f"Found '{hit[1]}' but Windows would not bring it forward."


@tool("desktop")
def minimize_all_windows() -> str:
    """Minimise every window (show the desktop)."""
    _powershell("(New-Object -ComObject Shell.Application).MinimizeAll()")
    return "All windows minimised."


@tool("desktop")
def show_desktop() -> str:
    """Toggle 'show desktop'."""
    _powershell("(New-Object -ComObject Shell.Application).ToggleDesktop()")
    return "Toggled the desktop."


@tool("desktop")
def restore_all_windows() -> str:
    """Bring back the windows that 'minimise all' hid."""
    _powershell("(New-Object -ComObject Shell.Application).UndoMinimizeAll()")
    return "Windows restored."


@tool("desktop")
def window_action(action: str, name: str = "") -> str:
    """Minimise, maximise, restore, make fullscreen, or snap a window.
    action: minimize | maximize | restore | fullscreen | exit_fullscreen | snap_left | snap_right.
    name: part of the window title / app (empty = the window in front)."""
    hit = find_window(name) if name else foreground()
    if not hit or not hit[0]:
        return f"No window matches '{name}'."
    hwnd = hit[0]
    a = action.lower()
    if a == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif a == "maximize":
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    elif a == "restore":
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif a in ("fullscreen", "exit_fullscreen"):
        focus(hwnd)
        pyautogui.press("f11")
    elif a in ("snap_left", "snap_right"):
        focus(hwnd)
        pyautogui.hotkey("win", "left" if a == "snap_left" else "right")
    else:
        return "Unknown window action."
    return f"{action} done for '{hit[1]}'."


@tool("desktop", risk="confirm")
def close_window(name: str = "") -> str:
    """Close a window (asks the app to close itself, so unsaved-work prompts still appear)."""
    hit = find_window(name) if name else foreground()
    if not hit or not hit[0]:
        return f"No window matches '{name}'."
    user32.PostMessageW(hit[0], WM_CLOSE, 0, 0)
    time.sleep(0.6)
    still = any(h == hit[0] for h, _, _ in all_windows())
    return f"Asked '{hit[1]}' to close." + (" It is still open (probably asking to save)." if still else "")


@tool("desktop")
def move_resize_window(name: str, x: int, y: int, width: int, height: int) -> str:
    """Move and resize a window (screen pixels)."""
    hit = find_window(name)
    if not hit:
        return f"No window matches '{name}'."
    user32.ShowWindow(hit[0], SW_RESTORE)
    user32.MoveWindow(hit[0], x, y, width, height, True)
    return f"Moved '{hit[1]}'."


@tool("desktop")
def open_start_menu() -> str:
    """Open the Start menu."""
    pyautogui.press("win")
    return "Start menu opened."


@tool("desktop")
def open_notifications() -> str:
    """Open the notification centre."""
    pyautogui.hotkey("win", "n")
    return "Notification centre opened."


@tool("desktop")
def open_system_tray() -> str:
    """Show the system tray's hidden icons."""
    pyautogui.hotkey("win", "b")
    time.sleep(0.2)
    pyautogui.press("enter")
    return "System tray opened."


# --------------------------------------------------------------------------
# screen
# --------------------------------------------------------------------------
def screenshot_image():
    return pyautogui.screenshot()


# Controls we are happy to click, most specific first (so 'Insert' finds the tab, not a paragraph of text)
_CLICKABLE = ("ButtonControl", "TabItemControl", "MenuItemControl", "HyperlinkControl", "ListItemControl",
              "TreeItemControl", "CheckBoxControl", "RadioButtonControl", "SplitButtonControl", "ComboBoxControl",
              "EditControl", "DataItemControl", "MenuControl")


def _find_control(text):
    """Find a control in the front window whose visible name contains the text. Local - uses Windows
    UI Automation (the same thing screen readers use), so it works in Word, Explorer, Settings, browsers..."""
    import uiautomation as auto
    root = auto.GetForegroundControl()
    top = root.GetTopLevelControl() if root else None
    if not top:
        return None
    want = text.lower().strip()
    best = None
    deadline = time.time() + 6
    for kind in _CLICKABLE:
        if time.time() > deadline:
            break
        try:
            ctl = getattr(top, kind)(SubName=text, searchDepth=14)
            if ctl.Exists(0, 0):
                if ctl.Name.lower().strip() == want:
                    return ctl                               # exact name beats partial
                best = best or ctl
        except Exception:
            continue
    return best


def control_names(limit=40):
    """Names of the clickable things in the front window (for 'what can I click?')."""
    import uiautomation as auto
    top = auto.GetForegroundControl().GetTopLevelControl()
    names = []
    for ctl, depth in auto.WalkControl(top, maxDepth=10):
        if ctl.ControlTypeName in _CLICKABLE and ctl.Name.strip() and ctl.Name not in names:
            names.append(ctl.Name.strip())
            if len(names) >= limit:
                break
    return names


@tool("desktop", risk=lambda a: "external" if SENSITIVE.search(a.get("text", "")) else "safe")
def click_control(text: str, double_click: bool = False, right_click: bool = False) -> str:
    """Click a button / tab / menu item / link / list item in the front window by its visible name,
    e.g. 'Insert', 'Pictures', 'Save', 'Downloads'."""
    ctl = _find_control(text)
    if not ctl:
        return f"I can't find a control called '{text}' in the front window."
    name = ctl.Name
    try:
        if not (double_click or right_click):
            pat = ctl.GetInvokePattern()
            if pat:
                pat.Invoke()
                time.sleep(0.4)
                return f"Pressed '{name}'."
    except Exception:
        pass
    try:
        if right_click:
            ctl.RightClick(simulateMove=False)
        elif double_click:
            ctl.DoubleClick(simulateMove=False)
        else:
            ctl.Click(simulateMove=False)
    except Exception as e:
        return f"I found '{name}' but couldn't click it: {e}"
    time.sleep(0.4)
    return f"Clicked '{name}'."


# --------------------------------------------------------------------------
# mouse / keyboard
# --------------------------------------------------------------------------
@tool("desktop")
def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Click at screen pixel coordinates."""
    pyautogui.click(x, y, clicks=clicks, button=button)
    return "Clicked."


@tool("desktop")
def mouse_drag(points: list[int], duration: float = 0.4) -> str:
    """Hold the left button and drag through screen points, e.g. to draw in Paint or select an area.
    points = a flat list [x1, y1, x2, y2, ...] of at least two points (screen pixels)."""
    if len(points) < 4 or len(points) % 2:
        return "I need at least two x,y points."
    xy = list(zip(points[0::2], points[1::2]))
    pyautogui.moveTo(*xy[0])
    pyautogui.mouseDown()
    for x, y in xy[1:]:
        pyautogui.moveTo(x, y, duration=duration)
    pyautogui.mouseUp()
    return f"Dragged through {len(xy)} points."


@tool("desktop")
def scroll(direction: str = "down", amount: int = 5) -> str:
    """Scroll the window under the mouse. direction: up | down. amount = number of notches."""
    pyautogui.scroll(amount * 120 * (1 if direction.lower() == "up" else -1))
    return f"Scrolled {direction}."


def _is_sensitive_keys(keys):
    k = keys.lower().replace(" ", "")
    if k in ("ctrl+enter", "ctrl+return"):
        return "external"
    if "delete" in k or k in ("del", "ctrl+shift+del"):
        return "destructive"
    if k == "alt+f4":
        return "confirm"
    return "safe"


@tool("desktop", risk=lambda a: _is_sensitive_keys(a.get("keys", "")))
def press_keys(keys: str, times: int = 1) -> str:
    """Press a key or shortcut in the focused window, e.g. 'enter', 'ctrl+s', 'alt+left', 'win+d', 'ctrl+shift+t'."""
    parts = [p.strip() for p in keys.lower().replace(" ", "").split("+") if p.strip()]
    for _ in range(max(1, times)):
        pyautogui.hotkey(*parts) if len(parts) > 1 else pyautogui.press(parts[0])
        time.sleep(0.08)
    return f"Pressed {keys}."


@tool("desktop", risk=lambda a: "external" if a.get("press_enter") else "safe")
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text into the focused window exactly as given. press_enter=True also presses Enter afterwards
    (which may SEND a message or submit a form, so it needs the user's approval)."""
    if text.isascii() and len(text) < 120:
        pyautogui.write(text, interval=0.01)
    else:                                              # long / non-English text: paste it, keep the clipboard
        old = _safe_paste()
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        if old is not None:
            pyperclip.copy(old)
    if press_enter:
        pyautogui.press("enter")
    return "Typed."


# --------------------------------------------------------------------------
# clipboard
# --------------------------------------------------------------------------
def _safe_paste():
    try:
        return pyperclip.paste()
    except Exception:
        return None


@tool("desktop")
def get_clipboard() -> str:
    """Read the text currently on the clipboard."""
    return _safe_paste() or "(the clipboard has no text)"


@tool("desktop")
def set_clipboard(text: str) -> str:
    """Put text on the clipboard."""
    pyperclip.copy(text)
    return "Copied to the clipboard."


@tool("desktop")
def copy_selection() -> str:
    """Copy whatever is selected in the front window, and return that text."""
    old = _safe_paste()
    pyperclip.copy("")
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.3)
    got = _safe_paste() or ""
    if not got and old is not None:
        pyperclip.copy(old)
    return got or "Nothing was selected."


@tool("desktop")
def paste_clipboard() -> str:
    """Paste the clipboard into the focused window."""
    pyautogui.hotkey("ctrl", "v")
    return "Pasted."
