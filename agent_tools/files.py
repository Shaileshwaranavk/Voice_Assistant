"""FILES tools: search, create, open, copy, move, rename, delete, metadata."""

import ctypes
import datetime
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from ctypes import wintypes
from pathlib import Path

from send2trash import send2trash

from . import tool, remember

_KNOWN = {                      # Windows "known folder" ids, so a redirected (OneDrive) Desktop is found too
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
_SKIP_DIRS = {"node_modules", "appdata", "$recycle.bin", "venv", ".venv", "__pycache__", "site-packages"}


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


def known_folder(name):
    """Path of Desktop / Documents / Downloads / Pictures / Music / Videos on this PC."""
    name = name.lower()
    try:
        u = uuid.UUID(_KNOWN[name])
        g = _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
            return Path(out.value)
    except Exception:
        pass
    return Path.home() / name.capitalize()


def resolve(p):
    """'desktop/Projects', 'downloads', '~/x', or a full path -> an absolute Path."""
    p = (p or "").strip().strip('"')
    if not p:
        return None
    first, _, rest = p.replace("/", "\\").partition("\\")
    if first.lower() in _KNOWN:
        return known_folder(first.lower()) / rest if rest else known_folder(first.lower())
    path = Path(os.path.expandvars(os.path.expanduser(p)))
    return path if path.is_absolute() else Path.home() / path


def _info(path):
    st = path.stat()
    return {"path": str(path), "type": "folder" if path.is_dir() else "file",
            "size_kb": round(st.st_size / 1024, 1),
            "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}


def _search_roots(folder):
    if folder:
        r = resolve(folder)
        return [r] if r else []
    roots = [known_folder(n) for n in ("desktop", "documents", "downloads", "pictures")]
    for env in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]))
    return list(dict.fromkeys(r for r in roots if r.exists()))


@tool("files")
def find_files(name_contains: str = "", extension: str = "", folder: str = "",
               modified_within_days: int = 0, limit: int = 15) -> list:
    """Search for files / folders. name_contains: words that must all appear in the name.
    extension: like 'pdf'. folder: 'desktop', 'downloads', ... or a path (empty = Desktop, Documents,
    Downloads, Pictures, OneDrive). modified_within_days: 0 = any. Newest first."""
    words = name_contains.lower().split()
    ext = extension.lower().lstrip(".")
    cutoff = time.time() - modified_within_days * 86400 if modified_within_days else 0
    deadline = time.time() + 12
    hits = []

    def walk(d, depth):
        if depth > 6 or time.time() > deadline:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            name = e.name.lower()
            try:
                if e.is_dir(follow_symlinks=False):
                    if name not in _SKIP_DIRS and not name.startswith("."):
                        if words and all(w in name for w in words) and not ext:
                            hits.append((e.stat().st_mtime, Path(e.path)))
                        walk(e.path, depth + 1)
                elif (not ext or name.endswith("." + ext)) and all(w in name for w in words):
                    m = e.stat().st_mtime
                    if m >= cutoff:
                        hits.append((m, Path(e.path)))
            except OSError:
                continue

    for root in _search_roots(folder):
        walk(root, 0)
    hits.sort(key=lambda t: t[0], reverse=True)
    out = [_info(p) for _, p in hits[:limit]]
    if out:
        remember(last_file=out[0]["path"])
    return out or ["No matching files found."]


@tool("files")
def recent_files(folder: str = "downloads", hours: int = 24, limit: int = 10) -> list:
    """Files changed in the last N hours in a folder (default Downloads), newest first."""
    root = resolve(folder)
    if not root or not root.exists():
        return ["That folder doesn't exist."]
    cutoff = time.time() - hours * 3600
    items = [p for p in root.iterdir() if p.is_file() and p.stat().st_mtime >= cutoff]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = [_info(p) for p in items[:limit]]
    if out:
        remember(last_file=out[0]["path"], last_folder=str(root))
    return out or [f"Nothing new in {root.name} in the last {hours} hours."]


@tool("files")
def list_folder(path: str, limit: int = 40) -> list:
    """List what is inside a folder."""
    p = resolve(path)
    if not p or not p.is_dir():
        return ["That folder doesn't exist."]
    remember(last_folder=str(p))
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    return [("[folder] " if i.is_dir() else "") + i.name for i in items[:limit]]


@tool("files")
def file_info(path: str) -> dict:
    """Size, type and modified date of a file or folder."""
    p = resolve(path)
    if not p or not p.exists():
        return {"error": "It doesn't exist."}
    return _info(p)


@tool("files")
def create_folder(path: str) -> str:
    """Create a folder (and any missing parents). path: 'desktop/Projects' or a full path."""
    p = resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    remember(last_folder=str(p))
    return f"Folder ready: {p}" if p.is_dir() else "Couldn't create it."


@tool("files", risk=lambda a: "confirm" if a.get("overwrite") else "safe")
def write_text_file(path: str, content: str, append: bool = False, overwrite: bool = False) -> str:
    """Create a text file (or append to one). Refuses to replace an existing file unless overwrite=True."""
    p = resolve(path)
    if p.exists() and not (append or overwrite):
        return f"{p.name} already exists. Use append, or ask the user before overwriting."
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    remember(last_file=str(p))
    return f"Saved {p}"


