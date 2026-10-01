"""Batch processing: many documents in, one job (one set of forms) per case and date out.

Two documents belong to the same job when they are about the same case - the same
index number or, when one of them has no index number, a matching caption - and
share a date of proceedings. A transcript and the invoice for it therefore make
one form, while two days of the same trial make two (unless
Settings.batch_combine_dates is on).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .extract_regex import RegexExtractor, dedupe_attorneys, find_dates
from .deliver import NO_TRANSCRIPT, generate, ledger_for
from .fill import is_generated, short_caption
from .invoice import InvoiceOpts
from .ingest import IMAGE_EXT, Ingested, ingest_file
from .log import error as log_error, log
from .merge import merge, refresh_delivery_date, refresh_rate
from .models import CaseInfo, Extraction, FIELD_LABELS, FieldState, SRC_REGEX, SRC_USER, to_int
from .settings import Settings

BATCH_EXT = {".pdf", ".eml", ".docx", ".txt", ".htm", ".html"} | IMAGE_EXT  # taken from a dropped folder
Progress = Callable[[int, int, str], None]

# Words that don't help tell two captions apart.
_NOISE = {"the", "of", "and", "in", "re", "matter", "et", "al", "ano", "inc", "llc", "llp", "corp",
          "corporation", "co", "company", "ltd", "pc", "as", "by"}


# ----------------------------------------------------------------- identity

@dataclass(frozen=True)
class Ident:
    """What a document says about which case and day it belongs to."""
    index: str = ""
    dates: frozenset = frozenset()
    sides: tuple = ()  # caption words, one set per side of the "v."

    def __bool__(self) -> bool:
        return bool(self.index or self.sides)


def _sides(name: str) -> tuple:
    """Caption words per side; a word written with a full stop ("Auth.") keeps it, as it may be short for another."""
    t = re.sub(r"[^a-z0-9. ]", " ", name.lower().replace("-against-", " v "))
    parts = re.split(r"\s+(?:v|vs|versus|against)\.?\s+", " ".join(t.split()), maxsplit=1)
    sides = []
    for p in parts:
        words = set()
        for w in p.split():
            core = w.replace(".", "")
            if len(core) > 1 and core not in _NOISE:
                words.add(core + ("." if w.endswith(".") else ""))
        sides.append(frozenset(words))
    return tuple(sides) if all(sides) else ()


def ident(case: CaseInfo) -> Ident:
    index = "/".join(str(int(n)) for n in re.findall(r"\d+", case.get("index_no")))
    dates = frozenset(d for _, _, d in find_dates(case.get("dates")))
    return Ident(index, dates, _sides(case.get("case_name")))


def _same_word(a: str, b: str) -> bool:
    x, y = a.rstrip("."), b.rstrip(".")
    if x == y:
        return True
    # "Auth." is Authority, but Smith is not Smithson
    return (a.endswith(".") and len(x) >= 3 and y.startswith(x)) or (b.endswith(".") and len(y) >= 3 and x.startswith(y))


def _same_caption(a: tuple, b: tuple) -> bool:
    """'Smith v Jones' matches 'John Smith v. Jones Trucking Corp.': on each side, every
    word of the shorter name appears in the longer one."""
    if not a or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        small, big = sorted((x, y), key=len)
        if not all(any(_same_word(w, v) for v in big) for w in small):
            return False
    return True


def same_case(a: Ident, b: Ident) -> bool:
    if a.index and b.index:
        return a.index == b.index
    return _same_caption(a.sides, b.sides)


# --------------------------------------------------------------------- jobs

@dataclass
class Doc:
    """One input with what was extracted from it."""
    ing: Ingested
    regex: Extraction
    ai: Extraction | None = None
    path: str = ""
    ident: Ident = field(default_factory=Ident)

    def extractions(self) -> list[Extraction]:
        return [self.regex] + ([self.ai] if self.ai is not None else [])


@dataclass
class Job:
    """The documents for one form (per attorney) and the editable result."""
    docs: list[Doc] = field(default_factory=list)
    case: CaseInfo = field(default_factory=CaseInfo)
    proc_touched: bool = False   # the user changed the proceeding types / attorneys,
    att_touched: bool = False    # so later extractions must not overwrite them
    batch: bool = False          # put together by group()
    include: bool = True         # ticked for "Fill all"
    saved: list[Path] = field(default_factory=list)
    error: str = ""
    parties: int = 0             # ordering parties on the invoice; 0 = the number of ticked attorneys
    invoice_choice: bool | None = None  # list every speed on the invoice; None = the setting

    def is_empty(self) -> bool:
        """Nothing dropped and nothing typed."""
        typed = any(f.source == SRC_USER and f.value for f in self.case.fields.values())
        return not (self.docs or typed or self.case.attorneys or self.proc_touched)

    def idents(self) -> list[Ident]:
        return [i for i in [ident(self.case)] + [d.ident for d in self.docs] if i]

    def title(self) -> str:
        return (short_caption(self.case.get("case_name"), 48) or self.case.get("index_no")
                or (self.docs[0].ing.name if self.docs else "New job"))

    def missing(self) -> list[str]:
        return self.case.missing_required()

    def no_attorney_chosen(self) -> bool:
        """Attorneys were found but none is ticked, so the form's attorney block would stay blank.
        (A transcript lists everyone who appeared; it can't say who ordered.)"""
        real = [a for a in self.case.attorneys if not a.is_placeholder()]
        return bool(real) and not any(a.checked for a in self.case.attorneys)

    def problems(self) -> list[str]:
        out = [FIELD_LABELS[k] + " is missing" for k in self.missing()]
        if self.no_attorney_chosen():
            out.append("no attorney is ticked")
        return out

    def name_key(self) -> tuple:
        return short_caption(self.case.get("case_name")).lower(), self.case.get("index_no")

    def form_count(self) -> int:
        return max(1, sum(1 for a in self.case.attorneys if a.checked))

    def transcript_pages(self) -> int:
        """Pages of the transcript PDFs among the inputs; 0 when there is none (then no invoice can be made)."""
        return sum(d.ing.page_count for d in self.docs if d.regex.doc_kind == "transcript" and d.ing.kind == "pdf")

    def invoice_pages(self) -> int:
        """The pages to bill: the Pages field (editable), else the transcript's own count; 0 = no transcript."""
        pages = self.transcript_pages()
        return to_int(self.case.get("est_pages"), pages) if pages else 0

    def invoice_opts(self) -> InvoiceOpts:
        return InvoiceOpts(self.invoice_pages(), self.parties or self.form_count(), self.invoice_choice)

    def output_problems(self, outputs) -> list[str]:
        """What stops the chosen outputs: the agreement's missing fields, or no transcript for an invoice."""
        out = self.problems() if {"agreement", "mofr"} & set(outputs) else []
        if "invoice" in outputs and not self.invoice_pages():
            out.append(NO_TRANSCRIPT)
        return out

    def makeable(self, outputs) -> list[str]:
        """The outputs this job can have: all of them, less the invoice when there is no transcript."""
        return [o for o in outputs if o != "invoice" or self.invoice_pages()]

    def file_count(self, outputs) -> int:
        """How many files generate() will make for this job."""
        per = {"agreement": self.form_count(), "mofr": 1, "invoice": self.form_count()}
        return sum(per[o] for o in self.makeable(outputs))


def make_doc(ing: Ingested, regex: Extraction, s: Settings, path: str = "") -> Doc:
    return Doc(ing, regex, path=path, ident=ident(merge([regex], s)))


def remerge(job: Job, s: Settings) -> None:
    """Rebuilds job.case from its documents, keeping what the user entered."""
    prev = job.case
    new = merge([e for d in job.docs for e in d.extractions()], s, previous=prev)
    if job.proc_touched:
        new.proc_types = prev.proc_types
    if job.att_touched:
        known = prev.attorneys
        new.attorneys = dedupe_attorneys(known + list(new.attorneys), s.profile)
        for a in new.attorneys[len(known):]:
            a.checked = False
    if job.batch:
        _all_dates(new, job)
    refresh_rate(new, s)  # a speed chosen by the user was restored after the defaults were applied
    if s.fill_delivery_date:
        refresh_delivery_date(new, s)
    job.case = new


def _date_key(d: str) -> tuple:
    m, day, y = (int(x) for x in d.split("/"))
    return y, m, day


def _all_dates(case: CaseInfo, job: Job) -> None:
    """When the grouped documents name different days, the form lists all of them (to review)."""
    fs = case.fields["dates"]
    if fs.source == SRC_USER:
        return
    have = {d for _, _, d in find_dates(fs.value)}
    every = have.union(*(d.ident.dates for d in job.docs))
    if every - have:
        value = ", ".join(sorted(every, key=_date_key))
        case.fields["dates"] = FieldState(value, fs.source or SRC_REGEX, 0.55,
                                          [value] + [a for a in fs.alternatives if a != value])


def group(docs: list[Doc], s: Settings, jobs: list[Job] | None = None) -> list[Job]:
    """Sorts documents into jobs: into one of `jobs` when they match it, else into new ones."""
    jobs = list(jobs or [])
    changed: list[Job] = []
    any_date = s.batch_combine_dates

    def put(doc: Doc, hits: list[Job]) -> None:
        if hits:
            job = hits[0]
            for other in hits[1:]:  # this document links jobs that were separate
                job.docs += other.docs
                job.proc_touched |= other.proc_touched
                job.att_touched |= other.att_touched
                jobs.remove(other)
        else:
            job = Job()
            jobs.append(job)
        job.docs.append(doc)
        if job not in changed:
            changed.append(job)

    # Documents that name a day go first; the others then join the case if there is only one candidate.
    for doc in [d for d in docs if d.ident and d.ident.dates]:
        same = [j for j in jobs if any(same_case(doc.ident, i) for i in j.idents())]
        hits = [j for j in same if any(same_case(doc.ident, i) and i.dates and (any_date or doc.ident.dates & i.dates)
                                       for i in j.idents())]
        undated = [j for j in same if not any(i.dates for i in j.idents())]
        put(doc, hits or (undated if len(undated) == 1 else []))
    for doc in [d for d in docs if d.ident and not d.ident.dates]:
        same = [j for j in jobs if any(same_case(doc.ident, i) for i in j.idents())]
        put(doc, same if len(same) == 1 else [])
    for doc in [d for d in docs if not d.ident]:  # no index number or caption: can't be matched
        put(doc, [])

    for job in changed:
        job.batch = True
        remerge(job, s)
        if not job.idents():
            job.include = False  # nothing says which case this is: no form unless the user ticks it
    return jobs


# ------------------------------------------------------------ files in, PDFs out

def expand_paths(paths: list[str]) -> list[str]:
    """Files as given, folders replaced by the documents in them; no duplicates."""
    out, seen = [], set()

    def add(p: Path) -> None:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(str(p))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                # forms this app filled earlier are not source documents
                if (f.is_file() and f.suffix.lower() in BATCH_EXT
                        and not f.name.startswith(("Minute Agreement", "MOFR", "~$")) and not is_generated(f)):
                    add(f)
        elif p.is_file():
            add(p)
    return out


def read_docs(paths: list[str], s: Settings, progress: Progress | None = None) -> tuple[list[Doc], list[str]]:
    """Reads and extracts every file. Returns the documents and a message per unreadable file."""
    return read_loaders([lambda p=p: ingest_file(p) for p in paths], [Path(p).name for p in paths], s, paths,
                        progress)


def read_loaders(loaders: list[Callable[[], Ingested]], names: list[str], s: Settings,
                 paths: list[str] | None = None, progress: Progress | None = None) -> tuple[list[Doc], list[str]]:
    """read_docs for any inputs (files, pasted text, images): each loader returns the Ingested document.
    One input that can't be read doesn't stop the others; it gets a message instead."""
    extractor = RegexExtractor(s.profile, s.title_case_names)
    paths = paths or [""] * len(loaders)
    docs, errors = [], []
    for i, (load, name, path) in enumerate(zip(loaders, names, paths)):
        if progress:
            progress(i, len(loaders), name)
        try:
            ing = load()
            docs.append(make_doc(ing, extractor.extract(ing), s, path))
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.info("skipped a document that could not be read")
    return docs, errors


def out_dir_for(job: Job, s: Settings) -> Path:
    if s.output_dir:
        return Path(s.output_dir)
    for d in job.docs:
        if d.path:
            return Path(d.path).parent
    return Path.home() / "Documents" / "Minute Agreements"


def fill_jobs(jobs: list[Job], s: Settings, progress: Progress | None = None,
              batch: list[Job] | None = None, outputs: list[str] | None = None) -> list[Path]:
    """Makes the outputs (default: Settings.outputs) of every job; problems are recorded in job.error
    instead of stopping the batch. A job without a transcript gets no invoice (noted in job.error).
    `batch` is the whole batch when only some of its jobs are filled: jobs for several days of one
    case get the date in their file names."""
    outputs = list(s.outputs if outputs is None else outputs)
    ledger = ledger_for(s)
    names = [j.name_key() for j in batch or jobs]
    done: list[Path] = []
    for i, job in enumerate(jobs):
        if progress:
            progress(i, len(jobs), job.title())
        try:
            want = job.makeable(outputs)
            job.saved = generate(job.case, s, out_dir_for(job, s), want, job.invoice_opts(), ledger,
                                 dated=names.count(job.name_key()) > 1)
            job.error = "" if want == outputs else "no invoice: no transcript PDF among the inputs"
            done += job.saved
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
            log_error("could not save the forms of a job", e)
    log.info("saved %d form(s) for %d job(s)", len(done), len(jobs))
    return done
