"""No. of copies is the number of ordering parties, as the invoice counts them (a firm is one), and it follows every
change of who is ticked: the attorney table, the Excerpts window, the Parties number, a job shown again, Generate.
The combined forms of a trial count the parties of its joint invoice. Est. number of pages of a job with a
transcript PDF is counted from the PDF ("PDF"), not a number in a document's words, and doesn't turn into a typed
number ("you", which the invoice bills as "the Pages field") by itself: not when a record is opened again with a
document gone (or its transcript moved), not when the suggestion already shown is picked from the menu. The count
typed again, or the field cleared, is still the count (the user's own pages are billed); only another number is
the user's. All names are made up."""
import json

import pytest

from minute_filler.batch import _date_key, case_forms, fill_jobs, form_groups, group, read_docs, remerge
from minute_filler.excerpts import Run, case_days, firms_of, store
from minute_filler.models import SRC_DERIVED, SRC_PDF, SRC_REGEX, SRC_USER, Attorney, FieldState
from minute_filler.settings import Settings

from helpers import pat_settings, text_doc, transcript_pdf

PR, DS = "pr", "ds"


def people(*ticked):
    """Alex and Robin of one firm, Dana and Sam of their own; those named in `ticked` are ticked."""
    out = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel"),
           Attorney(name="Robin Example", firm="Counsel & Counsel"),
           Attorney(name="Dana Smith", firm="Smith Law"),
           Attorney(name="Sam Advocate", firm="Advocate & Partners"),
           Attorney(firm="Example Firm LLP")]
    for a in out:
        a.checked = (a.name or a.firm) in ticked
    return out


@pytest.fixture
def s(tmp_path):
    s = pat_settings(initials="pr")
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def shared_job(tmp_path, s, name="Roe.pdf", date=""):
    """The job of a transcript of 30 pages: Pat Reporter's first 10, Dana Smith's ("ds") the other 20."""
    path = transcript_pdf(tmp_path / name, 30, date=date, initials=[PR] * 10 + [DS] * 20)
    docs, errors = read_docs([str(path)], s)
    assert not errors
    job, = group(docs, s)
    return job


# ------------------------------------------------------------------ copies
def test_copies_are_the_ordering_parties_a_firm_is_one(tmp_path, s):
    job = shared_job(tmp_path, s)
    job.case.attorneys = people("Alex B. Counsel", "Robin Example", "Dana Smith")  # two of one firm
    job.att_touched = True
    remerge(job, s)
    # (up to version 2.0, two attorneys of one firm were two parties: 3 copies)
    assert job.case.get("copies") == "2" and job.case.fields["copies"].source == SRC_DERIVED
    assert job.invoice_opts().parties == 2  # the same count as the invoice's
    firm = next(a for a in job.case.attorneys if a.firm == "Counsel & Counsel")
    assert firm.name == "Alex B. Counsel, Robin Example" and firm.checked  # the firm's two rows are one now


def test_the_parties_number_sets_the_copies_and_a_typed_number_wins(tmp_path, s):
    job = shared_job(tmp_path, s)
    job.case.attorneys = people("Alex B. Counsel", "Dana Smith")
    job.att_touched = True
    job.parties = 5
    remerge(job, s)
    assert job.case.get("copies") == "5" == str(job.invoice_opts().parties)
    job.parties = 0
    job.refresh_copies(s)
    assert job.case.get("copies") == "2"
    job.case.set("copies", "7")
    job.parties = 4
    job.refresh_copies(s)
    assert job.case.get("copies") == "7"


def test_excerpts_ticking_attorneys_updates_the_copies(tmp_path, s):
    """The reported case: one attorney ticked (1 copy), then three ticked in the Excerpts window: the copies
    stayed 1 until the job was merged again."""
    job = shared_job(tmp_path, s)
    job.case.attorneys = people("Alex B. Counsel")
    job.att_touched = True
    remerge(job, s)
    assert job.case.get("copies") == "1"
    day, = case_days([job])
    keys = [a.key() for a in job.case.attorneys[:3]]
    store(day, [Run(day, 1, day.pages, keys)], firms_of([job]), s)
    assert sum(a.checked for a in job.case.attorneys) == 3
    assert job.case.get("copies") == "3" and job.case.fields["copies"].source == SRC_DERIVED


def test_generate_makes_the_forms_with_the_parties_as_they_are_now(tmp_path, s):
    job = shared_job(tmp_path, s)
    job.case.attorneys = people("Alex B. Counsel", "Dana Smith", "Sam Advocate")
    job.case.fields["copies"] = FieldState("1", SRC_DERIVED, 0.9, ["1"])  # left behind by an earlier tick
    fill_jobs([job], s, outputs=["agreement"])
    assert not job.error and job.case.get("copies") == "3"


