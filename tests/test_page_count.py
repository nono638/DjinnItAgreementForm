"""A transcript's own pages, read four ways and reconciled (takes.count_pages): the pages up to the word index, the
pages with line numbers, the printed page numbers and the index found from the end. The word index may take a
quarter of the PDF (5 pages of a short one); a count that leaves it more is checked another way, and warned about
when no other way can tell. The window says what Est. number of pages counts ("Transcript pages 378–397 · excludes
the 4 word-index pages after them") and what the Billed row's two numbers are. Fictional transcripts only."""
import os
import time

import pymupdf
import pytest

from minute_filler import takes
from minute_filler.batch import group, read_docs, remerge
from minute_filler.ingest import ingest_file
from minute_filler.models import SRC_PDF, SRC_USER, FieldState
from minute_filler.takes import PageCount, PageFacts, PageMark, count_pages, index_limit

from helpers import page_numbered_transcript, pat_settings

PR, DS = "pr", "ds"


def job_of(*paths):
    """The one job the documents make, read as the window reads them."""
    s = pat_settings()
    docs, errors = read_docs([str(p) for p in paths], s)
    assert not errors
    jobs = group(docs, s)
    assert len(jobs) == 1
    return jobs[0], s


# ------------------------------------------------------------------ the four readings
def test_all_four_readings_agree(tmp_path):
    ing = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 20, index_pages=4))
    c = ing.count
    assert (c.pages, c.index_pages, c.first, c.last) == (20, 4, 378, 397)
    assert c.readings == {"scan": 20, "lines": 20, "numbers": 20, "back": 20}
    assert (c.confidence, c.note, c.warning, c.others) == (0.95, "", "", [])
    assert len(ing.marks) == 20


def test_a_page_that_looks_like_the_index_mid_transcript_is_counted_another_way(tmp_path):
    """An exhibit list on page 6 (no line numbers, no initials, page:line references) stops the first reading at 5
    pages: that would leave 30 of 35 pages to the index, more than a quarter. The line numbers, the printed numbers
    and the index from the end all say 30: that is the count, and the run sheet's takes cover all 30 pages."""
    ing = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 30, index_pages=5, index_like=(5,)))
    c = ing.count
    assert c.readings == {"scan": 5, "lines": 30, "numbers": 30, "back": 30}
    assert (c.pages, c.how, c.confidence, c.warning) == (30, "lines", 0.8, "")
    assert c.note == "counted from the pages with line numbers: the word index looked too long"
    assert len(ing.marks) == 30
    assert sum(t.pages for t in takes.find_takes(ing.marks)) == 30


def test_an_index_over_a_quarter_of_the_pdf_is_warned_about(tmp_path):
    """No printed page numbers: the scan, the line numbers and the index from the end all leave 12 of the 20 pages
    to the index. The count stays (nothing better), marked for review, with a warning: never
    "the pages couldn't be counted"."""
    ing = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 8, index_pages=12, numbered=False))
    c = ing.count
    assert c.readings == {"scan": 8, "lines": 8, "back": 8}
    assert (c.pages, c.confidence) == (8, 0.55)
    assert c.warning == "⚠ 12 of the PDF's 20 pages were taken for the word index, more than a quarter: check the count"


def test_a_short_transcripts_index_may_take_five_pages(tmp_path):
    assert (index_limit(10), index_limit(26), index_limit(96)) == (5, 7, 24)
    c = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 6, index_pages=4)).count  # 40% of the PDF
    assert (c.pages, c.index_pages, c.warning, c.confidence) == (6, 4, "", 0.95)


def test_no_word_index(tmp_path):
    c = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 12)).count
    assert (c.pages, c.index_pages, c.warning) == (12, 0, "")
    assert "back" not in c.readings  # (walking back over no index says nothing)


