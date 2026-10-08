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


# A title page whose "-against-" line carries the caption's right-hand column, and a defendant with a
# podiatrist's letters after the name (fictional)
CAPTION_DPM = """ 1  SUPREME COURT OF THE STATE OF NEW YORK
    COUNTY OF QUEENS :  CIVIL TERM :  PART 14
 2  ------------------------------------------X
    JANE ROE,
 3
                             Plaintiff,
 4
                -against-               Index No. 712345/2021
 5
    SAM POE, DPM,                      JURY TRIAL
 6
                             Defendant.
 7  ------------------------------------------X
 8                           June 3, 2026"""
INDEX_HEADING = "JANE ROE v.\nSAM POE, DPM\nJune 3, 2026"


def page_numbered_transcript(path: Path, pages: int = 20, first: int = 378, initials: list[str] | None = None,
                             index_pages: int = 0, heading: str = INDEX_HEADING, lined: bool = True,
                             numbered: bool = True, index_like: tuple[int, ...] = (), caption: str = CAPTION_DPM
                             ) -> Path:
    """A fictional transcript as a reporter's software prints it: the caption on its first page, each page's
    number at the top (from `first`), 25 numbered lines (lined) and the initials at the foot ("pr" on every page
    unless `initials` says), then `index_pages` pages of word index under `heading`. index_like: the places
    (0-based) of transcript pages printed without line numbers or initials and full of page:line references
    (an exhibit list, say), which look like the index to the first reading of the pages."""
    doc = pymupdf.open()
    refs = "\n".join(f"word{k} ({k % 3 + 1})\n    {first + k % max(1, pages)}:{k % 25 + 1};"
                     f"{first + (k * 7) % max(1, pages)}:{(k * 3) % 25 + 1}" for k in range(18))
    for i in range(pages):
        page = doc.new_page()
        if numbered:
            page.insert_text((300, 40), str(first + i), fontsize=10)
        if i in index_like:
            page.insert_text((72, 80), "EXHIBITS\n" + refs, fontsize=9)
            continue
        if i == 0:
            body = caption
        else:
            body = "\n".join(f"{n:2}  {'Q.  And then?' if n % 2 else 'A.  Yes.'}" for n in range(1, 26))
        if not lined:
            body = "\n".join(line[4:] if line[:2].strip().isdigit() else line for line in body.splitlines())
        page.insert_text((60, 80), body, fontsize=9)
        mark = (initials[i] if initials else "pr")
        if mark:
            page.insert_text((520, page.rect.height - 40), mark, fontsize=10)
    for i in range(index_pages):
        page = doc.new_page()
        page.insert_text((72, 60), (heading + "\n" if heading else "") + refs, fontsize=9)
    doc.save(path)
    return path
