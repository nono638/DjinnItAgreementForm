"""The window layer after the 2.2.0 sweep: a settings file that can't be read is never written over on the app's own;
a Trial tick survives the answer about the firms; a name being typed survives a merge and a status refresh, its
editor opened again where it was; the AI's questions don't hold the reads back, and New job drops those still
waiting; a save that fails still closes Settings and doesn't stop the Outputs box; the Clarify box's ticks follow the
rows, not their places; a signature that can't be stored is said; Settings waits for the batch; the status says
Reading while a folder is still coming in; crashes are hooked without a log file; an AI answer finds its document in
the job it moved to, and is dropped once the AI is off; AI errors show as text; Open this job again during a batch
says so over Records; the AI tick setting; selftest fills the UCS form too; a run sheet that can't be opened stops
Generate with a message. All names are made up."""
import os
import sys
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from minute_filler.models import Attorney, Extraction  # noqa: E402

from helpers import pat_settings, text_doc, transcript_pdf  # noqa: E402
from test_gui_batch import wait  # noqa: E402


def firm(name, firm_name, checked=True):
    return Attorney(name=name, firm=firm_name, checked=checked)


@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.welcomed = True
    s.output_dir = str(tmp_path / "out")
    return make_window(s)


# ------------------------------------------------------------------ G1: a settings file that can't be read
def test_a_settings_file_that_cant_be_read_is_not_written_over(qt, make_window, monkeypatch, tmp_path):
    """Starting up (a warning, not the Welcome questions), reading a file, an option ticked, a firm answer and
    closing all save on their own: none of them may write the defaults over the user's file; Save in Settings
    does (the user's choice)."""
    from minute_filler.gui.dialogs import SettingsDialog
    from minute_filler.gui.preview import WelcomeDialog
    from minute_filler.settings import Settings
    path = Settings().path
    path.parent.mkdir(parents=True, exist_ok=True)
    broken = b'{"profile": {"name": "Pat Reporter"'
    path.write_bytes(broken)
    s = Settings.load()
    assert s.unreadable and not s.profile.name
    s.use_ai = False
    warned, welcomed = [], []
    monkeypatch.setattr(qt.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]) or 0)
    monkeypatch.setattr(WelcomeDialog, "exec", lambda self: welcomed.append(1) or 0)
    win = make_window(s)
    win._startup()
    assert warned and all("Save in Settings" in w for w in warned) and not welcomed
    win.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(win, lambda: bool(win.cur.docs))
    win.output_boxes["mofr"].setChecked(not win.output_boxes["mofr"].isChecked())
    win._record_firm_answer(firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC"), True)
    win.close()
    assert path.read_bytes() == broken
    dlg = SettingsDialog(s)
    dlg.p_name.setText("Dana Smith")
    dlg.accept()
    assert Settings.load().profile.name == "Dana Smith" and not s.unreadable


# ------------------------------------------------------------------ G2: a Trial tick before the firm answer
def test_a_trial_tick_survives_the_firm_answer(window):
    window.cur.case.attorneys = [firm("Dana Smith", "Smith Law"), firm("Sam Poe", "Smith Law Group, PLLC")]
    window._show_case()
    window._update_status()
    assert window.firm_ask_box.isVisibleTo(window)
    window.proc_boxes["Trial"].setChecked(True)
    window._answer_firm(True)
    assert window.proc_boxes["Trial"].isChecked() and "Trial" in window.cur.case.proc_types


# ------------------------------------------------------------------ G3: a cell being typed in when a merge comes
def test_a_name_being_typed_survives_a_merge(window, tmp_path):
    """An AI answer or a second document merges the job while a name is half typed: it is taken as typed."""
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(window, lambda: bool(window.cur.docs))
    item = window.att.item(0, 1)
    window.att.editItem(item)
    editor = next(w for w in window.att.viewport().findChildren(QtWidgets.QLineEdit))
    editor.setText("Dana Smith")
    window.paste.setFocus()  # (the answer comes while the focus is anywhere)
    window._remerge()  # (the merge joins the firm's names to the typed one: it is kept, first)
    assert window.cur.case.attorneys[0].name.startswith("Dana Smith")
    assert window.att.item(0, 1).text().startswith("Dana Smith")
    assert window.att.state() != QtWidgets.QAbstractItemView.EditingState


# ------------------------------------------------------------------ G4: the AI's questions on a pool of their own
def test_ai_questions_do_not_hold_the_reads_back(window, tmp_path, monkeypatch):
    """Two AI questions waiting on Ollama, a global pool of two threads: a document is still read meanwhile."""
    from PySide6.QtCore import QThreadPool
    from minute_filler.extract_llm import OllamaExtractor
    pool = QThreadPool.globalInstance()
    was = pool.maxThreadCount()
    pool.setMaxThreadCount(2)
    gate = threading.Event()

    def slow(self, ing):
        gate.wait(10)
        return Extraction()
    monkeypatch.setattr(OllamaExtractor, "extract", slow)
    window.s.use_ai, window.ai_ok = True, True
    docs = [text_doc(window.s, "Please send the minutes", "a.txt"), text_doc(window.s, "and again", "b.txt")]
    try:
        window._maybe_ai(window.cur, docs)  # (not in the job: their answers are dropped)
        assert window.ai_pending == 2
        window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
        wait(window, lambda: bool(window.cur.docs), seconds=5)  # (before: never, both threads held by the AI)
    finally:
        gate.set()
        getattr(window, "ai_pool", pool).waitForDone(5000)
        pool.waitForDone(5000)
        pool.setMaxThreadCount(was)
    wait(window, lambda: window.ai_pending == 0)


# ------------------------------------------------------------------ G5, G10: a settings file that can't be written
def test_a_save_that_fails_still_closes_settings(qt, monkeypatch):
    from minute_filler.gui.dialogs import SettingsDialog
    from minute_filler.settings import Settings

    def locked(self, force=False):
        raise PermissionError("settings.json is held by another program")
    monkeypatch.setattr(Settings, "save", locked)
    shown = []
    monkeypatch.setattr(qt.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or 0)
    s = pat_settings()
    dlg = SettingsDialog(s)
    dlg.p_name.setText("Dana Smith")
    dlg.accept()
    assert dlg.result() == qt.QDialog.Accepted and s.profile.name == "Dana Smith"
    assert len(shown) == 1 and "held by another program" in shown[0]


def test_a_save_that_fails_does_not_stop_the_outputs_box(window, monkeypatch):
    """A locked settings file: the option still takes, its panel still greys, and the window still closes."""
    from minute_filler.settings import Settings

    def locked(self, force=False):
        raise PermissionError("settings.json is held by another program")
    monkeypatch.setattr(Settings, "save", locked)
    box = window.output_boxes["invoice"]
    box.setChecked(False)
    assert "invoice" not in window.s.outputs and not window.output_opts["invoice"].isEnabled()
    window.sheet_box.setCurrentIndex(window.sheet_box.currentIndex())
    assert window.close()


# ------------------------------------------------------------------ G8: the Clarify box's ticks follow the rows
def test_clarify_ticks_follow_the_rows_not_their_places(window, tmp_path, monkeypatch):
    """An AI answer puts the rows in another order while the box asks who ordered: the row ticked is the one
    asked about, not whichever is first now."""
    from minute_filler.gui.dialogs import ClarifyDialog
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(window, lambda: bool(window.cur.docs))
    window.output_boxes["invoice"].setChecked(False)
    window.output_boxes["runsheet"].setChecked(False)
    rows = list(window.cur.case.attorneys)
    assert len(rows) == 2 and not any(a.checked for a in rows)
    first = rows[0].firm  # (the row the box lists first: Advocate & Partners)
    shown = []

    def reorder(self):
        shown.append(1)
        window.cur.case.attorneys.reverse()  # (as a merge may)
        return ClarifyDialog.Accepted
    monkeypatch.setattr(ClarifyDialog, "exec", reorder)
    monkeypatch.setattr(ClarifyDialog, "checked_attorneys", lambda self: [0])
    monkeypatch.setattr(ClarifyDialog, "answers", lambda self: {})
    window.fill()
    assert shown, "the box asking who ordered was not shown"
    assert {a.firm: a.checked for a in window.cur.case.attorneys} == {first: True, "Counsel & Counsel": False}
    assert len(window.cur.saved) == 1 and first.split(" ")[0] in window.cur.saved[0].name


# ------------------------------------------------------------------ G9: a signature that can't be stored
def test_a_signature_that_cant_be_stored_is_said(qt, monkeypatch, tmp_path):
    from minute_filler.gui.dialogs import SettingsDialog
    s = pat_settings()
    dlg = SettingsDialog(s)
    dlg._sig_new = str(tmp_path / "not there.png")
    shown = []
    monkeypatch.setattr(qt.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or 0)
    dlg._store_signature()
    assert len(shown) == 1 and "stays" in shown[0] and s.signature_image == ""


# ------------------------------------------------------------------ G11: Settings waits for the batch
def test_settings_wait_for_the_batch(window, monkeypatch):
    from minute_filler.gui.dialogs import SettingsDialog
    opened = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened.append(1) or 0)
    window.filling = True
    window.open_settings()
    window.filling = False
    assert not opened
    window.open_settings()
    assert opened


