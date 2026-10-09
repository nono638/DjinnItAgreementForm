"""Drives the main window (off screen): several documents become a batch of jobs; outputs that need a
transcript; the Outputs box (each output's options and how many files it makes, parties, Peripherals... and
Customize..., the Speeds offered in the Invoice panel, the Generate button); the days of a case on one
invoice, billed once by Generate all; Excerpts... (which firm ordered which pages) and the prices of each: its rows following a renamed firm (an attorney's name fixed is the same
firm), kept to be checked when an attorney is unticked or the pages are typed again, and the days it shows (those
of the invoice); a document added or an attorney ticked while Generate all runs, and Generate tried again after a
problem; the granular detail box of each day; File -> Lock finished PDFs; the Invoice panel in a small window;
the big yin-yang picture when the window first shows; the Records window (paid, amounts changed, only amounts
taken); a preview whose records can't be opened; and background work that ends while the user is busy in the
window."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore, QtGui  # noqa: E402

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

    # Speeds offered (in the Invoice panel): a box per speed of the rate sheet; the ones ticked are saved and priced
    assert set(window.inv_speed_boxes) == {sp.name for sp in window.s.sheet().speeds}
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=12))])
    wait(window, lambda: len(window.cur.docs) == 1)
    assert "Expedite" in window.inv_info.text() and "Daily" not in window.inv_info.text()  # Regular, Expedited
    window.inv_speed_boxes["Expedite"].setChecked(False)
    assert "Expedited" not in window.s.invoice_speeds and "Expedite" not in window.inv_info.text()
    window.inv_speed_boxes["Immediate"].setChecked(True)
    assert "Immediate" in window.s.invoice_speeds and "Immediate" in window.inv_info.text()

    window.inv_detail.setChecked(True)  # for this job only; a new job starts with it off
    assert window.cur.invoice_detail is True and window.cur.invoice_opts().detail is True
    assert not hasattr(window.s, "invoice_detail")
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


def test_a_day_with_nobody_ticked_is_warned_about(window, tmp_path):
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=60, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    day1 = next(j for j in window.jobs if "6/3/2026" in j.case.get("dates"))
    day1.case.attorneys[0].checked = True  # an attorney ticked on June 3 only
    window._refresh_outputs()
    assert window.inv_info.text().startswith("⚠ Generate all: no invoice for these 2 days yet")
    assert "nobody is ticked on 6/4/2026" in window.inv_info.text()


def test_days_of_a_case_share_one_invoice_and_its_peripherals(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026")),
                      str(transcript_pdf(tmp_path / "b.pdf", pages=60, date="June 4, 2026"))])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    assert window.inv_info.text().startswith("Generate all: one invoice for 2 days (90 pp.)\nRegular $")
    assert "Generate this job" in window.inv_info.toolTip()
    assert window.inv_peripherals_info.text() == "E-mailed copy, index\n(a day of 60 pages, 50 or more)"
    # Peripherals… for this job: its other day on the same invoice gets the same choice
    monkeypatch.setattr(dialogs.InvoicePeripheralsDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.InvoicePeripheralsDialog, "values", lambda self: (False, "off"))
    window._invoice_peripherals()
    assert [(j.invoice_email, j.invoice_index) for j in window.jobs] == [(False, "off")] * 2
    assert window.inv_peripherals_info.text() == "No e-mailed copy (this job), no index\n(turned off for this job)"
    monkeypatch.setattr(dialogs.InvoiceShowDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.InvoiceShowDialog, "values", lambda self: ["days"])
    window._invoice_show()
    assert [j.invoice_show for j in window.jobs] == [["days"]] * 2


def test_granular_detail_starts_off_for_each_new_job(window, tmp_path):
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=12))])
    wait(window, lambda: len(window.cur.docs) == 1)
    window.inv_detail.setChecked(True)
    assert window.cur.invoice_detail
    window.new_job()
    assert not window.inv_detail.isChecked() and not window.cur.invoice_detail


def test_the_big_drop_picture_when_there_is_room(window):
    window.resize(1400, 1400)  # a tall window: room for the big picture above the inputs
    window.show()
    QtWidgets.QApplication.processEvents()
    window.resizeEvent(QtGui.QResizeEvent(window.size(), window.size()))
    assert not window.drop.compact
    window.resize(1400, 720)  # a short one: the small picture
    QtWidgets.QApplication.processEvents()
    window.resizeEvent(QtGui.QResizeEvent(window.size(), window.size()))
    assert window.drop.compact
    window.hide()


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

    monkeypatch.setattr(dialogs.InvoicePeripheralsDialog, "exec", untick_meanwhile)
    monkeypatch.setattr(dialogs.InvoicePeripheralsDialog, "values", lambda self: (False, "off"))
    window._invoice_peripherals()
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


# ------------------------------------------------------------ who ordered which pages, amounts changed in Records

def counsel(checked=True):
    from minute_filler.models import Attorney
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=checked)


def smith(checked=True):
    from minute_filler.models import Attorney
    return Attorney(name="Dana Smith", firm="Smith Law", checked=checked)


A, B = counsel().key(), smith().key()  # by the firm: "counsel and counsel", "smith law"


def one_day_two_attorneys(window, tmp_path, pages=30):
    """A 30-page day of Roe v. X.Y. ordered by Alex B. Counsel and Dana Smith, shown with the invoice ticked."""
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=pages))])
    wait(window, lambda: len(window.cur.docs) == 1)
    window.case.attorneys = [counsel(), smith()]
    window._show_attorneys()
    window._refresh_outputs()


def test_who_ordered_splits_the_day_and_the_prices_show_each_attorney(window, tmp_path, monkeypatch):
    one_day_two_attorneys(window, tmp_path)
    assert window.inv_who.isEnabled() and window.inv_parties.isEnabled()
    assert window.inv_info.text() == "30 pp. · Regular $124.50 · Expedite (form) $147.00 each"  # 30 x (4.30 / 2 + 2)
    split(window, monkeypatch, [(10, [A]), (30, [A, B])])
    # Alex: (10 + 20 / 2) x 4.30 + 30 + 30 = 146.00; Dana: 10 x 4.30 + 20 + 20 = 83.00
    assert window.inv_info.text().splitlines() == ["30 pp.", "Alex B. Counsel: Regular $146.00 · Expedite (form) $174.00",
                                                   "Dana Smith: Regular $83.00 · Expedite (form) $98.00"]
    assert not window.inv_parties.isEnabled() and window.inv_who.text().startswith("✓")
    split(window, monkeypatch, [(30, [A, B])])  # everyone ordered every page: no rows kept
    assert window.cur.portions is None and window.inv_parties.isEnabled()
    assert window.inv_info.text().endswith(" each")
    window.excerpts.close()


def test_who_ordered_needs_pages_to_bill(window, tmp_path):
    one_day_two_attorneys(window, tmp_path)
    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
    assert window.inv_who.isEnabled()  # one attorney may still have ordered an excerpt only
    window.new_job()
    assert not window.inv_who.isEnabled() and "no transcript pages" in window.inv_who.toolTip()


def test_the_days_of_a_case_bill_each_attorney_for_its_own_days(window, tmp_path):
    """Alex ordered both days, Dana only the second (60 pages, indexed): a price line for each."""
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.jobs[0].case.attorneys = [counsel(), smith(checked=False)]
    window.jobs[1].case.attorneys = [counsel(), smith()]
    window._show_case()
    window._refresh_outputs()
    # Alex: 30 x (4.30 + 4 x 1.00) = 249.00, then (60 / 2 x 4.30) + 60 + 60 + 60 (its own index) + 30 (half the
    # judge's) = 339.00; Dana: 339.00. Expedite: 294.00 + 393.00; 393.00
    assert window.inv_info.text().splitlines() == ["Generate all: one invoice for 2 days (90 pp.)",
                                                   "Alex B. Counsel: Regular $588.00 · Expedite (form) $687.00",
                                                   "Dana Smith: Regular $339.00 · Expedite (form) $393.00"]
    assert window.inv_who.isEnabled()  # Excerpts… shows both days, whoever is ticked on the day shown


def test_records_window_changes_amounts(window, tmp_path, monkeypatch):
    from minute_filler.deliver import ledger_for
    from minute_filler.gui import records_window as rw
    from minute_filler.records import Invoice
    window.s.records_dir = str(tmp_path / "records")
    lg = ledger_for(window.s)
    lg.add_invoice(Invoice("2026-0001", "2026-03-04", "Roe v. Doe", firm="Example Firm LLP",
                           amounts={"Regular": "63.00", "Daily": "91.00"}, billed_speed="Regular"))
    window.open_records()
    win = window._records_win
    win.i_year.setCurrentIndex(0)  # all years
    inv = win._inv_at(0)
    dlg = rw.AmountsDialog(inv, win)
    assert [b.text() for b in dlg.boxes.values()] == ["63.00", "91.00"]
    dlg.boxes["Daily"].setText("")
    dlg.accept()
    assert dlg.result() != QtWidgets.QDialog.Accepted and "Daily" in dlg.error.text()
    dlg.boxes["Daily"].setText("$84")
    dlg.accept()
    assert dlg.result() == QtWidgets.QDialog.Accepted and dlg.values() == {"Regular": "63.00", "Daily": "84.00"}

    monkeypatch.setattr(rw.AmountsDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    monkeypatch.setattr(rw.AmountsDialog, "values", lambda self: {"Regular": "58.50", "Daily": "84.00"})
    win._change_amounts(inv)
    assert lg.invoice("2026-0001").amounts == {"Regular": "58.50", "Daily": "84.00"}
    assert win.kpi["billed"].text() == "$58.50" and win.kpi["outstanding"].text() == "$58.50"
    offered, billed = (rw.col_index(rw.INVOICE_COLS, k) for k in ("offered", "billed"))
    assert "Regular $58.50" in win.inv_table.item(0, offered).text()
    assert win.inv_table.item(0, billed).text() == "$58.50"
    win.close()


def test_each_attorneys_prices_fit_in_a_small_window(window, tmp_path):
    """Four speeds, lines for each firm: the Invoice panel's text is not cut off at the smallest size."""
    if not os.environ.get("QT_QPA_FONTDIR"):
        pytest.skip("needs the real fonts to measure the text (see conftest.qt)")
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.jobs[0].case.attorneys = [counsel(), smith(checked=False)]
    window.jobs[1].case.attorneys = [counsel(), smith()]
    for cb in window.inv_speed_boxes.values():
        cb.setChecked(True)
    window.resize(1080, 720)
    window.show()
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    label = window.inv_info
    assert label.text().count("\n") == 4  # what Generate all does, then two lines for each firm
    assert label.width() >= label.sizeHint().width() and label.height() >= label.sizeHint().height()
    window.close()