def test_two_counts_each_with_two_readings_are_both_offered():
    """The scan and the line numbers say 22 (the last two pages look like the index but carry the reporter's
    initials), the index found from the end and the printed numbers 20: a warning names both, the scan's count
    is kept, and 20 is the other count offered."""
    facts = [PageFacts(PageMark(number=101 + i, initials=PR), lined=True) for i in range(22)]
    for f in facts[20:]:
        f.index = True
    facts += [PageFacts(PageMark(), index=True) for _ in range(2)]
    c = count_pages(facts)
    assert c.readings == {"scan": 22, "lines": 22, "back": 20, "numbers": 20}
    assert (c.pages, c.others, c.confidence) == (22, [20], 0.55)
    assert c.warning == ("⚠ the PDF reads as 22 pages (reading the pages up to the word index) or 20 (the word index "
                         "found from the end): check the count")


def test_a_scan_without_text_counts_every_page():
    c = count_pages([PageFacts(PageMark(), blank=True) for _ in range(7)])
    assert (c.pages, c.how, c.warning, c.confidence, c.readings) == (7, "pdf", "", 0.95, {})


# ------------------------------------------------------------------ the field and the job
def test_a_warning_marks_the_field_for_review_and_offers_the_other_count(tmp_path):
    job, s = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 8, index_pages=12, numbered=False))
    fs = job.case.fields["est_pages"]
    assert (fs.value, fs.source) == ("8", SRC_PDF) and fs.confidence < 0.6
    # a split between two counts: the other one is offered in the ▾ list, as the job's total
    doc = job.transcripts()[0]
    doc.ing.count = PageCount(8, 12, readings={"scan": 8, "lines": 10}, confidence=0.55, others=[10],
                              warning="⚠ the PDF reads as 8 pages or 10: check the count")
    remerge(job, s)
    assert job.case.fields["est_pages"].alternatives[:2] == ["8", "10"]


def test_a_count_from_another_reading_is_sure_enough(tmp_path):
    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 30, index_pages=5, index_like=(5,)))
    fs = job.case.fields["est_pages"]
    assert (fs.value, fs.confidence) == ("30", 0.8)
    assert "5" not in fs.alternatives  # (the scan's count that the other readings put right is no rival)


def test_the_line_under_the_pages_field(tmp_path):
    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 20, index_pages=4))
    assert job.pages_help() == ("Transcript pages 378–397 · excludes the 4 word-index pages after them", "")
    job.case.fields["est_pages"] = FieldState("50", SRC_USER, 1.0)
    assert job.pages_help()[0] == "Typed by you · the transcript has 20 pages, index excluded"

    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-4-2026.pdf", 12))
    assert job.pages_help() == ("Transcript pages 378–389 (no word index)", "")

    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-5-2026.pdf", 30, index_pages=5, index_like=(5,)))
    assert job.pages_help() == ("Transcript pages 378–407 · excludes the 5 word-index pages after them\n"
                                "counted from the pages with line numbers: the word index looked too long", "")

    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-8-2026.pdf", 8, index_pages=12, numbered=False))
    assert job.pages_help() == ("8 transcript pages · excludes the 12 word-index pages after them",
                                "⚠ 12 of the PDF's 20 pages were taken for the word index, more than a quarter: "
                                "check the count")


def test_two_transcripts_of_one_day(tmp_path):
    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026 AM.pdf", 20, index_pages=4),
                    page_numbered_transcript(tmp_path / "Roe 6-3-2026 PM.pdf", 12, first=398))
    assert job.transcript_pages() == 32
    assert job.pages_help() == ("2 transcripts, 20 + 12 pages · excludes the 4 word-index pages after them", "")


# ------------------------------------------------------------------ the window
@pytest.fixture
def window(tmp_path, monkeypatch, make_window):
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return make_window(s)


def load(window, path):
    """Drops a file on the window and waits until it is read."""
    from PySide6 import QtWidgets
    window.add_files([str(path)])
    end = time.time() + 20
    while time.time() < end and (window.work or not window.cur.docs):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


