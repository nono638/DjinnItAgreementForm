"""The window's smaller touches (2.0.0): New job greyed out while there is nothing to clear, the Records button in
a hue of its own, the job list's ⚠ explained (the mark, the count and the tooltip agree), and the Run sheet box
ticked when two or more reporters wrote a case's transcripts (unticked when one did) without changing the
saved choice."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from minute_filler.batch import case_reporters  # noqa: E402

from helpers import pat_settings, transcript_pdf  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch, make_window):
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    return make_window(s)


def wait(win, until, seconds=20):
    """Runs the event loop until no work is running and `until()` holds; fails after `seconds`."""
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_new_job_is_greyed_out_with_nothing_to_clear(window, tmp_path):
    assert not window.new_btn.isEnabled() and not window.new_action.isEnabled()
    window.paste.setPlainText("Please send the minutes")
    assert window.new_btn.isEnabled()
    window.paste.clear()
    assert not window.new_btn.isEnabled()
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=5))])
    wait(window, lambda: window.cur.docs)
    assert window.new_btn.isEnabled() and window.new_action.isEnabled()
    window.new_job()
    assert not window.cur.docs and not window.new_btn.isEnabled()


def test_the_records_button_has_its_own_hue(window):
    from minute_filler.gui.theme import DARK, LIGHT, QSS
    assert window.rec_btn.objectName() == "records"
    assert "QPushButton#records {" in QSS
    for tokens in (LIGHT, DARK):
        assert tokens["records_bg"] not in (tokens["card"], tokens["accent"])


def test_the_job_lists_marks_count_and_tooltips_agree(window, tmp_path):
    window.s.outputs = ["agreement"]
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=5, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=5, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    for j in window.jobs:
        j.case.attorneys[0].checked = True
    window.jobs[1].case.fields["judge"].value = ""  # something to check on one day only
    window._refresh_job_labels()
    marked = [window.job_list.topLevelItem(i).text(0).startswith("⚠") for i in range(2)]
    assert marked == [False, True]
    assert "1 to check (⚠)" in window.jobs_label.text() and "⚠" in window.jobs_label.toolTip()
    tip = window.job_list.topLevelItem(1).toolTip(0)
    assert "To check before Generate:" in tip and "⚠ Judge / Justice is missing" in tip
    assert "To check" not in window.job_list.topLevelItem(0).toolTip(0)
    window.jobs[0].error = "PermissionError: the file is open"  # a failed Generate is a reason too
    window._refresh_job_labels()
    assert window.job_list.topLevelItem(0).text(0).startswith("⚠") and "2 to check" in window.jobs_label.text()
    assert "Not saved: PermissionError" in window.job_list.topLevelItem(0).toolTip(0)


def test_the_run_sheet_is_ticked_for_two_reporters_and_not_for_one(window, tmp_path):
    from minute_filler.settings import Settings
    box = window.output_boxes["runsheet"]
    assert not box.isChecked() and "runsheet" not in window.s.outputs
    two = transcript_pdf(tmp_path / "two.pdf", pages=6, initials=["pr", "", "", "ds", "", ""])
    window.add_files([str(two)])
    wait(window, lambda: window.cur.docs)
    assert box.isChecked() and "runsheet" in window._outputs()
    assert "runsheet" not in window.s.outputs and "runsheet" not in Settings.load().outputs  # this job only
    window.output_boxes["invoice"].setChecked(True)  # another box clicked: the run sheet stays as saved
    assert window.s.outputs == ["agreement", "invoice"]
    window.new_job()
    assert not box.isChecked()

    window.s.outputs = ["agreement", "runsheet"]  # ticked as the default, but one reporter wrote it
    window.output_boxes["runsheet"].blockSignals(True)
    box.setChecked(True)
    window.output_boxes["runsheet"].blockSignals(False)
    window.add_files([str(transcript_pdf(tmp_path / "one.pdf", pages=6, initials=["pr"] * 6))])
    wait(window, lambda: window.cur.docs)
    assert not box.isChecked() and "runsheet" not in window._outputs()
    assert window.s.outputs == ["agreement", "runsheet"]
    box.setChecked(True)  # the user wants one anyway: it is kept
    assert "runsheet" in window._outputs() and not window.cur.runsheet_unneeded
    window._auto_runsheet()
    assert box.isChecked()


def test_the_run_sheet_counts_the_reporters_of_a_case_across_its_days(window, tmp_path):
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=4, date="June 3, 2026", initials=["pr"] * 4)),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=4, date="June 4, 2026", initials=["ds"] * 4))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    assert window.output_boxes["runsheet"].isChecked()
    assert case_reporters(window.jobs) == {"pr", "ds"}  # (the title page's Pat Reporter is PR)
    assert not any(j.runsheet_unneeded for j in window.jobs)


def test_an_old_ai_check_answering_late_is_ignored(window, monkeypatch):
    """Settings saved twice (another model): the first, slower check's answer overwrote the newer one's."""
    import minute_filler.gui.main_window as mw
    window.s.use_ai = True
    checks = []
    monkeypatch.setattr(mw.Runner, "start", lambda self, fn, *a, on_done=None, **k: checks.append(on_done))
    window._check_ai()
    window._check_ai()
    old, new = checks
    new((False, "Ollama isn't running"))
    old((True, "the old model is ready"))
    assert window.ai_ok is False and "isn't running" in window.ai_label.text()
