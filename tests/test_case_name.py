"""The case name, read several ways and reconciled: the court caption (also when its "-against-" line carries the
caption's Index column), the heading of the word index after the transcript, a "Re:" line, and the user's records
for the same index number (a suggestion: amber, and asked about at Generate). Two readings of different parts of
the document that agree make the name surer; one line read twice does not. A doctor's letters after a name keep
their case ("Sam Poe, DPM", not "Dpm"), and a witness sworn in with them is named without them. Fictional names
only."""
import pymupdf

from minute_filler.batch import RECORDS_CONF, group, read_docs, records_extraction
from minute_filler.extract_regex import RegexExtractor, smart_title
from minute_filler.ingest import Ingested, ingest_text
from minute_filler.models import SRC_RECORDS, SRC_REGEX, Extraction
from minute_filler.records import Ledger, default_db, recorded_cases
from minute_filler.takes import scan_page

from helpers import CAPTION_DPM, page_numbered_transcript, pat_settings

# a title page with the index number and the date, but no parties to read
NO_PARTIES = """ 1  SUPREME COURT OF THE STATE OF NEW YORK
    COUNTY OF QUEENS :  CIVIL TERM :  PART 14
 2  Index No. 712345/2021
 3  June 3, 2026
 4
 5"""


def best(ex: Extraction):
    return max(ex.fields["case_name"], key=lambda c: (c.confidence, len(c.value)))


def case_of(*paths):
    """The case_name field of the one job the documents make."""
    s = pat_settings()
    docs, errors = read_docs([str(p) for p in paths], s)
    assert not errors
    jobs = group(docs, s)
    assert len(jobs) == 1
    return jobs[0].case.fields["case_name"]


# ------------------------------------------------------------------ the caption and the letters after a name
def test_the_against_line_may_carry_the_index_column():
    ex = RegexExtractor().extract(Ingested("cover.txt", "text", text=CAPTION_DPM))
    c = best(ex)
    assert (c.value, c.confidence) == ("Jane Roe v. Sam Poe, DPM", 0.85)


def test_a_label_in_the_column_with_one_space_only():
    text = CAPTION_DPM.replace("-against-               Index No.", "-against- Index No.")
    assert best(RegexExtractor().extract(Ingested("cover.txt", "text", text=text))).value == "Jane Roe v. Sam Poe, DPM"


def test_letters_after_a_name_keep_their_case():
    assert smart_title("SAM POE, DPM") == "Sam Poe, DPM"
    assert smart_title("PAT ROE, PH.D.") == "Pat Roe, Ph.D."
    assert smart_title("ALEX DOE, D.O.") == "Alex Doe, D.O."
    assert smart_title("ALEX DOE, DO") == "Alex Doe, DO"
    assert smart_title("SAM POE, M.D., FACS") == "Sam Poe, M.D., FACS"
    assert smart_title("DANA SMITH, PSY.D") == "Dana Smith, Psy.D"
    assert smart_title("WE DO NOT") == "We Do Not"          # 'DO' anywhere else is a word
    assert smart_title("DO RIGHT LLC") == "Do Right LLC"


def test_a_witness_sworn_in_with_letters_after_the_name():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 80), " 1  SAM POE, DPM, called as a witness by the\n 2  Defendant, having been duly sworn,",
                     fontsize=9)
    assert scan_page(page).sworn == "SAM POE"


# ------------------------------------------------------------------ the word index's heading, and readings that agree
def test_the_word_index_heading_names_the_case(tmp_path):
    """No caption to read: the heading alone gives the name, at 0.75."""
    ex = RegexExtractor().extract(_ingest(page_numbered_transcript(tmp_path / "t.pdf", 6, index_pages=2,
                                                                    caption=NO_PARTIES)))
    c = best(ex)
    assert (c.value, c.confidence, c.note) == ("Jane Roe v. Sam Poe, DPM", 0.75, "the word index's heading")


def test_the_caption_and_the_heading_agree(tmp_path):
    ex = RegexExtractor().extract(_ingest(page_numbered_transcript(tmp_path / "t.pdf", 6, index_pages=2)))
    c = best(ex)
    assert (c.value, c.confidence) == ("Jane Roe v. Sam Poe, DPM", 0.95)


