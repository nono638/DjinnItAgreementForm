"""Bugs found in the 1.1.1 sweep, each with the case that showed it (fictional data)."""
import json
import threading

import pymupdf
import pytest

from minute_filler import deliver
from minute_filler.batch import Job, fill_jobs, group, read_docs, remerge
from minute_filler.deliver import generate
from minute_filler.invoice import InvoiceOpts
from minute_filler.models import Attorney, to_int
from minute_filler.mofr import build_values
from minute_filler.records import Invoice, Ledger
from minute_filler.settings import Settings

from helpers import ROE, make_case, pat_settings, text_doc, transcript_pdf

APPEARANCE = """SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF QUEENS
Index No. 712345/2021
Jane Roe
-against-
X.Y. Holding Corporation

A P P E A R A N C E S:

{firm}
Attorneys for the {side}
100 Main Street
Anytown, NY 10000
BY: {name}, ESQ.
"""


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


def widgets(path):
    with pymupdf.open(path) as doc:
        return {w.field_name: w.field_value for w in doc[0].widgets()}


def test_mofr_without_a_transcript_keeps_the_estimated_pages(s, tmp_path):
    # the window always passes the invoice options; without a transcript their page count is 0
    case = make_case({**ROE, "est_pages": "30"})
    ledger = Ledger(tmp_path / "r.db")
    mofr = generate(case, s, tmp_path / "out", ["mofr"], InvoiceOpts(0), ledger)[0]
    assert widgets(mofr)["Text Field12"] == "30"
    assert ledger.activity()[0].pages == 30


def test_files_made_before_a_failure_are_reported(s, tmp_path, monkeypatch):
    def locked(*a, **kw):
        raise PermissionError("the file is open")

    monkeypatch.setattr(deliver, "fill_mofr", locked)
    case = make_case(ROE)
    with pytest.raises(PermissionError) as err:
        generate(case, s, tmp_path / "out", ["agreement", "mofr"], ledger=Ledger(tmp_path / "r.db"))
    assert [p.name.split(" - ")[0] for p in err.value.made] == ["Minute Agreement"]

    job = Job(case=case, saved=[tmp_path / "from an earlier run.pdf"])
    done = fill_jobs([job], s, outputs=["agreement", "mofr"])
    assert len(job.saved) == 1 and job.saved == done and "PermissionError" in job.error


def test_invoice_numbers_without_seq_still_differ(tmp_path):
    ledger = Ledger(tmp_path / "r.db")
    numbers = []
    for pattern in ("INV", "INV", "{year}", "{seq.nonsense}"):
        got = []
        t = threading.Thread(target=lambda: got.append(ledger.next_invoice_no(pattern)), daemon=True)
        t.start()
        t.join(5)
        assert got, f"numbering with the pattern {pattern!r} never ends"
        no, year, seq = got[0]
        ledger.add_invoice(Invoice(no, f"{year}-01-01"), year, seq)
        numbers.append(no)
    assert len(set(numbers)) == 4 and numbers[0] == "INV-0001" and numbers[1] == "INV-0002"


def test_attorney_ticks_do_not_leak_between_documents(s):
    one = text_doc(s, APPEARANCE.format(firm="DOE & ROE LLP", side="Plaintiff", name="JOHN DOE"), "a.txt")
    two = text_doc(s, APPEARANCE.format(firm="SMITH LAW GROUP", side="Defendant", name="MARY SMITH"), "b.txt")
    job = Job(docs=[one, two])
    remerge(job, s)
    # a cover page lists who appeared, not who ordered: with two of them, neither is ticked
    assert [(a.name, a.checked) for a in job.case.attorneys] == [("John Doe", False), ("Mary Smith", False)]
    job.case.attorneys[0].email = "typed@example.com"
    assert not one.regex.attorneys[0].email  # what was read from the document stays as read

    mail = text_doc(s, "From: John Doe <jdoe@doeroe.example>\nSubject: minutes\n\nRoe v. X.Y. Holding, "
                       "Index No. 712345/2021, 6/3/2026 please.")
    job = Job(docs=[one, mail])
    remerge(job, s)
    assert job.case.attorneys[0].email == "jdoe@doeroe.example" and job.case.attorneys[0].checked
    job.docs.remove(mail)
    remerge(job, s)
    assert job.case.attorneys[0].email == ""  # the e-mail's address left with the e-mail


def test_criminal_mofr_title_without_the(s):
    s.mofr_division = "criminal"
    for name in ("People of the State of New York v. John Doe", "The People of the State of New York v. John Doe",
                 "People v. John Doe", "The People, etc. v. John Doe"):
        assert build_values(make_case({**ROE, "case_name": name}), s)["title"] == "John Doe"
    assert build_values(make_case({**ROE, "case_name": "People's Bank v. John Doe"}), s)["title"].startswith("People's")


def test_two_transcripts_in_one_job_are_billed_together(s, tmp_path):
    s.batch_combine_dates = True
    docs, errors = read_docs([str(transcript_pdf(tmp_path / "day1.pdf", 30)),
                              str(transcript_pdf(tmp_path / "day2.pdf", 20))], s)
    job, = group(docs, s)
    assert job.case.get("est_pages") == "50" and job.invoice_pages() == 50
    job.case.set("est_pages", "48")  # a number typed by the user still wins
    remerge(job, s)
    assert job.invoice_pages() == 48


def test_pages_with_a_thousands_comma():
    assert to_int("1,200") == 1200 and to_int("") == 0 and to_int("about 30", 7) == 7


def test_settings_with_entries_of_the_wrong_kind_still_load():
    Settings().path.write_text(json.dumps({"outputs": [["x"], "mofr"], "invoice_speeds": [1, "Daily"],
                                           "invoice_turnaround": {"Regular": 3, "Daily": "tomorrow"},
                                           "invoice_index_threshold": 0}), encoding="utf-8")
    s = Settings.load()
    assert s.outputs == ["mofr"] and s.invoice_speeds == ["Daily"]
    assert s.invoice_turnaround == {"Daily": "tomorrow"} and s.invoice_index_threshold == 1


def test_bad_file_name_pattern_falls_back(s, tmp_path):
    from minute_filler.fill import output_name
    case = make_case(ROE)
    for pattern in ("{case.nonsense}", "{case[0]}", "{nonsense}", "{"):
        s.filename_pattern = pattern
        assert output_name(case, Attorney(name="John Doe"), s).endswith(".pdf")
