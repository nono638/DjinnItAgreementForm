"""Drives the main window (off screen): several documents become a batch of jobs; outputs that need a
transcript; the Outputs box (each output's options, the invoice speeds, parties, Extras... and Customize...);
the days of a case on one invoice, billed once by Generate all; Who ordered... (which attorney ordered which
pages) and the prices of each attorney: its rows following a renamed attorney, kept to be checked when an
attorney is unticked or the pages are typed again, the attorneys of the day only, and a read that ends while it
is open; a document added while Generate all runs, and Generate tried again after a problem; the granular
detail box of each day; File -> Lock finished PDFs; the Invoice panel in a small window; the big djinn picture
when the window first shows; the Records window (paid, amounts changed, only amounts taken); and background work
that ends while the user is busy in the window."""
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

    # a box per speed of the rate sheet; the ones ticked are saved and priced
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


# ------------------------------------------------------------ who ordered which pages, amounts changed in Records

def counsel(checked=True):
    from minute_filler.models import Attorney
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=checked)


def smith(checked=True):
    from minute_filler.models import Attorney
    return Attorney(name="Dana Smith", firm="Smith Law", checked=checked)


A, B = "alex b counsel", "dana smith"


def one_day_two_attorneys(window, tmp_path, pages=30):
    """A 30-page day of Roe v. X.Y. ordered by Alex B. Counsel and Dana Smith, shown with the invoice ticked."""
    window.output_boxes["invoice"].setChecked(True)
    window.add_files([str(transcript_pdf(tmp_path / "t.pdf", pages=pages))])
    wait(window, lambda: len(window.cur.docs) == 1)
    window.case.attorneys = [counsel(), smith()]
    window._show_attorneys()
    window._refresh_outputs()


def test_who_ordered_splits_the_day_and_the_prices_show_each_attorney(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    one_day_two_attorneys(window, tmp_path)
    assert window.inv_who.isEnabled() and window.inv_parties.isEnabled()
    assert window.inv_info.text() == "30 pp. · Regular $124.50 · Expedite $147.00 each"  # 30 x (4.30 / 2 + 2)
    monkeypatch.setattr(dialogs.PortionsDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: [(10, [A]), (30, [A, B])])
    window._who_ordered()
    assert window.cur.portions == [(10, [A]), (30, [A, B])]
    # Alex: (10 + 20 / 2) x 4.30 + 30 + 30 = 146.00; Dana: 10 x 4.30 + 20 + 20 = 83.00
    assert window.inv_info.text().splitlines() == ["30 pp.", "Alex B. Counsel: Regular $146.00 · Expedite $174.00",
                                                   "Dana Smith: Regular $83.00 · Expedite $98.00"]
    assert not window.inv_parties.isEnabled() and window.inv_who.text().startswith("✓")
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: None)  # everyone ordered every page
    window._who_ordered()
    assert window.cur.portions is None and window.inv_parties.isEnabled()
    assert window.inv_info.text().endswith(" each")


def test_who_ordered_needs_one_day_and_two_attorneys(window, tmp_path):
    one_day_two_attorneys(window, tmp_path)
    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)
    assert not window.inv_who.isEnabled() and "two attorneys" in window.inv_who.toolTip()
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
    # Alex: 30 x (4.30 + 4 x 1.00) = 249.00, then (30 x 4.30) + 60 + 60 + 30 + 30 = 309.00; Dana: 309.00
    assert window.inv_info.text().splitlines() == ["Generate all: one invoice for 2 days (90 pp.)",
                                                   "Alex B. Counsel: Regular $558.00 · Expedite $654.00",
                                                   "Dana Smith: Regular $309.00 · Expedite $360.00"]
    # only Alex is ticked on the day shown: its pages can't be split with Dana, who ordered another day
    assert not window.inv_who.isEnabled() and "two attorneys on this day" in window.inv_who.toolTip()
    window.job_list.setCurrentRow(1)
    assert window.inv_who.isEnabled()


def test_portions_dialog_rows_and_checks(qt):
    from minute_filler.gui.dialogs import PortionsDialog
    dlg = PortionsDialog([counsel(), smith(), counsel()], 90, None, [A, B])
    assert dlg.rows() == [(90, [A, B])] and not dlg.lines[-1][1].isEnabled()  # the last row ends on the last page
    dlg._add_row()
    assert dlg.rows() == [(45, [A, B]), (90, [A, B])] and dlg.lines[1][0].text() == "Pages 46 to"
    dlg.lines[0][1].setValue(40)
    dlg.lines[0][2][0].setChecked(False)  # the first 40 pages: Dana only
    assert dlg.lines[1][0].text() == "Pages 41 to"
    for cb in dlg.lines[1][2]:
        cb.setChecked(False)
    dlg.accept()
    assert dlg.result() != qt.QDialog.Accepted and "Pages 41 to 90: tick who ordered them" in dlg.error.text()
    dlg.lines[1][2][0].setChecked(True)
    dlg.lines[1][2][1].setChecked(True)
    dlg.lines[0][1].setValue(90)  # a first row as long as the day leaves nothing for the second
    dlg.accept()
    assert dlg.result() != qt.QDialog.Accepted and "must end on a later page" in dlg.error.text()
    dlg.lines[0][1].setValue(40)
    dlg.accept()
    assert dlg.result() == qt.QDialog.Accepted and dlg.values() == [(40, [B]), (90, [A, B])]

    again = PortionsDialog([counsel(), smith()], 90, [(40, [B]), (90, [A, B])], [A, B])
    assert again.rows() == [(40, [B]), (90, [A, B])]
    again.lines[0][2][0].setChecked(True)  # both rows everyone's: the same as no rows at all
    again.accept()
    assert again.values() is None
    again._remove_row()
    assert again.rows() == [(90, [A, B])]
    one = PortionsDialog([counsel(), smith()], 1, None, [A])  # one page can't be split
    one._add_row()
    assert one.rows() == [(1, [A])]
    one._everyone()
    assert one.result() == qt.QDialog.Accepted and one.values() is None


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
    assert "Regular $58.50" in win.inv_table.item(0, 7).text() and win.inv_table.item(0, 8).text() == "$58.50"
    win.close()