def test_headings_of_other_shapes(tmp_path):
    x = RegexExtractor()
    for head in ("JANE ROE\nv.\nSAM POE, DPM", "JANE ROE -against-\nSAM POE, DPM", "JANE ROE v. SAM POE, DPM\nJune 3"):
        ex = Extraction()
        x._index_heading(head, ex)
        assert best(ex).value == "Jane Roe v. Sam Poe, DPM", head
    ex = Extraction()
    x._index_heading("WORD INDEX\nabout (2)\n380:7", ex)
    assert not ex.fields


def test_one_line_read_twice_is_no_agreement():
    """'Re: Jane Roe v. Sam Poe' is the label line's reading, and the running text's too: still 0.9."""
    c = best(RegexExtractor().extract(ingest_text("Re: Jane Roe v. Sam Poe\nPlease send the minutes.", "mail.txt")))
    assert (c.value, c.confidence) == ("Jane Roe v. Sam Poe", 0.9)


# ------------------------------------------------------------------ the user's records
def record(case_name, index="712345/2021"):
    Ledger(default_db()).log_activity("agreement", case_name=case_name, index_no=index)


def test_the_records_suggest_the_name_of_the_same_index_number(tmp_path):
    record("Jane Roe v. Sam Poe, DPM")
    fs = case_of(page_numbered_transcript(tmp_path / "t 6-3-2026.pdf", 6, caption=NO_PARTIES, heading=""))
    assert (fs.value, fs.source, fs.confidence) == ("Jane Roe v. Sam Poe, DPM", SRC_RECORDS, RECORDS_CONF)
    assert fs.needs_review


def test_a_recorded_name_that_agrees_raises_the_documents_own(tmp_path):
    """The records say 'Roe v. Poe', the caption 'Jane Roe v. Sam Poe, DPM': the same case, so the caption's name
    is surer (0.85 + 0.1) and keeps its own words and badge."""
    record("Roe v. Poe")
    fs = case_of(page_numbered_transcript(tmp_path / "t 6-3-2026.pdf", 6, heading=""))
    assert (fs.value, fs.source, round(fs.confidence, 2)) == ("Jane Roe v. Sam Poe, DPM", SRC_REGEX, 0.95)


def test_a_recorded_name_never_outranks_the_documents(tmp_path):
    record("Someone Else v. Another")
    fs = case_of(page_numbered_transcript(tmp_path / "t 6-3-2026.pdf", 6, heading=""))
    assert (fs.value, fs.source) == ("Jane Roe v. Sam Poe, DPM", SRC_REGEX)
    assert "Someone Else v. Another" in fs.alternatives


def test_only_the_same_index_number_counts(tmp_path):
    record("Jane Roe v. Sam Poe", index="712345/2022")    # another year
    record("Other v. Case", index="7123456/2021")         # a number that starts the same
    assert records_extraction("712345/2021", Extraction()) is None
    assert records_extraction("", Extraction()) is None
    assert recorded_cases("712345", tmp_path / "none.db") == []  # no database: nothing


def test_the_trash_is_not_asked():
    record("Jane Roe v. Sam Poe")
    lg = Ledger(default_db())
    lg.delete(activity=[a.id for a in lg.activity()])
    assert records_extraction("712345/2021", Extraction()) is None


# ------------------------------------------------------------------ the review (fictional names)
def test_a_witness_named_do_stays_one_witness():
    from minute_filler.takes import _same_person
    assert _same_person("LINH DO", "L. Do") and not _same_person("LINH DO", "L. Pham")
    assert _same_person("SAM POE, DPM", "S. Poe") and _same_person("PAT ROE, M.D., PH.D.", "Dr. Roe")


def test_do_inside_a_name_is_a_name():
    assert smart_title("ACME CORP., DO VAN HUNG AND SAM POE") == "Acme Corp., Do Van Hung and Sam Poe"
    assert smart_title("NGUYEN, DO THI MAI") == "Nguyen, Do Thi Mai"
    assert smart_title("SAM POE, DPM AND JANE ROE, DO") == "Sam Poe, DPM and Jane Roe, DO"


def test_the_against_line_with_ind_no_or_no():
    for label in ("Ind. No. 712345/2021", "No. 712345/2021", "Index # 712345/2021"):
        text = CAPTION_DPM.replace("-against-               Index No. 712345/2021", "-against- " + label)
        assert best(RegexExtractor().extract(Ingested("c.txt", "text", text=text))).value == \
            "Jane Roe v. Sam Poe, DPM", label


def test_re_matter_of_is_one_reading():
    c = best(RegexExtractor().extract(ingest_text("Re: Matter of Jane Roe\nPlease send the minutes.", "mail.txt")))
    assert (c.value, c.confidence) == ("Matter of Jane Roe", 0.9)


