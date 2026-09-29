"""Odd input: malformed AI replies, damaged settings and files, unusual characters."""
import json
import time
from datetime import date

import pymupdf
import pytest

from minute_filler.batch import Job, fill_jobs, group, make_doc
from minute_filler.extract_llm import OllamaExtractor
from minute_filler.extract_regex import EMAIL_RE, RegexExtractor, guess_year
from minute_filler.fill import fill_all, form_text
from minute_filler.ingest import ingest_file, ingest_text
from minute_filler.models import Attorney, CaseInfo
from minute_filler.settings import Profile, Settings, settings_dir

SOURCE = ("Smith v. Jones, Index 712345/2024, Part MDP, judge Lopez, 5/22/2026, expedited. "
          "John Doe, Esq. jdoe@example.com (212) 555-1212")


def ai(reply, source=SOURCE):
    return OllamaExtractor(Settings())._to_extraction(reply, source)


def value(ex, key):
    return ex.fields[key][0].value if key in ex.fields else ""


@pytest.mark.parametrize("reply", [
    {"proceeding_dates": "5/22/2026", "proceeding_types": "Trial", "attorneys": "John Doe"},
    {"delivery": ["Expedited"], "copies": 2, "part": 5},
    {"attorneys": ["John Doe", None, 7, {"name": "John Doe", "phone": 2125551212}]},
    {"case_name": {"a": 1}, "judge": ["Lopez"], "index_number": 712345, "attorneys": {"name": "John Doe"}},
    ["not", "an", "object"],
])
def test_ai_reply_of_the_wrong_shape_is_survived(reply):
    ex = ai(reply)
    assert value(ex, "case_name") == ""
    assert all(a.name == "John Doe" for a in ex.attorneys)


def test_ai_values_must_be_in_the_text():
    ex = ai({"part": "MDP", "index_number": "712345/2024",
             "attorneys": [{"name": "John Doe", "email": "jdoe@example.com", "phone": "212-555-1212"}]})
    assert value(ex, "part") == "MDP" and value(ex, "index_no") == "712345/2024"
    assert (ex.attorneys[0].email, ex.attorneys[0].phone) == ("jdoe@example.com", "(212) 555-1212")
    ex = ai({"part": "IAS", "index_number": "999999/2019",
             "attorneys": [{"name": "John Doe", "email": "made@up.com", "phone": "(999) 999-9999"}]})
    assert not ex.fields
    assert (ex.attorneys[0].email, ex.attorneys[0].phone) == ("", "")


def test_ai_that_read_a_picture_is_not_checked_against_text():
    ex = ai({"judge": "Maria Lopez", "index_number": "700001/2025"}, source=None)
    assert value(ex, "judge") == "Maria Lopez" and value(ex, "index_no") == "700001/2025"


def test_date_without_a_year_is_the_most_recent_one():
    today = date(2026, 1, 20)
    assert guess_year(12, 15, today) == 2025   # last month, not eleven months from now
    assert guess_year(1, 5, today) == 2026
    assert guess_year(2, 10, today) == 2026    # coming up soon: a daily copy ordered ahead
    assert guess_year(2, 29, date(2025, 6, 1)) == 2024
    assert guess_year(2, 30, today) is None


def test_date_without_a_year_takes_it_from_the_dates_it_is_listed_with():
    from minute_filler.extract_regex import find_dates
    found = [d for *_, d in find_dates("the trial on 3/3, 3/4 and 3/5/2019, and March 7 through March 8, 2019",
                                       allow_yearless=True)]
    assert found == ["3/3/2019", "3/4/2019", "3/5/2019", "3/7/2019", "3/8/2019"]


def test_long_unbroken_text_is_read_quickly():
    text = "Index No. 712345/2024\n" + "A9" * 40000 + "\njdoe@example.com"
    start = time.time()
    ex = RegexExtractor().extract(ingest_text(text))
    assert time.time() - start < 3
    assert value(ex, "index_no") == "712345/2024"
    assert EMAIL_RE.findall("write to jdoe@example.com.") == ["jdoe@example.com"]