def test_the_combined_forms_of_a_trial_count_the_parties_of_its_joint_invoice(tmp_path, s):
    d = tmp_path / "in"
    d.mkdir()
    for day, n in (("September 28, 2026", 12), ("September 30, 2026", 8)):
        transcript_pdf(d / f"Roe {day}.pdf", n, date=day)
    docs, errors = read_docs([str(p) for p in sorted(d.iterdir())], s)
    assert not errors
    trial = sorted(group(docs, s), key=lambda j: _date_key(j.case.get("dates")))
    trial[0].case.attorneys = people("Alex B. Counsel", "Robin Example")
    trial[1].case.attorneys = people("Alex B. Counsel", "Robin Example", "Dana Smith")
    for j in trial:
        j.refresh_copies(s)
    # each day: its own parties (Alex and Robin are one firm, one party)
    assert [j.case.get("copies") for j in trial] == ["1", "2"]
    g, = form_groups(trial, s)
    forms = case_forms(g, s, ["agreement", "mofr"])
    assert len(forms) == 3 and {f.case.get("copies") for f, _ in forms} == {"2"}  # the trial's: Dana too
    trial[1].parties = 6  # the Parties number of the joint invoice
    forms = case_forms(g, s, ["agreement", "mofr"])
    assert {f.case.get("copies") for f, _ in forms} == {"6"}


# ------------------------------------------------------------------ pages
def test_the_pages_of_a_transcript_are_its_count_not_a_number_in_an_email(tmp_path, s):
    path = transcript_pdf(tmp_path / "Roe.pdf", 30)
    mail = text_doc(s, "Please send the minutes in Jane Roe v. X.Y. Holding Corporation, Index No. 712345/2021, "
                       "June 3, 2026: about 30 pages.", "order.txt")
    pdf, = read_docs([str(path)], s)[0]
    job, = group([mail, pdf], s)
    assert job.case.get("est_pages") == "30" and job.case.fields["est_pages"].source == SRC_PDF


def test_a_count_kept_as_typed_is_the_count_again(tmp_path, s):
    job = shared_job(tmp_path, s)
    # the field shows every page; the invoice bills the user's own 10
    assert job.case.get("est_pages") == "30" and job.case.fields["est_pages"].source == SRC_PDF
    assert job.invoice_pages() == 10
    job.case.fields["est_pages"] = FieldState("10", SRC_USER, 0.95, ["10"])  # (a record of 1.x opened again)
    # the pages billed anyway are no number of the user's, before the next merge too (until 2.1 they were: every
    # agreement showed 10 until something merged the job again, then each attorney's pages)
    assert not job.pages_typed() and job.invoice_pages() == 10
    remerge(job, s)  # the count again
    assert job.case.fields["est_pages"].source == SRC_PDF and not job.pages_typed()
    assert job.case.get("est_pages") == "30" and job.case.fields["est_pages"].alternatives == ["30"]
    job.case.set("est_pages", "30")  # the count typed again: still the count, the user's own pages billed
    remerge(job, s)
    assert not job.pages_typed() and job.invoice_pages() == 10
    job.case.set("est_pages", "25")  # another number: the user's, billed as typed
    remerge(job, s)
    assert job.pages_typed() and job.invoice_pages() == 25


def test_another_number_in_the_words_gives_way_to_the_count(tmp_path, s):
    job = shared_job(tmp_path, s)
    job.case.fields["est_pages"] = FieldState("25", SRC_REGEX, 0.7, ["25"])
    remerge(job, s)
    assert job.case.get("est_pages") == "30" and job.invoice_pages() == 10


# ------------------------------------------------------------------ the window
@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt, s):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s.use_ai = s.open_after = False
    s.welcomed = True
    return make_window(s)


def wait(win, until, seconds=20):
    import time

    from PySide6.QtWidgets import QApplication
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def show(win, job):
    win.jobs, win.cur = [job], job
    win._refresh_jobs()
    win._show_job()


def test_the_window_shows_the_copies_of_the_excerpts_and_the_parties(window, tmp_path):
    job = shared_job(tmp_path, window.s)
    job.case.attorneys = people("Alex B. Counsel")
    job.att_touched = True
    remerge(job, window.s)
    show(window, job)
    assert window.rows["copies"].text() == "1"
    window.output_boxes["invoice"].setChecked(True)
    window._who_ordered(job)
    xw = window.excerpts
    day = xw.runs[0].day
    xw._keep(day, [Run(day, 1, day.pages, [a.key() for a in job.case.attorneys[:3]])])
    assert window.rows["copies"].text() == "3" and window.rows["copies"].state.source == SRC_DERIVED
    window.inv_parties.setValue(5)
    assert job.parties == 5 and window.rows["copies"].text() == "5"
    xw.close()


