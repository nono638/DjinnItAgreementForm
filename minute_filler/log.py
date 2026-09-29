"""A small log file to help with problem reports: %APPDATA%\\DjinnItAgreementForm\\logs\\app.log

It records what the program did and what went wrong (versions, file types, crashes with their
tracebacks), never what the documents said. Anything that could come from a document - file
names, e-mail addresses, index numbers, long digit strings - is removed by `scrub` before it is
written, and nothing is sent anywhere. The file stays under about 3 MB (1 MB, plus two older copies).
"""
from __future__ import annotations

import logging
import platform
import re
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from . import __version__
from .settings import settings_dir

NAME = "djinn"
log = logging.getLogger(NAME)
_handler: RotatingFileHandler | None = None
on_crash: Callable[[str], None] | None = None  # the window sets this to tell the user

_FILES = r"pdf|docx?|txt|eml|msg|html?|jpe?g|png|gif|bmp|tiff?|webp|heic|csv|md"
_SCRUBS = [
    (re.compile(rf"[A-Za-z]:\\[^\"'<>|\n]*?\.({_FILES})\b", re.I), lambda m: f"<file.{m[1].lower()}>"),
    (re.compile(r"[A-Za-z]:\\[^\s\"'<>|]+"), "<path>"),
    (re.compile(rf"[\w .,'()&-]{{1,60}}\.({_FILES})\b", re.I), lambda m: f"<file.{m[1].lower()}>"),
    (re.compile(r"\S+@\S+\.\w+"), "<email>"),
    (re.compile(r"\d{5,}(?:[-/]\d{2,4})?"), "#"),
    (re.compile(r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}"), "<phone>"),
]


def scrub(text: object, limit: int = 300) -> str:
    """The text with anything that could be case or personal data taken out, and cut short."""
    s = str(text).replace("\r", " ")
    for pattern, repl in _SCRUBS:
        s = pattern.sub(repl, s)
    return s if len(s) <= limit else s[:limit] + "..."


def describe(exc: BaseException) -> str:
    return scrub(f"{type(exc).__name__}: {exc}")


class _Formatter(logging.Formatter):
    def formatException(self, ei) -> str:
        # where it happened (file, line, function - no source text, no folders), then the scrubbed message
        etype, exc, tb = ei
        frames = [f"  {Path(f.filename).name}:{f.lineno} in {f.name}" for f in traceback.extract_tb(tb)]
        return "Traceback:\n" + "\n".join(frames) + "\n" + describe(exc)


def log_dir() -> Path:
    d = settings_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return log_dir() / "app.log"


def windows_version() -> str:
    try:
        v = sys.getwindowsversion()
        return f"Windows {v.major}.{v.minor} build {v.build}, {platform.machine()}"
    except AttributeError:
        return platform.platform()


def setup() -> None:
    """Starts the log file (once) and records crashes that nothing else catches."""
    global _handler
    if _handler is not None:
        return
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        _handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:  # a read-only profile: run without a log rather than not at all
        _handler = None
        log.addHandler(logging.NullHandler())
        return
    _handler.setFormatter(_Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(_handler)
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
    frozen = "installed app" if getattr(sys, "frozen", False) else "from source"
    log.info("started: version %s (%s), %s, Python %s", __version__, frozen, windows_version(),
             platform.python_version())


def shutdown() -> None:
    """Closes the file and puts things back (tests use this)."""
    global _handler
    if _handler is not None:
        log.removeHandler(_handler)
        _handler.close()
        _handler = None
    sys.excepthook = sys.__excepthook__
    threading.excepthook = threading.__excepthook__


def _excepthook(etype, exc, tb) -> None:
    if issubclass(etype, (KeyboardInterrupt, SystemExit)):
        return
    log.error("unexpected error", exc_info=(etype, exc, tb))
    if on_crash:
        try:
            on_crash(describe(exc))
        except Exception:
            pass


def error(what: str, exc: BaseException) -> None:
    """Records a failure the program handled itself (a message box was shown, the job went on)."""
    log.error("%s", what, exc_info=(type(exc), exc, exc.__traceback__))


def recent(lines: int = 60) -> str:
    try:
        text = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log yet)"
    return "\n".join(text[-lines:])


def diagnostics(settings=None) -> str:
    """Text for a problem report: versions, the machine, a few settings and the recent log."""
    from .ingest import ocr_available
    out = [f"DjinnItAgreementForm {__version__} ({'installed app' if getattr(sys, 'frozen', False) else 'from source'})",
           windows_version(), f"Python {platform.python_version()}",
           f"Windows text recognition (photos, scans): {'available' if ocr_available() else 'NOT available'}"]
    if settings is not None:
        out.append(f"Form: {settings.form_choice}; AI helper: {'on' if settings.use_ai else 'off'}; "
                   f"instructions page: {settings.include_instructions}; flatten: {settings.flatten}")
    out += ["", f"--- recent log ({log_path()}) ---", recent()]
    return "\n".join(out)
