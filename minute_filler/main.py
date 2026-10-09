"""Entry point: python -m minute_filler.main  (or the packaged .exe).

Opens the window, with any files or folders given on the command line ("Open with", dropped on the exe).
One window at a time: when one is open already, a launch hands it its files and quits (gui/single.py).
Without a window: --selftest OUTDIR files... checks a build, and --batch OUTDIR [--outputs ...] files...
makes the forms of a whole folder (see selftest and batch).
"""
from __future__ import annotations

import sys
from pathlib import Path


def selftest(out_dir: str, files: list[str]) -> int:
    """Headless check of a build: reads each file and fills its minute agreement on each of the three forms (ucs,
    clean and original: fill.FORMS), checks the libraries a build can lose without anything else failing (fuzzy_search,
    regex_search, heic_photos, update_check, printing, math_pdf, single_instance, moving_picture: True each,
    else the error), then writes selftest.json into out_dir.
    The default settings are used; the saved ones are not touched."""
    import json
    from minute_filler.extract_regex import RegexExtractor
    from minute_filler.fill import fill_all
    from minute_filler.ingest import ingest_file, ocr_available
    from minute_filler.merge import merge
    from minute_filler.settings import Settings

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = Settings()
    report = {"ocr_available": ocr_available(), "rate_sheet": s.sheet().name,
              "speeds": [sp.label() for sp in s.sheet().speeds], "results": []}
    try:  # the Records search's Fuzzy box needs rapidfuzz in the build: a misspelled firm must still be found
        from minute_filler.records import matcher
        report["fuzzy_search"] = matcher("Counsle", "fuzzy")(["Counsel & Counsel"])
    except Exception as e:
        report["fuzzy_search"] = f"{type(e).__name__}: {e}"
    try:  # the Regex box needs the regex library (it can stop a search that would freeze the window)
        from minute_filler.records import matcher
        report["regex_search"] = matcher("^counsel", "regex")(["Counsel & Counsel"])
    except Exception as e:
        report["regex_search"] = f"{type(e).__name__}: {e}"
    try:  # iPhone photos need pillow-heif and its libheif DLL: a small HEIC made and read back
        import io
        from PIL import Image
        from minute_filler.ingest import HEIF_OK
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buf, format="HEIF")
        report["heic_photos"] = HEIF_OK and Image.open(io.BytesIO(buf.getvalue())).size == (64, 64)
    except Exception as e:
        report["heic_photos"] = f"{type(e).__name__}: {e}"
    try:  # the daily look for a newer version asks GitHub over https: needs ssl and its certificates
        import ssl
        from minute_filler import update
        report["update_check"] = bool(ssl.create_default_context()) and update.version_key("v1.10.2") == (1, 10, 2)
    except Exception as e:
        report["update_check"] = f"{type(e).__name__}: {e}"
    try:  # Print... needs Qt's print support in the build
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter  # noqa: F401
        report["printing"] = True
    except Exception as e:
        report["printing"] = f"{type(e).__name__}: {e}"
    try:  # The math's Save as PDF... lays out HTML with pymupdf.Story: its tables, as explain() makes them
        from minute_filler.invoice import FirmInvoice, InvoiceOpts
        from minute_filler.invoice_calc import Quote, QuoteLine
        from minute_filler.invoice_math import explain, to_pdf
        from minute_filler.models import CaseInfo
        from decimal import Decimal
        q = Quote("Regular", 1, 2, [QuoteLine("Copy", Decimal("1.00"), 2, Decimal("2.00"))])
        tables = explain([(FirmInvoice(None, CaseInfo(), InvoiceOpts(1, 2), [q]), "")])[0]
        report["math_pdf"] = "<table" in tables and to_pdf(tables, out / "math.pdf").exists()
    except Exception as e:
        report["math_pdf"] = f"{type(e).__name__}: {e}"
    try:  # one window at a time needs Qt's local sockets (QtNetwork) in the build
        import uuid
        from PySide6.QtCore import QCoreApplication
        from minute_filler.gui.single import SingleInstance
        app = QCoreApplication.instance() or QCoreApplication(["selftest"])  # noqa: F841
        name = f"YinItAgreementForm-selftest-{uuid.uuid4().hex}"  # (never the window's own, nor another selftest's)
        one = SingleInstance(name)
        got: list = []
        one.files_received.connect(got.extend)
        other = SingleInstance(name)
        report["single_instance"] = one.claim() and not other.claim() and other.send(["a.pdf"]) \
            and (app.processEvents() or got == ["a.pdf"])
        one.close()
    except Exception as e:
        report["single_instance"] = f"{type(e).__name__}: {e}"
    try:  # the swirling yin-yang while documents are read is a WebP: needs Qt's imageformats plugin for it
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QImageReader
        app = QCoreApplication.instance() or QCoreApplication(["selftest"])  # noqa: F841 (loads the plugins)
        from minute_filler.gui.widgets import ASSETS
        reader = QImageReader(str(ASSETS / "yin_working.webp"))
        report["moving_picture"] = reader.supportsAnimation() and reader.imageCount() > 1
    except Exception as e:
        report["moving_picture"] = f"{type(e).__name__}: {e}"
    for f in files:
        try:
            case = merge([RegexExtractor(s.profile).extract(ingest_file(f))], s)
            pdfs = []
            for choice in ("ucs", "clean", "original"):
                s.form_choice = choice
                pdfs += [p.name for p in fill_all(case, s, out / choice)]
            report["results"].append({"file": f, "fields": {k: v.value for k, v in case.fields.items()},
                                      "attorneys": [a.name or a.firm for a in case.attorneys], "pdfs": pdfs})
        except Exception as e:
            report["results"].append({"file": f, "error": f"{type(e).__name__}: {e}"})
    (out / "selftest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


def batch(out_dir: str, paths: list[str], outputs: list[str] | None = None) -> int:
    """Headless batch: --batch OUTDIR [--outputs agreement,mofr,invoice,runsheet] files/folders... fills one set of
    forms per case and date with the saved settings, and writes batch.json (what was grouped, saved or
    unreadable). The files go into OUTDIR whatever folders Settings give each output (run sheets still go to
    their own folder). Without --outputs, the outputs ticked in the window are made. Returns 0, 1 when a file or
    job had a problem, or 2 for an unknown output (main also returns 2 when OUTDIR or the files are missing)."""
    import json
    from minute_filler.batch import expand_paths, fill_jobs, group, read_docs
    from minute_filler.extract_regex import use_firm_answers
    from minute_filler.settings import OUTPUTS, Settings

    unknown = [o for o in outputs or [] if o not in OUTPUTS]
    if unknown:  # a typo ("runsheets") must not quietly make nothing
        if sys.stderr:
            print(f"Unknown output(s): {', '.join(unknown)}. The outputs are: {', '.join(OUTPUTS)}.", file=sys.stderr)
        return 2
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = Settings.load()
    use_firm_answers(s.firm_answers)  # (rows the user said are one firm, or not, are read so)
    s.output_dir = str(out)
    s.output_dirs = {k: v for k, v in s.output_dirs.items() if k == "runsheet"}  # everything else into OUTDIR
    docs, errors = read_docs(expand_paths(paths), s)
    jobs = group(docs, s)
    fill_jobs([j for j in jobs if j.include], s, outputs=outputs)  # not the documents that name no case
    report = {"documents": len(docs), "unreadable": errors, "jobs": [
        {"case": j.case.get("case_name"), "index": j.case.get("index_no"), "dates": j.case.get("dates"),
         "documents": [d.path for d in j.docs], "problems": j.problems(),
         "forms": [p.name for p in j.saved], "error": j.error} for j in jobs]}
    (out / "batch.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if errors or any(j.error for j in jobs) else 0


def app_icon() -> Path:
    """The window icon, from the package's assets folder (in the built program this script is not in the
    package's folder, so a path next to it would miss)."""
    import minute_filler
    return Path(minute_filler.__file__).resolve().parent / "assets" / "app.ico"


def _tell_user_about_crashes(app) -> None:
    """A crash that nothing else caught: it is already in the log; say so instead of failing silently."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QMessageBox

    from minute_filler import log

    class Notifier(QObject):
        crashed = Signal(str)  # may be raised from any thread; shown on the window's thread

    notifier = Notifier()
    showing = []

    def show(what: str) -> None:
        if showing:  # one box at a time, however many errors follow
            return
        showing.append(1)
        box = QMessageBox(QMessageBox.Warning, "Something went wrong",
                          "The program hit an unexpected problem. You can carry on, but if it keeps happening "
                          "please send the log (Help \u2192 Copy details for a problem report).\n\n" + what)
        folder = box.addButton("Open log folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() == folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log.log_dir())))
        showing.clear()

    notifier.crashed.connect(show)
    log.on_crash = notifier.crashed.emit
    app._crash_notifier = notifier  # keep it alive


GATHER_MS = 400  # files from launches this close together are one drop (see _take_launches)


def _take_launches(app, win, single, files: list[str]) -> None:
    """The files this launch was given and those later launches hand over (gui/single.py) go to the window as
    drops. Launches close together are gathered into one drop: "Open with" on two transcripts starts two
    launches, and one drop of both makes a job per case and date, where two drops of one would put the second
    day into the first day's job. Files that come while a question is on screen (a modal dialog) wait until it
    is answered: dropped then, they could be lost by its answer (New job's "Clear all jobs?")."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    gathered: list[str] = list(files)
    timer = QTimer(win)
    timer.setSingleShot(True)
    timer.setInterval(GATHER_MS)

    def arrived(paths: list[str]) -> None:
        """Another launch: bring this window forward, and take its files with the others coming now."""
        win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()
        gathered.extend(paths)
        timer.start()

    def drop() -> None:
        if QApplication.activeModalWidget() is not None:
            timer.start()  # try again once the question is answered
            return
        paths = list(dict.fromkeys(gathered))
        gathered.clear()
        if paths:
            win.add_files(paths)

    timer.timeout.connect(drop)
    single.deliver(arrived)
    if gathered:
        timer.start()


def main() -> int:
    """Runs --selftest or --batch, else the window (or, when a window is open already, hands it the files and
    returns 0); returns the exit code."""
    from minute_filler import log
    log.setup()
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3:])
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        rest, outputs = sys.argv[3:], None
        if rest[:1] == ["--outputs"]:
            outputs, rest = [o.strip().lower() for o in (rest[1] if len(rest) > 1 else "").split(",") if o.strip()], \
                rest[2:]
        if len(sys.argv) < 3 or not rest or outputs == []:
            # a bare --batch must not open the window, which would read OUTDIR as a folder of documents
            if sys.stderr:
                print("usage: --batch OUTDIR [--outputs agreement,mofr,invoice,runsheet] files/folders...",
                      file=sys.stderr)
            return 2
        return batch(sys.argv[2], rest, outputs)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    try:  # own taskbar icon/grouping instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YinItAgreementForm")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("YinItAgreementForm")
    files = [a for a in sys.argv[1:] if Path(a).exists()]  # "Open with" / files or a folder dragged onto the exe
    # One window at a time, decided before the window's code is even loaded (it takes a second or more), so
    # that a launch right after this one finds it
    try:
        from minute_filler.gui.single import SingleInstance, hand_over
        single = SingleInstance(parent=app)
        if hand_over(single, [str(Path(f).resolve()) for f in files]):
            log.log.info("another copy is open: handed it the files and quit")
            return 0
    except Exception as e:  # (a build without Qt's local sockets: run, as a copy on its own)
        log.error("could not check for another copy", e)
        single = None

    from minute_filler.gui.main_window import MainWindow
    from minute_filler.gui.theme import apply_theme
    from minute_filler.settings import Settings

    _tell_user_about_crashes(app)
    app.setStyle("Fusion")
    icon = app_icon()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    settings = Settings.load()
    from minute_filler.gui.zoom import set_zoom
    set_zoom(settings.zoom)  # the zoom (Ctrl + / Ctrl -), kept from last time
    apply_theme(app, settings.theme)
    win = MainWindow(settings, app)
    win.show()
    if single is None:
        if files:
            win.add_files(files)
    else:
        _take_launches(app, win, single, files)
    code = app.exec()
    if single is not None:
        single.close()
    from PySide6.QtCore import QThreadPool
    from minute_filler.gui.dialogs import _BACKGROUND
    if QThreadPool.globalInstance().activeThreadCount() or win.ai_pool.activeThreadCount() \
            or any(t.isRunning() for t in _BACKGROUND):
        # Settings are saved. Don't linger (unseen, for minutes) until the AI model answers.
        import os
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
