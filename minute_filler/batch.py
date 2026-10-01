"""Batch processing: many documents in, one job (one set of forms) per case and date out.

Two documents belong to the same job when they are about the same case - the same
index number or, when one of them has no index number, a matching caption - and
share a date of proceedings. A transcript and the invoice for it therefore make
one job, while two days of the same trial make two (unless
Settings.batch_combine_dates is on).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .extract_regex import RegexExtractor, dedupe_attorneys, find_dates, norm_index
from .deliver import NO_INVOICE, generate, ledger_for
from .fill import is_generated, short_caption
from .invoice import InvoiceOpts
from .ingest import IMAGE_EXT, Ingested, ingest_file
from .log import error as log_error, log
from .merge import merge, refresh_delivery_date, refresh_rate
from .models import CaseInfo, Extraction, FIELD_LABELS, FieldState, SRC_REGEX, SRC_USER, to_int
from .runsheet import NO_RUNSHEET, RunSheetOpts, matches, name_rows, read_info, rows_from, transcript_pages
from .settings import OUTPUTS, Settings

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
        """False when there is neither index number nor caption: nothing to match the case by."""
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
    """What case and days these fields are about: the index number, the dates and the caption words."""
    nums = re.findall(r"\d+", case.get("index_no"))
    # "712345/21" is "712345/2021" (as on the run sheet, see runsheet.index_numbers)
    index = (norm_index(*nums) if len(nums) == 2 else None) or "/".join(str(int(n)) for n in nums)
    dates = frozenset(d for _, _, d in find_dates(case.get("dates")))
    return Ident(index, dates, _sides(case.get("case_name")))


def _same_word(a: str, b: str) -> bool:
    """The same caption word, or one is short for the other: "auth." is "authority", but "smith" is not
    "smithson" (only a word written with a full stop is a short form)."""
    x, y = a.rstrip("."), b.rstrip(".")
    if x == y:
        return True
    return (a.endswith(".") and len(x) >= 3 and y.startswith(x)) or (b.endswith(".") and len(y) >= 3 and x.startswith(y))


def _same_caption(a: tuple, b: tuple) -> bool:
    """'Roe v Poe' matches 'Jane Roe v. Poe Trucking Corp.': on each side, every
    word of the shorter name appears in the longer one."""
    if not a or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        small, big = sorted((x, y), key=len)
        if not all(any(_same_word(w, v) for v in big) for w in small):
            return False
    return True


def same_case(a: Ident, b: Ident) -> bool:
    """The same index number when both have one, else matching captions (dates are not compared)."""
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
        """The regex extraction, then the AI model's when there is one (for merge)."""
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
    runsheet_to: str | None = None  # the run sheet to add to; "" = a new one; None = as Settings say
    runsheet_group: int | None = None  # jobs of one case asked about together: they share the run sheet made

    def is_empty(self) -> bool:
        """Nothing dropped and nothing typed."""
        typed = any(f.source == SRC_USER and f.value for f in self.case.fields.values())
        return not (self.docs or typed or self.case.attorneys or self.proc_touched)

    def idents(self) -> list[Ident]:
        """What the job's fields and each of its documents say about the case (empty ones left out)."""
        return [i for i in [ident(self.case)] + [d.ident for d in self.docs] if i]

    def title(self) -> str:
        """The job's name in the list: the short caption, else the index number, else the first file's name."""
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
        """The case as file names show it: jobs with the same key (days of one case) get dated file names."""
        return short_caption(self.case.get("case_name")).lower(), self.case.get("index_no")

    def form_count(self) -> int:
        """One form (and invoice) per ticked attorney, at least one."""
        return max(1, sum(1 for a in self.case.attorneys if a.checked))

    def transcripts(self) -> list[Doc]:
        """The transcript PDFs among the job's documents (a photo of a transcript has no pages to count)."""
        return [d for d in self.docs if d.regex.doc_kind == "transcript" and d.ing.kind == "pdf"]

    def transcript_pages(self) -> int:
        """Pages of the transcript PDFs among the inputs, without the word index printed after them; 0 when
        there is none (then no invoice or run sheet can be made)."""
        return sum(transcript_pages(d.ing) for d in self.transcripts())

    def runsheet_opts(self, s: Settings) -> RunSheetOpts:
        """The takes of every transcript of the job, for the run sheet."""
        rows = []
        for d in self.transcripts():
            rows += rows_from(d.ing, self._day_of(d), s)
        rows.sort(key=lambda r: (r.day or date.max, r.start if r.start is not None else -1))
        name_rows(rows)  # a reporter named on one day's title page is named on the other days too
        return RunSheetOpts(rows, self.runsheet_to)

    def _day_of(self, doc: Doc) -> date | None:
        """The day of a transcript: its own date, else the job's first."""
        days = sorted(doc.ident.dates, key=_date_key) or [d for _, _, d in find_dates(self.case.get("dates"))]
        if not days:
            return None
        m, d, y = (int(x) for x in days[0].split("/"))
        return date(y, m, d)

    def invoice_pages(self) -> int:
        """The pages to bill: the Pages field (editable), else the transcript's own count; 0 = no transcript."""
        pages = self.transcript_pages()
        return to_int(self.case.get("est_pages"), pages) if pages else 0

    def invoice_opts(self) -> InvoiceOpts:
        """The invoice's pages, ordering parties (the ticked attorneys unless set) and speed choice."""
        return InvoiceOpts(self.invoice_pages(), self.parties or self.form_count(), self.invoice_choice)

    def output_problems(self, outputs) -> list[str]:
        """What stops the chosen outputs: missing fields for the agreement or MOFR, or no transcript for an
        invoice or run sheet."""
        out = self.problems() if {"agreement", "mofr"} & set(outputs) else []
        if "invoice" in outputs and not self.invoice_pages():
            out.append(NO_INVOICE)
        if "runsheet" in outputs and not self.transcript_pages():
            out.append(NO_RUNSHEET)
        return out

    def makeable(self, outputs) -> list[str]:
        """The outputs this job can have: all of them, less the invoice and the run sheet when there is no
        transcript."""
        return [o for o in outputs if o not in ("invoice", "runsheet") or self.transcript_pages()]

    def file_count(self, outputs) -> int:
        """How many files generate() will make for this job."""
        per = {"agreement": self.form_count(), "mofr": 1, "invoice": self.form_count(), "runsheet": 1}
        return sum(per[o] for o in self.makeable(outputs))


