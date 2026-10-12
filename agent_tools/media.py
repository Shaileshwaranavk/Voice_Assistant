"""MEDIA tools: system media keys, seeking, Spotify, local media files."""

import os
import time
import urllib.parse

import pyautogui

from . import tool, remember
from .apps import _match_start_app, open_app
from .browser import open_url

_KEYS = {"play_pause": "playpause", "play": "playpause", "pause": "playpause", "resume": "playpause",
         "next": "nexttrack", "previous": "prevtrack", "stop": "stop", "mute": "volumemute"}


@tool("media")
def media_key(action: str) -> str:
    """Control whatever is playing (Spotify, YouTube, VLC, ...) with the keyboard media keys.
    action: play | pause | resume | play_pause | next | previous | stop | mute."""
    key = _KEYS.get(action.lower().replace(" ", "_"))
    if not key:
        return "Unknown media action. Options: " + ", ".join(sorted(set(_KEYS)))
    pyautogui.press(key)
    return f"Sent '{action}'."


@tool("media")
def media_seek(direction: str = "forward", seconds: int = 10) -> str:
    """Skip forward / back in the player that is in front (YouTube, VLC, most video players)."""
    key = "right" if direction.lower().startswith("f") else "left"
    pyautogui.press(key, presses=max(1, seconds // 5))      # one arrow press = about 5 seconds
    return f"Skipped {direction} about {seconds} seconds."


@tool("media")
def open_spotify(query: str = "", profile: str = "") -> str:
    """Open Spotify (the desktop app if installed, otherwise the web player), optionally on a search for an
    artist / song / playlist. The result list still needs a click on the item to start playing."""
    if _match_start_app("spotify"):
        if query:
            try:
                os.startfile("spotify:search:" + urllib.parse.quote(query))
                time.sleep(3.5)
                return f"Opened Spotify on a search for '{query}'. " + press_play_in_front()
            except OSError:
                pass
        return open_app("spotify")
    url = "https://open.spotify.com/search/" + urllib.parse.quote(query) if query else "https://open.spotify.com"
    res = open_url(url, profile=profile)
    if query:
        time.sleep(4)
        res += " " + press_play_in_front()
    return res


def press_play_in_front():
    """Press the first 'Play' button in the window in front (Windows UI Automation, local)."""
    from .desktop import click_control
    out = click_control("Play")
    return "Started playing." if out.startswith(("Pressed", "Clicked")) else "Say 'play' to start it."


@tool("media")
def play_media_file(path: str) -> str:
    """Play an audio / video file with its default player."""
    from .files import resolve
    p = resolve(path)
    if not p or not p.exists():
        return "I can't find that file."
    os.startfile(str(p))
    remember(last_file=str(p))
    return f"Playing {p.name}."
