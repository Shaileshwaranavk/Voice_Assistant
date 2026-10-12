"""
Veera's brain - turns what you said into actions, entirely on this PC.

    clean the sentence -> split chained commands -> find the first skill whose pattern fits -> run its tools

No cloud model decides anything here. (Gemini is only used, in llm.py, to write a summary when you ask for one.)

Adding a command = adding one decorated function:

    @skill("pattern one", "pattern two")           # regexes, tried in the order the skills are written
    def my_skill(b, m, text):                      # b = the Brain, m = the regex match, text = what was said
        return "what to say back"                  # or None = "not mine", the next skill gets a turn
"""

import os
import re
import time

import agent_tools
import llm
import system_control
from agent_tools import TOOLS, CTX, remember, risk_of
from agent_tools.desktop import foreground
from agent_tools.files import resolve

agent_tools.load_all()

I = re.IGNORECASE
KNOWN_FOLDERS = ("desktop", "documents", "downloads", "pictures", "music", "videos")
FOLDER_WORDS = {"document": "documents", "documents": "documents", "download": "downloads", "downloads": "downloads",
                "desktop": "desktop", "picture": "pictures", "pictures": "pictures", "photos": "pictures",
                "music": "music", "video": "videos", "videos": "videos"}
TYPE_EXT = {"pdf": ["pdf"], "word": ["docx", "doc"], "doc": ["docx", "doc"], "docx": ["docx"],
            "excel": ["xlsx", "xls", "csv"], "spreadsheet": ["xlsx", "xls", "csv"], "sheet": ["xlsx", "xls"],
            "csv": ["csv"], "powerpoint": ["pptx", "ppt"], "presentation": ["pptx", "ppt"], "slides": ["pptx"],
            "ppt": ["pptx", "ppt"], "text": ["txt"], "notepad": ["txt"], "image": ["png", "jpg", "jpeg", "webp", "bmp"],
            "picture": ["png", "jpg", "jpeg", "webp", "bmp"], "photo": ["png", "jpg", "jpeg", "webp"],
            "screenshot": ["png"], "zip": ["zip"], "video": ["mp4", "mkv", "avi", "mov"], "song": ["mp3", "wav", "m4a"],
            "audio": ["mp3", "wav", "m4a"]}
SITES = {"gmail": "mail.google.com", "google": "google.com", "youtube": "youtube.com", "github": "github.com",
         "linkedin": "linkedin.com", "facebook": "facebook.com", "twitter": "x.com", "reddit": "reddit.com",
         "amazon": "amazon.in", "wikipedia": "wikipedia.org", "netflix": "netflix.com", "chatgpt": "chatgpt.com",
         "stack overflow": "stackoverflow.com", "maps": "maps.google.com", "google maps": "maps.google.com",
         "google drive": "drive.google.com", "google docs": "docs.google.com", "google sheets": "sheets.google.com",
         "flipkart": "flipkart.com", "hotstar": "hotstar.com", "prime video": "primevideo.com"}
BROWSER_WORDS = {"chrome": "chrome", "google chrome": "chrome", "edge": "edge", "microsoft edge": "edge",
                 "brave": "brave", "firefox": "firefox"}
YES = re.compile(r"\b(yes|yeah|yep|yup|sure|go ahead|do it|confirm|correct|ok|okay|please do)\b", I)
NO = re.compile(r"\b(no|nope|don't|dont|stop|cancel|wait|never mind)\b", I)
ORDINALS = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1, "third": 2, "3": 2, "three": 2,
            "fourth": 3, "4": 3, "four": 3, "last": -1, "latest": 0, "newest": 0}
FILLER = re.compile(r"^(?:\s*(?:hey|hi|ok|okay|veera|please|kindly|can you|could you|would you|will you|"
                    r"can u|can i|could i|may i|and you|you|i want you to|i want to|i'd like you to|i would like you to|"
                    r"i need you to|go ahead and|just|now|then|sorry|actually|also|and|so|well|um|uh)\b[\s,.]*)+", I)
TRAILING = re.compile(r"[\s,.!?]*(?:\bfor me\b|\bplease\b|\bnow\b|\bthanks\b)?[\s,.!?]*$", I)
START_VERBS = (r"open|launch|start|go to|go back|visit|navigate|search|google|play|create|make|find|close|minimi[sz]e|"
               r"maximi[sz]e|switch|type|press|copy|paste|read|summari[sz]e|download|set|turn|increase|reduce|decrease|"
               r"lower|raise|mute|unmute|click|scroll|save|rename|move|delete|remove|compare|check|show|bring|take|lock|"
               r"restart|refresh|reload|zoom|pause|resume|skip|next|previous|connect|disconnect|insert|select|cut|undo|"
               r"redo|extract|unzip|zip|compress|empty|cancel|what|who|when|how|tell|put|send|look|list|get|exit|quit")
CHAIN = re.compile(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+|\s+after\s+that\s+|"
                   r"\s+afterwards\s+)(?=(?:" + START_VERBS + r")\b)", I)


class Fail(Exception):
    """A skill understood the request but couldn't do it - the message is spoken."""


SKILLS = []


def skill(*patterns, prio=0):
    """prio: lower runs earlier (default 0, then in the order written). Use -1 for skills that must beat
    the general 'open ...' / 'find ...' ones, e.g. anything about email."""
    regs = [re.compile(p, I) for p in patterns]

    def register(fn):
        SKILLS.append((prio, len(SKILLS), fn.__name__, regs, fn))
        return fn
    return register


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def clean(text):
    t = FILLER.sub("", text.strip())
    t = TRAILING.sub("", t)
    return t.strip()


def split_clauses(text):
    """'open chrome and go to gmail then search for invoices' -> three commands."""
    t = clean(text)
    keep = re.match(r"^(type\b.+?\band(?:\s+then)?\s+press\s+(?:the\s+)?enter)\b(.*)$", t, I)   # keep 'type X and press enter' whole
    if keep:
        return [keep.group(1)] + ([clean(x) for x in CHAIN.split(keep.group(2).lstrip(" ,"))] if keep.group(2).strip() else [])
    if re.match(r"^(?:type|write|dictate)\b", t, I):
        return [t]                                                  # dictated text is never split
    return [c for c in (clean(x) for x in CHAIN.split(t)) if c]


def spoken_url(s):
    """'gmail dot com' / 'youtube' / 'github.com/foo' -> a web address (or None)."""
    t = s.strip().lower()
    t = re.sub(r"\s+dot\s+", ".", t)
    t = re.sub(r"\s+slash\s+", "/", t)
    t = re.sub(r"^(?:the\s+)?(?:website|site|page|web page|url|link|address)\s+", "", t)
    if re.match(r"^https?://", t):
        return t
    if t in SITES:
        return "https://" + SITES[t]
    t2 = t.replace(" ", "")
    if re.match(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|in|io|co|edu|gov|ai|app|dev|me|uk|us|info)(?:/\S*)?$", t2):
        return "https://" + t2
    return None


def modifiers(text):
    """Pull '... in chrome' and '... with my work profile' out of a sentence -> (text, browser, profile)."""
    browser = profile = ""
    m = re.search(r"\b(?:in|on|using|with|via)\s+(?:my\s+|the\s+)?(" + "|".join(sorted(BROWSER_WORDS, key=len, reverse=True)) +
                  r")\b(?!\s+profile)", text, I)
    if m:
        browser = BROWSER_WORDS[m.group(1).lower()]
        text = (text[:m.start()] + text[m.end():]).strip()
    m = re.search(r"\b(?:with|using|in|on|from|under)\s+(?:my\s+|the\s+)?([\w@.\- ]+?)\s+(?:chrome\s+|brave\s+|edge\s+|browser\s+)?profile\b", text, I)
    if m:
        profile = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
    return text, browser, profile


def key_combo(spoken):
    """'control shift t' / 'ctrl plus s' / 'alt left' -> 'ctrl+shift+t'"""
    t = spoken.lower().strip()
    t = re.sub(r"\b(plus|and)\b", " ", t)
    t = re.sub(r"\bcontrol\b", "ctrl", t)
    t = re.sub(r"\bwindows key\b|\bwindows\b|\bstart key\b", "win", t)
    t = re.sub(r"\bescape\b|\besc key\b", "esc", t)
    t = re.sub(r"\breturn\b", "enter", t)
    t = re.sub(r"\bpage (up|down)\b", r"page\1", t)
    t = re.sub(r"\b(up|down|left|right) arrow\b", r"\1", t)
    t = re.sub(r"\bspace ?bar\b", "space", t)
    t = re.sub(r"\bdelete key\b", "delete", t)
    parts = [p for p in re.split(r"[\s+\-]+", t) if p]
    return "+".join(parts)


def short(s, n=300):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."


