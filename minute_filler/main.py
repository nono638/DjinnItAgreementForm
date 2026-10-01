"""Entry point: python -m minute_filler.main  (or the packaged .exe)."""
from __future__ import annotations

import sys
from pathlib import Path


def selftest(out_dir: str, files: list[str]) -> int:
    """Headless check of a build: extract + fill each file, write a report. No settings are touched."""
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
    for f in files:
        try:
            case = merge([RegexExtractor(s.profile).extract(ingest_file(f))], s)
            pdfs = []
            for choice in ("clean", "original"):
                s.form_choice = choice
                pdfs += [p.name for p in fill_all(case, s, out / choice)]
            report["results"].append({"file": f, "fields": {k: v.value for k, v in case.fields.items()},
                                      "attorneys": [a.name or a.firm for a in case.attorneys], "pdfs": pdfs})
        except Exception as e:
            report["results"].append({"file": f, "error": f"{type(e).__name__}: {e}"})
    (out / "selftest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


def batch(out_dir: str, paths: list[str], outputs: list[str] | None = None) -> int:
    """Headless batch: --batch OUTDIR [--outputs agreement,mofr,invoice] files/folders... fills one set of
    forms per case and date with the saved settings, and writes batch.json (what was grouped, saved or
    unreadable). Without --outputs, the outputs ticked in the window are made."""
    import json
    from minute_filler.batch import expand_paths, fill_jobs, group, read_docs
    from minute_filler.settings import Settings

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = Settings.load()
    s.output_dir = str(out)
    docs, errors = read_docs(expand_paths(paths), s)
    jobs = group(docs, s)
    fill_jobs([j for j in jobs if j.include], s, outputs=outputs)  # not the documents that name no case
    report = {"documents": len(docs), "unreadable": errors, "jobs": [
        {"case": j.case.get("case_name"), "index": j.case.get("index_no"), "dates": j.case.get("dates"),
         "documents": [d.path for d in j.docs], "problems": j.problems(),
         "forms": [p.name for p in j.saved], "error": j.error} for j in jobs]}
    (out / "batch.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if errors or any(j.error for j in jobs) else 0


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


def main() -> int:
    from minute_filler import log
    log.setup()
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3:])
    if len(sys.argv) > 3 and sys.argv[1] == "--batch":
        rest, outputs = sys.argv[3:], None
        if len(rest) > 1 and rest[0] == "--outputs":
            outputs, rest = [o.strip().lower() for o in rest[1].split(",") if o.strip()], rest[2:]
        return batch(sys.argv[2], rest, outputs)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from minute_filler.gui.main_window import MainWindow
    from minute_filler.gui.theme import apply_theme
    from minute_filler.settings import Settings

    try:  # own taskbar icon/grouping instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DjinnItAgreementForm")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("DjinnItAgreementForm")
    _tell_user_about_crashes(app)
    app.setStyle("Fusion")
    icon = Path(__file__).with_name("assets") / "app.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    settings = Settings.load()
    apply_theme(app, settings.theme)
    win = MainWindow(settings, app)
    win.show()
    files = [a for a in sys.argv[1:] if Path(a).exists()]  # "Open with" / files or a folder dragged onto the exe
    if files:
        win.add_files(files)
    code = app.exec()
    from PySide6.QtCore import QThreadPool
    from minute_filler.gui.dialogs import _BACKGROUND
    if QThreadPool.globalInstance().activeThreadCount() or any(t.isRunning() for t in _BACKGROUND):
        # Settings are saved. Don't linger (unseen, for minutes) until the AI model answers.
        import os
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
