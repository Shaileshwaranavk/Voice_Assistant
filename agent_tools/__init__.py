"""
The tool layer the agent works with. Each tool is a small, plain Python function.

    @tool("files")                       - safe: just runs
    @tool("files", risk="destructive")   - the agent must get a spoken "yes" first
    @tool("desktop", risk=lambda a: ...) - risk decided from the arguments

Groups (one module each):  desktop · files · apps · browser · documents · media · system · comms
Docstrings are what the model reads to decide when to use a tool, so keep them short and exact.
"""

import importlib

TOOLS = {}          # name -> function
CTX = {}            # what we were just working on: last_file, last_folder, last_app, last_url ...

RISKS = ("safe", "confirm", "destructive", "external")


def tool(group, risk="safe"):
    """Register a function as an agent tool."""
    def register(fn):
        fn.group = group
        fn.risk = risk
        TOOLS[fn.__name__] = fn
        return fn
    return register


def remember(**kw):
    """Note what we just touched, so 'open it' / 'now summarise it' know what 'it' is."""
    CTX.update({k: v for k, v in kw.items() if v})


def risk_of(name, args):
    fn = TOOLS[name]
    r = fn.risk(args) if callable(fn.risk) else fn.risk
    return r if r in RISKS else "confirm"


def load_all():
    """Import every tool module (each registers its tools when imported)."""
    for mod in ("desktop", "ocr", "files", "apps", "browser", "documents", "media", "system_tools", "comms", "briefing"):
        try:
            importlib.import_module(f"{__name__}.{mod}")
        except Exception as e:                     # one broken module must not take the rest down
            print(f"[tools] {mod} failed to load: {e}")
    return TOOLS