def test_a_job_shown_again_shows_the_copies_of_its_ticks(window, tmp_path):
    job = shared_job(tmp_path, window.s)
    job.case.attorneys = people("Alex B. Counsel", "Dana Smith", "Sam Advocate")
    job.case.fields["copies"] = FieldState("1", SRC_DERIVED, 0.9, ["1"])
    show(window, job)
    assert window.rows["copies"].text() == "3"


def test_picking_the_shown_suggestion_keeps_the_pdf_count(window, tmp_path):
    job = shared_job(tmp_path, window.s)
    show(window, job)
    row = window.rows["est_pages"]
    assert row.state.source == SRC_PDF and row.text() == "30"
    assert [a.text() for a in row.alts.menu().actions()] == ["30"]  # (not the user's own 10: typed, on every agreement)
    row.alts.menu().actions()[0].trigger()  # "30", the one shown
    assert row.state.source == SRC_PDF and not job.pages_typed() and job.invoice_pages() == 10
    row.choose("30")  # the count typed in: still the count (as the user's it would bill Dana's pages too)
    assert row.state.source == SRC_PDF and not job.pages_typed() and job.invoice_pages() == 10
    row.choose("25")  # another number: the user's, billed as the Pages field
    assert row.state.source == SRC_USER and job.pages_typed() and job.invoice_pages() == 25


def test_a_past_job_with_a_document_gone_counts_its_transcript_again(window, tmp_path):
    """Opened again from the records while one of its documents had moved: the page count was kept as typed, and
    the invoice billed "the Pages field" rather than the user's own pages."""
    from minute_filler.batch import job_origin
    from minute_filler.deliver import case_snapshot
    job = shared_job(tmp_path, window.s)
    job.case.attorneys = people("Alex B. Counsel", "Dana Smith")
    origin = json.loads(json.dumps({**job_origin(job), "case": case_snapshot(job.case)}))
    origin["sources"].append(str(tmp_path / "moved away.eml"))
    assert origin["case"]["fields"]["est_pages"][:2] == ["30", SRC_PDF]
    assert window.open_past_job(json.dumps(origin)) is True
    wait(window, lambda: window.cur.docs)
    cur = window.cur
    assert cur.case.fields["est_pages"].source == SRC_PDF and not cur.pages_typed()
    assert cur.invoice_pages() == 10 and window.rows["est_pages"].state.source == SRC_PDF
    assert window.rows["copies"].text() == "2"
    assert "Your pages: 10 of 30" in window.inv_pages_info.text()


# ------------------------------------------------------------------ found in the 2.1 sweep
def test_a_past_job_whose_transcript_moved_keeps_its_page_count(window, tmp_path):
    """The transcript had moved and an order letter saved as a PDF was still there: a PDF read again was taken
    for the transcript, the count was dropped and no invoice could be made."""
    import pymupdf
    from minute_filler.batch import job_origin
    from minute_filler.deliver import case_snapshot
    job = shared_job(tmp_path, window.s)
    job.case.attorneys = people("Alex B. Counsel")
    origin = json.loads(json.dumps({**job_origin(job), "case": case_snapshot(job.case)}))
    (tmp_path / "Roe.pdf").rename(tmp_path / "Roe moved.pdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Dear Pat Reporter,\nPlease send the minutes of June 3, 2026.\n"
                                         "Thank you, Dana Smith", fontsize=10)
    doc.save(tmp_path / "order letter.pdf")
    origin["sources"] = [str(tmp_path / "Roe.pdf"), str(tmp_path / "order letter.pdf")]
    assert window.open_past_job(json.dumps(origin)) is True
    wait(window, lambda: window.cur.docs)
    assert window.cur.case.get("est_pages") == "30" and window.cur.invoice_pages() == 30


def test_a_pages_field_cleared_bills_the_users_own_pages(tmp_path, s):
    """A cleared field was a number typed in: the invoice billed every page, the other reporter's too."""
    job = shared_job(tmp_path, s)
    job.case.attorneys = people("Dana Smith")
    job.case.fields["est_pages"] = FieldState("", SRC_USER, 1.0, [])
    assert not job.pages_typed() and job.invoice_pages() == 10 and job.ordered_pages()["smith law"] == 30
    remerge(job, s)
    assert job.case.get("est_pages") == "30" and job.case.fields["est_pages"].source == SRC_PDF