# ------------------------------------------------------------ found in the sweep of who ordered which pages

def split(window, monkeypatch, rows):
    """Excerpts… as if the user set these runs ((last page, Attorney.key()s), from the page after the run above)
    for the day shown."""
    from minute_filler.excerpts import Run
    window._who_ordered()
    w = window.excerpts
    day = next(r.day for r in w.runs if r.day.job is window.cur)
    runs, start = [], 1
    for last, keys in rows:
        runs.append(Run(day, start, last, list(keys)))
        start = last + 1
    w._keep(day, runs)
    assert window.cur.portions == (None if len(rows) == 1 else rows)


def answer(monkeypatch, button):
    """QMessageBox.question answers `button`; returns the questions asked (and the warnings shown)."""
    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda parent, title, text, *a: asked.append(text) or button)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda parent, title, text, *a: asked.append(text))
    return asked


def test_who_ordered_follows_a_rename_and_needs_checking_when_an_attorney_is_unticked(window, tmp_path, monkeypatch):
    from minute_filler.deliver import ledger_for
    one_day_two_attorneys(window, tmp_path)
    split(window, monkeypatch, [(10, [A]), (30, [A, B])])
    window.att.item(1, 1).setText("Dana M. Smith")  # a typo fixed in the attorney table: the same firm, key
    assert window.cur.portions == [(10, [A]), (30, [A, B])]
    assert "Dana M. Smith: Regular $83.00" in window.inv_info.text()
    window.att.item(1, 2).setText("Smith Law Group")  # the firm's name changed: the rows follow it
    assert window.cur.portions == [(10, [A]), (30, [A, "smith law group"])]
    assert "Dana M. Smith: Regular $83.00" in window.inv_info.text()

    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)  # Dana unticked: her pages are not Alex's
    assert window.cur.portions == [(10, [A]), (30, [A, "smith law group"])]
    assert window.inv_info.text() == "⚠ Excerpts… needs checking (it names an attorney no longer ticked)"
    assert window.inv_who.isEnabled() and window.inv_who.text().startswith("⚠")
    window.output_boxes["agreement"].setChecked(False)
    said = answer(monkeypatch, QtWidgets.QMessageBox.Yes)
    window.fill()  # the invoice alone: nothing is made
    assert "No invoice is made for this day" in said[0] and not ledger_for(window.s).invoices()
    window.output_boxes["agreement"].setChecked(True)
    window.fill()  # asked: the agreement without the invoice
    assert "Make the other outputs without the invoice?" in said[1] and not ledger_for(window.s).invoices()
    assert [p.name.split(" - ")[0] for p in window.cur.saved] == ["Minute Agreement"]

    split(window, monkeypatch, [(30, [A])])  # Excerpts…: Alex alone ordered the day
    assert window.cur.portions is None and [a.checked for a in window.cur.case.attorneys] == [True, False]
    assert window.inv_info.text().startswith("30 pp. · Regular $189.00") and window.inv_parties.isEnabled()