# ------------------------------------------------------------------ G13: a file read while a folder still comes in
def test_the_status_says_reading_while_a_folder_is_still_coming_in(window, tmp_path):
    window.work += 1  # (a folder still being looked through)
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(window, lambda: bool(window.cur.docs))
    window._update_status()
    assert window.status.text().startswith("Reading") and window.status.property("state") == "busy"
    assert not window.busy.isHidden()
    window.work -= 1
    window._update_status()
    assert "Ready to fill" in window.status.text()


# ------------------------------------------------------------------ G14: crashes are hooked without a log file
def test_crashes_are_hooked_without_a_log_file(monkeypatch):
    from minute_filler import log

    def no_file(*a, **k):
        raise OSError("read-only profile")
    log.shutdown()
    monkeypatch.setattr(log, "RotatingFileHandler", no_file)
    try:
        log.setup()
        assert sys.excepthook is log._excepthook and threading.excepthook is not threading.__excepthook__
    finally:
        log.shutdown()


# ------------------------------------------------------------------ G15, G16 and the AI turned off meanwhile
def ai_callbacks(window, monkeypatch):
    """Starts an AI question on a text document of the job and returns (the document, on_done, on_error)."""
    import minute_filler.gui.main_window as mw
    started = []
    monkeypatch.setattr(mw.Runner, "start", lambda self, fn, *a, on_done=None, on_error=None, **k:
                        started.append((on_done, on_error)))
    window.s.use_ai, window.ai_ok = True, True
    d = text_doc(window.s, "Please send the minutes", "a.txt")
    window.cur.docs.append(d)
    window._maybe_ai(window.cur, [d])
    assert len(started) == 1
    return d, started[0][0], started[0][1]