def make_doc(ing: Ingested, regex: Extraction, s: Settings, path: str = "") -> Doc:
    """A Doc, with what it says about its case worked out from its regex fields."""
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
    _all_pages(new, job)
    refresh_rate(new, s)  # a speed chosen by the user was restored after the defaults were applied
    if s.fill_delivery_date:
        refresh_delivery_date(new, s)
    job.case = new


def _date_key(d: str) -> tuple:
    """'6/2/2026' -> (2026, 6, 2), to sort M/D/YYYY dates."""
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


def _all_pages(case: CaseInfo, job: Job) -> None:
    """Several transcripts in one job (the days of a trial, volumes): their pages are added up."""
    fs = case.fields["est_pages"]
    counts = [transcript_pages(d.ing) for d in job.transcripts()]
    if len(counts) > 1 and fs.source != SRC_USER:
        total = str(sum(counts))
        case.fields["est_pages"] = FieldState(total, SRC_REGEX, 0.9,
                                              [total] + [a for a in fs.alternatives if a != total])


def group(docs: list[Doc], s: Settings, jobs: list[Job] | None = None) -> list[Job]:
    """Sorts documents into jobs: into one of `jobs` when they match it, else into new ones. A document that
    matches several jobs joins them into one. Returns every job, old and new."""
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
    """Where a job's files go: Settings.output_dir, else the folder of its first document, else
    Documents/Minute Agreements."""
    if s.output_dir:
        return Path(s.output_dir)
    for d in job.docs:
        if d.path:
            return Path(d.path).parent
    return Path.home() / "Documents" / "Minute Agreements"


def input_folders(job: Job) -> list[Path]:
    """The folders of the job's documents (a run sheet may be kept next to the transcripts)."""
    return list(dict.fromkeys(Path(d.path).parent for d in job.docs if d.path))


def fill_jobs(jobs: list[Job], s: Settings, progress: Progress | None = None,
              batch: list[Job] | None = None, outputs: list[str] | None = None) -> list[Path]:
    """Makes the outputs (default: Settings.outputs) of every job; problems are recorded in job.error
    instead of stopping the batch. A job without a transcript gets no invoice or run sheet (noted in job.error).
    `batch` is the whole batch when only some of its jobs are filled: jobs for several days of one
    case get the date in their file names."""
    outputs = list(s.outputs if outputs is None else outputs)
    ledger = ledger_for(s)
    names = [j.name_key() for j in batch or jobs]
    done: list[Path] = []
    started: list[Path] = []  # run sheets started by this batch: the other days of the trial go on them too
    made_by_group: dict[int, Path] = {}  # the run sheet of each case the window asked about (Job.runsheet_group)
    for i, job in enumerate(jobs):
        if progress:
            progress(i, len(jobs), job.title())
        sheet = None
        try:
            want = job.makeable(outputs)
            sheet = job.runsheet_opts(s) if "runsheet" in want else None
            if sheet and job.runsheet_group in made_by_group:
                sheet.target = str(made_by_group[job.runsheet_group])
            elif sheet and sheet.target is None and s.runsheet_existing != "new":
                sheet.target = _started_for(job, started, s)
            job.saved = generate(job.case, s, out_dir_for(job, s), want, job.invoice_opts(), ledger,
                                 dated=names.count(job.name_key()) > 1, runsheet=sheet,
                                 folders=input_folders(job))
            left_out = [OUTPUTS[o].lower() for o in outputs if o not in want]
            job.error = f"no {' or '.join(left_out)}: no transcript PDF among the inputs" if left_out else ""
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
        except Exception as e:
            job.saved = list(getattr(e, "made", []))  # what was made before the problem
            job.error = f"{type(e).__name__}: {e}"
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
            log_error("could not save the forms of a job", e)
        if sheet and sheet.path:
            if job.runsheet_group is not None:
                made_by_group.setdefault(job.runsheet_group, sheet.path)
            if sheet.created and sheet.path not in started:
                started.append(sheet.path)
    log.info("saved %d form(s) for %d job(s)", len(done), len(jobs))
    return done


def _started_for(job: Job, started: list[Path], s: Settings) -> str | None:
    """A run sheet this batch started that is this job's case too (another day of the trial): the same index
    number, or the same case name when Settings say to add to such run sheets. None: as Settings say."""
    for p in started:
        info = read_info(p)
        why = matches(info, job.case.get("case_name"), job.case.get("index_no")) if info else ""
        if why == "index" or (why == "name" and s.runsheet_existing == "add"):
            return str(p)
    return None


def files_to_make(jobs: list[Job], outputs) -> int:
    """How many files generate() will make for these jobs: the days of one case share a run sheet."""
    count = sum(j.file_count([o for o in outputs if o != "runsheet"]) for j in jobs)
    cases: list[Ident] = []
    for j in jobs:
        if "runsheet" in j.makeable(outputs):
            i = ident(j.case)
            if not i or not any(same_case(i, k) for k in cases):
                cases.append(i)
    return count + len(cases)
