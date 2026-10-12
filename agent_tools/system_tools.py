"""SYSTEM tools: wallpaper, settings pages, power, and the built-in volume / brightness / Wi-Fi / Bluetooth."""

import ctypes
import os
import re
import subprocess
import time

import psutil

import system_control
from . import tool, remember
from .files import resolve

SETTINGS = {
    "display": "display", "brightness": "display", "bluetooth": "bluetooth", "wifi": "network-wifi",
    "network": "network", "sound": "sound", "volume": "sound", "notifications": "notifications",
    "personalization": "personalization", "wallpaper": "personalization-background",
    "background": "personalization-background", "default apps": "defaultapps", "keyboard": "typing",
    "mouse": "mousetouchpad", "language": "regionlanguage", "date": "dateandtime", "time": "dateandtime",
    "accessibility": "easeofaccess", "apps": "appsfeatures", "update": "windowsupdate",
    "power": "powersleep", "battery": "batterysaver", "storage": "storagesense", "privacy": "privacy",
}


@tool("system")
def set_wallpaper(path: str) -> str:
    """Set an image file as the desktop wallpaper."""
    p = resolve(path)
    if not p or not p.exists():
        return "I can't find that image."
    ok = ctypes.windll.user32.SystemParametersInfoW(20, 0, str(p), 3)   # SPI_SETDESKWALLPAPER
    return f"Wallpaper set to {p.name}." if ok else "Windows refused to set that wallpaper."


@tool("system")
def set_wallpaper_from_url(url: str) -> str:
    """Download a picture from a web address and make it the wallpaper."""
    from .browser import download_file
    res = download_file(url, "pictures")
    m = re.search(r"Downloaded to (.+)$", res)
    return set_wallpaper(m.group(1)) if m else res


@tool("system")
def open_settings(page: str) -> str:
    """Open a Windows Settings page: display, brightness, bluetooth, wifi, network, sound, notifications,
    personalization, wallpaper, default apps, keyboard, mouse, language, date, accessibility, apps, update,
    power, battery, storage, privacy."""
    key = page.lower().strip()
    uri = SETTINGS.get(key) or next((v for k, v in SETTINGS.items() if k in key or key in k), None)
    if not uri:
        return "I don't know that settings page. Options: " + ", ".join(SETTINGS)
    os.startfile(f"ms-settings:{uri}")
    time.sleep(1.2)
    return f"Opened the {page} settings."


@tool("system", risk=lambda a: "confirm" if re.search(r"wi.?fi|bluetooth|connect|pair", a.get("text", ""), re.I) else "safe")
def system_command(text: str) -> str:
    """Built-in controls, in plain words: 'reduce volume by 20', 'set volume to 40', 'mute', 'increase brightness by 10',
    'set brightness to 50', 'turn off wifi', 'turn on bluetooth', 'connect bluetooth to <device>',
    'connect wifi to <network>', 'disconnect wifi'."""
    out = system_control.try_handle(text)
    return out or "That isn't a volume / brightness / Wi-Fi / Bluetooth command I know."


@tool("system")
def system_status() -> dict:
    """Date and time, battery, memory and disk usage."""
    import datetime
    b = psutil.sensors_battery()
    return {"now": datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p"),
            "battery": f"{b.percent:.0f}%{' (charging)' if b.power_plugged else ''}" if b else "no battery",
            "memory_used": f"{psutil.virtual_memory().percent:.0f}%",
            "disk_free_gb": round(psutil.disk_usage(os.path.abspath(os.sep)).free / 2**30, 1)}


@tool("system", risk="confirm")
def lock_computer() -> str:
    """Lock the computer."""
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


@tool("system", risk="confirm")
def sleep_computer() -> str:
    """Put the computer to sleep."""
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"])
    return "Going to sleep."


@tool("system", risk="destructive")
def restart_computer() -> str:
    """Restart the computer in 15 seconds (can be cancelled)."""
    os.system("shutdown /r /t 15")
    return "Restarting in 15 seconds."


@tool("system", risk="destructive")
def shutdown_computer() -> str:
    """Shut the computer down in 15 seconds (can be cancelled)."""
    os.system("shutdown /s /t 15")
    return "Shutting down in 15 seconds."


@tool("system")
def cancel_shutdown() -> str:
    """Cancel a pending shutdown or restart."""
    os.system("shutdown /a")
    return "Cancelled the pending shutdown / restart."


@tool("system", risk="destructive")
def empty_recycle_bin() -> str:
    """Permanently empty the Recycle Bin."""
    subprocess.run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force"],
                   capture_output=True, timeout=60, creationflags=0x08000000)
    return "Recycle Bin emptied."