# --------------------------------------------------------------------------
# the brain
# --------------------------------------------------------------------------
class Brain:
    READ_CHUNK = 1400

    def __init__(self, assistant):
        self.a = assistant
        self.rest = ""                                   # unread remainder of something we read aloud

    # ---- talking to the user ------------------------------------------------
    def ask(self, prompt, source, timeout=8, limit=8):
        return self.a.ask(source, prompt, timeout=timeout, limit=limit)

    def confirm(self, description, source, risk="confirm"):
        warn = {"destructive": "That can't easily be undone. ", "external": "That reaches other people. "}.get(risk, "")
        ans = self.ask(f"{warn}I'm about to {description}. Shall I go ahead? Say yes or no.", source, timeout=7, limit=4)
        return bool(ans and YES.search(ans) and not NO.search(ans))

    def choose(self, options, source, what="one"):
        """Several candidates: let the user pick by number, order or part of the name."""
        if len(options) == 1:
            return options[0]
        names = [os.path.basename(str(o)) for o in options[:4]]
        spoken = "; ".join(f"{i + 1}, {n}" for i, n in enumerate(names))
        ans = self.ask(f"I found {len(options)}: {spoken}. Which {what}?", source, timeout=9, limit=6)
        if not ans:
            raise Fail("I didn't hear which one, so I didn't do anything.")
        a = ans.lower()
        for word, idx in ORDINALS.items():
            if re.search(rf"\b{word}\b", a) and -len(names) <= idx < len(names):
                return options[idx]
        for o, n in zip(options, names):
            if any(w in n.lower() for w in re.findall(r"[a-z0-9]{3,}", a)):
                return o
        raise Fail("I couldn't tell which one you meant, so I didn't do anything.")

    # ---- calling tools (with the safety gate) ----------------------------------
    def call(self, tool_name, source=None, /, **kw):
        """Run a tool; if it is risky, get a spoken yes first. (positional-only so tools may have their own
        'name' / 'source' arguments.)"""
        fn = TOOLS[tool_name]
        risk = risk_of(tool_name, kw)
        if risk != "safe" and not self.confirm(self.describe(tool_name, kw), source, risk):
            raise Fail("Okay, I didn't do it.")
        return fn(**kw)

    @staticmethod
    def describe(name, kw):
        base = lambda p: os.path.basename(str(p).rstrip("\\/")) or str(p)
        if name == "delete_path":
            return f"delete {base(kw.get('path', ''))} to the Recycle Bin"
        if name == "type_text" and kw.get("press_enter"):
            return f"type '{str(kw.get('text', ''))[:80]}' and press Enter, which may send it"
        if name in ("close_app", "close_window"):
            return f"close {kw.get('name') or 'the front window'}"
        if name == "click_control":
            return f"click {kw.get('text', 'that')}"
        if name == "system_command":
            return kw.get("text", "change that setting")
        return name.replace("_", " ") + (" " + ", ".join(str(v)[:40] for v in kw.values() if v not in ("", None, False)) if kw else "")

    # ---- resolving "it", "the latest pdf", "invoice" ------------------------------
    def kind_exts(self, words):
        for w in re.findall(r"[a-z]+", words.lower()):
            if w in TYPE_EXT:
                return TYPE_EXT[w]
        return []

    def folder(self, spec, default="desktop"):
        s = (spec or "").lower().strip()
        s = re.sub(r"\b(my|the|folder|directory|dir|in|on|inside|under|at|to|into)\b", " ", s).strip()
        if not s:
            return default
        if s in ("that", "it", "there", "this", "that one", "same"):
            if CTX.get("last_folder"):
                return CTX["last_folder"]
            raise Fail("I'm not sure which folder you mean.")
        if s in FOLDER_WORDS:
            return FOLDER_WORDS[s]
        hits = [h for h in TOOLS["find_files"](name_contains=s, folder="", limit=6) if isinstance(h, dict) and h["type"] == "folder"]
        if hits:
            return hits[0]["path"]
        raise Fail(f"I can't find a folder called {s}.")

    def file(self, spec, source=None, strict=False, folder=""):
        """Turn a spoken file reference into a real path."""
        s = (spec or "").lower().strip()
        s = re.sub(r"^(?:the|my|a|an)\s+", "", s)
        if s in ("it", "that", "this", "that one", "this one", "the file", "file", "that file", "this file",
                 "the document", "document", "that document", "this document", ""):
            last = CTX.get("last_file")
            if last and os.path.exists(last):
                return last
            raise Fail("I'm not sure which file you mean.")
        demo = re.match(r"^(?:this|that|the|my|the same|that same)\s+(?P<k>image|picture|photo|pic|pdf|document|doc|file|spreadsheet|"
                        r"presentation|word document|excel file|screenshot|download(?:ed file)?)$", s)
        if demo:
            exts = self.kind_exts(demo.group("k"))
            last = CTX.get("last_file")
            if last and os.path.exists(last) and (not exts or last.lower().rsplit(".", 1)[-1] in exts):
                return last
            if exts:
                return self.file("latest " + demo.group("k"), source)
            if last and os.path.exists(last):
                return last
            raise Fail("I'm not sure which file you mean.")
        latest = re.match(r"^(?:latest|newest|last|most recent|recent)\s+(?:downloaded\s+|downloads?\s+)?(?P<w>.*)$", s)
        if latest:
            exts = self.kind_exts(latest.group("w"))
            root = folder or "downloads"
            if "download" in s or not folder:
                root = "downloads"
            if exts:
                paths = [TOOLS["latest_document"](folder=root, extension=e) for e in exts]
                paths = [p for p in paths if os.path.exists(p)]
                if paths:
                    best = max(paths, key=os.path.getmtime)
                    remember(last_file=best)
                    return best
            else:
                p = TOOLS["latest_document"](folder=root)
                if os.path.exists(p):
                    return p
            raise Fail("I couldn't find a recent file like that.")
        exts = self.kind_exts(s)
        words = " ".join(w for w in re.findall(r"[^\s]+", s) if w not in TYPE_EXT and w not in ("file", "files", "document", "documents", "called", "named"))
        hits = []
        if exts:
            for e in exts:
                hits += [h for h in TOOLS["find_files"](name_contains=words, extension=e, folder=folder, limit=8) if isinstance(h, dict)]
        else:
            hits = [h for h in TOOLS["find_files"](name_contains=words, folder=folder, limit=8) if isinstance(h, dict)]
        hits = [h for h in hits if not os.path.basename(h["path"]).startswith("~$")]
        hits.sort(key=lambda h: h["modified"], reverse=True)
        if not hits:
            raise Fail(f"I couldn't find a file called {words or spec}.")
        if len(hits) > 1 and strict:
            return self.choose([h["path"] for h in hits], source, "file")
        remember(last_file=hits[0]["path"] if hits[0]["type"] == "file" else None)
        return hits[0]["path"]

    # ---- reading aloud ---------------------------------------------------------------
    def read_out(self, text):
        text = text.strip()
        if len(text) <= self.READ_CHUNK:
            self.rest = ""
            return text
        cut = text.rfind(". ", 0, self.READ_CHUNK)
        cut = cut if cut > 400 else self.READ_CHUNK
        self.rest = text[cut:].strip()
        return text[:cut + 1].strip() + " That's the first part. Say read more to continue."

    # ---- running a sentence --------------------------------------------------------------
    def run(self, text, source, legacy=None):
        """Run one spoken request (possibly several chained commands).
        Returns the text to say, '' if everything already spoke for itself, or None if nothing matched."""
        clauses = split_clauses(text)
        if not clauses:
            return None
        replies, handled_any = [], False
        for i, clause in enumerate(clauses):
            if self.a.state.toggle_request and i:               # a click on Veera = stop the chain
                return "Stopped."
            try:
                out = self.run_one(clause, source)
            except Fail as e:
                return str(e) if not handled_any else f"{'; '.join(replies[-1:])} But then: {e}"
            except Exception as e:
                print(f"[brain] {type(e).__name__}: {e}")
                return "Something went wrong while doing that."
            if out is None and legacy is not None and legacy(clause, source):
                out = ""                                           # a built-in command ran and spoke itself
            if out is None:
                if len(clauses) == 1:
                    return None
                return f"{'Done up to there. ' if handled_any else ''}I didn't understand '{short(clause, 60)}'."
            handled_any = True
            if out:
                replies.append(out)
        if not replies:
            return ""
        return replies[-1] if len(clauses) == 1 else ("Done. " + replies[-1] if len(replies[-1]) < 120 else replies[-1])

    @staticmethod
    def speakable(out):
        """Status messages say 'Copied to C:\\Users\\...\\file.docx' - say just 'file.docx'."""
        if isinstance(out, str) and len(out) < 400:
            return re.sub(r"[A-Za-z]:\\[^\s,;|]*", lambda m: os.path.basename(m.group().rstrip("\\.")) or m.group(), out)
        return out

    def run_one(self, clause, source):
        for _, _, name, regs, fn in sorted(SKILLS, key=lambda s: (s[0], s[1])):
            for r in regs:
                m = r.search(clause)
                if m:
                    out = fn(self, m, clause, source) if fn.__code__.co_argcount >= 4 else fn(self, m, clause)
                    if out is not None:
                        return self.speakable(out)
                    break
        return None


# ==========================================================================
# SKILLS - written most specific first. Each returns what to say (or None = not mine).
# ==========================================================================