def test_retyping_the_pages_keeps_who_ordered(window, tmp_path, monkeypatch):
    one_day_two_attorneys(window, tmp_path)
    rows = [(10, [A]), (30, [A, B])]
    split(window, monkeypatch, rows)
    window.rows["est_pages"].choose("3")  # "30" -> "3" -> "30", as when a digit is typed again
    assert window.cur.portions == rows
    assert window.inv_info.text() == "⚠ Excerpts… needs checking (its rows don't end on this day's 3 pages)"
    window.rows["est_pages"].choose("30")
    assert window.cur.portions == rows and window.inv_who.text().startswith("✓")
    assert window.inv_info.text().splitlines()[1].startswith("Alex B. Counsel: Regular $146.00")


def test_who_ordered_shows_the_days_of_the_invoice_and_counts_the_files_again(window, tmp_path, monkeypatch):
    window.s.invoice_joint = False  # an invoice for each day: Excerpts… shows the day shown alone
    window.output_boxes["agreement"].setChecked(False)
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.jobs[0].case.attorneys = [counsel(), smith()]
    window.jobs[1].case.attorneys = [counsel(), smith(checked=False)]
    window._show_case()
    window._update_status()
    assert window.fill_all_btn.text() == "Generate all  (3 files)"
    split(window, monkeypatch, [(30, [A])])  # Dana ordered nothing of the first day
    assert window.fill_all_btn.text() == "Generate all  (2 files)"  # counted again straight away
    assert [r.day.job for r in window.excerpts.runs] == [window.jobs[0]]
    window.job_list.setCurrentRow(1)
    assert [r.day.job for r in window.excerpts.runs] == [window.jobs[1]]  # it follows the day shown
    window.excerpts.close()


