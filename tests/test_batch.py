"""Batch grouping: documents about the same case and date become one job."""
from pathlib import Path

import pytest

from minute_filler.batch import expand_paths, fill_jobs, group, make_doc, read_docs
from minute_filler.extract_regex import RegexExtractor
from minute_filler.ingest import ingest_text
from minute_filler.settings import Profile, Settings

SAMPLES = Path(__file__).parent / "samples"

INVOICE = """Invoice
To: Example Firm LLP, attn: billing@examplefirm.com
Title: {title}
Index No. {index}
Date of proceedings: {date}
Judge: Lopez
Part: 53
"""


@pytest.fixture
def settings():
    s = Settings()
    s.profile = Profile(name="Pat Reporter", email="preporter@example.com", phone="(555) 010-0000")
    return s


def doc(s, text, name="doc.txt"):
    ing = ingest_text(text, name)
    return make_doc(ing, RegexExtractor(s.profile).extract(ing), s)


def invoice(s, title="Smith v Jones", index="712222-2024", date="5-22-2026", name="invoice.txt"):
    return doc(s, INVOICE.format(title=title, index=index, date=date), name)


def names(job):
    return [d.ing.name for d in job.docs]


def test_same_case_and_date_make_one_job(settings):
    docs = [
        invoice(settings, name="a"),
        doc(settings, (SAMPLES / "email_informal.txt").read_text(), "b"),
        # same case and day as "a", index written differently
        doc(settings, "Please send the minutes of Smith v. Jones, Index No. 712222/2024, from 5/22/2026. "
                      "Thanks - Maria Kelly, Esq.", "c"),
        invoice(settings, date="5-26-2026", name="d"),  # same case, another day
    ]
    jobs = group(docs, settings)
    assert [names(j) for j in jobs] == [["a", "c"], ["b"], ["d"]]
    assert jobs[0].case.get("index_no") == "712222/2024"
    assert jobs[0].case.get("dates") == "5/22/2026"
    assert jobs[2].case.get("dates") == "5/26/2026"
    assert all(j.batch for j in jobs)


def test_caption_matches_when_index_is_missing(settings):
    docs = [
        invoice(settings, title="John Smith v. Jones Trucking Corp.", name="a"),
        doc(settings, "Can I get the transcript for Smith v Jones from 5/22/2026? Thanks", "b"),
        doc(settings, "Can I get the transcript for Smith v Brown from 5/22/2026? Thanks", "c"),
    ]
    jobs = group(docs, settings)
    assert [names(j) for j in jobs] == [["a", "b"], ["c"]]


def test_different_cases_on_the_same_day_stay_apart(settings):
    jobs = group([invoice(settings, name="a"),
                  invoice(settings, title="Roe v Doe", index="700001-2025", name="b")], settings)
    assert len(jobs) == 2


def test_document_without_a_date_joins_the_only_job_of_its_case(settings):
    undated = "Following up on the minutes for Smith v. Jones, Index No. 712222/2024."
    jobs = group([doc(settings, undated, "u"), invoice(settings, name="a")], settings)
    assert [names(j) for j in jobs] == [["a", "u"]]
    # with two days to choose from it can't be placed, so it gets a job of its own
    jobs = group([doc(settings, undated, "u"), invoice(settings, name="a"),
                  invoice(settings, date="5-26-2026", name="d")], settings)
    assert [names(j) for j in jobs] == [["a"], ["d"], ["u"]]


def test_combine_dates_option(settings):
    settings.batch_combine_dates = True
    jobs = group([invoice(settings, name="a"), invoice(settings, date="5-26-2026", name="d")], settings)
    assert [names(j) for j in jobs] == [["a", "d"]]
    assert jobs[0].case.get("dates") == "5/22/2026, 5/26/2026"


def test_unidentified_documents_are_kept_apart(settings):
    jobs = group([doc(settings, "see attached", "x"), doc(settings, "call me", "y")], settings)
    assert [names(j) for j in jobs] == [["x"], ["y"]]
    assert not any(j.include for j in jobs)  # no form for them unless the user asks


def test_new_documents_join_existing_jobs_and_keep_edits(settings):
    jobs = group([invoice(settings, name="a")], settings)
    jobs[0].case.set("judge", "Maria T. Lopez")  # typed by the user
    jobs = group([invoice(settings, name="a2"), invoice(settings, index="799999-2026", name="z")], settings, jobs)
    assert [names(j) for j in jobs] == [["a", "a2"], ["z"]]
    assert jobs[0].case.get("judge") == "Maria T. Lopez"


def test_folder_in_forms_out(settings, tmp_path):
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    (src / "1.txt").write_text(INVOICE.format(title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    (src / "sub" / "2.txt").write_text(INVOICE.format(title="Smith v Jones", index="712222/2024", date="5/22/2026"))
    (src / "3.txt").write_text(INVOICE.format(title="Roe v Doe", index="700001-2025", date="6-1-2026"))
    (src / "Minute Agreement - old.pdf").write_bytes(b"")  # an earlier output, not a source
    (src / "notes.xyz").write_text("ignored")
    paths = expand_paths([str(src), str(src / "1.txt")])
    assert sorted(Path(p).name for p in paths) == ["1.txt", "2.txt", "3.txt"]

    (src / "bad.pdf").write_bytes(b"not a pdf")
    docs, errors = read_docs(paths + [str(src / "bad.pdf")], settings)
    assert len(docs) == 3 and len(errors) == 1 and errors[0].startswith("bad.pdf")

    settings.output_dir = str(tmp_path / "out")
    jobs = group(docs, settings)
    seen = []
    saved = fill_jobs(jobs, settings, progress=lambda i, n, name: seen.append((i, n)))
    assert len(jobs) == 2 and len(saved) == 2 and seen == [(0, 2), (1, 2)]
    assert all(p.exists() and p.parent == tmp_path / "out" for p in saved)
    assert not any(j.error for j in jobs)