# ---- briefing & summaries (the only Gemini use) -------------------------------------------
@skill(r"\b(?:check|review|go through|look at|run)\s+(?:everything|all)(?:\s+that'?s)?\s+(?:important|new|urgent)",
       r"\b(?:morning|daily)\s+briefing\b", r"\bbrief me\b", r"\bcatch me up\b", r"\bwhat did i miss\b",
       r"\bcheck everything\b")
def briefing(b, m, text, source):
    return b.call("check_everything_important", source)


STYLE_WORDS = (("action_items", r"action items?|to-?dos?|tasks|what do i (?:need|have) to do|what (?:am i|are they) (?:asking|expecting)"),
               ("detailed", r"in detail|detailed|full summary|thorough"),
               ("executive", r"executive"),
               ("quick", r"quick|short|brief|one sentence|in a sentence|tl;?dr"))


@skill(r"\b(?:summari[sz]e|summary of|sum up|give me (?:a |an )?(?:\w+ )?summary|tl;?dr|recap)\b(?P<rest>.*)")
def summarize(b, m, text, source):
    rest = m.group("rest").lower()
    style = next((s for s, p in STYLE_WORDS if re.search(p, text, I)), "quick")
    if re.search(r"\b(?:unread|new)\s+(?:e-?mails?|mails?)\b|\bmy (?:e-?mails?|inbox)\b", rest):
        TOOLS["gmail_search"](query="is:unread in:inbox")
        return llm.summarize(TOOLS["read_conversation"](), "message")
    if re.search(r"\b(?:page|website|site|article|tab|web page|blog)\b", rest):
        return llm.summarize(TOOLS["read_current_page"](max_chars=60000), style)
    if re.search(r"\bclipboard\b", rest):
        return llm.summarize(TOOLS["get_clipboard"](), style)
    if re.search(r"\b(?:selection|selected|highlighted|what i (?:selected|copied))\b", rest):
        return llm.summarize(TOOLS["copy_selection"](), style)
    if re.search(r"\b(?:screen|window)\b", rest):
        return llm.summarize(TOOLS["read_screen"](), style)
    if re.search(r"\b(?:e-?mail|mail|message|messages|chat|conversation|thread|group|whatsapp|slack|teams|discord)\b", rest):
        return llm.summarize(TOOLS["read_conversation"](), "message" if style == "quick" else style)
    ref = re.sub(r"^(?:\s*(?:me|up|of|the|this|that|a|an|for me|about)\b)+", "", rest).strip(" .") or "it"
    ref = re.sub(r"\b(?:in detail|detailed|in a sentence|in one sentence|quick|quickly|short|briefly|brief|executive|"
                 r"the action items|action items)\b", "", ref).strip(" .")
    ref = re.sub(r"^(?:the\s+|this\s+|that\s+|my\s+)?(?:document|doc|file|one)$", "it", ref).strip() or "it"
    if ref in ("it", "this", "that") or not rest.strip():
        _, title, proc = foreground()
        if not CTX.get("last_file") and any(x in proc.lower() for x in ("chrome", "msedge", "brave", "firefox")):
            return llm.summarize(TOOLS["read_current_page"](max_chars=60000), style)
    path = b.file(ref, source)
    return TOOLS["summarize_document"](path=path, style=style)


# ---- reading & screen text -----------------------------------------------------------------
@skill(r"^(?:read|read out|read aloud)\s+(?:out\s+|aloud\s+)?more$", r"^(?:continue|keep|go on)(?:\s+reading)?$", r"^go on$")
def read_more(b, m, text):
    if not b.rest:
        return "There's nothing more to read."
    chunk, b.rest = b.rest, ""
    return b.read_out(chunk)


@skill(r"^(?:read|read out|read aloud)(?:\s+out|\s+aloud)?\s*(?P<what>.*)$",
       r"\bwhat does (?:it|this|that|the \w+) say\b", r"\btell me what (?:it|this|that) says\b",
       r"\bwhat(?:'s| is) (?:on|in) (?:my |the )?(?:screen|window)\b")
def read_aloud(b, m, text, source):
    if re.match(r"^read(?:\s+(?:the|my))?(?:\s+(?:file|notes?))?$", text.strip(), I):
        return None                                             # the original 'read the file' command keeps working
    what = (m.groupdict().get("what") or "").lower().strip()
    what = re.sub(r"^(?:the\s+)?(?:text|words|writing|content|contents)\s+(?:in|on|from|of|inside)\s+(?:the\s+)?", "", what).strip()
    if what in ("", "it", "this", "that", "to me", "this to me"):
        what = ""
    if re.search(r"\bscreen|window|error|dialog|popup|message box\b", what + " " + text.lower()) and not re.search(r"e-?mail|chat", what):
        return b.read_out(TOOLS["read_screen"]())
    if re.search(r"\bclipboard\b", what):
        return b.read_out(TOOLS["get_clipboard"]())
    if re.search(r"\b(?:selection|selected|highlighted)\b", what):
        return b.read_out(TOOLS["copy_selection"]())
    if re.search(r"\b(?:page|website|site|article|blog)\b", what):
        return b.read_out(TOOLS["read_current_page"](max_chars=60000))
    if re.search(r"\b(?:e-?mail|mail|message|messages|chat|conversation|thread)\b", what) and not re.search(r"\b(?:from|latest|newest|last|unread)\b", what):
        return b.read_out(TOOLS["read_conversation"]())
    if re.search(r"\b(?:e-?mail|mail|message|messages)\b", what):
        return None                                             # "read my latest email" -> the mail skill
    if what == "":
        _, _, proc = foreground()
        if not CTX.get("last_file") and any(x in proc.lower() for x in ("chrome", "msedge", "brave", "firefox")):
            return b.read_out(TOOLS["read_current_page"](max_chars=60000))
        if not CTX.get("last_file"):
            return b.read_out(TOOLS["read_screen"]())
    path = b.file(what or "it", source)
    if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff")):
        return b.read_out(TOOLS["read_image_text"](path=path))
    out = TOOLS["read_document"](path=path, max_chars=60000)
    return b.read_out(re.sub(r"^\[[^\]]*\]\s*", "", out))


# ---- questions about a document, answered locally ------------------------------------------
FACT_WORDS = r"(deadline|due date|dates?|total|amount|amounts|price|cost|money|emails?|phone numbers?|phones?|links|urls?|invoice number|order number|customer|client|action items|tasks)"


@skill(r"\b(?:what(?:'s| is| are)|find|tell me|show me|get|extract|list|give me|how much(?: money)?(?: does| is)?)\b.*?\b" + FACT_WORDS +
       r"\b.*?\b(?:in|from|of|mentioned in|does)\b\s*(?P<doc>(?:it|this|that|the)\b.*|\w.*)?$")
def doc_facts(b, m, text, source):
    fact = m.group(1)
    doc = (m.group("doc") or "").strip()
    has_doc_ref = bool(re.search(r"\b(?:in|from|of|does|mentioned in)\s+(?:it|this|that|the\s+(?:\w+\s+)?(?:document|doc|pdf|file|invoice|contract|report|resume|letter|sheet|slides?)|\S+\.\w{2,4})\b", text, I))
    if fact.lower() in ("date", "dates") and not has_doc_ref:
        return None                                              # "what is the date" belongs to the clock
    if not has_doc_ref and not CTX.get("last_file"):
        return None
    ref = "it"
    mm = re.search(r"\b(?:in|from|of|mentioned in)\s+(?:the\s+)?(?P<r>\S+\.\w{2,4})\b", text, I)
    if mm:
        ref = mm.group("r")
    elif re.search(r"\b(?:invoice|contract|resume|report|letter)\b", text, I) and not CTX.get("last_file"):
        ref = re.search(r"\b(invoice|contract|resume|report|letter)\b", text, I).group(1)
    return TOOLS["find_in_document"](what=fact, path=b.file(ref, source))


@skill(r"^(?:what|when|who|how much|how many|where)\b.*\b(?:in|does|is|mention\w*)\b.*\b(?:this|that|the|it)\s+(?:document|doc|pdf|file|invoice|contract|report|resume|letter|sheet)\b")
def doc_question(b, m, text, source):
    what = "action items" if re.search(r"\b(?:need to do|have to do|supposed to do|action items|tasks|being asked|asking me|expect)", text, I) else text
    return TOOLS["find_in_document"](what=what, path=b.file("it", source))


@skill(r"\bcompare\s+(?P<a>.+?)\s+(?:and|with|to|against|versus|vs\.?)\s+(?P<b>.+)$")
def compare(b, m, text, source):
    a, c = b.file(m.group("a"), source), b.file(m.group("b"), source)
    return TOOLS["compare_documents"](path_a=a, path_b=c)