def test_a_document_added_while_generate_all_runs_is_billed_next_time(window, tmp_path, monkeypatch):
    import threading
    from minute_filler.deliver import ledger_for
    from minute_filler.gui import main_window as mw
    window.s.invoice_joint = False
    window.output_boxes["agreement"].setChecked(False)
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    go, real = threading.Event(), mw.fill_jobs

    def slow(*a, **k):
        go.wait(20)
        return real(*a, **k)

    monkeypatch.setattr(mw, "fill_jobs", slow)
    window.fill_all_jobs()
    day1 = window.jobs[0]
    window.add_files([str(transcript_pdf(tmp_path / "a2.pdf", pages=25, date="June 3, 2026"))])  # day 1, vol. 2
    end = time.time() + 20
    while len(day1.docs) < 2 and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert len(day1.docs) == 2
    go.set()
    wait(window, lambda: not window.filling)
    assert sorted(i.pages for i in ledger_for(window.s).invoices()) == [30, 60]  # as the days were
    assert not day1.invoiced and day1.include and day1.invoice_pages() == 55  # its 25 new pages are still to bill
    assert window.jobs[1].invoiced and not window.jobs[1].include
    assert window.fill_all_btn.text() == "Generate all  (1 file)"


def test_generate_this_job_after_a_problem_does_not_bill_an_attorney_twice(window, tmp_path, monkeypatch):
    from minute_filler import deliver
    from minute_filler.deliver import ledger_for
    from minute_filler.gui import main_window as mw
    one_day_two_attorneys(window, tmp_path)
    window.output_boxes["agreement"].setChecked(False)
    real, calls = deliver.make_invoice, []

    def flaky(case, atty, *a, **k):
        calls.append(atty.name)
        if calls == ["Alex B. Counsel", "Dana Smith"]:
            raise PermissionError("the PDF is open in another program")
        return real(case, atty, *a, **k)

    monkeypatch.setattr(deliver, "make_invoice", flaky)
    monkeypatch.setattr(mw, "show_save_error", lambda *a: None)
    window.fill()
    assert [i.bill_to for i in ledger_for(window.s).invoices()] == ["Alex B. Counsel"]
    assert not window.cur.invoiced and window.cur.invoiced_keys == [A]
    window.fill()  # tried again: Dana's invoice only
    assert sorted(i.bill_to for i in ledger_for(window.s).invoices()) == ["Alex B. Counsel", "Dana Smith"]
    assert window.cur.invoiced