def test_an_ai_answer_finds_its_document_in_the_job_it_moved_to(window, monkeypatch):
    from minute_filler.batch import Job
    d, done, _ = ai_callbacks(window, monkeypatch)
    other = Job()
    other.docs, window.cur.docs = [d], []
    window.jobs = [other]  # (a read that ended meanwhile merged the job into another)
    window.cur = other
    merged = []
    monkeypatch.setattr(window, "_remerge", lambda job=None: merged.append(job))
    ex = Extraction()
    done(ex)
    assert d.ai is ex and merged == [other] and window.ai_pending == 0


def test_an_ai_answer_after_the_ai_was_turned_off_is_dropped(window, monkeypatch):
    d, done, _ = ai_callbacks(window, monkeypatch)
    window.s.use_ai = False
    done(Extraction())
    assert d.ai is None and window.ai_pending == 0


def test_ai_errors_show_as_text(window, monkeypatch):
    """The AI line is rich text: an error with angle brackets in it must not vanish into a tag."""
    _, _, failed = ai_callbacks(window, monkeypatch)
    failed("ConnectionError: <urlopen error [Errno 61] refused>")
    assert "&lt;urlopen error" in window.ai_label.text()


def test_the_ai_check_shows_its_message_as_text(window, monkeypatch):
    import minute_filler.gui.main_window as mw
    checks = []
    monkeypatch.setattr(mw.Runner, "start", lambda self, fn, *a, on_done=None, **k: checks.append(on_done))
    window.s.use_ai = True
    window._check_ai()
    checks[0]((False, "<no model>"))
    assert "&lt;no model&gt;" in window.ai_label.text() and 'href="setup"' in window.ai_label.text()


# ------------------------------------------------------------------ G17: Open this job again during a batch
def test_open_this_job_again_during_a_batch_says_so_over_records(window, monkeypatch, qt):
    shown = []
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: shown.append(a[0]) or 0)
    records = qt.QWidget()
    window._records_win = records
    window.filling = True
    assert not window.open_past_job("{}")
    window.filling = False
    assert shown == [records]


# ------------------------------------------------------------------ the AI tick setting
def test_the_ai_tick_setting_is_in_settings(qt):
    """Settings → AI offers the choices of AI_TICKS, "text" by default with a tooltip saying why; the one chosen
    is saved."""
    from minute_filler.gui.dialogs import SettingsDialog
    from minute_filler.settings import AI_TICKS, Settings
    s = pat_settings()
    dlg = SettingsDialog(s)
    assert [dlg.a_ticks.itemText(i) for i in range(dlg.a_ticks.count())] == list(AI_TICKS.values())
    assert dlg.a_ticks.currentData() == "text" and "who appeared" in dlg.a_ticks.toolTip()
    dlg.a_ticks.setCurrentIndex(dlg.a_ticks.findData("never"))
    dlg.accept()
    assert s.ai_ticks == "never" and Settings.load().ai_ticks == "never"