# ---- creating things ---------------------------------------------------------------------------
@skill(r"\b(?:create|make|add|new)\s+(?:a\s+|an\s+)?(?:new\s+)?folder\b(?P<rest>.*)$")
def create_folder(b, m, text, source):
    rest = m.group("rest").strip()
    where = ""
    w = re.search(r"\b(?:on|in|inside|under|at)\s+(?:my\s+|the\s+)?(?P<w>.+)$", rest, I)
    if w:
        where, rest = w.group("w"), rest[:w.start()]
    name = re.sub(r"^\s*(?:called|named|name it(?: as)?|with the name|as)\s+", "", rest.strip(), flags=I).strip(" .")
    if not name:
        ans = b.ask("What should I name the folder?", source)
        name = re.sub(r"^\s*(?:name it(?: as)?|call it|it's|its|named|called)\s+", "", (ans or "").strip(), flags=I).strip(" .")
        if not name:
            raise Fail("I didn't catch a name, so I didn't create the folder.")
    base = b.folder(where, default="desktop")
    out = b.call("create_folder", source, path=os.path.join(str(resolve(base)), name))
    return f"Created the folder {name}." if out.startswith("Folder ready") else out


DOC_KIND = r"(?P<kind>word|excel|powerpoint|presentation|spreadsheet|text|notepad|csv|slides?|python|py|json|html|markdown|batch|javascript|css)"


@skill(r"\b(?:create|make|new|start|add)\s+(?:a\s+|an\s+)?(?:new\s+|blank\s+|empty\s+)?" + DOC_KIND +
       r"\s*(?:document|doc|file|workbook|sheet|note)?\b(?P<rest>.*)$")
def create_doc(b, m, text, source):
    rest = m.group("rest").strip()
    where = "desktop"
    w = re.search(r"\b(?:on|in|inside|under|at)\s+(?:my\s+|the\s+)?(?P<w>.+)$", rest, I)
    if w:
        where, rest = w.group("w"), rest[:w.start()]
    where = b.folder(where, default="desktop") if w else (CTX.get("last_folder") or "desktop")
    name = re.sub(r"^\s*(?:called|named|name it(?: as)?|with the name|as)\s+", "", rest.strip(), flags=I).strip(" .")
    out = b.call("create_document", source, kind=m.group("kind"), name=name, folder=where)
    path = out.replace("Created ", "", 1)
    return f"Created {os.path.basename(path)} in {os.path.basename(os.path.dirname(path))}."


# ---- opening things ---------------------------------------------------------------------------------
SERVICE_WORDS = r"(gmail|g ?mail|google mail|e-?mail|mail|inbox|outlook calendar|outlook|whats ?app|instagram|insta|google chat|gchat|teams|ms teams|microsoft teams|slack|discord|telegram|messenger|google calendar|calendar|google meet|meet|google drive|one ?drive)"


def open_target(b, target, source):
    t, browser, profile = modifiers(target.strip())
    t = re.sub(r"^(?:the|my|up|a|an)\s+", "", t.strip(), flags=I).strip(" .")
    tl = t.lower()
    if not tl:
        raise Fail("What should I open?")
    if re.fullmatch(r"(?:web |internet |default |my )?browser|(?:the )?internet|(?:the )?web", tl):
        return b.call("open_browser", source, browser=browser, profile=profile)   # "open the browser" = the one in use
    tl = re.sub(r"\s+windows?$", "", tl)                                            # "chrome window" -> "chrome"
    if tl in ("it", "that", "this", "that one", "this one", "the file", "the folder"):
        if CTX.get("last_file"):
            return b.call("open_path", source, path=CTX["last_file"])
        if CTX.get("last_folder"):
            return b.call("open_path", source, path=CTX["last_folder"])
        if CTX.get("last_url"):
            return b.call("open_url", source, url=CTX["last_url"])
        raise Fail("I'm not sure what 'it' is.")
    if re.match(r"^(?:the\s+)?(?:file explorer|explorer|windows explorer|my files|this pc|my computer|files)$", tl):
        TOOLS["open_path"](path=os.path.expanduser("~"))
        return "Opened File Explorer."
    mm = re.match(r"^(.*?)\s*settings?$", tl)
    if mm and mm.group(1):
        return b.call("open_settings", source, page=mm.group(1))
    if tl in ("settings", "windows settings"):
        return b.call("open_settings", source, page="display").replace("display", "")
    base = re.sub(r"\s*(?:folder|directory)$", "", tl).strip()
    if base in FOLDER_WORDS and not re.match(r"^(?:browser|chrome)\s", tl):
        return b.call("open_path", source, path=FOLDER_WORDS[base])
    if re.search(r"\b(?:latest|newest|last|most recent)\b", tl) or re.search(r"\b(?:file|document|pdf|spreadsheet|presentation|image|picture|photo)\b", tl) \
            or re.search(r"\.\w{2,4}$", tl):
        if not re.search(r"\.(?:com|org|net|in|io)$", tl):
            return b.call("open_path", source, path=b.file(t, source))
    sm = re.match(r"^(?:(?P<p>[a-z0-9@.\-]+)\s+)?" + SERVICE_WORDS + r"(?:\s+(?:inbox|messages?|app|mail))?$", tl)
    if sm:
        who = profile or (sm.group("p") if sm.group("p") not in ("the", "my", "google", "microsoft", "ms") else "")
        out = b.call("open_service", source, name=sm.group(2).replace("  ", " "), profile=who)
        remember(last_service=sm.group(2).replace(" ", "").lower() if "whats" in sm.group(2) else sm.group(2).lower(), last_service_t=time.time())
        return out
    url = spoken_url(t)
    if url:
        if "mail.google" in url or "gmail.com" in url:
            remember(last_service="gmail", last_service_t=time.time())
        return b.call("open_url", source, url=url, browser=browser, profile=profile)
    if tl in BROWSER_WORDS or re.match(r"^(?:the\s+)?(?:" + "|".join(BROWSER_WORDS) + r")(?:\s+browser)?$", tl):
        bw = BROWSER_WORDS[re.sub(r"\s+browser$", "", tl.replace("the ", ""))]
        return b.call("open_browser", source, browser=bw, profile=profile)
    for suffix in (" app", " application", " program", " browser"):
        if tl.endswith(suffix):
            tl = tl[: -len(suffix)]
    res = TOOLS["open_app"](name=tl)
    if "started" in res and "no window" in res or res.startswith("I couldn't start"):
        pass
    if "I couldn't" in res or "no window has appeared" in res:
        # maybe it is a file or folder name rather than an app
        try:
            path = b.file(t, source)
            return b.call("open_path", source, path=path)
        except Fail:
            return res
    return res


@skill(r"^(?:open|launch|edit|view|play)\s+(?P<f>.+?)\s+(?:in|with|using)\s+(?:the\s+|my\s+)?(?P<app>[\w ]+?)(?:\s+app)?$", prio=-1)
def open_with(b, m, text, source):
    app = m.group("app").strip().lower()
    if app in BROWSER_WORDS or re.search(r"\bprofile\b", text, I):
        return None                                              # "open gmail in chrome" belongs to the browser skills
    if not re.search(r"^(?:word|microsoft word|excel|microsoft excel|powerpoint|microsoft powerpoint|notepad|paint|vlc|"
                     r"photos|vs code|vscode|visual studio code|adobe|acrobat|wordpad|media player|spotify)", app):
        return None
    path = b.file(m.group("f"), source)
    return b.call("open_with", source, path=path, app=app)


@skill(r"^(?:open|launch|start|run|fire up|bring up|load)\s+(?P<t>.+)$")
def open_thing(b, m, text, source):
    return open_target(b, m.group("t"), source)


@skill(r"^(?:search|look)(?:\s+for)?\s+(?P<q>.+)$", prio=-1)
def search_here(b, m, text, source):
    """'search for invoices' straight after opening Gmail / WhatsApp / Slack ... searches inside that app."""
    svc = CTX.get("last_service")
    if not svc or time.time() - CTX.get("last_service_t", 0) > 90:
        return None
    from agent_tools.comms import SEARCH_KEYS
    if svc not in SEARCH_KEYS:
        return None
    return b.call("search_in_app", source, query=m.group("q").strip(), app=svc)


@skill(r"^(?:go to|visit|navigate to|browse to|take me to)\s+(?P<t>.+)$")
def go_to(b, m, text, source):
    t, browser, profile = modifiers(m.group("t"))
    url = spoken_url(t)
    if not url:
        mm = re.match(r"^(?:the\s+)?" + SERVICE_WORDS + r"$", t.strip(), I)
        if mm:
            return b.call("open_service", source, name=mm.group(1), profile=profile)
        return None                                              # maybe "go to the next tab"
    return b.call("open_url", source, url=url, browser=browser, profile=profile)


@skill(r"^(?:search|look|google)\s+(?:for\s+|up\s+)?(?P<q>.+?)\s+(?:on|in|using)\s+(?P<engine>google|youtube|bing|duckduckgo)(?P<tail>.*)$",
       r"^(?:search|look up|find)\s+(?:the web|the internet|google|online|the net)\s+for\s+(?P<q>.+?)(?P<tail>)$",
       r"^(?:search|look)\s+(?:the web|google|online)\s+(?:for\s+)?(?P<q>.+?)(?P<tail>)$",
       r"^google\s+(?P<q>.+?)(?P<tail>)$", r"^youtube\s+(?P<q>.+?)(?P<tail>)$",
       r"^search\s+youtube\s+for\s+(?P<q>.+?)(?P<tail>)$", r"^look up\s+(?P<q>.+?)\s+online(?P<tail>)$")
