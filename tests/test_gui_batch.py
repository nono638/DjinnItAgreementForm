"""Drives the main window (off screen): several documents become a batch of jobs."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402

from helpers import invoice_text, pat_settings, transcript_pdf  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch, make_window):
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)  # no dialogs waiting for a click
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    return make_window(s)


def wait(win, until, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def write(folder, name, **kw):
    p = folder / name
    p.write_text(invoice_text(**kw))
    return str(p)


def test_batch_of_documents(window, tmp_path):
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    b = write(tmp_path, "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")
    c = write(tmp_path, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([a, b, c, a])
    wait(window, lambda: len(window.jobs) == 2)
    assert [[d.ing.name for d in j.docs] for j in window.jobs] == [["a.txt", "c.txt"], ["b.txt"]]
    assert not window.job_list.isHidden() and window.job_list.count() == 2
    assert "2 documents" in window.job_list.item(0).text()
    assert window.rows["index_no"].text() == "712222/2024" and window.input_list.count() == 2

    # edits stay with their job when another one is selected
    window.rows["judge"].choose("Maria T. Lopez")
    window.job_list.setCurrentRow(1)
    assert window.rows["index_no"].text() == "700001/2025" and window.rows["judge"].text() == "Lopez"
    window.job_list.setCurrentRow(0)
    assert window.rows["judge"].text() == "Maria T. Lopez"

    # a document dropped later finds its job; one that is already loaded is skipped
    d = write(tmp_path, "d.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")
    window.add_files([d, b])
    wait(window, lambda: len(window.jobs[1].docs) == 2)
    assert len(window.jobs) == 2

    window.fill_all_jobs()
    wait(window, lambda: all(j.saved for j in window.jobs))
    saved = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(saved) == 2 and "712222-2024" in saved[1] and "700001-2025" in saved[0]
    assert window.job_list.item(0).text().startswith("✓")
    assert not any(j.include for j in window.jobs)  # nothing left for a second "Fill all"


def test_documents_about_one_case_stay_one_job(window, tmp_path):
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    c = write(tmp_path, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([a, c])
    wait(window, lambda: len(window.cur.docs) == 2)
    assert len(window.jobs) == 1 and window.job_list.isHidden() and window.fill_all_btn.isHidden()
    assert window.rows["index_no"].text() == "712222/2024"
    # one more document goes to the job on screen, as before
    window.add_files([write(tmp_path, "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")])
    wait(window, lambda: len(window.cur.docs) == 3)
    assert len(window.jobs) == 1


def test_folder_and_split(window, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    write(src, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    write(src, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([str(src)], batch=True)
    wait(window, lambda: len(window.cur.docs) == 2)
    assert len(window.jobs) == 1
    window.new_job()
    assert window.cur.is_empty() and window.input_list.count() == 0 and window.rows["index_no"].text() == ""


def test_outputs_and_invoice_need_a_transcript(window, tmp_path):
    window.s.records_dir = str(tmp_path / "records")
    window.output_boxes["invoice"].setChecked(True)
    window.output_boxes["mofr"].setChecked(True)
    assert window.s.outputs == ["agreement", "mofr", "invoice"]  # saved as the default straight away

    window.add_files([write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")])
    wait(window, lambda: len(window.cur.docs) == 1)
    assert "no transcript" in window.inv_info.text()
    assert window.cur.output_problems(window.s.outputs)

    window.new_job()
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=12))])
    wait(window, lambda: len(window.cur.docs) == 1)
    assert window.cur.invoice_pages() == 12
    assert "12 pp." in window.inv_info.text() and "Regular" in window.inv_info.text()
    for a in window.case.attorneys:
        a.checked = "Counsel" in (a.firm or "")
    window._show_attorneys()
    window.fill()
    kinds = [p.name.split(" - ")[0].split(" 20")[0] for p in window.cur.saved]
    assert kinds == ["Minute Agreement", "MOFR", "Invoice"]


def test_records_window_marks_paid(window, tmp_path, monkeypatch):
    from minute_filler.gui import records_window as rw
    from minute_filler.records import Invoice
    window.s.records_dir = str(tmp_path / "records")
    from minute_filler.deliver import ledger_for
    lg = ledger_for(window.s)
    lg.add_invoice(Invoice("2026-0001", "2026-03-04", "Roe v. Doe", firm="Example Firm LLP",
                           amounts={"Regular": "63.00", "Daily": "91.00"}, billed_speed="Regular"))
    window.open_records()
    win = window._records_win
    win.i_year.setCurrentIndex(0)  # all years
    assert win.inv_table.rowCount() == 1 and win.kpi["outstanding"].text() == "$63.00"
    monkeypatch.setattr(rw.PaidDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    monkeypatch.setattr(rw.PaidDialog, "values", lambda self: ("Daily", "91.00", "2026-03-10"))
    win.inv_table.item(0, 0).setCheckState(QtCore.Qt.Checked)
    QtWidgets.QApplication.processEvents()  # the tick is dealt with right after the click
    assert lg.invoice("2026-0001").status == "paid"
    assert win.kpi["paid"].text() == "$91.00" and win.kpi["outstanding"].text() == "$0.00"
    assert win.firm_table.item(0, 0).text() == "Example Firm LLP"
    win.close()


def test_same_file_dropped_twice_in_a_row_is_read_once(window, tmp_path):
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    window.add_files([a])
    window.add_files([a])  # before the first drop has been read
    wait(window, lambda: len(window.cur.docs) >= 1)
    assert len(window.cur.docs) == 1 and len(window.jobs) == 1
    assert window.work == 0 and not window._loading and window.busy.isHidden()


def test_one_batch_at_a_time_and_edits_do_not_reach_it(window, tmp_path):
    import pymupdf
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    b = write(tmp_path, "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")
    window.add_files([a, b])
    wait(window, lambda: len(window.jobs) == 2)
    window.fill_all_jobs()
    assert window.filling and not window.fill_all_btn.isEnabled()
    # while the batch is being made: no second batch, no single job, no "New job" ...
    window.fill_all_jobs()
    window.fill()
    window.new_job()
    window._refresh_job_labels()
    assert not window.fill_all_btn.isEnabled()
    # ... and what is typed now belongs to the next run, not to the files being written
    window.rows["judge"].choose("Changed Meanwhile")
    wait(window, lambda: not window.filling)
    out = sorted((tmp_path / "out").iterdir())
    assert len(out) == 2 and len(window.jobs) == 2 and all(j.saved for j in window.jobs)
    for p in out:
        with pymupdf.open(p) as doc:
            assert not any("Changed Meanwhile" in str(w.field_value) for w in doc[0].widgets())
    assert window.work == 0 and window.busy.isHidden() and window.fill_btn.isEnabled()
    assert window.cur.case.get("judge") == "Changed Meanwhile"


def test_ticked_attorneys_set_the_invoice_parties(window, tmp_path):
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=12))])
    wait(window, lambda: len(window.cur.docs) == 1)
    assert window.att.rowCount() >= 2
    for r in range(window.att.rowCount()):
        window.att.item(r, 0).setCheckState(QtCore.Qt.Unchecked)
    window.att.item(0, 0).setCheckState(QtCore.Qt.Checked)
    assert window.inv_parties.value() == 1 and "each" not in window.inv_info.text()
    window.inv_choice.setChecked(not window.inv_choice.isChecked())  # must not fix the number of parties
    window.att.item(1, 0).setCheckState(QtCore.Qt.Checked)
    assert window.inv_parties.value() == 2 and "each" in window.inv_info.text()
    assert window.cur.invoice_opts().parties == 2
    window.inv_parties.setValue(3)  # typed: stays, whatever is ticked
    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
    assert window.inv_parties.value() == 3


def test_an_answer_arriving_while_typing_leaves_the_text_alone(window):
    row = window.rows["judge"]
    row.edit.setText("Maria T. Lopez")
    row.edit.setCursorPosition(5)
    row.set_state(type(row.state)("Maria T. Lopez", "you", 1.0, []))  # e.g. a merge after an AI answer
    assert row.edit.cursorPosition() == 5
