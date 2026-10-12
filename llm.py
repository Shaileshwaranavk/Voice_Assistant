"""
The ONLY place Veera talks to Gemini - and only to SUMMARISE text it has already read on this PC.

Everything else (understanding what you said, choosing and running commands, reading documents, OCR,
finding files, clicking, typing ...) happens locally. Nothing is sent until you ask for a summary, and what
is sent is just the text to be summarised.

Needs GEMINI_API_KEY in the .env file.
"""

import os
import re
import time

try:
    from google import genai
    from google.genai import types
except ImportError:                       # pip install google-genai
    genai = types = None

# "-latest" always points at Google's newest model of that size, so it never gets retired under us.
SUMMARY_MODEL = os.environ.get("VEERA_SUMMARY_MODEL", "gemini-flash-lite-latest")

STYLES = {
    "quick": "Give the main idea in 2-4 short sentences.",
    "detailed": "Give a detailed summary: the important sections and the details that matter.",
    "executive": "Write an executive summary with: Objective, Key points, Decisions, Risks, Actions.",
    "action_items": "List only the action items: who must do what, by when, and the context.",
    "message": "Summarise as a conversation: main topic, who said what, requests, decisions, follow-ups, "
               "attachments or links shared. Resolve words like 'it' from earlier messages.",
    "briefing": "Write a short spoken briefing of what needs attention: the most urgent item first, "
                "then the rest. Never invent counts, names or deadlines.",
}
MAX_CHARS = 120_000                       # how much text we hand over in one go

_client = None


def api_key():
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def available():
    return genai is not None and bool(api_key())


def _get_client():
    global _client
    if _client is None:
        if not available():
            raise RuntimeError("No Gemini key - add GEMINI_API_KEY=... to the .env file.")
        _client = genai.Client(api_key=api_key(), http_options=types.HttpOptions(timeout=60000))
    return _client


def limit_wait_seconds(error):
    """If this is a 'too many requests' error, how many seconds Google says to wait (else None)."""
    msg = str(error)
    if "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
        return None
    m = re.search(r"retryDelay'?\W+(\d+(?:\.\d+)?)s", msg)
    return float(m.group(1)) if m else 30.0


def summarize(text, style="quick", subject="", extra=""):
    """Summarise text. style: quick | detailed | executive | action_items | message | briefing.
    Returns the summary, or a short plain explanation if Gemini can't be used."""
    text = (text or "").strip()
    if not text:
        return "There is nothing there to summarise."
    if not available():
        return "Summaries need a Gemini key. Add GEMINI_API_KEY to the dot env file."
    prompt = (f"{STYLES.get(style, STYLES['quick'])}\n"
              "Use ONLY the text below. Do not invent anything. Plain spoken English, no markdown, no bullet symbols."
              f"{(' ' + extra) if extra else ''}\n\n"
              f"{('Subject: ' + subject + chr(10)) if subject else ''}TEXT:\n{text[:MAX_CHARS]}")
    cfg = types.GenerateContentConfig(
        temperature=0.2, max_output_tokens=1200,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    for attempt in range(2):
        try:
            resp = _get_client().models.generate_content(model=SUMMARY_MODEL, contents=prompt, config=cfg)
            return re.sub(r"[*_`#>]+", "", (resp.text or "").strip()) or "I couldn't produce a summary."
        except Exception as e:
            wait = limit_wait_seconds(e)
            if wait is not None and wait <= 35 and attempt == 0:
                time.sleep(wait + 0.5)
                continue
            if wait is not None:
                return (f"I've hit the Gemini free-tier limit. Try the summary again in about {int(wait)} seconds."
                        if wait < 90 else "I've hit the Gemini limit for today.")
            print("[summary] error:", str(e)[:200])
            return "I couldn't reach Gemini for the summary."
    return "I couldn't produce a summary."