def web_search(b, m, text, source):
    g = m.groupdict()
    q = (g.get("q") or "").strip()
    engine = (g.get("engine") or ("youtube" if re.match(r"^(?:youtube|search youtube)", text, I) else "google")).lower()
    q, browser, profile = modifiers(q + " " + (g.get("tail") or ""))
    return b.call("web_search", source, query=q.strip(), engine=engine, browser=browser, profile=profile)


# ---- browser & tabs ----------------------------------------------------------------------------------------
BROWSER_ACTIONS = [
    (r"^(?:open |start |make )?(?:a )?new tab$", "new_tab"), (r"^close (?:this |the |current )?tab$", "close_tab"),
    (r"^re-?open (?:the )?(?:last |closed )?(?:closed )?tab$|^undo close tab$", "reopen_tab"),
    (r"^(?:go to |switch to |move to )?(?:the )?next tab$", "next_tab"),
    (r"^(?:go to |switch to |move to )?(?:the )?(?:previous|last) tab$", "previous_tab"),
    (r"^go back(?: a page)?$|^back$|^previous page$", "back"), (r"^go forward$|^forward$|^next page$", "forward"),
    (r"^(?:refresh|reload)(?: the| this)?(?: page)?$", "refresh"),
    (r"^(?:open |show )?(?:the )?(?:browser )?bookmarks$", "bookmarks"),
    (r"^(?:open |show )?(?:the )?(?:browser |browsing )?history$", "history"),
    (r"^(?:open |show )?(?:the )?browser downloads$", "downloads"), (r"^zoom in$", "zoom_in"),
    (r"^zoom out$", "zoom_out"), (r"^(?:reset zoom|zoom reset|normal zoom)$", "zoom_reset"),
    (r"^(?:open )?(?:a )?(?:new )?incognito(?: window| tab)?$|^private window$", "incognito"),
    (r"^(?:open )?(?:a )?new (?:browser )?window$", "new_window"), (r"^scroll (?:to )?(?:the )?top$", "scroll_top"),
    (r"^scroll (?:to )?(?:the )?(?:bottom|end)$", "scroll_bottom"), (r"^(?:focus|select) (?:the )?address bar$", "address_bar"),
]


@skill(*[p for p, _ in BROWSER_ACTIONS])
def browser_action(b, m, text, source):
    in_browser = any(x in foreground()[2].lower() for x in ("chrome", "msedge", "brave", "firefox"))
    for p, action in BROWSER_ACTIONS:
        if re.search(p, text, I):
            if action == "back" and re.match(r"^go back$", text.strip(), I) and not in_browser:
                return None                                     # 'go back' outside a browser = the original 'go to sleep'
            return b.call("browser_action", source, action=action)


@skill(r"^(?:go to|switch to|find|open)\s+(?:the\s+)?(?P<t>.+?)\s+tab$", r"^(?:go to|switch to)\s+(?:the\s+)?tab\s+(?:called|named|with)?\s*(?P<t>.+)$")
def goto_tab(b, m, text, source):
    return TOOLS["switch_browser_tab"](title_contains=m.group("t"))


@skill(r"^(?:what|which)\s+(?:tab|page|website|site)\s+(?:am i|is this|is open|is in front)|^what(?:'s| is) (?:the )?(?:url|address|link)|^copy (?:the |this )?(?:page )?(?:link|url|address)$")
def page_info(b, m, text, source):
    url = TOOLS["current_page_url"]()
    if re.search(r"\bcopy\b", text, I):
        TOOLS["set_clipboard"](text=url)
        return "Copied the link."
    return f"You're on {short(foreground()[1], 80)}. The address is {url}"


@skill(r"^download\s+(?:the\s+)?(?:file\s+|pdf\s+|image\s+)?(?:from\s+|at\s+)?(?P<u>https?://\S+|www\.\S+)$",
       r"^download\s+(?:this|that|it)(?:\s+(?:file|pdf|image))?$")
def download(b, m, text, source):
    u = m.groupdict().get("u") or TOOLS["current_page_url"]()
    if not u.startswith("http"):
        u = "https://" + u
    return TOOLS["download_file"](url=u)


# ---- windows & desktop -------------------------------------------------------------------------------------------
@skill(r"^(?:minimi[sz]e|hide)\s+(?:everything|all(?: (?:the )?windows)?|all)$", r"^show (?:me )?(?:the )?desktop$", r"^go to (?:the )?desktop$")
def minimize_all(b, m, text, source):
    return b.call("minimize_all_windows", source) if re.search(r"minimi|hide", text, I) else b.call("show_desktop", source)


@skill(r"^(?:restore|bring back|unminimi[sz]e)\s+(?:everything|all(?: (?:the )?windows)?|my windows|the windows)$")
def restore_all(b, m, text, source):
    return b.call("restore_all_windows", source)


@skill(r"^(?P<a>minimi[sz]e|maximi[sz]e|restore)\s+(?P<t>.+)$")
def window_state(b, m, text, source):
    t = re.sub(r"^(?:the\s+)?", "", m.group("t").strip(), flags=I)
    name = "" if t.lower() in ("this", "this window", "window", "the window", "it", "current window", "this app") else re.sub(r"\s+window$", "", t, flags=I)
    return b.call("window_action", source, action=m.group("a").lower().replace("z", "s").replace("minimise", "minimize").replace("maximise", "maximize"), name=name)


@skill(r"^(?:go |make (?:it |this )?)?full ?screen$", r"^exit full ?screen$|^(?:leave|stop) full ?screen$")
def fullscreen(b, m, text, source):
    return b.call("window_action", source, action="exit_fullscreen" if re.search(r"exit|leave|stop", text, I) else "fullscreen")


@skill(r"^(?:snap|move|put)\s+(?:this\s+)?(?:window\s+)?(?:to\s+)?(?:the\s+)?(?P<d>left|right)(?:\s+side|\s+half)?$")
def snap(b, m, text, source):
    return b.call("window_action", source, action="snap_" + m.group("d").lower())


@skill(r"^(?:switch|jump|change|go)\s+(?:back\s+)?to\s+(?P<t>.+?)(?:\s+(?:window|app|application))?$",
       r"^bring\s+(?:up\s+)?(?P<t>.+?)\s+(?:forward|to the front|to front|up)$",
       r"^(?:show|focus)\s+(?:me\s+)?(?:the\s+)?(?P<t>[\w ]+?)\s+window$")
def switch_app(b, m, text, source):
    t = re.sub(r"^(?:the|my)\s+", "", m.group("t").strip(), flags=I)
    if re.search(r"\b(?:tab|page|back|forward|desktop|settings|folder|email|mail|inbox|downloads|file|wallpaper|background|"
                 r"volume|brightness|wifi|bluetooth|sleep|sleep mode)\b", t, I) or spoken_url(t):
        return None
    if t.lower() in ("back", "previous", "the previous app", "last app", "previous app"):
        TOOLS["press_keys"](keys="alt+tab")
        return "Switched."
    return TOOLS["switch_to_app"](name=t)


@skill(r"^close\s+(?:this\s+|the\s+|current\s+)?(?:window|app|application|program)$", r"^close\s+(?:this|it)$")
def close_this(b, m, text, source):
    return b.call("close_window", source, name="")


@skill(r"^(?:close|quit|exit|kill)\s+(?P<t>.+)$")
def close_app(b, m, text, source):
    t = re.sub(r"^(?:the\s+|my\s+)", "", m.group("t").strip(), flags=I)
    t = re.sub(r"\s+(?:app|application|program|window|browser)$", "", t, flags=I)
    if t.lower() in ("", "veera", "yourself", "you", "assistant"):
        return None
    return b.call("close_app", source, name=t)


@skill(r"^(?:what|which)\s+(?:apps?|windows?|programs?|applications?)\s+(?:are\s+)?(?:open|running)", r"^list (?:the\s+)?(?:open\s+)?(?:windows|apps)$",
       r"^what(?:'s| is) running$")
def list_apps(b, m, text, source):
    apps = TOOLS["list_running_apps"]()
    return "These are open: " + ", ".join(apps[:12]) + "." if apps else "Nothing seems to be open."


@skill(r"^what am i (?:looking at|in|using)$", r"^(?:which|what)\s+(?:window|app)\s+(?:is|am i)\s+(?:this|active|in front|in)", r"^what(?:'s| is) (?:the )?active window$")
def active(b, m, text, source):
    return "You're in " + short(TOOLS["active_window"](), 100)


@skill(r"^(?:open\s+)?(?:the\s+)?start menu$", r"^(?:open|show)\s+(?:the\s+)?notifications?(?:\s+centre|\s+center)?$",
       r"^(?:open|show)\s+(?:the\s+)?(?:system )?tray$", r"^(?:show|open)\s+(?:the\s+)?hidden icons$")
def shell_bits(b, m, text, source):
    if re.search(r"start", text, I):
        return b.call("open_start_menu", source)
    return b.call("open_notifications", source) if re.search(r"notif", text, I) else b.call("open_system_tray", source)


