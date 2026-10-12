# Veera - desktop voice assistant (Windows)

An eagle mascot in a suit that lives in the corner of your screen. Say **"Hey Veera"** and control your PC by voice:
open apps, search the web, manage files, read documents (with OCR), check email, adjust volume/brightness and more.
Speech recognition and command understanding run **locally**; Gemini is used only to write summaries.

## Install (Windows 10/11)
1. Install [Python 3.10+](https://www.python.org/downloads/) - tick **Add python.exe to PATH**.
2. Download and unzip the Veera package.
3. Double-click **install.bat** (creates `.venv`, installs `requirements.txt`, creates `.env`).
4. *(Optional)* Put your free Gemini key in `.env` for "summarize ..." commands.
5. Double-click **run.bat**, then say **"Hey Veera"**.

Manual install: `python -m venv .venv` -> `.venv\Scripts\activate` -> `pip install -r requirements.txt` -> `python veera.py`

See [COMMANDS.md](COMMANDS.md) for everything he understands. Start with Windows: `python startup.py on`.
