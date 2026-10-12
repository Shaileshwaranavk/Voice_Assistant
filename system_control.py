"""
Voice-driven system controls for Veera (Windows): volume, Wi-Fi, Bluetooth.

    try_handle("connect bluetooth to boat rockerz", say)   -> "Connected to ..." / None if not ours

Needs:  pip install pycaw        (exact volume control; falls back to media keys without it)
Wi-Fi uses the built-in `netsh`; only networks this PC already knows (saved profiles) can be joined.
Bluetooth uses the Win32 bthprops API; the device must already be PAIRED in Windows settings.
"""

import ctypes
import difflib
import re
import subprocess
import time
from ctypes import wintypes

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}


def _norm(text):
    """Lower-case, and fold the many ways 'wifi' is heard/written ('wi-fi', 'why fi', 'y fi'...)."""
    return re.sub(r"\b(?:wi|why|y|by|my|hi)[\s-]?(?:fi|fy)\b", "wifi", text.lower())


def _number_in(text):
    """First number in the text, as digits ('20') or words ('twenty five', 'a hundred')."""
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total, seen = 0, False
    for w in re.findall(r"[a-z]+", text):
        if w in _TENS:
            total, seen = total + _TENS[w], True
        elif w in _ONES:
            total, seen = total + _ONES[w], True
        elif w == "hundred":
            return 100
        elif seen:
            break
    return total if seen else None


def _best_match(spoken, names):
    """Closest name to what was heard, or None. Ignores case, spaces and punctuation."""
    def norm(t):
        return re.sub(r"[^a-z0-9]", "", t.lower())

    target = norm(spoken)
    if not target:
        return None
    best, best_score = None, 0.0
    for n in names:
        cand = norm(n)
        if not cand:
            continue
        score = difflib.SequenceMatcher(None, target, cand).ratio()
        if target in cand or cand in target:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = n, score
    return best if best_score >= 0.6 else None