# ---- keyboard, mouse, clipboard -----------------------------------------------------------------------------------------
@skill(r"^type\s+(?P<t>.+?)\s+and(?:\s+then)?\s+press\s+(?:the\s+)?enter$")
def type_and_enter(b, m, text, source):
    return b.call("type_text", source, text=m.group("t").strip(), press_enter=True)


@skill(r"^(?:type|dictate)\s+(?P<t>.+)$")
def type_it(b, m, text, source):
    return TOOLS["type_text"](text=m.group("t").strip())


@skill(r"^press\s+(?:the\s+)?(?P<k>.+?)(?:\s+(?P<n>\d+|twice|three times|two times)\s*(?:times)?)?$", r"^hit\s+(?:the\s+)?(?P<k>enter|escape|tab|space)$")
def press(b, m, text, source):
    k = key_combo(m.group("k"))
    n = {"twice": 2, "two times": 2, "three times": 3}.get((m.groupdict().get("n") or "").lower(), None) or int(m.groupdict().get("n") or 1) if (m.groupdict().get("n") or "").strip() else 1
    return b.call("press_keys", source, keys=k, times=n)


SHORTCUTS = [(r"^copy(?: (?:this|that|it|the selection|selected(?: text)?))?$", "ctrl+c", "Copied."), (r"^cut(?: (?:this|that|it|the selection))?$", "ctrl+x", "Cut."),
             (r"^paste(?: (?:it|that|here|this|the clipboard))?$", "ctrl+v", "Pasted."), (r"^select all$", "ctrl+a", "Selected everything."),
             (r"^undo(?: (?:that|it))?$", "ctrl+z", "Undone."), (r"^redo(?: (?:that|it))?$", "ctrl+y", "Redone."),
             (r"^save(?: (?:this|it|the (?:file|document)))?$", "ctrl+s", "Saved."), (r"^save as$", "f12", "Save as opened."),
             (r"^(?:new|create) (?:file|document|window)$", "ctrl+n", "New one opened."), (r"^print(?: this| it)?$", "ctrl+p", "Print dialog opened."),
             (r"^(?:find|search) (?:in|on) (?:this )?(?:page|document|file)$", "ctrl+f", "Find opened."),
             (r"^(?:select|choose) (?:the )?address bar$", "ctrl+l", "Address bar selected."), (r"^(?:cancel|dismiss|escape)(?: this)?$", "esc", "Cancelled."),
             (r"^(?:press )?enter$|^confirm that$|^hit enter$", "enter", "Done."), (r"^(?:switch|alt) tab$", "alt+tab", "Switched.")]


@skill(*[p for p, _, _ in SHORTCUTS])
def shortcut(b, m, text, source):
    for p, keys, reply in SHORTCUTS:
        if re.search(p, text, I):
            b.call("press_keys", source, keys=keys)
            return reply


@skill(r"^(?:read|what(?:'s| is) on) (?:the |my )?clipboard$", r"^what did i copy$")
def read_clip(b, m, text, source):
    return b.read_out(TOOLS["get_clipboard"]())


@skill(r"^(?P<a>double[- ]?click|right[- ]?click|click|press|tap|select|choose)\s+(?:on\s+)?(?:the\s+)?(?P<t>.+?)(?:\s+(?:button|tab|link|menu|option|icon))?$")
def click(b, m, text, source):
    a = m.group("a").lower()
    t = m.group("t").strip()
    if a in ("press", "select", "choose") and (re.match(r"^(?:enter|escape|tab|space|ctrl|control|alt|shift|win|delete|f\d+|all|the )", t, I) or a == "press" and key_combo(t) != t.lower()):
        return None
    return b.call("click_control", source, text=t, double_click=a.startswith("double"), right_click=a.startswith("right"))


@skill(r"^scroll\s+(?P<d>up|down)(?:\s+(?:a\s+)?(?P<amt>lot|little|bit|page|more))?$")
def scroll(b, m, text, source):
    amt = {"lot": 12, "page": 8, "more": 8, "little": 2, "bit": 2}.get((m.group("amt") or "").lower(), 5)
    return b.call("scroll", source, direction=m.group("d").lower(), amount=amt)


# ---- files ---------------------------------------------------------------------------------------------------------------------------
@skill(r"^(?:find|locate|look for|where(?:'s| is| are))\s+(?:me\s+)?(?:my\s+|the\s+|a\s+|any\s+)?(?P<what>.+?)(?:\s+(?:in|on|inside|under|from)\s+(?:my\s+|the\s+)?(?P<where>desktop|documents?|downloads?|pictures?|music|videos?)(?:\s+folder)?)?$")
def find_files(b, m, text, source):
    what = m.group("what").strip()
    if re.search(r"\b(?:e-?mails?|mails?|messages?|chat|inbox|whatsapp|conversation|web|online|internet|meeting|tab|window|app)\b", what, I):
        return None
    if re.match(r"^(?:the\s+)?(?:date|time|weather|answer|way|latest news)\b", what, I):
        return None
    where = FOLDER_WORDS.get((m.group("where") or "").lower().rstrip("s") + ("s" if (m.group("where") or "").lower().endswith("s") else ""), FOLDER_WORDS.get((m.group("where") or "").lower(), ""))
    exts = b.kind_exts(what)
    words = " ".join(w for w in re.findall(r"[^\s]+", what) if w.lower() not in TYPE_EXT and w.lower() not in ("file", "files", "document", "documents", "called", "named", "latest", "newest", "last", "recent"))
    if re.match(r"^(?:latest|newest|last|most recent)", what, I):
        path = b.file(what, source, folder=where or "")
        return f"The latest one is {os.path.basename(path)}. Say open it to open it."
    hits = []
    for e in (exts or [""]):
        hits += [h for h in TOOLS["find_files"](name_contains=words, extension=e, folder=where, limit=8) if isinstance(h, dict)]
    hits.sort(key=lambda h: h["modified"], reverse=True)
    if not hits:
        return f"I couldn't find anything called {words or what}."
    remember(last_file=hits[0]["path"] if hits[0]["type"] == "file" else None,
             last_folder=hits[0]["path"] if hits[0]["type"] == "folder" else None)
    names = ", ".join(os.path.basename(h["path"]) for h in hits[:3])
    return (f"I found {len(hits)}: {names}." if len(hits) > 1 else f"I found {names}.") + " Say open it to open the first one."


@skill(r"^(?:what(?:'s| is| are)|show|list)\s+(?:me\s+)?(?:the\s+)?(?:my\s+)?(?:recent|latest|new|newest)\s+(?:files?|downloads?|documents?)(?:\s+in\s+(?:my\s+)?(?P<where>\w+))?$",
       r"^what(?:'s| is) (?:new )?in (?:my |the )?(?P<where2>downloads?|documents?|desktop|pictures?)(?: folder)?$", r"^what did i download(?: today| recently)?$")
def recent(b, m, text, source):
    g = m.groupdict()
    where = FOLDER_WORDS.get((g.get("where") or g.get("where2") or "downloads").lower(), "downloads")
    items = TOOLS["recent_files"](folder=where, hours=24 * 14, limit=6)
    names = [os.path.basename(i["path"]) for i in items if isinstance(i, dict)]
    return ("Newest in " + where + ": " + ", ".join(names[:5]) + ".") if names else f"Nothing recent in {where}."


@skill(r"^(?:copy|duplicate)\s+(?P<src>.+?)\s+(?:to|into|in)\s+(?P<dst>.+)$", r"^(?:move|put|send)\s+(?P<src>.+?)\s+(?:to|into|in)\s+(?!the recycle)(?P<dst>.+)$")
def copy_move(b, m, text, source):
    verb = "copy" if re.match(r"^(?:copy|duplicate)", text, I) else "move"
    src = b.file(m.group("src"), source, strict=True)
    dst = b.folder(m.group("dst"))
    return b.call(f"{verb}_path", source, source=src, destination=dst) if verb == "move" else TOOLS["copy_path"](source=src, destination=dst)


@skill(r"^duplicate\s+(?P<src>.+)$")
def duplicate(b, m, text, source):
    return TOOLS["duplicate_path"](path=b.file(m.group("src"), source))


@skill(r"^rename\s+(?P<src>.+?)\s+(?:to|as)\s+(?P<new>.+)$")
def rename(b, m, text, source):
    src = b.file(m.group("src"), source, strict=True)
    new = m.group("new").strip(" .")
    if not os.path.splitext(new)[1]:
        new += os.path.splitext(src)[1]
    return TOOLS["rename_path"](path=src, new_name=new)


@skill(r"^(?:delete|remove|trash|erase|get rid of)\s+(?P<src>.+?)(?:\s+(?:from|in)\s+(?:my\s+)?(?P<where>desktop|documents|downloads))?$",
       r"^(?:send|put|move)\s+(?P<src>.+?)\s+(?:to|in|into)\s+the\s+(?:recycle bin|trash|bin)$")