@tool("files")
def open_path(path: str) -> str:
    """Open a file with its default app, or open a folder in File Explorer."""
    p = resolve(path)
    if not p or not p.exists():
        return "I can't find that."
    os.startfile(str(p))
    remember(last_file=str(p) if p.is_file() else None, last_folder=str(p) if p.is_dir() else None)
    return f"Opened {p.name}."


@tool("files")
def show_in_explorer(path: str) -> str:
    """Open File Explorer with this file selected."""
    p = resolve(path)
    if not p or not p.exists():
        return "I can't find that."
    subprocess.Popen(["explorer", "/select,", str(p)])
    return "Shown in File Explorer."


def _dest(src, dst):
    d = resolve(dst)
    return d / src.name if d.is_dir() else d


@tool("files")
def copy_path(source: str, destination: str) -> str:
    """Copy a file or folder to a folder (or to a new name). Never overwrites."""
    s, d = resolve(source), None
    if not s or not s.exists():
        return "The source doesn't exist."
    d = _dest(s, destination)
    if d.exists():
        return f"{d.name} already exists there."
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s, d) if s.is_dir() else shutil.copy2(s, d)
    remember(last_file=str(d) if d.is_file() else None)
    return f"Copied to {d}"


@tool("files")
def move_path(source: str, destination: str) -> str:
    """Move a file or folder into a folder (or to a new path). Never overwrites."""
    s = resolve(source)
    if not s or not s.exists():
        return "The source doesn't exist."
    d = _dest(s, destination)
    if d.exists():
        return f"{d.name} already exists there, so I didn't move it."
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    remember(last_file=str(d) if d.is_file() else None)
    return f"Moved to {d}" if d.exists() else "The move didn't work."


@tool("files")
def rename_path(path: str, new_name: str) -> str:
    """Rename a file or folder (new_name is just the name, not a path)."""
    p = resolve(path)
    if not p or not p.exists():
        return "It doesn't exist."
    n = p.with_name(new_name)
    if n.exists():
        return f"{new_name} already exists."
    p.rename(n)
    remember(last_file=str(n) if n.is_file() else None)
    return f"Renamed to {new_name}."


@tool("files")
def duplicate_path(path: str) -> str:
    """Make a copy of a file next to the original ('name - Copy')."""
    p = resolve(path)
    if not p or not p.exists():
        return "It doesn't exist."
    n = p.with_name(f"{p.stem} - Copy{p.suffix}")
    shutil.copytree(p, n) if p.is_dir() else shutil.copy2(p, n)
    return f"Created {n.name}."


@tool("files", risk="destructive")
def delete_path(path: str) -> str:
    """Delete a file or folder by sending it to the Recycle Bin (it can be restored)."""
    p = resolve(path)
    if not p or not p.exists():
        return "It doesn't exist."
    send2trash(str(p))
    return f"Moved {p.name} to the Recycle Bin." if not p.exists() else "I couldn't delete it."


@tool("files")
def compress_path(path: str, zip_name: str = "") -> str:
    """Zip a file or folder. zip_name optional (saved next to the original)."""
    p = resolve(path)
    if not p or not p.exists():
        return "It doesn't exist."
    z = p.with_name(zip_name if zip_name.endswith(".zip") else (zip_name or p.stem) + ".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        if p.is_dir():
            for f in p.rglob("*"):
                zf.write(f, f.relative_to(p.parent))
        else:
            zf.write(p, p.name)
    remember(last_file=str(z))
    return f"Created {z}"


@tool("files")
def extract_zip(zip_path: str, destination: str = "") -> str:
    """Extract a .zip file (default: a folder next to it with the same name)."""
    z = resolve(zip_path)
    if not z or not z.exists():
        return "That zip doesn't exist."
    d = resolve(destination) if destination else z.with_suffix("")
    with zipfile.ZipFile(z) as zf:
        for m in zf.namelist():                       # refuse paths that try to escape the folder
            if not (d / m).resolve().is_relative_to(d.resolve()):
                return "That zip contains unsafe paths, so I didn't extract it."
        zf.extractall(d)
    remember(last_folder=str(d))
    return f"Extracted to {d}"


@tool("files")
def create_shortcut(target: str, shortcut_path: str) -> str:
    """Create a Windows shortcut (.lnk) to a file / folder / program."""
    t, s = resolve(target), resolve(shortcut_path)
    if s.suffix.lower() != ".lnk":
        s = s.with_suffix(".lnk")
    ps = ("$w=New-Object -ComObject WScript.Shell;$l=$w.CreateShortcut('%s');$l.TargetPath='%s';$l.Save()"
          % (str(s).replace("'", "''"), str(t).replace("'", "''")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=15,
                   creationflags=0x08000000)
    return f"Created {s}" if s.exists() else "I couldn't create the shortcut."


@tool("files")
def last_file_used() -> str:
    """The file we most recently found, opened or saved (what 'it' / 'that file' usually means)."""
    from . import CTX
    return CTX.get("last_file", "No file yet.")