def test_the_detail_box_shows_the_jobs_own_choice(window, tmp_path):
    """Generate this job uses the day's own choice, so the box shows it, not the joint invoice's."""
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.jobs[1].include = False
    window._refresh_outputs()
    window.inv_detail.setChecked(True)  # the first day alone
    window.jobs[1].include = True
    window.job_list.setCurrentRow(1)
    assert not window.inv_detail.isChecked() and not window.jobs[1].invoice_opts().detail
    window.job_list.setCurrentRow(0)
    assert window.inv_detail.isChecked()


def test_the_big_drop_picture_when_the_window_first_shows(window):
    """Measured once the window is laid out: at 1500 x 900 the big picture fits without a scroll bar."""
    if not os.environ.get("QT_QPA_FONTDIR"):
        pytest.skip("needs the real fonts to measure the text (see conftest.qt)")
    window.resize(1500, 900)
    window.show()
    for _ in range(10):
        QtWidgets.QApplication.processEvents()
    assert not window.drop.compact and not window.left_scroll.verticalScrollBar().isVisible()
    window.close()


def test_the_amounts_dialog_takes_amounts_only(window):
    from minute_filler.gui import records_window as rw
    from minute_filler.records import Invoice
    inv = Invoice("2026-0001", "2026-03-04", "Roe <b>v.</b> Doe", firm="Example & Firm LLP",
                  amounts={"Regular": "63.00"}, billed_speed="Regular")
    dlg = rw.AmountsDialog(inv, window)
    head = dlg.findChildren(QtWidgets.QLabel)[0].text()
    assert "Roe &lt;b&gt;v.&lt;/b&gt; Doe" in head and "Example &amp; Firm LLP" in head
    for bad in ("-5", "6O.00", "12,50", "63 or 70"):
        dlg.boxes["Regular"].setText(bad)
        dlg.accept()
        assert dlg.result() != QtWidgets.QDialog.Accepted and "Regular" in dlg.error.text(), bad
    dlg.boxes["Regular"].setText("$1,250.00")
    dlg.accept()
    assert dlg.result() == QtWidgets.QDialog.Accepted and dlg.values() == {"Regular": "1250.00"}


