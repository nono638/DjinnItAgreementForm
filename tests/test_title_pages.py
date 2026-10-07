"""Transcripts whose title runs over two pages, and whose line numbers stand in front of the text; also firm
names told apart from street addresses in the appearances (fictional names)."""
import pymupdf

from minute_filler.batch import group, read_docs
from minute_filler.extract_regex import RegexExtractor, strip_line_numbers, title_pages
from minute_filler.ingest import Ingested

from helpers import PAT, best, pat_settings

PAGE_1 = """310



 1    SUPREME COURT OF THE STATE OF NEW YORK
     COUNTY OF QUEENS:  CIVIL TERM:  PART MDP
 2    --------------------------------------X

 3    JANE ROE AS GUARDIAN AD LITEM FOR RILEY ROE

 4                       Plaintiffs,         INDEX NO.
                                            712345-2021
 5             -against-
     SAM POE M.D., EXAMPLE PRIMARY CARE OF ANYTOWN,
 6    ANYTOWN CARE CENTER, LLC

 7                       Defendants.         Jury Trial

 8    --------------------------------------X

 9    H E L D:

10             123 Courthouse Plaza
              Anytown, New York 10000
11             June 3, 2026

12    B E F O R E :

13             THE HONORABLE MARIA T. ALVAREZ

14    A P P E A R A N C E S :

15             ADVOCATE LAW GROUP
              Attorneys for the Plaintiff
16             10-20 Example Avenue, Suite 22
              Anytown, New York 10001
17             BY: SAMUEL R. ADVOCATE, Esq. and
                  DANA WHITE, Esq.
18
              COUNSEL, WARD & COUNSEL, LLP
19             Attorneys for Sam Poe, M.D.
              500 Sample Road, Suite 31
20             Lake Town, New York 10002
              BY: ALEX B. COUNSEL, Esq.
21

22

23

24

25    {last_line}
                                                               pr
COPY
"""

PAGE_2 = """311



 1
              BRIGHT FOSTER & HAYES
 2             Attorneys for the Anytown Care Center
              200 Sample Square, Suite 12
 3             Hill Town, New York 10003
              BY: MORGAN BRIGHT, Esq., and
 4                 TAYLOR GREEN, Esq.

 5
              QUINN HARTWELL, LLP
 6             Attorneys for Poe, Hill, Marsh, and
              Example Health System
 7             400 Orchard Lane
              Orchard Town, New York 10004
 8             BY: JORDAN QUINN, Esq.
 9

10

11

24                                           PAT REPORTER
25                                           Senior Court Reporter
                                                               pr
"""

PAGE_3 = """Proceedings
312

 1

 2              THE COURT:  Back on the record.  Mr. Counsel?

 3              MR. COUNSEL:  Thank you, Your Honor.  I spoke with
               Casey Stone, Esq., attorneys for the estate, this morning.
 4
 5              MS. BRIGHT:  No objection.
"""

EVERYONE = ["Samuel R. Advocate", "Dana White", "Alex B. Counsel", "Morgan Bright", "Taylor Green", "Jordan Quinn"]
FIRMS_FOUND = ["Advocate Law Group", "Counsel, Ward & Counsel, LLP", "Bright Foster & Hayes", "Quinn Hartwell, LLP"]


def everyone(attorneys) -> list[str]:
    """The attorneys named in the entries, in order (a firm's entry names all of its attorneys)."""
    return [n for a in attorneys for n in a.names()]


def transcript(last_line="(Title continues on next page.)", pages=(PAGE_2, PAGE_3)) -> str:
    """The text of a transcript: PAGE_1 ending with last_line, then pages, split by form feeds."""
    return "\n\f\n".join([PAGE_1.format(last_line=last_line), *pages])


def extract(text):
    """The rules' extraction of a 95-page transcript PDF (starting at page 310) with this text."""
    return RegexExtractor(PAT).extract(Ingested("Roe transcript.pdf", "pdf", text=text, page_count=95,
                                                first_page_no=310))


def test_attorneys_on_the_second_title_page_are_found():
    ex = extract(transcript())
    assert ex.doc_kind == "transcript" and best(ex, "est_pages") == "95"
    assert everyone(ex.attorneys) == EVERYONE  # nobody from the caption or the dialogue
    assert [a.firm for a in ex.attorneys] == FIRMS_FOUND  # one entry per firm, with all its attorneys
    by_name = {n: a for a in ex.attorneys for n in a.names()}
    assert by_name["Samuel R. Advocate"].firm == "Advocate Law Group" and by_name["Dana White"].party == "Plaintiff"
    assert by_name["Samuel R. Advocate"] is by_name["Dana White"]
    assert by_name["Alex B. Counsel"].party == "Sam Poe, M.D."
    assert by_name["Alex B. Counsel"].address == "500 Sample Road, Suite 31\nLake Town, New York 10002"
    assert by_name["Taylor Green"].firm == "Bright Foster & Hayes"
    # a role that runs on to the next line is read whole
    assert by_name["Jordan Quinn"].party == "Poe, Hill, Marsh, and Example Health System"
    assert by_name["Jordan Quinn"].address == "400 Orchard Lane\nOrchard Town, New York 10004"