def test_each_attorneys_prices_fit_in_a_small_window(window, tmp_path):
    """Four speeds, a line for each attorney: the Invoice panel's text is not cut off at the smallest size."""
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
    assert label.text().count("\n") == 4  # what Generate all does, then two lines for each attorney
    assert label.width() >= label.sizeHint().width() and label.height() >= label.sizeHint().height()
    window.close()


# ------------------------------------------------------------ found in the sweep of who ordered which pages

def split(window, monkeypatch, rows):
    """Who ordered… as if the user ticked these rows and clicked OK."""
    from minute_filler.gui import dialogs
    monkeypatch.setattr(dialogs.PortionsDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: rows)
    window._who_ordered()
    assert window.cur.portions == rows


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
    window.att.item(1, 1).setText("Dana M. Smith")  # a typo fixed in the attorney table
    assert window.cur.portions == [(10, [A]), (30, [A, "dana m smith"])]
    assert "Dana M. Smith: Regular $83.00" in window.inv_info.text()

    window.att.item(1, 0).setCheckState(QtCore.Qt.Unchecked)  # Dana unticked: her pages are not Alex's
    assert window.cur.portions == [(10, [A]), (30, [A, "dana m smith"])]
    assert window.inv_info.text() == "⚠ Who ordered… needs checking (it names an attorney no longer ticked)"
    assert window.inv_who.isEnabled() and window.inv_who.text().startswith("⚠")
    window.output_boxes["agreement"].setChecked(False)
    said = answer(monkeypatch, QtWidgets.QMessageBox.Yes)
    window.fill()  # the invoice alone: nothing is made
    assert "No invoice is made for this day" in said[0] and not ledger_for(window.s).invoices()
    window.output_boxes["agreement"].setChecked(True)
    window.fill()  # asked: the agreement without the invoice
    assert "Make the other outputs without the invoice?" in said[1] and not ledger_for(window.s).invoices()
    assert [p.name.split(" - ")[0] for p in window.cur.saved] == ["Minute Agreement"]

    window._who_ordered()  # one attorney left: the rows can be cleared
    assert "Clear it" in said[2] and window.cur.portions is None
    assert window.inv_info.text().startswith("30 pp. · Regular $189.00") and window.inv_parties.isEnabled()


def test_retyping_the_pages_keeps_who_ordered(window, tmp_path, monkeypatch):
    one_day_two_attorneys(window, tmp_path)
    rows = [(10, [A]), (30, [A, B])]
    split(window, monkeypatch, rows)
    window.rows["est_pages"].choose("3")  # "30" -> "3" -> "30", as when a digit is typed again
    assert window.cur.portions == rows
    assert window.inv_info.text() == "⚠ Who ordered… needs checking (its rows don't end on this day's 3 pages)"
    window.rows["est_pages"].choose("30")
    assert window.cur.portions == rows and window.inv_who.text().startswith("✓")
    assert window.inv_info.text().splitlines()[1].startswith("Alex B. Counsel: Regular $146.00")


def test_who_ordered_lists_the_days_own_attorneys_and_counts_the_files_again(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    window.s.invoice_joint = False
    window.output_boxes["agreement"].setChecked(False)
    window.output_boxes["invoice"].setChecked(True)
    two_days(window, tmp_path)
    window.jobs[0].case.attorneys = [counsel(), smith()]
    window.jobs[1].case.attorneys = [counsel(), smith(checked=False)]
    window._show_case()
    window._update_status()
    assert window.fill_all_btn.text() == "Generate all  (3 files)"
    seen = []
    monkeypatch.setattr(dialogs.PortionsDialog, "exec", lambda self: seen.append(self.keys) or 1)
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: [(30, [A])])  # Dana ordered nothing
    window._who_ordered()
    assert window.fill_all_btn.text() == "Generate all  (2 files)"  # counted again straight away
    window.job_list.setCurrentRow(1)
    assert not window.inv_who.isEnabled()  # Dana isn't ticked on the second day
    window.jobs[1].case.attorneys[1].checked = True
    window._show_case()
    window._who_ordered()
    assert seen == [[A, B], [A, B]]


def test_who_ordered_and_a_read_that_ends_while_it_is_open(window, tmp_path, monkeypatch):
    from minute_filler.gui import dialogs
    one_day_two_attorneys(window, tmp_path)
    said = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda parent, title, text: said.append(text))
    job = window.cur

    def pages_change(self):
        job.case.set("est_pages", "25")
        return 1

    monkeypatch.setattr(dialogs.PortionsDialog, "exec", pages_change)
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: [(10, [A]), (30, [A, B])])
    window._who_ordered()
    assert job.portions is None and "changed while the window was open" in said[-1]
    job.case.set("est_pages", "30")
    job.portions = [(10, [A]), (30, [A, B])]
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: None)  # everyone ordered every page
    window._who_ordered()
    assert job.portions is None  # taken, whatever changed

    def joined(self):
        window.jobs = [type(job)()]  # a read merged the day into another job
        return 1

    job.case.set("est_pages", "30")
    monkeypatch.setattr(dialogs.PortionsDialog, "exec", joined)
    monkeypatch.setattr(dialogs.PortionsDialog, "values", lambda self: [(10, [A]), (30, [A, B])])
    window._who_ordered()
    assert job.portions is None and "joined with another job" in said[-1]
    window.jobs = [job]


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