@pytest.mark.parametrize("content", [
    {"days_regular": "21", "per_email": "yes", "profile": None, "rate_sheet": None, "default_copies": 1},
    {"profile": {"name": 5, "email": "pat@example.com"}, "flatten": True},
    [1, 2], "text", None,
])
def test_damaged_settings_fall_back_to_defaults(content, tmp_path):
    (settings_dir() / "settings.json").write_text(json.dumps(content))
    s = Settings.load()
    assert s.days_regular == 21 and s.per_email is True and s.default_copies == "1"
    assert isinstance(s.profile, Profile) and s.profile.name == ""
    if isinstance(content, dict) and content.get("flatten"):
        assert s.flatten and s.profile.email == "pat@example.com"
    case = CaseInfo()
    case.set("delivery", "Regular")
    assert fill_all(case, s, tmp_path)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "cp1252"])
def test_text_files_in_any_usual_encoding(encoding, tmp_path):
    p = tmp_path / "mail.txt"
    p.write_bytes("José Muñoz v. Müller, Index No. 712345/2024".encode(encoding))
    assert ingest_file(p).text == "José Muñoz v. Müller, Index No. 712345/2024"


@pytest.mark.parametrize("name, content", [("a.pdf", b""), ("b.pdf", b"not a pdf"), ("c.docx", b"PK nope"),
                                           ("d.jpg", b"\xff\xd8\xff nope")])
def test_damaged_files_are_reported_plainly(name, content, tmp_path):
    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(ValueError, match="empty|valid"):
        ingest_file(p)


def test_letters_the_form_font_lacks():
    assert form_text("José Ñuñez “Pepe” O’Brien – Müller") == "José Ñuñez “Pepe” O’Brien – Müller"
    assert form_text("Łódź Çelik v. Nguyễn Šimek") == "Lódz Çelik v. Nguyen Šimek"


def test_form_is_filled_with_plain_letters(tmp_path):
    case = CaseInfo()
    case.set("judge", "Věra Nguyễn")
    s = Settings()
    doc = pymupdf.open(fill_all(case, s, tmp_path)[0])
    assert "Vera Nguyen" in [w.field_value for w in doc[0].widgets()]


def doc(s, title, index, day, name):
    text = f"Invoice\nTitle: {title}\nIndex No. {index}\nDate of proceedings: {day}\nJudge: Lopez\n"
    ing = ingest_text(text, name)
    return make_doc(ing, RegexExtractor(s.profile).extract(ing), s)


def test_caption_matching_is_not_too_loose():
    s = Settings()
    request = "Can I get the transcript for {} from 5/22/2026? Thanks"

    def jobs_for(caption_a, caption_b):
        a = doc(s, caption_a, "712222-2024", "5-22-2026", "a")
        ing = ingest_text(request.format(caption_b), "b")
        return group([a, make_doc(ing, RegexExtractor(s.profile).extract(ing), s)], s)

    assert len(jobs_for("Garcia v. Metro Transit Authority", "Garcia v. Metro Transit Auth.")) == 1
    assert len(jobs_for("Smithson v. Jones", "Smith v. Jones")) == 2


def test_days_of_one_case_get_the_date_in_the_file_name(tmp_path):
    s = Settings()
    s.output_dir = str(tmp_path)
    jobs = group([doc(s, "Smith v Jones", "712222-2024", "5-22-2026", "a"),
                  doc(s, "Smith v Jones", "712222-2024", "5-26-2026", "b"),
                  doc(s, "Roe v Doe", "700001-2025", "6-1-2026", "c")], s)
    fill_jobs(jobs[1:], s, batch=jobs)  # the rest of a batch that was filled in two goes
    fill_jobs(jobs[:1], s, batch=jobs)
    assert sorted(p.name for p in tmp_path.glob("*.pdf")) == [
        "Minute Agreement - Roe v. Doe - 700001-2025.pdf",
        "Minute Agreement - Smith v. Jones - 712222-2024 - 5-22-2026.pdf",
        "Minute Agreement - Smith v. Jones - 712222-2024 - 5-26-2026.pdf",
    ]


def test_job_with_attorneys_but_none_ticked_is_flagged():
    job = Job()
    for k, v in {"case_name": "A v. B", "index_no": "1/2024", "dates": "5/22/2026", "judge": "Lopez"}.items():
        job.case.set(k, v)
    assert job.problems() == [] and job.is_empty() is False
    job.case.attorneys = [Attorney(name="Alex Counsel", checked=False), Attorney(name="Unrepresented", checked=False)]
    assert job.problems() == ["no attorney is ticked"]
    job.case.attorneys[0].checked = True
    assert job.problems() == []
    typed = Job()
    typed.case.attorneys = [Attorney(name="Alex Counsel")]
    assert not typed.is_empty() and Job().is_empty()