def _target_name(cmd):
    """Strip the command words, leaving just the device / network name."""
    t = _norm(cmd)
    t = re.sub(r"\b(please|connect|disconnect|reconnect|bluetooth|blue tooth|wifi|network|"
               r"device|devices|headphones?|earbuds?|speaker|to|from|the|my|with|on|off|of|for|named|called)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------
# volume
# --------------------------------------------------------------------------
def _endpoint():
    from pycaw.pycaw import AudioUtilities
    dev = AudioUtilities.GetSpeakers()
    return dev.EndpointVolume


def _set_volume(pct):
    pct = max(0, min(100, pct))
    _endpoint().SetMasterVolumeLevelScalar(pct / 100.0, None)
    return pct


def _get_volume():
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def _brightness(cmd):
    """Screen brightness (works on laptop panels and on monitors that support DDC/CI)."""
    words = cmd.lower()
    if not re.search(r"\b(brightness|brighter|dimmer|dim)\b", words):
        return None
    try:
        import screen_brightness_control as sbc
        cur = sbc.get_brightness()[0]
        n = _number_in(words)
        down = re.search(r"\b(reduce|lower|decrease|down|dim|dimmer|less|drop)\b", words)
        up = re.search(r"\b(increase|raise|up|brighter|more|boost|higher)\b", words)

        if re.search(r"\b(set|make|put|change)\b.*\b(to|at)\b", words) or (n is not None and not up and not down):
            if n is None:
                return "What brightness should I set it to?"
            new = max(0, min(100, n))
        elif down or up:
            new = max(0, min(100, cur + (n if n is not None else 10) * (1 if up else -1)))
        elif re.search(r"\bwhat\b", words):
            return f"The brightness is at {cur} percent."
        else:
            return None
        sbc.set_brightness(new)
        return f"Brightness set to {new} percent."
    except Exception as e:                       # package missing / monitor without DDC-CI
        print("Brightness error:", e)
        return "Sorry, I can't change the brightness on this screen."


def _volume(cmd):
    words = cmd.lower()
    is_volume = re.search(r"\b(volume|sound|audio)\b", words)
    if not (is_volume or re.search(r"\b(mute|unmute|louder|quieter)\b", words)):
        return None
    try:
        if re.search(r"\bunmute\b", words):
            _endpoint().SetMute(0, None)
            return "Unmuted."
        if re.search(r"\bmute\b", words):
            _endpoint().SetMute(1, None)
            return "Muted."

        n = _number_in(words)
        cur = _get_volume()
        down = re.search(r"\b(reduce|lower|decrease|down|quieter|less|drop|dec)\b", words)
        up = re.search(r"\b(increase|raise|up|louder|more|boost|higher)\b", words)

        if re.search(r"\b(set|make|put|change)\b.*\b(to|at)\b", words) or (n is not None and not up and not down):
            if n is None:
                return "What volume should I set it to?"
            return f"Volume set to {_set_volume(n)} percent."
        if down or up:
            step = n if n is not None else 10
            new = _set_volume(cur + (step if up else -step))
            return f"Volume {'up' if up else 'down'} to {new} percent."
        if re.search(r"\bwhat\b", words):
            return f"The volume is at {cur} percent."
        return None
    except Exception as e:                        # pycaw missing / audio device problem
        print("Volume error:", e)
        try:                                      # fallback: media keys, ~2% per press
            import pyautogui
            n = _number_in(words) or 10
            key = "volumeup" if re.search(r"\b(increase|raise|up|louder|more)\b", words) else "volumedown"
            pyautogui.press(key, presses=max(1, n // 2))
            return "Done."
        except Exception:
            return "Sorry, I couldn't change the volume."


# --------------------------------------------------------------------------
# Wi-Fi  (netsh)
# --------------------------------------------------------------------------
def _run(args):
    r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=25,
                       creationflags=0x08000000)         # CREATE_NO_WINDOW
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _wifi_profiles():
    _, out = _run(["netsh", "wlan", "show", "profiles"])
    return [m.strip() for m in re.findall(r"All User Profile\s*:\s*(.+)", out)]


def _wifi_disconnect():
    _run(["netsh", "wlan", "disconnect"])
    return "Wi-Fi disconnected."


def _wifi_connect(name, say):
    if not name and _radio("WiFi", "Query") == "Off":        # "connect wifi" while it's off = turn it on
        return _radio_switch("WiFi", "Wi-Fi", True)
    profiles = _wifi_profiles()
    if not profiles:
        return "I couldn't read any saved Wi-Fi networks."
    match = _best_match(name, profiles) if name else None
    if not match:
        return f"I don't know a Wi-Fi network called {name}." if name else "Which Wi-Fi network?"
    code, out = _run(["netsh", "wlan", "connect", f"name={match}"])
    if code == 0 and "success" in out.lower():
        return f"Connecting to {match}."
    return f"I couldn't connect to {match}."


# --------------------------------------------------------------------------
# Bluetooth  (bthprops.cpl via ctypes - no extra packages)
# --------------------------------------------------------------------------
class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wintypes.WORD) for n in
                ("year", "month", "dow", "day", "hour", "minute", "second", "ms")]


class _BTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("Address", ctypes.c_ulonglong),
                ("ulClassofDevice", wintypes.ULONG), ("fConnected", wintypes.BOOL),
                ("fRemembered", wintypes.BOOL), ("fAuthenticated", wintypes.BOOL),
                ("stLastSeen", _SYSTEMTIME), ("stLastUsed", _SYSTEMTIME),
                ("szName", wintypes.WCHAR * 248)]


class _BTH_SEARCH(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("fReturnAuthenticated", wintypes.BOOL),
                ("fReturnRemembered", wintypes.BOOL), ("fReturnUnknown", wintypes.BOOL),
                ("fReturnConnected", wintypes.BOOL), ("fIssueInquiry", wintypes.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", wintypes.HANDLE)]


def _guid(s):
    import uuid
    g = uuid.UUID(s)
    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                    ("d4", ctypes.c_ubyte * 8)]
    return GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))


# audio sink (headphones / speakers), headset, hands-free
_BT_SERVICES = ["0000110B-0000-1000-8000-00805F9B34FB",
                "00001108-0000-1000-8000-00805F9B34FB",
                "0000111E-0000-1000-8000-00805F9B34FB"]


