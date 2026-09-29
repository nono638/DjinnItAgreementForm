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


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3:])

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
    app.setStyle("Fusion")
    icon = Path(__file__).with_name("assets") / "app.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    settings = Settings.load()
    apply_theme(app, settings.theme)
    win = MainWindow(settings, app)
    win.show()
    files = [a for a in sys.argv[1:] if Path(a).is_file()]  # "Open with" / drag onto the exe
    if files:
        win.add_files(files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
