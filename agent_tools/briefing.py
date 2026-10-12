"""'Check everything important' - one command that looks through the user's configured sources.

Set in .env (all optional):
    BRIEFING_SOURCES=gmail,google calendar,whatsapp,google chat      (what to look at)
    BRIEFING_PROFILE=work                                           (browser profile for the web ones)

Each source is opened in the user's own signed-in browser / app and its text is read from the window -
nothing is fetched from a private API, so a source that isn't signed in simply comes back empty. Only the
collected TEXT is sent to Gemini, to write the spoken summary.
"""

import os
import time

import llm
from . import tool
from .comms import service_key, open_service, screen_text
from .files import recent_files

DEFAULT_SOURCES = "gmail,google calendar,whatsapp,google chat"


@tool("comms")
def check_everything_important(sources: str = "") -> str:
    """The 'check everything important' briefing: unread email, chats needing a reply, action items, today's
    meetings and new documents, most urgent first. Takes a minute: it opens each source and reads it."""
    wanted = [s.strip() for s in (sources or os.environ.get("BRIEFING_SOURCES") or DEFAULT_SOURCES).split(",") if s.strip()]
    profile = os.environ.get("BRIEFING_PROFILE", "")
    parts = []
    for name in wanted:
        key = service_key(name)
        if not key:
            continue
        try:
            if key == "gmail":
                from .comms import gmail_search
                gmail_search("is:unread in:inbox", profile)
            else:
                open_service(key, profile=profile, prefer="app")
                time.sleep(4)
            text = screen_text(6000)
            parts.append(f"### {key}\n{text if len(text) > 40 else '(nothing readable - maybe not signed in)'}")
        except Exception as e:
            parts.append(f"### {key}\n(couldn't read it: {e})")
    docs = recent_files("downloads", hours=24, limit=8)
    names = [d["path"].rsplit("\\", 1)[-1] for d in docs if isinstance(d, dict)]
    parts.append("### new documents in Downloads (last 24 hours)\n" + ("\n".join(names) or "(none)"))
    return llm.summarize("\n\n".join(parts), "briefing",
                         extra="Say how many items are in each area, then the single most urgent thing first "
                               "with who it is from and any deadline. If a source was unreadable, say it "
                               "could not be checked rather than saying there was nothing.")