def delete(b, m, text, source):
    spec = m.group("src")
    if re.match(r"^(?:the\s+)?recycle bin$|^(?:everything|all)\b", spec, I):
        return None
    src = b.file(spec, source, strict=True, folder=(m.groupdict().get("where") or ""))
    return b.call("delete_path", source, path=src)


@skill(r"^(?:zip|compress)\s+(?P<src>.+)$")
def zip_it(b, m, text, source):
    return TOOLS["compress_path"](path=b.file(m.group("src"), source))


@skill(r"^(?:unzip|extract|decompress)\s+(?P<src>.+?)(?:\s+(?:to|into)\s+(?P<dst>.+))?$")
def unzip(b, m, text, source):
    src = b.file(m.group("src") if ".zip" in m.group("src") or "zip" in m.group("src") else m.group("src") + " zip", source)
    return TOOLS["extract_zip"](zip_path=src, destination=b.folder(m.group("dst")) if m.group("dst") else "")


@skill(r"^(?:show|reveal)\s+(?P<src>.+?)\s+in\s+(?:file )?explorer$")
def reveal(b, m, text, source):
    return TOOLS["show_in_explorer"](path=b.file(m.group("src"), source))


# ---- media ----------------------------------------------------------------------------------------------------------------------------------
@skill(r"^(?:pause|resume|continue|unpause)(?:\s+the)?(?:\s+(?:music|song|video|playing|playback|it|audio|spotify|youtube))?$",
       r"^play(?:\s+the)?(?:\s+(?:music|song|video|it|audio))?$", r"^(?:play|pause)\s*/\s*(?:pause|play)$")
def play_pause(b, m, text, source):
    return b.call("media_key", source, action="pause" if re.match(r"^pause", text, I) else "play_pause")


@skill(r"^(?:next|skip)(?:\s+(?:the|this))?(?:\s+(?:song|track|one|video))?$", r"^(?:play\s+)?(?:the\s+)?next\s+(?:song|track)$")
def next_track(b, m, text, source):
    return b.call("media_key", source, action="next")


@skill(r"^(?:previous|go back to the (?:last|previous))(?:\s+(?:song|track|one))?$", r"^(?:play\s+)?(?:the\s+)?(?:previous|last)\s+(?:song|track)$")
def prev_track(b, m, text, source):
    return b.call("media_key", source, action="previous")


@skill(r"^stop\s+(?:the\s+)?(?:music|song|playing|playback|video|audio)$")
def stop_music(b, m, text, source):
    return b.call("media_key", source, action="stop")


@skill(r"^(?P<d>skip forward|skip ahead|fast forward|forward|rewind|skip back(?:ward)?|go back|back)\s*(?:by\s+)?(?P<n>\w+)?\s*(?:seconds?|secs?)$")
def seek(b, m, text, source):
    n = system_control._number_in(m.group("n") or "") or 10
    fwd = not re.search(r"rewind|back", m.group("d"), I)
    return b.call("media_seek", source, direction="forward" if fwd else "back", seconds=n)


@skill(r"^play\s+(?P<q>.+?)\s+(?:on|in|using)\s+spotify$", r"^play\s+(?:my\s+|the\s+)?(?P<q>.+?)\s+(?:playlist|album|on spotify)$",
       r"^(?:open\s+)?spotify\s+(?:and\s+)?(?:play|search(?: for)?)\s+(?P<q>.+)$")
def spotify(b, m, text, source):
    return b.call("open_spotify", source, query=m.group("q").strip())


# ---- system ---------------------------------------------------------------------------------------------------------------------------------------
@skill(r"^(?:set|make|use)\s+(?P<img>.+?)\s+as\s+(?:my\s+|the\s+)?(?:desktop\s+)?(?:wallpaper|background)$",
       r"^(?:change|set)\s+(?:my\s+|the\s+)?(?:wallpaper|background)\s+(?:to|as)\s+(?P<img>.+)$", prio=-1)
def wallpaper(b, m, text, source):
    spec = m.group("img")
    path = b.file(spec if re.search(r"\b(?:image|picture|photo|it|that|this)\b|\.\w{2,4}$", spec, I) or True else spec, source)
    return TOOLS["set_wallpaper"](path=path)


@skill(r"^make\s+(?P<b>chrome|google chrome|edge|microsoft edge|brave|firefox)\s+(?:my\s+|the\s+)?default(?:\s+browser)?$")
def default_browser(b, m, text, source):
    TOOLS["open_settings"](page="default apps")
    return f"Windows doesn't let apps change the default browser, so I opened the Default apps page. Choose {m.group('b')} there."


@skill(r"^lock(?:\s+(?:the|my))?(?:\s+(?:computer|pc|screen|laptop|system))?$")
def lock(b, m, text, source):
    return b.call("lock_computer", source)


@skill(r"^(?:put (?:the |my )?(?:computer|pc|laptop) to )?sleep(?:\s+(?:the |my )?(?:computer|pc|laptop))?$|^go to sleep mode$")
def sleep_pc(b, m, text, source):
    return b.call("sleep_computer", source)


@skill(r"^(?:restart|reboot)(?:\s+(?:the|my))?(?:\s+(?:computer|pc|laptop|system))?$")
def restart(b, m, text, source):
    return b.call("restart_computer", source)


@skill(r"^cancel\s+(?:the\s+)?(?:shutdown|restart|reboot)$")
def cancel_shutdown(b, m, text, source):
    return TOOLS["cancel_shutdown"]()


@skill(r"^empty\s+(?:the\s+)?(?:recycle bin|trash|bin)$")
def empty_bin(b, m, text, source):
    return b.call("empty_recycle_bin", source)


@skill(r"^(?:what(?:'s| is)|how(?:'s| is)|show|check)\s+(?:my\s+|the\s+)?(?:battery|memory|ram|disk|storage|system)(?:\s+(?:level|usage|space|status|left))?$",
       r"^how much (?:battery|memory|disk space|storage)(?: is)?(?: left| used| free)?$")
def sysinfo(b, m, text, source):
    s = TOOLS["system_status"]()
    if re.search(r"battery", text, I):
        return f"Battery: {s['battery']}."
    if re.search(r"memory|ram", text, I):
        return f"Memory is {s['memory_used']} used."
    return f"Battery {s['battery']}, memory {s['memory_used']} used, {s['disk_free_gb']} gigabytes free on the main drive."


# ---- email & chat ---------------------------------------------------------------------------------------------------------------------------------------------
CHAT_APPS = ("whatsapp", "slack", "teams", "discord", "telegram", "google chat", "messenger", "instagram")


def _chat_app(text):
    t = text.lower()
    t = re.sub(r"whats ?app", "whatsapp", t)
    for app in CHAT_APPS:
        if app in t:
            return app
    return None


@skill(r"\b(?:find|search|show|open|check|read|get|look for|any|do i have)\b.*?\b(?:e-?mails?|mails?|inbox)\b(?!\s+address)", prio=-1)
def email(b, m, text, source):
    t, browser, profile = modifiers(text)
    low = t.lower()
    q = ["in:inbox"] if not re.search(r"\b(?:sent|draft|spam|trash)\b", low) else []
    who = re.search(r"\bfrom\s+(?P<w>[\w@.\- ]+?)(?=\s+(?:about|regarding|with|containing|today|yesterday|saying|that|which)\b|$)", low)
    if who:
        name = re.sub(r"^(?:my|the|a)\s+", "", who.group("w").strip())
        q.append(f'from:"{name}"' if " " in name else f"from:{name}")
    topic = re.search(r"\b(?:about|regarding|containing|mentioning|subject)\s+(?P<w>.+?)(?=\s+(?:from|with|today|yesterday)\b|$)", low)
    if topic:
        q.append(re.sub(r"^(?:the|a|an|my)\s+", "", topic.group("w").strip()))
    if re.search(r"\bunread|new\b", low):
        q.append("is:unread")
    if re.search(r"\battachments?|attached\b", low):
        q.append("has:attachment")
    ft = re.search(r"\b(pdf|docx|xlsx|pptx|csv)\b", low)
    if ft:
        q.append(f"filename:{ft.group(1)}")
    if re.search(r"\btoday'?s?\b", low):
        q.append("newer_than:1d")
    if re.search(r"\byesterday\b", low):
        q.append("newer_than:2d")
    TOOLS["gmail_search"](query=" ".join(q), profile=profile)
    want_body = re.search(r"\b(?:latest|newest|last|most recent|read)\b", low) and not re.search(r"\bunread|emails\b", low) \
        or re.search(r"\b(?:latest|newest|last|most recent)\s+(?:e-?mail|mail)\b", low)
    if want_body:
        opened = TOOLS["open_first_result"]()
        time.sleep(1.5)
        body = TOOLS["read_conversation"]()
        return b.read_out(body) if body else opened
    listing = TOOLS["read_conversation"]()
    return "Here is your mail list. " + short(listing, 700) if listing else "I opened Gmail, but couldn't read the list."