def _bt_devices():
    """Paired Bluetooth devices as {name: device_info}."""
    lib = ctypes.WinDLL("bthprops.cpl")
    lib.BluetoothFindFirstDevice.restype = wintypes.HANDLE
    lib.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(_BTH_SEARCH), ctypes.POINTER(_BTH_DEVICE_INFO)]
    lib.BluetoothFindNextDevice.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BTH_DEVICE_INFO)]
    lib.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]

    p = _BTH_SEARCH()
    p.dwSize = ctypes.sizeof(p)
    p.fReturnAuthenticated = p.fReturnRemembered = p.fReturnConnected = True
    p.fReturnUnknown = p.fIssueInquiry = False
    info = _BTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(info)

    found = {}
    h = lib.BluetoothFindFirstDevice(ctypes.byref(p), ctypes.byref(info))
    if not h:
        return found
    try:
        while True:
            copy = _BTH_DEVICE_INFO.from_buffer_copy(info)
            found[copy.szName] = copy
            if not lib.BluetoothFindNextDevice(h, ctypes.byref(info)):
                break
    finally:
        lib.BluetoothFindDeviceClose(h)
    return found


def _bt_set(info, enable):
    lib = ctypes.WinDLL("bthprops.cpl")
    lib.BluetoothSetServiceState.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BTH_DEVICE_INFO),
                                             ctypes.c_void_p, wintypes.DWORD]
    ok = False
    for s in _BT_SERVICES:
        g = _guid(s)
        if enable:
            lib.BluetoothSetServiceState(None, ctypes.byref(info), ctypes.byref(g), 0)   # reset...
        if lib.BluetoothSetServiceState(None, ctypes.byref(info), ctypes.byref(g), 1 if enable else 0) == 0:
            ok = True
    return ok


def _bt_action(name, connect, say):
    try:
        devices = _bt_devices()
    except Exception as e:
        print("Bluetooth error:", e)
        return "Sorry, I can't reach Bluetooth."
    if not devices:
        return "I don't see any paired Bluetooth devices."
    match = _best_match(name, list(devices)) if name else None
    if not name and connect and _radio("Bluetooth", "Query") == "Off":
        return _radio_switch("Bluetooth", "Bluetooth", True)
    if not match:
        return f"I couldn't find a paired Bluetooth device called {name}." if name else "Which Bluetooth device?"
    say(f"{'Connecting to' if connect else 'Disconnecting'} {match}.")
    if _bt_set(devices[match], connect):
        return f"{'Connected to' if connect else 'Disconnected from'} {match}."
    return f"I couldn't {'connect to' if connect else 'disconnect from'} {match}. Is it switched on and nearby?"


