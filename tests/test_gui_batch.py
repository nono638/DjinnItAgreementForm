"""Drives the main window (off screen): several documents become a batch of jobs; outputs that need a
transcript; the Outputs box (each output's options, the invoice speeds, parties, Extras... and Customize...);
the days of a case on one invoice, billed once by Generate all; File -> Lock finished PDFs; the Invoice panel in
a small window; the Records window; and background work that ends while the user is busy in the window."""
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
    """Runs the Qt event loop until no background task is left and until() is true."""
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def write(folder, name, **kw):
    """Writes a fictional invoice (see helpers.invoice_text) and returns its path."""
    p =folder / name
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
    assert not any(j.include for j in window.jobs)  # nothing left for a second "Generate all"


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
    """A folder of documents about one case and day gives one job, even as a batch; New job clears it."""
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


def test_outputs_box_options(window, tmp_path):
    """Each output's options sit under its own box: greyed out while it is unticked, saved when changed."""
    window.output_boxes["invoice"].setChecked(False)
    assert not window.output_opts["invoice"].isEnabled()
    window.output_boxes["invoice"].setChecked(True)
    assert window.output_opts["invoice"].isEnabled()
    window.output_boxes["agreement"].setChecked(False)
    assert not window.per_email.isEnabled() and not window.form_choice.isEnabled()
    window.output_boxes["agreement"].setChecked(True)
    assert window.per_email.isEnabled()

    # a box per speed of the rate sheet; the ones ticked are saved and priced
    assert set(window.inv_speed_boxes) == {sp.name for sp in window.s.sheet().speeds}
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=12))])
    wait(window, lambda: len(window.cur.docs) == 1)
    assert "Expedite" in window.inv_info.text() and "Daily" not in window.inv_info.text()  # Regular, Expedited
    window.inv_speed_boxes["Expedite"].setChecked(False)
    assert "Expedited" not in window.s.invoice_speeds and "Expedite" not in window.inv_info.text()
    window.inv_speed_boxes["Immediate"].setChecked(True)
    assert "Immediate" in window.s.invoice_speeds and "Immediate" in window.inv_info.text()

    window.inv_detail.setChecked(True)
    assert window.s.invoice_detail is True
    window.mofr_division.setCurrentIndex(window.mofr_division.findData("criminal"))
    assert window.s.mofr_division == "criminal"
    window.rs_existing.setCurrentIndex(window.rs_existing.findData("add"))
    assert window.s.runsheet_existing == "add"


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
    window.inv_detail.setChecked(not window.inv_detail.isChecked())  # must not fix the number of parties
    window.att.item(1, 0).setCheckState(QtCore.Qt.Checked)
    assert window.inv_parties.value() == 2 and "each" in window.inv_info.text()
    assert window.cur.invoice_opts().parties == 2
    window.inv_parties.setValue(3)  # typed: stays, whatever is ticked
    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
    assert window.inv_parties.value() == 3