def test_an_attorney_ticked_while_generate_all_runs_is_billed_by_the_next_run():
    """Ticked while the batch worked on a copy: the day was marked invoiced, so Generate all never billed the new
    attorney (and Generate billed the first one again)."""
    from copy import deepcopy
    from dataclasses import replace
    from minute_filler.batch import Job
    from minute_filler.gui.main_window import _billed_by_copy, _billing_changed, _pages_changed
    from minute_filler.models import Attorney
    job = Job()
    job.case.attorneys = [Attorney(name="Dana Smith", firm="Smith Law", checked=True)]
    copy = replace(job, case=deepcopy(job.case))
    job.case.attorneys.append(Attorney(name="Sam Poe", firm="Poe Law", checked=True))
    assert _billing_changed(job, copy) and not _pages_changed(job, copy)
    assert _billed_by_copy(copy) == ["smith law"]


def test_a_preview_whose_records_cant_be_opened_leaves_the_window_usable(window, monkeypatch):
    """The preview's copy of the records failed after the window was marked busy: Generate, Generate all and New
    job were refused until the app was started again."""
    from minute_filler.gui import main_window

    class Broken:
        def preview_copy(self, _folder):
            raise OSError("the records can't be opened")
    monkeypatch.setattr(main_window, "ledger_for", lambda _s: Broken())
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.No)
    window._preview_batch([], {}, ["agreement"], lambda _shown: None)
    assert not window.filling and window.work == 0


# ------------------------------------------------------------------ the Outputs box, 2026-10-08

def test_each_output_says_how_many_files_generate_makes(window, tmp_path):
    """Under each ticked output's heading, how many files Generate makes of it, kept up to date as attorneys are
    ticked ("Will generate 2 minute agreement forms"; digits, as "two minute" reads as "two-minute"); hidden while
    the output is unticked; the invoice's detailed copies too."""
    for key in ("agreement", "mofr"):
        window.output_boxes[key].setChecked(True)
    one_day_two_attorneys(window, tmp_path)
    count = window.output_counts
    assert count["agreement"].text() == "Will generate 2 minute agreement forms"
    assert count["invoice"].text() == "Will generate 2 invoices" and count["mofr"].text() == "Will generate 1 MOFR"
    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)  # Dana Smith unticked: at once
    assert count["agreement"].text() == "Will generate 1 minute agreement form"
    assert count["invoice"].text() == "Will generate 1 invoice"
    window.output_boxes["mofr"].setChecked(False)
    assert count["mofr"].isHidden() and not count["agreement"].isHidden()
    window.s.invoice_detailed_copy = True
    window._refresh_outputs()
    assert count["invoice"].text() == "Will generate 1 invoice (and 1 detailed copy)"


def test_with_several_jobs_the_counts_say_what_generate_all_makes(window, tmp_path):
    """Two days of a case, Alex on both: Generate makes the day shown's (1 agreement, 1 invoice); Generate all
    makes one agreement and one joint invoice for the case (Settings: one for the whole case)."""
    for key in ("agreement", "invoice"):
        window.output_boxes[key].setChecked(True)
    two_days(window, tmp_path)
    for job in window.jobs:
        job.case.attorneys = [counsel()]
    window._show_case()
    window._refresh_outputs()
    assert window.output_counts["agreement"].text() == "Will generate 1 minute agreement form\nGenerate all: 1"
    assert window.output_counts["invoice"].text() == "Will generate 1 invoice\nGenerate all: 1"


def test_the_invoice_panel_links_to_the_math(window, tmp_path, monkeypatch):
    """The Invoice panel has the same link to the whole math as Who pays what, while there are prices."""
    from minute_filler.gui import preview
    shown = []
    monkeypatch.setattr(preview.MathDialog, "exec", lambda dlg: shown.append(dlg.plain) or 0)
    assert not window.inv_form.isRowVisible(window.inv_math)  # nothing priced yet
    one_day_two_attorneys(window, tmp_path)
    assert window.inv_form.isRowVisible(window.inv_math)
    window.inv_math.linkActivated.emit("math")
    assert len(shown) == 1 and "Bill to Alex B. Counsel" in shown[0] and "Bill to Dana Smith" in shown[0]
    window.output_boxes["invoice"].setChecked(False)
    assert not window.inv_form.isRowVisible(window.inv_math)