# --------------------------------------------------------------------------
# radios on / off  (Windows.Devices.Radios via PowerShell - no extra packages)
# --------------------------------------------------------------------------
_PS_RADIO = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$acc = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ($acc -ne 'Allowed') { Write-Output "DENIED"; exit }
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object { $_.Kind -eq '__KIND__' } | Select-Object -First 1
if (-not $r) { Write-Output "NONE"; exit }
if ('__STATE__' -ne 'Query') {
    $res = Await ($r.SetStateAsync('__STATE__')) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($res -ne 'Allowed') { Write-Output "DENIED"; exit }
}
Write-Output ("STATE=" + $r.State)
"""


def _radio(kind, state):
    """kind: 'WiFi' | 'Bluetooth'; state: 'On' | 'Off' | 'Query'. Returns 'On'/'Off' or 'NONE'/'DENIED'/'ERROR'."""
    script = _PS_RADIO.replace("__KIND__", kind).replace("__STATE__", state)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, errors="replace", timeout=30,
                           creationflags=0x08000000)
    except Exception as e:
        print("Radio error:", e)
        return "ERROR"
    out = r.stdout.strip()
    if "STATE=" in out:
        return out.split("STATE=")[-1].strip()
    print("Radio script output:", out, r.stderr.strip()[:300])
    return out or "ERROR"


def _radio_switch(kind, label, on):
    res = _radio(kind, "On" if on else "Off")
    if res == res.capitalize() and res in ("On", "Off"):
        return f"{label} is now {res.lower()}."
    if res == "NONE":
        return f"I can't find the {label} adapter. It may be switched off in Windows or by a hardware key."
    if res == "DENIED":
        return f"Windows won't let me change {label}. Check Settings, Privacy, Radios."
    return f"Sorry, I couldn't switch {label} {'on' if on else 'off'}."


# --------------------------------------------------------------------------
# offline speech: phrases the small offline model should expect
# --------------------------------------------------------------------------
_NUM_WORDS = {5: "five", 10: "ten", 15: "fifteen", 20: "twenty", 25: "twenty five", 30: "thirty",
              40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 75: "seventy five",
              80: "eighty", 90: "ninety", 100: "hundred"}


def offline_phrases():
    """Everything Veera understands here, as whole phrases. Handing these to the offline
    recogniser makes it far more accurate than open dictation (it can only pick from them)."""
    def spoken(name):                    # the model only knows plain words: drop digits / symbols
        return " ".join(re.findall(r"[a-z]+", name.lower()))

    ph = []
    for what in ("wifi", "wi fi", "bluetooth"):
        for pre in ("turn on", "turn off", "turn on the", "turn off the", "switch on", "switch off"):
            ph.append(f"{pre} {what}")
    for w in ("mute", "what is the volume"):
        ph.append(w)
    for n in _NUM_WORDS.values():
        for verb in ("reduce", "lower", "decrease", "increase", "raise"):
            ph += [f"{verb} the volume by {n}", f"{verb} the volume by {n} percent",
                   f"{verb} volume by {n} percent"]
        ph += [f"set volume to {n}", f"set the volume to {n} percent"]
    ph += ["reduce the brightness", "increase the brightness", "dim the screen", "make it brighter",
           "what is the brightness", "reduce the volume", "increase the volume", "volume up", "volume down",
           "goodbye", "stop", "quit", "exit", "thank you", "what is the time", "what is the date",
           "take a screenshot", "yes", "no", "disconnect wifi", "disconnect bluetooth"]
    try:
        for name in _bt_devices():
            n = spoken(name)
            if n:
                ph += [f"connect bluetooth to {n}", f"connect to {n}",
                       f"disconnect {n}", f"disconnect bluetooth from {n}"]
    except Exception:
        pass
    try:
        for name in _wifi_profiles():
            n = spoken(name)
            if n:
                ph += [f"connect wifi to {n}", f"connect to {n}"]
    except Exception:
        pass
    return ph


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# memory of the conversation, so follow-ups work:  "lower the volume by 30"  ->  "make it ten"
# --------------------------------------------------------------------------
_ctx = {"topic": None, "device": "", "up": False, "t": 0.0}
_CTX_SECONDS = 180                       # how long a follow-up like "make it ten" still makes sense
_NUMBER_WORDS = set(_ONES) | set(_TENS) | {"hundred", "percent", "and"}


def _remember(c):
    """Note what the last handled command was about."""
    _ctx["t"] = time.time()
    if re.search(r"\b(volume|sound|audio|louder|quieter|mute|brightness|brighter|dimmer|dim)\b", c):
        _ctx["topic"] = "brightness" if re.search(r"\b(brightness|brighter|dimmer|dim)\b", c) else "volume"
        if re.search(r"\b(increase|raise|up|louder|more|boost|higher|brighter)\b", c):
            _ctx["up"] = True
        elif re.search(r"\b(reduce|lower|decrease|down|quieter|less|drop)\b", c):
            _ctx["up"] = False
    elif "wifi" in c:
        _ctx["topic"] = "wifi"
    elif re.search(r"\b(bluetooth|blue tooth)\b", c):
        _ctx["topic"] = "bluetooth"
        _ctx["device"] = _target_name(c) or _ctx["device"]


def _followup(c):
    """Rewrite a short follow-up ('make it ten', 'a bit more', 'turn it off') into a full command
    using what we were just talking about. Anything else comes back unchanged."""
    topic = _ctx["topic"]
    if not topic or time.time() - _ctx["t"] > _CTX_SECONDS:
        return c
    c = re.sub(r"^(?:(?:ok|okay|and|now|then|can you|could you|please)\s+)+", "", c.strip())

    if topic in ("volume", "brightness"):
        m = re.fullmatch(r"(?:make|set|put|change|take)\s+(?:it|that|the volume|the sound|the brightness)\s+(?:(?:to|at)\s+)?(.+)", c)
        arg = m.group(1) if m else None
        if arg is None:
            m = re.fullmatch(r"(?:(?:to|at)\s+)?([a-z0-9 ]+?)(?:\s+percent)?", c)
            if m and all(w.isdigit() or w in _NUMBER_WORDS for w in m.group(1).split()):
                arg = m.group(1)
        if arg is not None and _number_in(arg) is not None:
            return f"set {topic} to {_number_in(arg)}"

        step = re.fullmatch(r"(?:(?:a\s+)?(?:bit|little)\s+)?(more|less|higher|lower|louder|quieter|brighter|dimmer|again)", c)
        if step:
            word = step.group(1)
            if word in ("more", "higher", "louder", "brighter"):
                up = True
            elif word in ("less", "lower", "quieter", "dimmer"):
                up = False
            else:
                up = _ctx["up"]
            return f"{'increase' if up else 'reduce'} {topic} by {5 if 'bit' in c or 'little' in c else 10}"
        return c

    # wifi / bluetooth: "turn it off", "disconnect it", "connect that"
    if (re.search(r"\b(it|that)\b", c) and re.search(r"\b(on|off|connect|disconnect|reconnect)\b", c)
            and not re.search(r"\b(wifi|bluetooth|volume)\b", c)):
        if topic == "bluetooth" and _ctx["device"]:
            # "turn it off" after talking about a device means that device, not the whole adapter
            if re.search(r"\b(off|disconnect)\b", c):
                return f"disconnect bluetooth {_ctx['device']}"
            return f"connect bluetooth {_ctx['device']}"
        what = topic if topic == "wifi" else "bluetooth"
        return re.sub(r"\b(it|that)\b", what, c)
    return c


def try_handle(cmd, say=lambda t: None, confirm=lambda: True):
    """Return the reply to speak, or None if this isn't a volume / Wi-Fi / Bluetooth command."""
    c = _followup(_norm(cmd))
    out = _handle(c, say, confirm)
    if out:
        _remember(c)
    return out