def test_days_of_a_case_share_one_invoice_and_its_extras(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=60, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    assert window.inv_info.text().startswith("Generate all: one invoice for 2 days (90 pp.)\nRegular $")
    assert "Generate this job" in window.inv_info.toolTip()
    assert window.inv_extras_info.text().startswith("E-mailed copy, index from 50 pp.")
    # Extras… for this job: its other day on the same invoice gets the same choice
    monkeypatch.setattr(dialogs.InvoiceExtrasDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.InvoiceExtrasDialog, "values", lambda self: (False, "off"))
    window._invoice_extras()
    assert [(j.invoice_email, j.invoice_index) for j in window.jobs] == [(False, "off")] * 2
    assert window.inv_extras_info.text() == "No e-mailed copy, no index (this job)"
    monkeypatch.setattr(dialogs.InvoiceShowDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.InvoiceShowDialog, "values", lambda self: ["days"])
    window._invoice_show()
    assert [j.invoice_show for j in window.jobs] == [["days"]] * 2


def test_an_answer_arriving_while_typing_leaves_the_text_alone(window):
    row = window.rows["judge"]
    row.edit.setText("Maria T. Lopez")
    row.edit.setCursorPosition(5)
    row.set_state(type(row.state)("Maria T. Lopez", "you", 1.0, []))  # e.g. a merge after an AI answer
    assert row.edit.cursorPosition() == 5


# ------------------------------------------------------------ found in the sweep of the joint invoices

def two_days(window, tmp_path):
    """Loads two days of Roe v. X.Y. (30 and 60 pages) as a batch and shows the first."""
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=60, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    window.job_list.setCurrentRow(0)
    assert window.cur.invoice_pages() == 30


def test_generate_all_bills_each_day_once(window, tmp_path, monkeypatch):
    """Days billed by Generate all are marked invoiced: ticked again, they are done without a second invoice.
    Generate this job still bills the day shown when asked."""
    from minute_filler.deliver import ledger_for
    from minute_filler.gui import dialogs
    monkeypatch.setattr(dialogs.ClarifyDialog, "exec", lambda self: 0)  # (not expected; never waits)
    window.output_boxes["agreement"].setChecked(False)
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.fill_all_jobs()
    wait(window, lambda: not window.filling)
    assert len(ledger_for(window.s).invoices()) == 1
    assert all(j.invoiced and j.saved for j in window.jobs) and not any(j.include for j in window.jobs)
    for job in window.jobs:
        job.include = True
    window._refresh_job_labels()
    assert "(0 files)" in window.fill_all_btn.text()
    window.fill_all_jobs()
    wait(window, lambda: not window.filling)
    assert len(ledger_for(window.s).invoices()) == 1
    assert not any(j.include for j in window.jobs)  # done, though nothing was left to make
    assert all(window.job_list.item(i).text().startswith("✓") for i in range(2))

    for a in window.cur.case.attorneys:
        a.checked = "Counsel" in (a.firm or "")
    window._show_attorneys()
    window.fill()
    assert sorted(i.pages for i in ledger_for(window.s).invoices()) == [30, 90]
    assert window.cur.invoiced


def test_the_price_line_says_what_generate_all_bills(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    assert window.inv_info.text().startswith("Generate all: one invoice for 2 days (90 pp.)\n")
    window.jobs[1].include = False  # unticked: Generate all bills the day shown alone
    window._refresh_outputs()
    assert window.inv_info.text().startswith("30 pp. · Regular") and not window.inv_info.toolTip()
    window.jobs[1].include, window.jobs[1].invoiced = True, True  # billed already
    window._refresh_outputs()
    assert window.inv_info.text().startswith("30 pp. · Regular")
    window.jobs[1].invoiced = False

    def untick_meanwhile(self):  # the days on the invoice are taken when the window closes
        window.jobs[1].include = False
        return 1

    monkeypatch.setattr(dialogs.InvoiceExtrasDialog, "exec", untick_meanwhile)
    monkeypatch.setattr(dialogs.InvoiceExtrasDialog, "values", lambda self: (False, "off"))
    window._invoice_extras()
    assert [(j.invoice_email, j.invoice_index) for j in window.jobs] == [(False, "off"), (None, None)]


def test_an_empty_job_has_no_invoice_warning_and_the_run_sheets_folder_opens_unticked(window):
    window.output_boxes["invoice"].setChecked(True)
    assert window.inv_info.text() == ""
    window.output_boxes["runsheet"].setChecked(False)
    assert window.rs_folder.isEnabled() and not window.output_opts["runsheet"].isEnabled()


def test_reloading_the_rate_sheets_updates_the_invoice_speeds(window):
    from minute_filler.rates import sheets_dir
    assert len(window.inv_speed_boxes) == 4
    (sheets_dir() / "Sample Rates.csv").write_text("Rate,Original\nRegular,$4.00\nDaily,$6.00\n")
    window._reload_sheets()
    assert set(window.inv_speed_boxes) == {"Regular", "Daily"}


def test_lock_leaves_out_pdfs_without_fields(window, tmp_path, monkeypatch):
    from helpers import ROE, make_case
    from minute_filler.fill import fill
    case = make_case(ROE)
    editable = fill(case, None, window.s, tmp_path / "a")
    window.s.flatten = True
    flat = fill(case, None, window.s, tmp_path / "b")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileNames",
                        lambda *a, **k: ([str(editable), str(flat)], ""))
    said = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda parent, title, text: said.append(text))
    window.lock_pdfs()
    assert {p.name for p in (tmp_path / "a").iterdir()} == {editable.name, f"{editable.stem} (locked).pdf"}
    assert list((tmp_path / "b").iterdir()) == [flat]
    assert "Already locked or flattened" in said[0] and flat.name in said[0]


def test_the_invoice_prices_fit_in_a_small_window(window, tmp_path):
    """Four speeds and a two-day invoice: the Invoice panel's text is not cut off at the smallest size."""
    if not os.environ.get("QT_QPA_FONTDIR"):
        pytest.skip("needs the real fonts to measure the text (see conftest.qt)")
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    for cb in window.inv_speed_boxes.values():
        cb.setChecked(True)
    window.resize(1080, 720)
    window.show()
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    label = window.inv_info
    assert label.text().count("\n") == 2  # what Generate all does, then two speeds a line
    assert label.width() >= label.sizeHint().width() and label.height() >= label.sizeHint().height()
    window.close()
