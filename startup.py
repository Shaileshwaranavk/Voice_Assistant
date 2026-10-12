"""Start Veera automatically when you sign in to Windows (a shortcut in your Startup folder - no admin needed).

    python startup.py on        python startup.py off        python startup.py status
or say "start Veera when Windows starts" / "stop Veera starting with Windows".
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Veera.lnk"


def _startup_dir():
    return os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")


def _link():
    return os.path.join(_startup_dir(), NAME)


def _pythonw():
    """pythonw.exe from the same environment (runs with no console window)."""
    d = os.path.dirname(sys.executable)
    w = os.path.join(d, "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def enabled():
    return os.path.exists(_link())


def enable():
    target, script = _pythonw(), os.path.join(HERE, "veera.py")
    ps = ("$w=New-Object -ComObject WScript.Shell;$l=$w.CreateShortcut('%s');$l.TargetPath='%s';"
          "$l.Arguments='\"%s\"';$l.WorkingDirectory='%s';$l.WindowStyle=7;$l.Save()"
          % (_link().replace("'", "''"), target.replace("'", "''"), script, HERE.replace("'", "''")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20,
                   creationflags=0x08000000)
    return "Done. I'll start with Windows from now on." if enabled() else "I couldn't set that up."


def disable():
    if enabled():
        os.remove(_link())
        return "Okay, I won't start with Windows any more."
    return "I wasn't set to start with Windows."


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    print({"on": enable, "off": disable}.get(cmd, lambda: "Starts with Windows: " + ("yes" if enabled() else "no"))())