def test_headings_that_are_no_case():
    x = RegexExtractor()
    for head, want in (("ROE v.\nPOE et al.", "Roe v. Poe et al."), ("JANE ROE v.\nJune 3, 2026", None),
                       ("Roe v. Poe | Word Index | June 3, 2026", "Roe v. Poe"),
                       ("Word Index | ROE v. POE", "Roe v. Poe")):
        ex = Extraction()
        x._index_heading(head, ex)
        assert (best(ex).value if ex.fields else None) == want, head


def test_an_index_number_typed_with_its_label_in_the_records():
    record("Jane Roe v. Sam Poe", index="Index No. 712345/21")
    ex = records_extraction("712345/2021", Extraction())
    assert [c.value for c in ex.fields["case_name"]] == ["Jane Roe v. Sam Poe"]


def test_the_records_agree_once_and_the_documents_words_keep_their_badge(tmp_path):
    """An e-mail with only the index number and the transcript both ask the records: whichever is read first, the
    caption's name keeps its own badge and the records add +0.1 once."""
    record("Jane Roe v. Sam Poe, DPM")
    mail = tmp_path / "mail 6-3-2026.txt"
    mail.write_text("From: Alex Counsel <alex@example.com>\nIndex No. 712345/2021\nPlease send the minutes of "
                    "June 3, 2026.\n", encoding="utf-8")
    pdf = page_numbered_transcript(tmp_path / "t 6-3-2026.pdf", 6, heading="")
    for order in ((mail, pdf), (pdf, mail)):
        fs = case_of(*order)
        assert (fs.value, fs.source, round(fs.confidence, 2)) == ("Jane Roe v. Sam Poe, DPM", SRC_REGEX, 0.95), order


def _ingest(path):
    from minute_filler.ingest import ingest_file
    return ingest_file(path)


# ------------------------------------------------------------------ the review of the readings
def test_a_heading_side_partly_in_capitals_is_tidied_as_a_whole():
    """'SAM POE, DPM, et al.' was title-cased a word at a time ('Dpm'); a side in mixed case stays as written."""
    x = RegexExtractor()
    for head, want in (("JANE ROE v.\nSAM POE, DPM, et al.", "Jane Roe v. Sam Poe, DPM, et al."),
                       ("JANE ROE v.\nTHE CITY OF NEW YORK, et al.", "Jane Roe v. The City of New York, et al."),
                       ("JANE ROE v.\nSAM POE AND ACME HOSPITAL, et al.", "Jane Roe v. Sam Poe and Acme Hospital, et al."),
                       ("Jane Roe v.\nSam Poe, DPM", "Jane Roe v. Sam Poe, DPM"),
                       ("Jane Roe v.\nABC Holding Corp.", "Jane Roe v. ABC Holding Corp."),
                       ("WORD INDEX        JANE ROE v. SAM POE", "Jane Roe v. Sam Poe")):
        ex = Extraction()
        x._index_heading(head, ex)
        assert best(ex).value == want, head


def test_a_short_transcripts_word_index_is_no_caption(tmp_path):
    """Two pages and a page of index, all among the first pages read: the index heading's 'v.' line alone was read
    as a caption, eight lines up into the testimony and down into the index's entries, and won."""
    path = page_numbered_transcript(tmp_path / "t.pdf", 2, index_pages=1, heading="JANE ROE\nv.\nSAM POE, DPM")
    ex = RegexExtractor().extract(_ingest(path))
    assert best(ex).value == "Jane Roe v. Sam Poe, DPM"
    assert not [c.value for c in ex.fields["case_name"] if "word" in c.value or "Yes" in c.value]


def test_as_a_matter_of_law_is_no_case():
    text = CAPTION_DPM + "\n 9  Q.  And as a matter of law the defendant was negligent?\n10  A.  Yes."
    ex = RegexExtractor().extract(Ingested("cover.txt", "text", text=text))
    assert best(ex).value == "Jane Roe v. Sam Poe, DPM"
    assert not [c.value for c in ex.fields["case_name"] if c.value.startswith("Matter of")]
    ex = RegexExtractor().extract(ingest_text("Please send the minutes in the Matter of Jane Roe, of June 3, 2026.",
                                              "mail.txt"))
    assert best(ex).value == "Matter of Jane Roe"