def test_the_window_says_what_each_page_number_counts(window, tmp_path):
    """Under Est. number of pages: which pages the count is, without the word index; on the Billed row: the user's
    pages of the whole transcript's."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    load(window, page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 6, initials=[PR, PR, DS, DS, DS, PR],
                                          index_pages=2))
    window.output_boxes["invoice"].setChecked(True)
    window._refresh_outputs()
    assert window.rows["est_pages"].text() == "6"
    assert window.pages_help.text() == "Transcript pages 378–383 · excludes the 2 word-index pages after them"
    assert not window.pages_help.isHidden() and window.pages_warn.isHidden()
    assert window.inv_pages_info.text() == "You wrote 3 of 6 total pages"


def test_the_window_warns_when_the_count_looks_wrong(window, tmp_path):
    load(window, page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 8, index_pages=12, numbered=False))
    window._refresh_outputs()
    assert window.pages_warn.text().startswith("⚠ 12 of the PDF's 20 pages were taken for the word index")
    assert not window.pages_warn.isHidden()
    assert window.rows["est_pages"].edit.property("review")  # amber


def test_the_count_typed_again_stays_amber(window, tmp_path):
    """Typed again, the count with a warning is the count (badge PDF) as sure as its readings: it lost its amber
    (0.95) while the warning under it still showed."""
    load(window, page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 8, index_pages=12, numbered=False))
    window.rows["est_pages"].choose("8")
    st = window.rows["est_pages"].state
    assert (st.source, st.confidence) == (SRC_PDF, 0.55)
    assert window.rows["est_pages"].edit.property("review")


def test_a_number_typed_over_a_scanned_pdf_says_so(tmp_path):
    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 7))
    job.transcripts()[0].ing.count = PageCount(7, how="pdf")  # (no reading could tell, as for a scan)
    job.case.fields["est_pages"] = FieldState("50", SRC_USER, 1.0)
    assert job.pages_help()[0] == "Typed by you · the PDF has 7 pages"


def test_no_transcript_no_line(window, tmp_path):
    from helpers import SAMPLES
    load(window, SAMPLES / "email_short.txt")
    window._refresh_outputs()
    assert not window.order_form.isRowVisible(window.pages_help_row)


# ------------------------------------------------------------------ the review of the readings (fictional layouts)
def pages(n, start=0, **kw):
    """n made-up PageFacts: a page of the transcript (numbered from `start` when given, lined, signed "pr")
    unless kw says otherwise."""
    out = []
    for i in range(n):
        mark = PageMark(number=start + i if start else None, initials=kw.get("initials", PR))
        out.append(PageFacts(mark, lined=kw.get("lined", True), index=kw.get("index", False),
                             blank=kw.get("blank", False)))
    return out


def test_a_scanned_transcript_with_a_text_word_index():
    """Ten pages of pictures (no text), then three of index: the transcript is the ten, not one page."""
    c = count_pages(pages(10, initials="", lined=False, blank=True) + pages(3, initials="", lined=False, index=True))
    assert (c.pages, c.how, c.index_pages, c.warning) == (10, "back", 3, "")
    # with an index that would be most of the PDF, every page is counted and the count is to be checked
    c = count_pages(pages(4, initials="", lined=False, blank=True) + pages(12, initials="", lined=False, index=True))
    assert (c.pages, c.how, c.confidence) == (16, "pdf", 0.55) and "every page is counted" in c.warning


def test_blank_pages_at_the_end_are_no_word_index(tmp_path):
    path = page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 20)
    doc = pymupdf.open(path)
    for _ in range(10):
        doc.new_page()
    doc.saveIncr()
    doc.close()
    ing = ingest_file(path)
    assert (ing.count.pages, ing.count.index_pages, ing.count.warning, ing.count.confidence) == (20, 0, "", 0.95)
    job, _ = job_of(path)
    assert job.pages_help() == ("Transcript pages 378–397 (no word index)", "")


def test_a_numbered_word_index_is_not_counted_by_the_printed_numbers():
    """The index's pages numbered on (9 to 20): the printed numbers never count past the index found from the
    end, so all four readings say 8, and the index (12 of 20 pages) is warned about, never billed."""
    c = count_pages(pages(8, start=1) + pages(12, start=9, initials="", lined=False, index=True))
    assert c.readings == {"scan": 8, "lines": 8, "back": 8, "numbers": 8}
    assert c.pages == 8 and c.warning.startswith("⚠ 12 of the PDF's 20 pages")
    # an exhibit list on page 6 stops the scan; the index found from the end, 13 numbered pages, isn't billed
    body = pages(83, start=378, lined=False)
    body[5] = PageFacts(PageMark(number=383), index=True)
    c = count_pages(body + pages(13, start=461, initials="", lined=False, index=True))
    assert (c.pages, c.how, c.confidence) == (83, "back", 0.8)
    assert c.note == "counted from the word index found from the end: the word index looked too long"


def scanned(path, scans, cover_number=True, index_pages=0, lined=True):
    """A typed caption page (its number printed when cover_number, signed "pr", its lines numbered when lined),
    then `scans` pages that are only a picture, as a scanner makes them, then `index_pages` pages of typed word
    index."""
    page_numbered_transcript(path, 1, first=1, index_pages=index_pages, numbered=cover_number, lined=lined)
    doc = pymupdf.open(path)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    for i in range(scans):
        doc.new_page(pno=1 + i).insert_image(pymupdf.Rect(36, 36, 576, 756), pixmap=pix)
    doc.save(path.with_suffix(".tmp"))
    doc.close()
    path.with_suffix(".tmp").replace(path)
    return path


def test_scanned_pages_after_a_typed_cover_page_are_counted(tmp_path):
    """The scanned pages have no text, but they aren't blank: they were all left out (1 page counted, sure of it)."""
    ing = ingest_file(scanned(tmp_path / "Roe 6-3-2026.pdf", 29, cover_number=False))
    c = ing.count
    assert c.readings == {"scan": 1, "lines": 1}
    assert (c.pages, c.how, c.confidence) == (30, "pdf", 0.55) and "every page is counted" in c.warning
    assert ing.marks == []
    job, _ = job_of(tmp_path / "Roe 6-3-2026.pdf")
    assert job.pages_help() == ("30 pages: every page of the PDF", c.warning)  # (no "no word index" beside it)