def test_the_invoice_panel_rows_and_the_generate_button(window, tmp_path):
    """No "Shows:" before Show granular detail; Peripherals… (not Extras…); and Generate is the main action,
    styled to stand out (Generate all when there are several jobs)."""
    labels = [window.inv_form.itemAt(r, QtWidgets.QFormLayout.LabelRole) for r in range(window.inv_form.rowCount())]
    texts = [w.widget().text() for w in labels if w is not None and w.widget() is not None]
    assert "Shows:" not in texts and "Includes:" in texts
    assert window.inv_peripherals.text() == "Peripherals…"
    assert window.fill_btn.objectName() == "generate" and window.fill_all_btn.objectName() == "generate"
    two_days(window, tmp_path)
    assert window.fill_btn.objectName() == "" and window.fill_btn.text() == "Generate this job"
    from minute_filler.gui.theme import DARK, LIGHT, QSS
    assert "QPushButton#generate {" in QSS and all(k in t for t in (LIGHT, DARK) for k in ("go", "go_hover"))


def test_peripherals_are_for_this_job_only_and_say_so(qt):
    """Peripherals… (it was Extras…): its title, the index choices for this job (Auto from the whole transcript's
    pages, the number set in Settings), and a note that new jobs start from Settings."""
    from minute_filler.gui.dialogs import InvoicePeripheralsDialog
    s = pat_settings()
    s.invoice_index_threshold = 60
    dlg = InvoicePeripheralsDialog(None, None, s)
    assert dlg.windowTitle() == "Peripherals for this job"
    radios = [b.text() for b in dlg.index.buttons()]
    assert radios == ["Auto: when the whole transcript has 60 pages or more",
                      "Yes, for this job (however short the transcript)", "No, for this job"]
    notes = " ".join(w.text() for w in dlg.findChildren(QtWidgets.QLabel))
    assert "for this job only" in notes and "New jobs start from Settings → Invoice, where the 60 pages" in notes
    assert "Auto counts every reporter's pages of the transcript, not only yours." in notes
    assert "Same as Settings" in [b.text() for b in dlg.findChildren(QtWidgets.QPushButton)]
    assert dlg.values() == (None, None)
    dlg.index.buttons()[2].setChecked(True)
    assert dlg.values() == (None, "off")


# ------------------------------------------------------------------ found by the 2.5.0 sweep

def test_generate_counts_a_day_invoiced_already_as_it_bills_it_again(window, tmp_path):
    """Generate this job bills a day again when asked, invoiced already or not (Job.billed_keys): its count
    says so, not "Will generate no invoice" after every Generate. Generate all doesn't bill it again."""
    from minute_filler.batch import output_counts
    one_day_two_attorneys(window, tmp_path)
    window.cur.invoiced = True  # (as Generate leaves it)
    window._update_status()
    assert window.output_counts["invoice"].text() == "Will generate 2 invoices"
    assert output_counts([window.cur], ["invoice"], window.s)["invoice"] == 0  # (Generate all's count)


def test_a_keystroke_counts_the_jobs_of_generate_all_once(window, tmp_path, monkeypatch):
    """With several jobs, the counts under the outputs and the Generate all button come from one count of every
    job: each keystroke counted them twice (the button through files_to_make), and typing slowed down."""
    from minute_filler import batch
    from minute_filler.gui.widgets import plural
    two_days(window, tmp_path)
    real, calls = batch.output_counts, []
    monkeypatch.setattr(batch, "output_counts", lambda jobs, *a, **k: calls.append(len(jobs)) or real(jobs, *a, **k))
    window._update_status()
    assert calls.count(2) == 1
    files = sum(real(window.jobs, window._outputs(), window.s).values())
    assert window.fill_all_btn.text() == f"Generate all  ({plural(files, 'file')})"


