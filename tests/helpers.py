"""Fictional test documents and small helpers shared by the test files."""
from pathlib import Path

import pymupdf

from minute_filler.batch import make_doc
from minute_filler.extract_regex import RegexExtractor
from minute_filler.ingest import ingest_text
from minute_filler.models import CaseInfo
from minute_filler.settings import Profile, Settings

SAMPLES = Path(__file__).parent / "samples"
# The fictional reporter of every test
PAT = Profile(name="Pat Reporter", email="preporter@example.com", phone="(555) 010-0000")

# The case of tests/samples/transcript_cover.txt
ROE = {"court": "Supreme", "county": "Queens", "part": "14", "judge": "Maria T. Alvarez",
       "case_name": "Jane Roe v. X.Y. Holding Corporation", "index_no": "712345/2021", "dates": "6/3/2026"}


def pat_settings(**profile) -> Settings:
    """Settings for Pat Reporter; profile fields given here replace the defaults (e.g. address1="Room 100")."""
    s = Settings()
    s.profile = Profile(name="Pat Reporter", **profile)
    return s


def make_case(fields: dict, attorneys=(), procs=()) -> CaseInfo:
    """A CaseInfo with these fields typed in by the user."""
    c = CaseInfo()
    for k, v in fields.items():
        c.set(k, v)
    c.attorneys = list(attorneys)
    c.proc_types = set(procs)
    return c


def text_doc(s: Settings, text: str, name: str = "doc.txt"):
    """A batch document made from text, as if it were pasted."""
    ing = ingest_text(text, name)
    return make_doc(ing, RegexExtractor(s.profile).extract(ing), s)


# A short invoice as an attorney's office might send it (fictional).
INVOICE = """Invoice
To: Example Firm LLP, attn: billing@examplefirm.com
Title: {title}
Index No. {index}
Date of proceedings: {date}
Judge: Lopez
Part: 53
"""


def invoice_text(title="Smith v Jones", index="712222-2024", date="5-22-2026") -> str:
    """The INVOICE text for one case and day of proceedings."""
    return INVOICE.format(title=title, index=index, date=date)


def best(ex, key):
    """The most confident value an extraction found for a field ("" if none)."""
    cands = sorted(ex.fields.get(key, []), key=lambda c: -c.confidence)
    return cands[0].value if cands else ""


def transcript_pdf(path: Path, pages: int = 30, date: str = "", initials: list[str] | None = None) -> Path:
    """A fictional transcript: the cover page from the samples, then numbered pages. date: another day for
    it than the cover's ("June 4, 2026"); initials: the reporter's initials at the foot of each page ("" for
    none; as many as the pages), for a transcript of several reporters."""
    doc = pymupdf.open()
    text = (SAMPLES / "transcript_cover.txt").read_text(encoding="utf-8")
    if date:
        text = text.replace("June 3, 2026", date)
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 60), text if i == 0 else f"{101 + i}\n 1\n 2\n 3\n Q. And then?", fontsize=9)
        if initials and initials[i]:
            page.insert_text((520, page.rect.height - 40), initials[i], fontsize=10)
    doc.save(path)
    return path
