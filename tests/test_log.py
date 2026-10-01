"""The log file: it works, stays small, and never holds what the documents said."""
import sys

import pytest

from minute_filler import log
from minute_filler.ingest import ingest_file
from minute_filler.settings import Settings


@pytest.fixture
def logfile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    log.shutdown()
    log.setup()
    yield log.log_path()
    log.shutdown()


def text(logfile):
    for h in log.log.handlers:
        h.flush()
    return logfile.read_text(encoding="utf-8")


def test_startup_is_recorded(logfile):
    t = text(logfile)
    assert "started: version" in t and "Windows" in t or "Python" in t


def test_setup_twice_does_not_duplicate(logfile):
    log.setup()
    log.log.info("once")
    assert text(logfile).count("once") == 1


def test_scrub_removes_case_and_personal_data():
    s = log.scrub(r"cannot open C:\Users\jane\Cases\Powell v Powell Invoice.pdf for jdoe@doeroelaw.com "
                  r"index 712222-2024 call (555) 555-0100 in C:\Users\jane\Cases")
    for secret in ("Powell", "jane", "jdoe", "doeroelaw", "712222", "555-0100", "Cases"):
        assert secret not in s
    assert "<file.pdf>" in s and "<email>" in s and "<path>" in s
    assert len(log.scrub("x" * 1000)) < 400
    assert log.scrub("KeyError: 'court'") == "KeyError: 'court'"       # ordinary messages survive


def test_an_unreadable_file_is_logged_without_its_name(logfile, tmp_path):
    bad = tmp_path / "Powell v Powell 712222-2024.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(ValueError):
        ingest_file(bad)
    t = text(logfile)
    assert "could not read a .pdf file" in t
    assert "Powell" not in t and "712222" not in t


def test_crashes_are_logged_with_traceback_and_reported(logfile):
    seen = []
    log.on_crash = seen.append
    try:
        try:
            raise RuntimeError("bad data for jdoe@doeroelaw.com in 712222-2024")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        log.on_crash = None
    t = text(logfile)
    assert "unexpected error" in t and "Traceback:" in t
    assert "test_crashes_are_logged" in t          # the code path is there
    assert "RuntimeError: bad data for <email> in #" in t
    assert "jdoe" not in t and "712222" not in t
    assert seen and "jdoe" not in seen[0]


def test_handled_errors_are_logged(logfile):
    try:
        raise PermissionError(r"C:\Users\jane\Docs\Smith v Jones.pdf is open")
    except PermissionError as e:
        log.error("could not fill the form", e)
    t = text(logfile)
    assert "could not fill the form" in t and "PermissionError" in t and "Smith" not in t


def test_the_file_stays_small(logfile):
    for i in range(30000):
        log.log.info("line %d %s", i, "x" * 60)
    total = sum(p.stat().st_size for p in logfile.parent.glob("app.log*"))
    assert total < 3_200_000 and len(list(logfile.parent.glob("app.log*"))) <= 3


def test_diagnostics_have_the_facts_and_no_case_data(logfile):
    s = Settings()
    s.use_ai = False
    d = log.diagnostics(s)
    assert "DjinnItAgreementForm 1." in d and "Windows" in d and "text recognition" in d
    assert "AI helper: off" in d and "recent log" in d
    s.profile.name, s.profile.email = "Pat Reporter", "preporter@example.com"
    d = log.diagnostics(s)
    assert "Pat Reporter" not in d and "preporter@example.com" not in d


def test_no_log_when_the_folder_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    log.shutdown()
    (tmp_path / "DjinnItAgreementForm").write_text("a file where the folder should be")
    try:
        log.setup()                       # must not raise
        log.log.info("still fine")
    finally:
        log.shutdown()


def test_menu_has_the_log_items(qt, make_window):
    s = Settings()
    s.use_ai = False
    win = make_window(s)
    names = [a.text() for m in win.menuBar().findChildren(qt.QMenu) for a in m.actions()]
    assert "Open the &log folder" in names and "&Copy details for a problem report" in names
    win._copy_diagnostics()
    assert "DjinnItAgreementForm" in qt.QApplication.clipboard().text()