def test_with_two_days_the_outputs_box_keeps_four_panels_a_row(window, tmp_path, qt):
    """What Generate all makes goes on a line of its own under each count: at the end of the line it made the
    panels wider, and at the window's first size (1320 wide) the Outputs box went from four panels a row to three
    when MOFR was ticked. Measured with the app's style sheet, as on screen."""
    from minute_filler.gui.theme import apply_theme
    apply_theme(qt.QApplication.instance(), "light")
    for key in ("agreement", "invoice", "mofr"):
        window.output_boxes[key].setChecked(True)
    two_days(window, tmp_path)
    window.resize(1320, 860)
    window.show()
    for _ in range(5):
        QtWidgets.QApplication.processEvents()
    window._cols_per_row = 0
    window._place_output_cols()
    assert window.output_counts["mofr"].text().startswith("Will generate 1 MOFR\nGenerate all: ")
    assert window._cols_per_row == 4


def test_who_pays_what_names_only_what_each_firm_pays_for_its_own(window, tmp_path):
    """"Each firm also pays for its own ..." says what the invoices charge: a 30-page day has no index, and with
    the e-mailed copy turned off for the job only the copy is left (it said "copy, e-mailed copy and index"
    whatever was charged). The tooltips say what Settings charge."""
    from minute_filler.gui.main_window import _who_splits
    one_day_two_attorneys(window, tmp_path)
    assert "Each firm also pays for its own copy and e-mailed copy;" in window.pays.text()
    window.cur.invoice_email = False
    window._update_status()
    assert "Each firm also pays for its own copy;" in window.pays.text()
    s = pat_settings()
    assert _who_splits(s)["own"] == "copy, e-mailed copy and index"
    s.invoice_include_email = False
    assert _who_splits(s)["own"] == "copy and index"
    s.invoice_index_shared = "split"  # (the index is then one the firms split)
    assert _who_splits(s) == {"shared": "the original, the index and the judge's index", "own": "copy"}


def test_an_empty_job_still_shows_what_generate_all_makes(window, tmp_path):
    """The job shown emptied (its only document removed) while other jobs are loaded: its own counts go, but what
    Generate all makes is still said (the button still counts it)."""
    from minute_filler.batch import remerge
    window.output_boxes["agreement"].setChecked(True)
    two_days(window, tmp_path)
    job = window.cur
    window._sync_from_ui()
    job.docs.clear()  # (as Remove from job does to a day's only document)
    remerge(job, window.s)
    window._refresh_jobs()
    window._show_job()
    assert job.is_empty() and window.output_counts["agreement"].text() == "Generate all: 1"
    assert not window.output_counts["agreement"].isHidden()


def test_a_held_invoice_says_what_it_waits_for(window, tmp_path, monkeypatch):
    """An invoice held until Whose pages... is chosen: Generate asks it first and then makes the invoice, so the
    count says what it waits for, not only "no invoice"."""
    from minute_filler.batch import Job
    one_day_two_attorneys(window, tmp_path)
    monkeypatch.setattr(Job, "ownership_problem", lambda self: "the transcript has pages of two reporters")
    window._update_status()
    assert window.output_counts["invoice"].text() == "No invoice until Whose pages… is chosen"


def test_the_includes_line_breaks_after_a_comma_not_before_a_last_word(window):
    """Two days indexed together (Settings: the days' pages added up): "(130 total pages over 2 days, 50 or
    more)" is broken after its comma, not with "more)" alone on the last line."""
    from minute_filler.gui.main_window import _lines
    from minute_filler.invoice import InvoiceOpts
    window.s.invoice_index_rule = "total"
    opts = InvoiceOpts(130, 1, days=[("6/3/2026", 60), ("6/4/2026", 70)])
    assert _lines(window._peripherals_text(opts)).splitlines() == [
        "E-mailed copy, index", "(130 total pages over 2 days,", "50 or more)"]