# ------------------------------------------------------------------ selftest fills the default form too
def test_selftest_fills_the_ucs_form_too(qt, tmp_path, monkeypatch):
    import minute_filler.fill as fill
    from minute_filler.main import selftest
    chosen = []

    def noted(case, s, out):
        chosen.append((s.form_choice, out.name))
        return []
    monkeypatch.setattr(fill, "fill_all", noted)
    selftest(str(tmp_path / "out"), [str(transcript_pdf(tmp_path / "Roe.pdf", 3))])
    assert chosen == [("ucs", "ucs"), ("clean", "clean"), ("original", "original")]


# ------------------------------------------------------------------ the review of the sweep's own fixes
def typing_in(window, text: str):
    """Opens the editor of the first Name cell and types `text` in it; returns the editor."""
    window.activateWindow()
    window.att.setCurrentCell(0, 1)
    window.att.editItem(window.att.item(0, 1))
    viewport = window.att.viewport()
    editor = next(w for w in viewport.findChildren(QtWidgets.QLineEdit) if w.isVisibleTo(viewport))
    editor.setFocus()
    editor.setText(text)
    return editor


def open_editor(window):
    viewport = window.att.viewport()
    return next((w for w in viewport.findChildren(QtWidgets.QLineEdit) if w.parent() == viewport
                 and w.isVisibleTo(viewport)), None)


def test_a_status_refresh_leaves_a_name_being_typed_alone(window, tmp_path):
    """The status pill only reads the fields: 'Dana Smi' stays in its open editor, so the next keys go on with it
    (before: the editor was closed, and the next key typed replaced the whole name)."""
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(window, lambda: bool(window.cur.docs))
    editor = typing_in(window, "Dana Smi")
    window._update_status()
    assert window.att.state() == QtWidgets.QAbstractItemView.EditingState
    assert open_editor(window) is editor and editor.text() == "Dana Smi"


def test_a_merge_from_the_background_opens_the_editor_again_where_it_was(window, tmp_path):
    """An AI answer or a document merges the job while 'Dana Smi' is typed: it is kept, and the editor opens again on
    that row with the cursor after it, so that typing 'th' makes 'Dana Smith'."""
    window.add_files([str(transcript_pdf(tmp_path / "Roe.pdf", 5))])
    wait(window, lambda: bool(window.cur.docs))
    typing_in(window, "Dana Smi")
    window._remerge()
    editor = open_editor(window)
    assert window.att.state() == QtWidgets.QAbstractItemView.EditingState and editor is not None
    assert editor.text().startswith("Dana Smi") and editor.cursorPosition() == len("Dana Smi")
    assert not editor.hasSelectedText()


def test_new_job_drops_the_ai_questions_still_waiting(window, monkeypatch):
    """Three AI questions on the one-thread AI pool, then New job: the two still waiting are never asked (before: the
    next job's questions waited behind them, each up to the AI timeout)."""
    from minute_filler.extract_llm import OllamaExtractor
    gate, asked = threading.Event(), []

    def slow(self, ing):
        asked.append(ing.name)
        gate.wait(10)
        return Extraction()
    monkeypatch.setattr(OllamaExtractor, "extract", slow)
    window.s.use_ai, window.ai_ok = True, True
    docs = [text_doc(window.s, f"Please send the minutes {n}", f"{n}.txt") for n in "abc"]
    try:
        window._maybe_ai(window.cur, docs)
        assert window.ai_pending == 3
        window._clear_jobs()
        assert len(window.ai_runner._live) <= 1
    finally:
        gate.set()
        window.ai_pool.waitForDone(5000)
    assert len(asked) <= 1


def test_a_run_sheet_that_cant_be_opened_is_said_and_nothing_is_made(window, monkeypatch):
    """A workbook of the case that can't be opened just now (runsheet.SheetUnreadable): Generate says which and stops,
    instead of the "Something went wrong" box."""
    from minute_filler.gui import main_window as mw
    from minute_filler.runsheet import SheetUnreadable

    def unreadable(*a, **k):
        raise SheetUnreadable("Roe - Run Sheet.xlsx could not be read. If it is being synced, try again in a moment.")
    shown = []
    monkeypatch.setattr(mw, "find_sheets", unreadable)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or 0)
    window.s.runsheet_existing = "ask"
    assert window._run_sheet_for(window.cur) is None
    assert "Roe - Run Sheet.xlsx" in shown[0] and "Nothing was made. Press Generate again" in shown[0]
    assert window._run_sheet_for(window.cur, "Generate all") is None and "Press Generate all again" in shown[1]