def _handle(cmd, say, confirm):
    """Return the reply to speak, or None if this isn't a volume / Wi-Fi / Bluetooth command."""
    c = _norm(cmd)

    def go(action):
        """Run a state-changing action - but only after confirm() says the command was heard right."""
        return action() if confirm() else "Okay, cancelled."

    if not re.search(r"\b(play|search|download|write|edit|read)\b", c):
        reply = _brightness(c) or _volume(c)
        if reply:
            return reply

    # "off wifi", "switch bluetooth on" ... - the word "turn" is often lost when heard offline
    radio = re.search(r"\b(on|off|enable|disable)\b", c)
    if (radio and ("wifi" in c or "bluetooth" in c or "blue tooth" in c)
            and not re.search(r"\b(connect|disconnect|reconnect|join|pair)\b", c)):
        on = radio.group(1) in ("on", "enable")
        if "wifi" in c:
            return go(lambda: _radio_switch("WiFi", "Wi-Fi", on))
        return go(lambda: _radio_switch("Bluetooth", "Bluetooth", on))

    disc = re.search(r"\b(disconnect)\b", c)
    conn = re.search(r"\b(connect|reconnect|join|pair)\b", c)
    if not (conn or disc):
        return None

    name = _target_name(c)
    if "wifi" in c:
        return go(_wifi_disconnect if disc else (lambda: _wifi_connect(name, say)))
    if "bluetooth" in c or "blue tooth" in c:
        return go(lambda: _bt_action(name, connect=not disc, say=say))

    # no keyword ("connect boat rockerz"): decide by which list knows the name
    if name:
        try:
            bt = _best_match(name, list(_bt_devices()))
        except Exception:
            bt = None
        if bt:
            return go(lambda: _bt_action(name, connect=not disc, say=say))
        if _best_match(name, _wifi_profiles()):
            return go(_wifi_disconnect if disc else (lambda: _wifi_connect(name, say)))
    return None