def test_the_against_line_with_a_case_number_beside_it():
    for column in ("Case No. 712345/2021", "#712345/2021"):
        text = CAPTION_DPM.replace("-against-               Index No. 712345/2021", "-against- " + column)
        assert best(RegexExtractor().extract(Ingested("c.txt", "text", text=text))).value == \
            "Jane Roe v. Sam Poe, DPM", column


def test_more_letters_after_a_name():
    assert smart_title("PAT ROE, PA-C") == "Pat Roe, PA-C"
    assert smart_title("SAM POE, M.D., F.A.C.S.") == "Sam Poe, M.D., F.A.C.S."
    assert smart_title("JANE ROE, RN, BSN") == "Jane Roe, RN, BSN"
    assert smart_title("JANE ROE-CRUZ") == "Jane Roe-Cruz"  # (a hyphen still joins two names)
    for line, who in ((" 1  SAM POE, M.D., F.A.C.S., called as a witness", "SAM POE"),
                      (" 1  PAT ROE, PA-C, called as a witness", "PAT ROE"),
                      (" 1  JANE ROE, RN, BSN, called as a witness", "JANE ROE"),
                      (" 1  MARY McDONALD, called as a witness", "MARY McDONALD")):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((60, 80), line + "\n 2  by the Defendant, having been duly sworn,", fontsize=9)
        assert scan_page(page).sworn == who, line


def test_the_recorded_wording_is_offered_beside_the_documents(tmp_path):
    """Day 1's record says 'Jane Roe v. Sam Poe, DPM'; day 2's e-mail only 'roe v poe'. The e-mail's wording is
    still shown (the records never outrank the document), but the recorded one is offered, so Generate asks."""
    record("Jane Roe v. Sam Poe, DPM")
    mail = tmp_path / "mail 6-4-2026.txt"
    mail.write_text("From: Alex Counsel <alex@example.com>\nPlease send the minutes in roe v poe, Index No. "
                    "712345/2021, of June 4, 2026.\n", encoding="utf-8")
    fs = case_of(mail)
    assert fs.value == "Roe v. Poe" and "Jane Roe v. Sam Poe, DPM" in fs.alternatives
    assert fs.confidence < 0.8 and fs.needs_review


def test_a_records_suggestion_is_amber_and_asked_about_at_generate(make_window, monkeypatch, tmp_path):
    """However sure (the AI agreeing gives 0.65), a name only the records give is amber, and Generate asks."""
    from minute_filler.gui import main_window
    from minute_filler.models import FieldState
    from helpers import ROE, make_case
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.outputs = ["agreement"]
    win = make_window(s)
    case = make_case(ROE)
    case.fields["case_name"] = FieldState("Jane Roe v. Sam Poe", SRC_RECORDS, 0.65)
    win.case = case
    win._show_case()
    assert win.rows["case_name"].edit.property("review")
    asked = []

    class Ask:
        Accepted = 1

        def __init__(self, questions, attorneys, parent):
            asked.append([key for key, _, _ in questions])

        def exec(self):
            return 0  # (Cancel)
    monkeypatch.setattr(main_window, "ClarifyDialog", Ask)
    monkeypatch.setattr(main_window, "generate", lambda *a, **k: [])
    monkeypatch.setattr(win, "_saved_box", lambda *a, **k: None)
    win.fill()
    assert asked == [["case_name"]]


def test_a_name_only_the_records_give_still_has_the_ai_read_the_pdf(make_window, tmp_path):
    """The AI reads a PDF while a required field is blank: a records suggestion in the case name doesn't stop it."""
    from minute_filler.batch import Doc, Job
    from minute_filler.models import FieldState
    from helpers import ROE, make_case
    s = pat_settings()
    s.use_ai, s.open_after = True, False
    win = make_window(s)
    win.ai_ok = True
    started = []
    win.ai_runner.start = lambda fn, ing, **k: started.append(ing)
    job = Job(case=make_case(ROE))
    job.case.fields["case_name"] = FieldState("Jane Roe v. Sam Poe", SRC_RECORDS, 0.55)
    doc = Doc(Ingested("t.pdf", "pdf", text="transcript"), Extraction())
    win._maybe_ai(job, [doc])
    assert started == [doc.ing]
    win.ai_pending = 0
    started.clear()
    job.case.fields["case_name"] = FieldState("Jane Roe v. Sam Poe", SRC_REGEX, 0.85)
    win._maybe_ai(job, [doc])
    assert started == []