def test_a_scanned_page_sheet_writes_no_warning_in_the_log(tmp_path, caplog):
    """A PDF without line numbers (a scanned run sheet printout, say) is seldom a transcript: its count's warning is
    only an info line in the log."""
    import logging
    with caplog.at_level(logging.INFO, logger="yin"):
        ing = ingest_file(scanned(tmp_path / "Sheet.pdf", 11, lined=False))
    assert ing.count.warning
    lines = [r for r in caplog.records if r.getMessage().startswith("page count:")]
    assert lines and all(r.levelno == logging.INFO for r in lines)


def test_a_reading_that_leaves_a_sensible_index_beats_two_that_dont(tmp_path):
    """A numbered cover page, 10 scanned pages, a 3-page typed index: the scan and the line numbers both see the
    cover alone (1 page, leaving 13 of 14 to the index); the index found from the end says 11. 11 is kept, and the
    warning names the other count."""
    c = ingest_file(scanned(tmp_path / "Roe.pdf", 10, index_pages=3)).count
    assert c.readings == {"scan": 1, "lines": 1, "back": 11}
    assert (c.pages, c.index_pages, c.others, c.confidence) == (11, 3, [1], 0.55)
    assert c.warning == ("⚠ the PDF reads as 11 pages (the word index found from the end) or 1 (reading the pages "
                         "up to the word index; that leaves more than a quarter of the PDF to the index): check the "
                         "count")