def test_the_rest_of_the_title_is_read_without_the_line_numbers():
    ex = extract(transcript())
    assert (best(ex, "index_no"), best(ex, "part"), best(ex, "judge"), best(ex, "dates")) == \
        ("712345/2021", "MDP", "Maria T. Alvarez", "6/3/2026")
    name = best(ex, "case_name")  # the column on the right ("INDEX NO.", "Jury Trial") is not part of it
    assert name.startswith("Jane Roe") and name.endswith("Anytown Care Center, LLC") and "INDEX" not in name.upper()


def test_a_second_title_page_is_recognised_without_the_words():
    for last in ("", "(Appearances continued on the following page)", "(Continued on next page)"):
        assert everyone(extract(transcript(last)).attorneys) == EVERYONE, last


def test_a_one_page_title_stops_at_the_testimony():
    ex = extract(transcript("", pages=(PAGE_3,)))
    assert everyone(ex.attorneys) == EVERYONE[:3]
    assert title_pages("cover\fBY: A. B., Esq.\n\fTHE COURT:  Good morning.").count("Esq") == 1
    assert "Good morning" not in title_pages("cover (continued on next page)\f  THE COURT:  Good morning.")


def test_line_numbers_are_only_removed_from_numbered_text():
    text, numbered = strip_line_numbers(PAGE_1)
    assert numbered and "\n    SUPREME COURT" not in text and "\nSUPREME COURT OF THE STATE" in text
    assert "10-20 Example Avenue" in text and "123 Courthouse Plaza" in text  # street numbers stay
    letter = "Dear Pat,\n\nPlease send:\n 1  the minutes of 6/3/2026\n 2  the minutes of 6/4/2026\n\nThanks"
    assert strip_line_numbers(letter) == (letter, False)
    own_lines = "COVER\n 1\n 2\n 3\n 4\n 5\n 6\n"  # numbers on lines of their own: nothing to remove
    assert strip_line_numbers(own_lines) == (own_lines, False)


def test_such_a_transcript_can_be_invoiced(tmp_path):
    doc = pymupdf.open()
    for text in (PAGE_1.format(last_line="(Title continues on next page.)"), PAGE_2, PAGE_3, PAGE_3):
        doc.new_page().insert_text((40, 40), text, fontsize=7, fontname="cour")
    path = tmp_path / "Roe transcript.pdf"
    doc.save(path)
    s = pat_settings()
    docs, errors = read_docs([str(path)], s)
    job, = group(docs, s)
    assert not errors and job.transcript_pages() == 4 and job.invoice_pages() == 4
    assert len(job.case.attorneys) == 4 and not any(a.checked for a in job.case.attorneys)  # one per firm


FIRMS = """SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF QUEENS
Index No. 712345/2021

A P P E A R A N C E S :

HILL & LANE
Attorneys for the Plaintiff
350 Broadway, Suite 4 & 5
Anytown, New York 10001
BY: JOHN LANE

BROADWAY LAW GROUP, PLLC
Attorneys for the Defendant
12 Court Street
Lake Town, New York 10002
BY: MARY PARKWAY, ESQ.

LAW OFFICE OF DANA PLAZA
Attorneys for the Third-Party Defendant
One Example Plaza
Hill Town, New York 10003
BY: DANA PLAZA, ESQ.
"""


def test_a_firm_mark_beats_a_street_word():
    from minute_filler.extract_regex import is_firm_line
    for firm in ("HILL & LANE", "Broadway Law Group, PLLC", "Law Office of Dana Plaza", "Place & Drive LLP",
                 "The Law Offices of Sam Road, P.C."):
        assert is_firm_line(firm), firm
    for address in ("350 Broadway, Suite 4 & 5", "Broadway & 42nd Street", "One Example Plaza", "12 Court Street",
                    "Anytown, New York 10001", "P.O. Box 12, c/o Hill & Lane"):
        assert not is_firm_line(address), address
    found = {a.name: a for a in RegexExtractor(PAT).extract(Ingested("cover.txt", "text", text=FIRMS)).attorneys}
    assert list(found) == ["John Lane", "Mary Parkway", "Dana Plaza"]
    assert found["John Lane"].firm == "Hill & Lane"
    assert found["John Lane"].address == "350 Broadway, Suite 4 & 5\nAnytown, New York 10001"
    assert found["Mary Parkway"].firm == "Broadway Law Group, PLLC" and found["Mary Parkway"].party == "Defendant"
    assert found["Dana Plaza"].firm == "Law Office of Dana Plaza"
    assert found["Dana Plaza"].address == "One Example Plaza\nHill Town, New York 10003"