@skill(r"\bwhat did\s+(?P<who>[\w ]+?)\s+(?:say|send|write|wrote|message|text)\b", r"\bwhat(?:'s| is| has)\s+(?P<who>[\w ]+?)\s+(?:said|sent|saying)\b",
       r"^(?:read|check|open)\s+(?:my\s+)?(?:latest\s+|new\s+|unread\s+)?(?P<app>whats ?app|slack|teams|discord|telegram|google chat|messenger|instagram)(?:\s+messages?)?(?:\s+from\s+(?P<who2>[\w ]+))?$",
       r"^(?:read|check|open|show)\s+(?:the\s+|my\s+)?(?:latest\s+)?(?:messages?|chats?)\s+from\s+(?P<who2>[\w ]+?)(?:\s+(?:on|in)\s+(?P<app2>\w+(?: \w+)?))?$", prio=-1)
def chat(b, m, text, source):
    g = m.groupdict()
    who = (g.get("who") or g.get("who2") or "").strip()
    app = _chat_app(" ".join(filter(None, [g.get("app"), g.get("app2"), text]))) or CTX.get("last_service")
    if app not in CHAT_APPS:
        app = _chat_app(b.ask("Which app: WhatsApp, Slack, Teams, Discord, Telegram or Google Chat?", source) or "")
        if not app:
            raise Fail("I need to know which chat app, so I didn't open anything.")
    TOOLS["open_service"](name=app, prefer="app")
    if who:
        TOOLS["search_in_app"](query=who, app=app)
    time.sleep(1)
    body = TOOLS["read_conversation"]()
    return b.read_out(body) if body else f"I opened {app} but couldn't read it."


@skill(r"\b(?:what(?:'s| is)|when(?:'s| is)|any|do i have)\b.*\b(?:next meeting|meetings?|events?|schedule|calendar)\b", r"^(?:open|show|check)\s+(?:my\s+)?calendar$", prio=-1)
def calendar(b, m, text, source):
    TOOLS["open_service"](name="calendar", prefer="web")
    time.sleep(3)
    if re.match(r"^(?:open|show)", text, I):
        return "Opened your calendar."
    page = TOOLS["read_conversation"]()
    return llm.summarize(page, "quick", subject="calendar page",
                         extra="List today's meetings with their times, and say which one is next.")


@skill(r"^(?:insert|add|put)\s+(?:the\s+|that\s+|this\s+|a\s+)?(?:(?:latest|newest|downloaded|last)\s+)?(?P<w>image|picture|photo|pic)(?:\s+(?:in|into|to|on)\s+(?:the\s+|this\s+|that\s+)?(?:document|doc|file|word|slide|presentation|here))?$", prio=-1)
def insert_picture(b, m, text, source):
    path = b.file("latest " + m.group("w"), source)
    from agent_tools.desktop import click_control
    for step in ("Insert", "Pictures", "This Device"):
        out = click_control(step)
        if not out.startswith(("Pressed", "Clicked")):
            raise Fail(f"I couldn't find the {step} button. Open Word's Insert tab first.")
        time.sleep(1.2)
    time.sleep(1.0)
    TOOLS["type_text"](text=path)
    TOOLS["press_keys"](keys="enter")
    return f"Inserted {os.path.basename(path)}."


# The broad "open ...", "find ...", "click ...", "switch to ...", "close ..." skills go LAST, so the specific ones
# ("open bookmarks", "close this tab", "open the start menu") get the first chance.
_LATE = {"open_thing", "click", "find_files", "switch_app", "close_app"}
SKILLS[:] = [((1 if s_[2] in _LATE else s_[0]), s_[1], s_[2], s_[3], s_[4]) for s_ in SKILLS]


# ---- the evening greeting asked "how was your day?" - answer it (see greeting.py) ---------------------------------------------
@skill(r".", prio=-2)
def day_answer(b, m, text, source):
    t0 = CTX.get("asked_day_t")
    if not t0 or time.time() - t0 > 120:
        return None
    from greeting import day_reply
    out = day_reply(text)
    if out:
        CTX.pop("asked_day_t", None)
    return out


@skill(r"^(?:start|launch|run)\s+(?:veera\s+)?(?:when|at|on)\s+(?:windows\s+)?(?:starts?|startup|boot|login|log in|sign in)\b",
       r"^(?:start|launch|run)\s+(?:veera\s+)?automatically$", r"^(?:enable|turn on)\s+(?:veera\s+)?(?:auto ?start|startup)$")
def startup_on(b, m, text, source):
    from startup import enable
    return enable()


@skill(r"^(?:stop|don't|do not|dont)\s+(?:starting\s+)?(?:veera\s+)?(?:when|at|on|from starting)\s+(?:windows\s+)?(?:starts?|startup|boot|login)\b",
       r"^(?:disable|turn off)\s+(?:veera\s+)?(?:auto ?start|startup)$")
def startup_off(b, m, text, source):
    from startup import disable
    return disable()


# ---- Wi-Fi / Bluetooth: ask which one, and understand the answer --------------------------------------------------------------------
def _pick_by_voice(b, options, question, source):
    """Say the choices, listen, and pick the closest one (the names are known, so a rough hearing is enough)."""
    import difflib
    ans = b.ask(f"{question} {', '.join(options[:5])}.", source, timeout=9, limit=6)
    if not ans:
        raise Fail("I didn't hear which one, so I didn't change anything.")
    a = re.sub(r"[^a-z0-9]", "", ans.lower())
    norm = {re.sub(r"[^a-z0-9]", "", o.lower()): o for o in options}
    for k, o in norm.items():
        if k and (k in a or a in k):
            return o
    close = difflib.get_close_matches(a, list(norm), n=1, cutoff=0.45)
    if close:
        return norm[close[0]]
    for word in re.findall(r"[a-z0-9]{3,}", ans.lower()):                  # one word of the name is enough
        for k, o in norm.items():
            if difflib.SequenceMatcher(None, word, k[:len(word) + 2]).ratio() > 0.7:
                return o
    raise Fail("I couldn't tell which one you meant, so I didn't change anything.")


@skill(r"^(?:connect|join|switch)\s*(?:to|on)?\s*(?:the\s+|my\s+|a\s+)?(?:wi-?fi|wireless|wlan)(?:\s+network)?$",
       r"^(?:connect|join)\s+(?:me\s+)?(?:to\s+)?(?:the\s+)?internet$", prio=-1)
def wifi_connect_ask(b, m, text, source):
    import system_control as sc
    if sc._radio("WiFi", "Query") == "Off":
        return TOOLS["system_command"](text="turn on wifi")
    names = sc._wifi_profiles()
    if not names:
        return "I don't know any saved Wi-Fi networks."
    chosen = _pick_by_voice(b, names, "Which network? I know", source)
    return TOOLS["system_command"](text=f"connect wifi to {chosen}")


@skill(r"^connect\s+(?:to\s+)?(?:the\s+|my\s+|a\s+)?(?:bluetooth|blue tooth)(?:\s+(?:device|headphones?|speaker|earbuds))?$", prio=-1)
def bluetooth_connect_ask(b, m, text, source):
    import system_control as sc
    names = [n for n in sc._bt_devices() if n]
    if not names:
        return "I don't see any paired Bluetooth devices."
    chosen = _pick_by_voice(b, names, "Which device? I know", source)
    return TOOLS["system_command"](text=f"connect bluetooth to {chosen}")


@skill(r"^(?:what|which|list|show|read|tell me)\s+(?:me\s+)?(?:are\s+)?(?:the\s+|my\s+)?(?:saved\s+|known\s+)?(?:wi-?fi|wireless)\s+(?:networks?|connections?|names?)$",
       r"^(?:wi-?fi\s+networks?)$", prio=-1)
def list_wifi(b, m, text, source):
    import system_control as sc
    names = sc._wifi_profiles()
    return ("Saved Wi-Fi networks: " + ", ".join(names) + ".") if names else "I don't know any saved Wi-Fi networks."


@skill(r"^(?:what|which|list|show|read|tell me)\s+(?:me\s+)?(?:are\s+)?(?:the\s+|my\s+)?(?:paired\s+)?bluetooth\s+devices$", prio=-1)
def list_bluetooth(b, m, text, source):
    import system_control as sc
    names = [n for n in sc._bt_devices() if n]
    return ("Paired devices: " + ", ".join(names) + ".") if names else "I don't see any paired Bluetooth devices."


@skill(r"^(?:go to|open|show)\s+(?:the\s+|my\s+)?(?:wi-?fi|bluetooth|sound|display|network|volume|brightness|battery)$", prio=-1)
def settings_by_name(b, m, text, source):
    t = re.sub(r"^(?:go to|open|show)\s+(?:the\s+|my\s+)?", "", text.strip(), flags=I).lower()
    return b.call("open_settings", source, page="wifi" if "fi" in t else t)


# ---- a bare app name is an order to open it ("file explorer", "task manager", "calculator") ---------------------------------------
@skill(r"^(?:the\s+)?(?P<t>file explorer|explorer|task manager|calculator|notepad|paint|word|excel|powerpoint|chrome|brave|edge|firefox|spotify|whatsapp|gmail|outlook|teams|vlc|calendar)$", prio=2)
def bare_app(b, m, text, source):
    return open_target(b, m.group("t"), source)