def test_the_pages_marks_when_the_first_reading_stops_at_page_two(tmp_path):
    """An unsigned title page, then an exhibit list: the first reading ends at the title page and finds no initials,
    but the other readings count 20 pages, and their initials are read (whose pages, the run sheet's takes)."""
    from minute_filler.runsheet import rows_from
    ing = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 20, initials=[""] + [PR] * 9 + [DS] * 10,
                                               numbered=False, index_like=(1,), index_pages=4))
    assert ing.count.pages == 20 and "scan" not in ing.count.readings
    assert len(ing.marks) == 20 and {m.initials for m in ing.marks} >= {PR, DS}
    rows = rows_from(ing, None, pat_settings())
    assert len(rows) == 2 and sum(r.pages for r in rows) == 20


def test_a_short_signed_last_page_is_no_blank_separator():
    """The transcript's last page holds only its number, a line and the initials: it is a page of the transcript,
    not a blank page before the index to step over."""
    last = PageFacts(PageMark(number=397, initials=DS), lined=False, blank=True)
    c = count_pages(pages(19, start=378) + [last] + pages(4, initials="", lined=False, index=True))
    assert c.readings == {"scan": 20, "lines": 19, "back": 20, "numbers": 20}
    assert (c.pages, c.index_pages, c.confidence, c.note) == (20, 4, 0.95, "")


def test_the_printed_span_only_when_the_pages_print_it():
    """Two transcripts numbered apart (378-407, then 1-6), or an unnumbered cover before page 378: no "Transcript
    pages 378-413" or "377-397", pages that aren't printed anywhere."""
    c = count_pages(pages(30, start=378) + pages(6, start=1))
    assert c.pages == 36 and (c.first, c.last) == (None, None)
    c = count_pages(pages(1, lined=False) + pages(20, start=378) + pages(3, initials="", lined=False, index=True))
    assert c.pages == 21 and (c.first, c.last) == (None, None)
    c = count_pages(pages(20, start=378) + pages(3, initials="", lined=False, index=True))
    assert (c.first, c.last) == (378, 397)


def test_an_index_with_a_page_of_line_numbers_after_it_is_warned_about():
    """The index numbered on (398-401), then a certificate with line numbers (402): the line numbers and the
    printed numbers count through the index. That count is kept, but with a warning offering the first reading's."""
    facts = (pages(20, start=378) + pages(4, start=398, initials="", lined=False, index=True)
             + pages(1, start=402, initials=""))
    c = count_pages(facts)
    assert c.readings == {"scan": 20, "lines": 25, "numbers": 25}
    assert (c.pages, c.others, c.confidence) == (25, [20], 0.55)
    assert c.warning == ("⚠ the PDF reads as 25 pages (the pages with line numbers) or 20 (reading the pages up to "
                         "the word index): pages in between look like the word index, check the count")


def test_a_cover_page_without_a_number_shows_no_page_zero():
    c = count_pages(pages(1, lined=False) + pages(19, start=1))
    assert c.pages == 20 and (c.first, c.last) == (None, None)


def test_the_run_sheet_row_of_a_transcript_without_initials(tmp_path):
    from minute_filler.runsheet import rows_from
    ing = ingest_file(page_numbered_transcript(tmp_path / "Roe.pdf", 20, initials=[""] * 20, numbered=False,
                                               index_pages=5))
    assert ing.marks == [] and ing.count.pages == 20
    assert [r.pages for r in rows_from(ing, None, pat_settings())] == [20]


def test_a_scanned_transcript_says_every_page_is_counted(tmp_path):
    job, _ = job_of(page_numbered_transcript(tmp_path / "Roe 6-3-2026.pdf", 7))
    job.transcripts()[0].ing.count = PageCount(7, how="pdf")
    assert job.pages_help() == ("7 pages: every page of the PDF (no text to find a word index in)", "")
