"""Batch processing: many documents in, one job (one set of forms) per case and date out.

Two documents belong to the same job when they are about the same case - the same
index number or, when one of them has no index number, a matching caption - and
share a date of proceedings. A transcript and the invoice for it therefore make
one job, while two days of the same trial make two (unless
Settings.batch_combine_dates is on). The days of one case still share one invoice
(see invoice_groups and joint_invoice), unless Settings.invoice_joint is off; each
attorney's invoice bills only the days it is ticked on, and of a day split by
Excerpts... (Job.portions) only the pages it ordered.

A transcript written by several reporters (their initials alternate at the foot
of its pages, see takes.page_owners) is billed for the user's own pages only,
unless Whose pages... says otherwise (Job.page_basis, Job.front_owner): it can
also tick other reporters, whose pages are billed on invoices in their name
(Job.bill_reporters, Job.billed_by, joint_invoice_sets). A transcript whose
pages are someone else's, or that begins with pages nobody's initials are on,
gets no invoice until the user says whose pages to bill (Job.ownership_problem).

A minute agreement, though, counts every page its attorney ordered, whoever wrote it (Job.ordered_pages: the
whole day, or the attorney's runs of an excerpt): firm A ordering a day of 150 pages and firm B 80 of them get
agreements for 150 and 80 pages, while each reporter's invoices bill only that reporter's pages of them (the
pages B didn't order to A alone, those both ordered split between them). The MOFR counts the pages anyone
ordered. The Est. number of pages field shows every page of the transcripts (a number typed in there is the
user's: their invoice bills it, and the agreements count the day as that many pages).

fill_jobs makes the outputs of every job (Generate all). A day once invoiced
(Job.invoiced) is not billed again until a new document is added to it; when a run stops part way, the
attorneys already invoiced (Job.invoiced_keys) are not billed again by the next one. A day whose Excerpts...
rows no longer fit it (Job.portions_problem) gets no invoice until they are checked. The days of a case on one
invoice also get one minute agreement per attorney and one MOFR for all of them (form_groups, case_forms),
unless Settings.forms_per_case is off.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable

from .extract_regex import (RegexExtractor, dedupe_attorneys, find_dates, maybe_same_entry, merge_entry, norm_index,
                            same_entry, same_firm)
from .deliver import NO_INVOICE, CaseForm, generate, ledger_for, make_forms
from .fill import agreement_orderers, is_generated, short_caption
from .invoice import (ORDERED_BY_NOBODY, DayOrder, InvoiceOpts, Portion, day_reporters, invoice_count,
                      reporters_text)
from .ingest import IMAGE_EXT, Ingested, ingest_file
from .log import error as log_error, log
from .merge import merge, refresh_copies, refresh_delivery_date, refresh_rate, same_value
from .models import (Attorney, Candidate, CaseInfo, Extraction, FIELD_LABELS, FieldState, SRC_DERIVED, SRC_PDF,
                     SRC_RECORDS, SRC_REGEX, SRC_USER, to_int)
from .rates import speed_key
from .records import recorded_cases
from .runsheet import NO_RUNSHEET, RunSheetOpts, matches, name_rows, read_info, rows_from, transcript_pages
from .takes import my_initials, page_owners
from .settings import OUTPUTS, Settings

BATCH_EXT = {".pdf", ".eml", ".docx", ".txt", ".htm", ".html"} | IMAGE_EXT  # taken from a dropped folder
Progress = Callable[[int, int, str], None]
# How fill_jobs begins a day's error when its invoice was held back (a choice to make): the window tells these
# apart from files that could not be saved
NOT_INVOICED = "invoice not made:"
# Job.page_basis of a transcript none of whose pages one reporter's invoices bill (see Job.billed_by)
NOBODY = "-"

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


def index_key(value: str) -> str:
    """An index number as jobs compare it: "712345/21" is "712345/2021" (as on the run sheet, see
    runsheet.index_numbers); "" when it has no number."""
    nums = re.findall(r"\d+", value or "")
    return (norm_index(*nums) if len(nums) == 2 else None) or "/".join(str(int(n)) for n in nums)


def ident(case: CaseInfo) -> Ident:
    """What case and days these fields are about: the index number, the dates and the caption words."""
    dates = frozenset(d for _, _, d in find_dates(case.get("dates")))
    return Ident(index_key(case.get("index_no")), dates, _sides(case.get("case_name")))


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
    records: Extraction | None = None  # the case names the user's records give its index number (a suggestion)

    def extractions(self) -> list[Extraction]:
        """The regex extraction, the records' suggestion and the AI model's, those there are (for merge)."""
        return [x for x in (self.regex, self.records, self.ai) if x is not None]

    def key(self) -> str:
        """The document as Job.page_basis and Job.front_owner name it: its path (its name when pasted)."""
        return self.path or self.ing.name

    def owners(self) -> list[str]:
        """Whose each page of this transcript is (the word index after them left out, see
        runsheet.transcript_pages): the reporter's initials ("" for pages before the first initials; see
        takes.page_owners); a page past the last initials found counts for the last reporter. [] when no page
        has initials (a scan, or a reporter who doesn't initial the pages)."""
        n = transcript_pages(self.ing)
        owners = page_owners(self.ing.marks)[:n]
        if not any(owners):
            return []
        return owners + [owners[-1]] * (n - len(owners))

    def reporters(self) -> list[str]:
        """The initials on this transcript's pages, in the order they first appear (["pr", "ds"])."""
        return list(dict.fromkeys(o for o in self.owners() if o))

    def title_names(self) -> list[str]:
        """The reporters its title page names ("DANA SMITH"; see takes.title_reporters); [] for a document
        without text."""
        from .extract_regex import strip_line_numbers, title_page_count
        from .takes import title_reporters
        if not self.ing.text:
            return []
        title_text, _ = strip_line_numbers(self.ing.text)
        return title_reporters("\n".join(title_text.split("\f")[:title_page_count(title_text)]))

    def front_pages(self) -> int:
        """How many pages this transcript begins with before the first initials (whose they are isn't known
        when several reporters wrote it)."""
        owners = self.owners()
        return next((i for i, o in enumerate(owners) if o), 0)


@dataclass(eq=False)
class Job:
    """One case and day (or days, see Settings.batch_combine_dates): its documents, the editable result (case) and
    the user's choices for its outputs - an agreement per ticked attorney, the MOFR, the invoices and the run
    sheet. eq=False: a job is only equal to itself. The window keeps its jobs in a list and finds them with
    index, remove and "in"; two emptied jobs (New job twice) compare equal field by field, and the wrong one
    would be found."""
    docs: list[Doc] = field(default_factory=list)
    case: CaseInfo = field(default_factory=CaseInfo)
    proc_touched: bool = False   # the user changed the proceeding types / attorneys,
    att_touched: bool = False    # so later extractions must not overwrite them
    batch: bool = False          # put together by group()
    include: bool = True         # ticked for "Generate all"
    saved: list[Path] = field(default_factory=list)  # the files the last Generate made
    error: str = ""  # what the last Generate couldn't do ("" = all done); see issues
    invoiced: bool = False       # an invoice covering this day was made: Generate all must not bill it again
    # the Attorney.key()s invoiced for this day by a run that stopped before every attorney's invoice was made:
    # the next run leaves them out (see unbill)
    invoiced_keys: list = field(default_factory=list)
    parties: int = 0             # ordering parties on the invoice; 0 = the attorneys ticked (see ticked_keys)
    # The invoice's own choices (Peripherals and Customize); None = as Settings say.
    invoice_email: bool | None = None  # an e-mailed copy for each party
    invoice_index: str | None = None   # the index: "auto", "on" or "off"
    invoice_show: list | None = None   # what granular detail shows (keys of settings.DETAIL_ITEMS)
    invoice_detail: bool = False       # "Show granular detail" for this job's invoice (off for every new job)
    # Who ordered which pages of the day, set in the Excerpts window (excerpts.store): rows of (last page,
    # Attorney.key()s of attorneys ticked on the day, or [ORDERED_BY_NOBODY] for pages nobody ordered), each
    # row from the page after the one above; the last ends at the day's pages. None = every ticked attorney
    # ordered every page. Only for a job of one day. Kept when they no longer fit (the pages changed, an
    # attorney was unticked): see portions_problem.
    portions: list | None = None
    runsheet_to: str | None = None  # the run sheet to add to; "" = a new one; None = as Settings say
    # one reporter wrote this case's transcripts: no run sheet for it, though the box is ticked for other cases
    # (set by the window while it ticks the box itself, see MainWindow._auto_runsheet)
    runsheet_unneeded: bool = False
    # Whose pages of a transcript of several reporters are billed (Whose pages...), by Doc.key(): "me" (the
    # default: the pages with the user's initials), another reporter's initials (billed on invoices in their
    # name), a list of these (each billed on its own invoices, the user's first), or "*" (the whole transcript,
    # on the user's invoices). See basis_of; NOBODY (set by billed_by only): none of its pages.
    page_basis: dict = field(default_factory=dict)
    # Who the pages before the first initials count for, by Doc.key(): their initials, or "none". Missing on a
    # transcript of several reporters that begins with such pages: Whose pages... must say (ownership_problem).
    front_owner: dict = field(default_factory=dict)
    own: tuple = ()  # the user's initials (takes.my_initials), set from the settings by remerge
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
        """The keys of the required fields (models.REQUIRED_KEYS) that are still blank."""
        return self.case.missing_required()

    def no_attorney_chosen(self) -> bool:
        """Attorneys were found but none is ticked, so the form's attorney block would stay blank.
        (A transcript lists everyone who appeared; it can't say who ordered.)"""
        real = [a for a in self.case.attorneys if not a.is_placeholder()]
        return bool(real) and not any(a.checked for a in self.case.attorneys)

    def problems(self) -> list[str]:
        """What to check before the forms are made: "Index Number is missing", "no attorney is ticked"."""
        out = [FIELD_LABELS[k] + " is missing" for k in self.missing()]
        if self.no_attorney_chosen():
            out.append("no attorney is ticked")
        return out

    def asked_speed(self) -> tuple[str, str, Doc | None]:
        """A speed one of the job's e-mails (or texts) asks for, as (speed, the words found, the document):
        ("Daily", "daily copy", <the e-mail>); ("", "", None) when none does. Transcripts and invoices order
        nothing: "regular" in testimony, or an old invoice's list of speeds, ask for no speed. The likeliest
        when several are named."""
        best: tuple[Candidate, Doc] | None = None
        for d in self.docs:
            if d.regex.doc_kind not in ("email", "text"):
                continue
            for ex in d.extractions():
                for c in ex.fields.get("delivery", []):
                    if c.value and (best is None or c.confidence > best[0].confidence):
                        best = (c, d)
        if best is None:
            return "", "", None
        c, d = best
        return c.value, c.note or c.value.lower(), d

    def speed_question(self) -> str:
        """What to ask the user about this job's speed ("" when nothing): a document asks for another speed
        than the agreement form names (the Settings rule's), and the user hasn't chosen one for the job:
        'the e-mail mentions "daily copy"'. Choosing any speed (merge.refresh_speed keeps it) answers it."""
        f = self.case.fields["delivery"]
        if f.source == SRC_USER:
            return ""
        speed, words, doc = self.asked_speed()
        if not speed or speed_key(speed) == speed_key(f.value):
            return ""
        if doc is not None and doc.regex.doc_kind == "email":
            what = "the e-mail"
        elif doc is not None and not doc.ing.name.startswith("Pasted text"):
            what = doc.ing.name  # (a letter or note saved as a file: "Order.docx")
        else:
            what = "the pasted text"
        return f'{what} mentions "{words}"'

    def name_key(self) -> tuple:
        """The case as file names show it: jobs with the same key (days of one case) get dated file names."""
        return short_caption(self.case.get("case_name")).lower(), self.case.get("index_no")

    def form_count(self) -> int:
        """How many minute agreements generate() makes for this day: one per ticked entry (each Attorney.key()
        once) that ordered pages of it, or one with a blank attorney block when nobody is ticked
        (fill.agreement_orderers; invoices: see CaseInfo.invoice_orderers)."""
        return len(agreement_orderers(self.case, self.ordered_pages()))

    def transcripts(self) -> list[Doc]:
        """The transcript PDFs among the job's documents (a photo of a transcript has no pages to count)."""
        return [d for d in self.docs if d.regex.doc_kind == "transcript" and d.ing.kind == "pdf"]

    def transcript_pages(self) -> int:
        """Pages of the transcript PDFs among the inputs, without the word index printed after them; 0 when
        there is none (then no run sheet can be made, nor an invoice unless the pages are typed in)."""
        return sum(transcript_pages(d.ing) for d in self.transcripts())

    def count_confidence(self) -> float:
        """How sure the readings of the transcripts' pages are (takes.count_pages): the least sure one's, 0.95
        when there is nothing to say."""
        reads = [d.ing.count for d in self.transcripts() if getattr(d.ing, "count", None) is not None]
        return min([c.confidence for c in reads] or [0.95])

    def pages_help(self) -> tuple[str, str]:
        """What Est. number of pages counts, for the line under it in the window, and the warnings of the
        transcripts whose readings of their pages disagree (takes.count_pages): ("Transcript pages 378–460 ·
        excludes the 13 word-index pages after them", ""). A count taken from another reading than the scan says
        so on a second line. ("", "") without a transcript PDF."""
        docs = self.transcripts()
        if not docs:
            return "", ""
        reads = [(d, transcript_pages(d.ing), getattr(d.ing, "count", None)) for d in docs]
        total = sum(n for _, n, _ in reads)
        index = sum(c.index_pages if c else max(0, d.ing.page_count - n) for d, n, c in reads)
        many = len(docs) > 1
        name = (lambda d: f"{d.ing.name}: " if many else "")
        warns = [name(d) + c.warning.removeprefix("⚠ ") for d, _, c in reads if c and c.warning]
        warning = "⚠ " + "\n".join(warns) if warns else ""
        if all(c and c.how == "pdf" for _, _, c in reads):  # no reading could tell: every page is counted
            if self.pages_typed():
                return f"Typed by you · the PDF{'s have' if many else ' has'} {total} pages", warning
            why = "" if warns else " (no text to find a word index in)"
            return f"{total} pages: every page of the PDF{'s' if many else ''}{why}", warning
        if index:
            gone = (f"excludes the {index} word-index page{'s' if index > 1 else ''} "
                    f"after {'them' if index > 1 else 'it'}")
        else:
            gone = "no word index"
        if self.pages_typed():
            line = (f"Typed by you · the transcript{'s have' if many else ' has'} {total} pages, "
                    f"{'index excluded' if index else 'no word index'}")
        elif many:
            line = f"{len(docs)} transcripts, {' + '.join(str(n) for _, n, _ in reads)} pages · {gone}"
        else:
            c = reads[0][2]
            span = f"Transcript pages {c.first}–{c.last}" if c and c.first is not None else f"{total} transcript pages"
            line = f"{span} · {gone}" if index else f"{span} ({gone})"
        notes = [name(d) + c.note for d, _, c in reads if c and c.note and not c.warning]
        return "\n".join([line] + notes), warning

    def runsheet_opts(self, s: Settings) -> RunSheetOpts:
        """The takes of every transcript of the job, for the run sheet. The pages before the first initials go
        to whoever Whose pages... said they are (front_owner), as the invoice bills them (reporter_pages)."""
        rows = []
        for d in self.transcripts():
            rows += rows_from(d.ing, self._day_of(d), s, self.front_owner.get(d.key(), ""))
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

    def pages_typed(self) -> bool:
        """The Pages field was typed in: it is what the user's own invoice bills, whoever wrote the pages
        (another reporter's invoices bill their own pages: see billed_by). The transcripts' own page count typed
        in again is no number of the user's: as one, it would bill the other reporters' pages on the user's
        invoice (billing the whole transcript is Whose pages... -> The whole transcript)."""
        fs = self.case.fields["est_pages"]
        return fs.source == SRC_USER and not self.is_the_count(fs.value)

    def is_the_count(self, value: str) -> bool:
        """A Pages value typed in that is no number of the user's (see pages_typed): blank or no number (a field
        cleared), the transcripts' own count, or the pages the invoice bills anyway (own_pages); "1,200" is
        1200. _all_pages turns such a value back into the count. ("0" is the user's: no invoice for the day.)"""
        n = to_int(value, -1)
        if n < 0:
            return True
        whole = self.transcript_pages()
        return bool(whole) and n in (whole, self.own_pages() or whole)

    def basis_of(self, doc: Doc) -> list[str]:
        """Whose pages of a transcript are billed (see page_basis): ["me"], ["me", "ds"], ["ds"], ["*"]..."""
        v = self.page_basis.get(doc.key(), "me")
        out = [v] if isinstance(v, str) else [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
        return out or ["me"]

    def bill_reporters(self) -> list[str]:
        """Whose invoices the job's transcripts make: "me" (the user's own: their pages, or the whole transcript)
        and the initials of each other reporter Whose pages... ticks, the user first. ["me"] without any."""
        out: list[str] = []
        for d in self.transcripts():
            for b in self.basis_of(d):
                r = "me" if b in ("me", "*") else b
                if r != NOBODY and r not in out:
                    out.append(r)
        return sorted(out, key=lambda r: r != "me") or ["me"]

    def billed_by(self, reporter: str) -> "Job":
        """This job as the invoices of one reporter bill it ("me": the user's own; else another reporter's
        initials): a copy whose transcripts bill that reporter's pages only (those Whose pages... ticks for it;
        none of a transcript it isn't ticked for). The job itself when it bills nobody else."""
        if reporter == "me" and self.bill_reporters() == ["me"]:
            return self
        basis = {}
        for d in self.transcripts():
            chosen = self.basis_of(d)
            if reporter == "me":
                mine = [b for b in chosen if b in ("me", "*")]
                basis[d.key()] = mine[0] if mine else NOBODY
            else:
                basis[d.key()] = reporter if reporter in chosen else NOBODY
        # (its own case: same_entries, run on the copies by joint_invoice, fills a firm into a row and renames the
        # copy's Excerpts... rows; on rows shared with the job, the job's own rows would no longer match them)
        job = replace(self, page_basis=basis, case=deepcopy(self.case))
        if reporter != "me" and self.pages_typed():
            # A Pages number typed in is the user's own count: another reporter's invoices bill their own pages
            # of the transcripts (else each set billed the typed number, the pages twice over)
            job.case.fields["est_pages"] = FieldState()
        return job

    def invoice_sets(self) -> list[InvoiceOpts]:
        """The invoices of this job: the user's own (invoice_opts), and one set for each other reporter Whose
        pages... ticks, in their name (InvoiceOpts.reporter). See joint_invoice_sets."""
        return [opts for _, opts in joint_invoice_sets([self])]

    def billed_mask(self, doc: Doc) -> list[bool] | None:
        """Which pages of a transcript are billed, page by page; None when all of them are. They all are when no
        page has initials, when one reporter wrote it all (the user, or nobody says who the user is), or when
        Whose pages... says the whole transcript. Otherwise those with the user's initials (or the reporter's
        chosen under Whose pages...); the pages before the first initials count for Job.front_owner. With
        several reporters ticked, the first (the user, when ticked) is the one counted here; billed_by gives
        each reporter's."""
        return self._mask(doc, self.basis_of(doc)[0])

    def _mask(self, doc: Doc, basis: str) -> list[bool] | None:
        """billed_mask for one of the choices of Whose pages... ("me", initials, "*" or NOBODY)."""
        owners = doc.owners()
        found = list(dict.fromkeys(o for o in owners if o))
        if basis == NOBODY:
            return [False] * transcript_pages(doc.ing)
        if basis == "*" or not found:
            return None
        if basis == "me" and len(found) == 1 and (not self.own or found[0] in self.own):
            return None
        targets = set(self.own) if basis == "me" else {basis}
        front = found[0] if len(found) == 1 else self.front_owner.get(doc.key(), "")
        return [(o or front) in targets for o in owners]

    def billed_pages_of(self, doc: Doc) -> int:
        """The pages of one transcript that are billed (see billed_mask)."""
        mask = self.billed_mask(doc)
        return transcript_pages(doc.ing) if mask is None else sum(mask)

    def own_pages(self) -> int:
        """The pages of the job's transcripts that are billed: on a transcript of several reporters, the user's
        own or those of the reporter Whose pages... chose first (see billed_mask)."""
        return sum(self.billed_pages_of(d) for d in self.transcripts())

    def shared_transcripts(self) -> list[Doc]:
        """The transcripts written by more than one reporter (Whose pages... lists them)."""
        return [d for d in self.transcripts() if len(d.reporters()) > 1]

    def ownership_problem(self) -> str:
        """Why the job's transcripts can't be billed as they are ("" when they can): a transcript of several
        reporters begins with pages nobody's initials are on and Whose pages... hasn't said whose they are, none
        of a transcript's pages carry the user's initials (or the user's initials aren't known), or the reporter
        chosen under Whose pages... is no longer on it. The invoice is held until Whose pages... says (see
        invoice_hold). A Pages field typed in is billed as it is."""
        if self.pages_typed():
            return ""
        for d in self.transcripts():
            found = d.reporters()
            for basis in self.basis_of(d):
                if basis in ("*", NOBODY) or not found:
                    continue
                if basis != "me" and basis not in found:
                    return f"{d.ing.name}: {basis.upper()}'s initials are no longer on its pages"
                front = d.front_pages()
                if len(found) > 1 and front and d.key() not in self.front_owner:
                    pages = "page has" if front == 1 else f"{front} pages have"
                    return f"the first {pages} no reporter's initials in {d.ing.name}: whose are they?"
                if basis == "me" and len(found) > 1 and not self.own:
                    return (f"{d.ing.name} was written by several reporters "
                            f"({', '.join(x.upper() for x in found)}): your initials aren't known (Settings → My info)")
                mask = self._mask(d, basis)
                if basis == "me" and mask is not None and not any(mask):
                    mine = "/".join(sorted(x.upper() for x in self.own))
                    return (f"none of the pages of {d.ing.name} carry your initials ({mine}); "
                            f"they are {', '.join(x.upper() for x in found)}'s")
        return ""

    def invoice_hold(self) -> str:
        """Why no invoice is made for this job until the user checks something ("" when it can be made):
        Excerpts... rows that no longer fit, or whose pages to bill (Whose pages...)."""
        if self.portions_problem():
            return self.portions_check()
        why = self.ownership_problem()
        return f"Whose pages… needs choosing ({why})" if why else ""

    def invoice_pages(self) -> int:
        """The pages to bill: the Pages field when typed in (with a transcript or without one: a caption page
        and "40" typed are enough for an invoice), else the billed pages of the transcripts (own_pages: all of
        them when one reporter wrote them, else the user's own unless Whose pages... says otherwise). 0 = no
        transcript and no pages typed (a number read from an e-mail is no count to bill), or none of the
        transcript's pages is billed (then ownership_problem says why)."""
        if self.pages_typed():
            return max(0, to_int(self.case.get("est_pages"), self.transcript_pages()))
        # worked out afresh, not read from the Pages field: Whose pages... may have changed since it was filled
        return self.own_pages()

    def day_mask(self) -> list[bool] | None:
        """Which pages of the job's transcripts (one after the other) are billed; None when they all are or the
        Pages field was typed in. Excerpts... rows count pages in this whole stretch."""
        if self.pages_typed():
            return None
        masks = [(self.billed_mask(d), transcript_pages(d.ing)) for d in self.transcripts()]
        if all(m is None for m, _ in masks):
            return None
        return [b for m, n in masks for b in (m if m is not None else [True] * n)]

    def portion_pages(self) -> int:
        """The pages Excerpts... splits: every page of the day's transcripts when only some of them are
        billed (a firm is billed for the billed pages of the stretch it ordered), else the pages billed."""
        mask = self.day_mask()
        return len(mask) if mask is not None else self.invoice_pages()

    def printed_pages(self) -> list[int | None]:
        """The number printed on each page Excerpts... counts ([358, 359, ...]; None where none is known, a page
        after a known one counting on from it); [] when the Pages field was typed in or the count doesn't come to
        portion_pages."""
        if self.pages_typed():
            return []
        out: list[int | None] = []
        for d in self.transcripts():
            n = transcript_pages(d.ing)
            nums = [m.number for m in d.ing.marks[:n]]
            nums += [None] * (n - len(nums))
            prev = d.ing.first_page_no - 1 if d.ing.first_page_no is not None and not d.ing.marks else None
            for x in nums:
                prev = x if x is not None else (prev + 1 if prev is not None else None)
                out.append(prev)
        return out if len(out) == self.portion_pages() else []

    def invoice_days(self) -> list[tuple[str, int]]:
        """(date, pages) of each day billed, earliest first: a line per day of the transcripts (volumes of one
        day added up), or one line with the job's dates and all the pages when there is one day, the Pages
        field was typed in, or a transcript has no date (its day isn't known). Empty without pages to bill (no
        transcript and no pages typed)."""
        pages = self.invoice_pages()
        if not pages:
            return []
        by_day: dict[date | None, int] = {}
        for d in self.transcripts():
            day = self._day_of(d)
            by_day[day] = by_day.get(day, 0) + self.billed_pages_of(d)
        typed = self.pages_typed()
        if len(by_day) < 2 or typed or None in by_day:
            return [(self.case.get("dates"), pages)]
        return [(f"{d.month}/{d.day}/{d.year}", n) for d, n in sorted(by_day.items())]

    def day_totals(self) -> list[int]:
        """Every page of the transcripts of each day of invoice_days (the user's or not), in the same order."""
        days = self.invoice_days()
        if len(days) < 2:
            return [self.transcript_pages()] if days else []
        by_day: dict[date | None, int] = {}
        for d in self.transcripts():
            by_day[self._day_of(d)] = by_day.get(self._day_of(d), 0) + transcript_pages(d.ing)
        return [n for _, n in sorted(by_day.items())]

    def ordered_days(self) -> list[tuple[str, dict[str, int]]]:
        """The pages each attorney ordered on each day of the job, as its minute agreement counts them: every
        page of the stretches it ordered, whoever wrote them (its invoice bills only the billed pages among them,
        see invoice_orders), without the word index. (date, {Attorney.key(): pages, "": the pages anyone
        ordered}) per day, earliest first, the days as invoice_days lists them. A day cut into runs under
        Excerpts... (valid_portions) counts the runs each attorney ordered (a run nobody ordered: nobody's, not
        in ""); else every ticked attorney ordered the whole day. With nobody ticked, "" is the whole day (the
        agreement with a blank attorney block). A Pages number typed in (pages_typed) is the day's pages, with a
        transcript or without one: the whole day is that number, an excerpt its runs of it. [] with neither a
        transcript nor pages typed, or with the Pages field typed as 0 (the forms then show the Pages field as
        it is)."""
        whole = self.transcript_pages()
        if not whole and not self.pages_typed():
            return []
        keys = self.ticked_keys()
        if self.pages_typed():
            n = to_int(self.case.get("est_pages"), whole)
            days = [(self.case.get("dates"), n)] if n > 0 else []
        else:
            by_day: dict[date | None, int] = {}
            for d in self.transcripts():
                by_day[self._day_of(d)] = by_day.get(self._day_of(d), 0) + transcript_pages(d.ing)
            days = ([(self.case.get("dates"), whole)] if len(by_day) < 2 or None in by_day else
                    [(f"{d.month}/{d.day}/{d.year}", n) for d, n in sorted(by_day.items())])
        if len(days) != 1:  # (several days on one form: every day is ordered whole, as invoice_orders bills it)
            return [(day, {**{k: n for k in keys}, "": n}) for day, n in days]
        day, n = days[0]
        rows = self.valid_portions()
        if not rows or rows[-1][0] != n:
            rows = [(n, keys)]
        got: dict[str, int] = {k: 0 for k in keys}
        got[""], start = 0, 0
        for last, ks in rows:
            real = [k for k in dict.fromkeys(ks) if k != ORDERED_BY_NOBODY]
            for k in real:
                got[k] = got.get(k, 0) + last - start
            got[""] += (last - start) if real else 0
            start = last
        if not keys:
            got[""] = n
        return [(day, got)]

    def ordered_pages(self) -> dict[str, int]:
        """The pages each attorney ordered on the job's days together, whoever wrote them (ordered_days):
        {Attorney.key(): pages, "": the pages anyone ordered}, what each minute agreement and the MOFR count
        (deliver.generate). Empty with neither a transcript nor pages typed, or with the Pages field typed as
        0."""
        out: dict[str, int] = {}
        for _, got in self.ordered_days():
            for k, n in got.items():
                out[k] = out.get(k, 0) + n
        return out

    def ticked_keys(self) -> list[str]:
        """The Attorney.key() of each attorney ticked on this day who gets an invoice, each once (see
        CaseInfo.invoice_orderers): who can order its pages."""
        return [a.key() for a in self.case.invoice_orderers() if a is not None]

    def refresh_copies(self, s: Settings) -> None:
        """No. of copies of this day's forms follows its ordering parties, as its invoice counts them (the
        Parties number when set, else the attorneys ticked: merge.refresh_copies), unless the user typed a
        number. Called wherever the attorneys ticked, or the Parties number, change. On a day split under
        Excerpts... the Parties number counts for nothing, as on its invoices: the firms that ordered do."""
        refresh_copies(self.case, s, self.copies_parties())

    def copies_parties(self) -> int:
        """The Parties number as No. of copies counts it: on a day split under Excerpts..., whose invoices leave
        it out, the firms that ordered pages of it (split_orderers; a firm ticked without a run of its own orders
        nothing and gets no forms); else the number set (0: not set, the firms ticked count)."""
        return self.split_orderers() if self.valid_portions() is not None else self.parties

    def split_orderers(self) -> int:
        """How many firms ordered pages of the day (ordered_pages: more than 0), as its agreements are made for
        them (fill.agreement_orderers)."""
        return sum(1 for k, n in self.ordered_pages().items() if k and n > 0)

    def parties_mismatch(self) -> str:
        """A Parties number set by hand that the day's Excerpts... rows disagree with ("" when there is none):
        "you set Parties to 3, but 2 firms ordered pages under Excerpts…". The invoices and No. of copies count
        the firms that ordered; a firm left unticked by mistake is what Generate asks about."""
        if not self.parties or self.valid_portions() is None:
            return ""
        n = self.split_orderers()
        if n == self.parties:
            return ""
        firms = f"{n} firm{'s' if n != 1 else ''}"
        return f"you set Parties to {self.parties}, but {firms} ordered pages under Excerpts…"

    def invoice_opts(self) -> InvoiceOpts:
        """The invoice's pages (of each day), ordering parties (the ticked attorneys unless set), who ordered
        which pages, the attorneys invoiced already by a run that stopped part way, and the job's own Peripherals
        and granular detail choices."""
        return InvoiceOpts(self.invoice_pages(), self.parties or max(1, len(self.ticked_keys())),
                           days=self.invoice_days(), email=self.invoice_email, index=self.invoice_index,
                           show=self.invoice_show, detail=self.invoice_detail, orders=self.invoice_orders(),
                           skip=self.billed_keys(), my_pages=self.invoice_pages(),
                           total_pages=self.transcript_pages(), reporters=self.reporters_text(),
                           typed=self.pages_typed())

    def reporter_pages(self, docs: list[Doc] | None = None) -> dict[str, int]:
        """How many pages of the job's transcripts (or of these of them) each reporter wrote, by initials
        ({"pr": 65, "ds": 85}; "" for pages nobody's initials are on). Empty when no page has initials."""
        out: dict[str, int] = {}
        for d in self.transcripts() if docs is None else docs:
            owners, found = d.owners(), d.reporters()
            front = found[0] if len(found) == 1 else self.front_owner.get(d.key(), "")
            for o in owners:
                who = o or (front if front != "none" else "")
                out[who] = out.get(who, 0) + 1
        return out

    def reporters_text(self) -> str:
        """'PR 65, DS 85' (see reporter_pages), for the records; "" when no page has initials."""
        return reporters_text(self.reporter_pages())

    def day_reporters(self) -> list[dict[str, int]]:
        """reporter_pages of each day of invoice_days, in the same order (a firm's invoice of some of the days
        names who wrote those days only)."""
        days = self.invoice_days()
        if len(days) < 2:
            return [self.reporter_pages()] if days else []
        by_day: dict[date | None, list[Doc]] = {}
        for d in self.transcripts():
            by_day.setdefault(self._day_of(d), []).append(d)
        return [self.reporter_pages(docs) for _, docs in sorted(by_day.items())]

    def billed_keys(self) -> list[str]:
        """The attorneys not to invoice again for this day: those a stopped run invoiced already. Empty once
        the day is invoiced (Generate this job bills it again when asked)."""
        return [] if self.invoiced else list(self.invoiced_keys)

    def unbill(self) -> None:
        """New documents may mean new pages to bill: the day is billable again, for every attorney."""
        self.invoiced = False
        self.invoiced_keys = []

    def portions_unavailable(self) -> str:
        """Why the pages of this job can't be split between attorneys (Excerpts...), or "" when they can: it
        needs pages to bill (a job of several days is in the Excerpts window too, ordered whole)."""
        return "" if self.invoice_days() else ("There are no transcript pages to bill yet (without a transcript, "
                                               "type the pages in Est. number of pages).")

    def portions_fit_pages(self) -> bool:
        """Job.portions (when there are any) are rows that end in order, the last on the day's pages (all of
        them, see portion_pages)."""
        rows = self.portions
        try:
            ends = [int(last) for last, _ in rows]
            return bool(ends) and ends[-1] == self.portion_pages() and all(a < b for a, b in zip([0] + ends, ends))
        except (TypeError, ValueError):
            return False

    def portions_problem(self) -> str:
        """Why Job.portions can't bill the day as they are ("" when they can, or there are none): the job no
        longer has one day of pages, the rows don't end on its pages, a row names an attorney not ticked on it
        (unticked or renamed since), or nobody is ticked. The rows are kept for the user to check:
        no invoice is made for the day until they do (see the window's Excerpts...)."""
        if self.portions is None:
            return ""
        if not self.invoice_days() or len(self.invoice_days()) > 1:
            return "it is for one day, and this job no longer is"
        if not self.portions_fit_pages():
            return f"its rows don't end on this day's {self.portion_pages()} pages"
        ticked = set(self.ticked_keys()) | {ORDERED_BY_NOBODY}  # (a run nobody ordered: billed to nobody)
        if any(not keys or any(k not in ticked for k in keys) for _, keys in self.portions):
            return "it names an attorney no longer ticked"
        if not self.ticked_keys():
            return "nobody is ticked"
        return ""

    def portions_check(self) -> str:
        """The warning for portions_problem ("" when there is none): 'Excerpts… needs checking (…)'."""
        why = self.portions_problem()
        return f"Excerpts… needs checking ({why})" if why else ""

    def valid_portions(self) -> list | None:
        """Job.portions when they bill the day as they are (see portions_problem), else None. Nothing is
        changed: rows that don't fit are kept until the user checks them."""
        return self.portions if self.portions is not None and not self.portions_problem() else None

    def rename_in_portions(self, old: str, new: str) -> None:
        """An attorney's name (or firm) was changed, from Attorney.key() `old` to `new`: the Excerpts... rows
        follow it (a name cleared drops it from them), and so do the attorneys a stopped run invoiced already
        (invoiced_keys: "key", or "key@ds" for another reporter's invoice), or a retry would bill them again."""
        if not old or old == new:
            return
        if self.invoiced_keys and new:
            self.invoiced_keys = list(dict.fromkeys(
                new + k[len(old):] if k == old or k.startswith(old + "@") else k for k in self.invoiced_keys))
        if self.portions is None:
            return
        self.portions = [(last, list(dict.fromkeys(new if k == old else k for k in keys if k != old or new)))
                         for last, keys in self.portions]

    def invoice_orders(self) -> list[DayOrder]:
        """Who ordered the pages of each day billed (see invoice.DayOrder): the job's Excerpts... rows when
        they fit it, else every ticked attorney orders every page, shared by the Parties number when it was
        set. A row counts the billed pages in it (on a transcript of several reporters, see day_mask). (Rows
        that need checking must stop the invoice before this: see portions_problem.)"""
        days, totals, counts = self.invoice_days(), self.day_totals(), self.day_reporters()
        rows = self.valid_portions()
        if rows and len(days) == 1:
            out, start = [], 0
            mask, printed = self.day_mask(), self.printed_pages()
            for last, keys in rows:
                # the printed numbers when they are known and run in order through the stretch; else (Pages
                # typed in, volumes that start their numbering again) its place in the day: "pages 1-20"
                nums = printed[start:last]
                ok = bool(nums) and None not in nums and all(a <= b for a, b in zip(nums, nums[1:]))
                span = f"pp. {nums[0]}–{nums[-1]}" if ok else f"pages {start + 1}–{last}"
                out.append(Portion(sum(mask[start:last]) if mask is not None else last - start, list(keys),
                                   span=span))
                start = last
            return [DayOrder(days[0][0], days[0][1], out, totals[0] if totals else 0, counts[0] if counts else {})]
        keys = self.ticked_keys()
        n = self.parties or len(keys)  # with nobody ticked: every attorney on the invoice (see firm_pages)
        totals += [0] * (len(days) - len(totals))
        counts += [{}] * (len(days) - len(counts))
        return [DayOrder(day, pages, [Portion(pages, keys, n)], total, who)
                for (day, pages), total, who in zip(days, totals, counts)]

    def order_lines(self, attorneys: list[Attorney] | None = None) -> list[OrderLine]:
        """Who orders what, spelled out for the window's "Who ordered what" card: a line per day and attorney,
        with the stretches of pages it ordered (whole day or an excerpt), who shares each of them, and how many
        billed pages that is (see day_mask). attorneys: also give a line ("orders nothing") to these when they
        are not ticked on the day (the other days' attorneys of a joint invoice). While the invoice is held
        (invoice_hold) each line is a note saying why. [] without pages to bill."""
        days = self.invoice_days()
        if not days and not self.ownership_problem():
            return []
        held = self.invoice_hold()  # (the wording the Invoice panel and Generate all use)
        ticked = [a for a in self.case.invoice_orderers() if a is not None]
        keys = [a.key() for a in ticked]
        names = {a.key(): a.label() for a in ticked}
        others = [a for a in attorneys or [] if a.key() not in names]

        def nothing(day: str) -> list[OrderLine]:
            """The lines of the attorneys of the other days who aren't ticked on this one."""
            return [OrderLine(day, a.key(), a.label(), note="not ticked on this day: orders nothing")
                    for a in others]

        out: list[OrderLine] = []
        if held or not days:
            day = days[0][0] if len(days) == 1 else self.case.get("dates")
            return [OrderLine(day, k, names[k], note="⚠ " + held) for k in keys] + nothing(day)
        if len(days) > 1:  # a job of several days (Settings: all days on one form): every day is whole
            for (day, billed), whole in zip(days, self.day_totals()):
                for k in keys:
                    line = OrderLine(day, k, names[k], [(1, whole)], True, {o: [(1, whole)] for o in keys
                                                                            if o != k}, billed, whole)
                    line.parties = self.parties  # invoice_orders shares each day that many ways when typed
                    out.append(line)
                out += nothing(day)
            return out
        day = days[0][0]
        pages = self.portion_pages()
        rows = self.valid_portions() or [(pages, keys)]
        mask, printed = self.day_mask(), self.printed_pages()
        stretches, start = [], 0  # (first, last, keys), 1-based
        for last, ks in rows:
            stretches.append((start + 1, last, [k for k in ks if k in names]))
            start = last

        def billed(first: int, last: int) -> int:
            """The pages billed among first..last (see day_mask: some only, when several reporters wrote the day)."""
            return sum(mask[first - 1:last]) if mask is not None else last - first + 1

        for k in keys:
            mine = [(a, b) for a, b, ks in stretches if k in ks]
            spans = _join(mine)
            shared: dict[str, list[tuple[int, int]]] = {}
            for a, b, ks in stretches:
                if k in ks:
                    for o in ks:
                        if o != k:
                            shared.setdefault(o, []).append((a, b))
            line = OrderLine(day, k, names[k], spans, spans == [(1, pages)],
                             {o: _join(s) for o, s in shared.items()}, sum(billed(a, b) for a, b in spans), pages)
            line.printed = [_printed_span(printed, a, b) for a, b in spans]
            line.parties = self.parties if self.valid_portions() is None else 0
            line.typed = self.pages_typed()
            out.append(line)
        return out + nothing(day)

    def output_problems(self, outputs) -> list[str]:
        """What stops the chosen outputs: missing fields for the agreement or MOFR, or a speed to choose for
        them (speed_question), no pages to bill for an invoice (no transcript and no pages typed), no transcript
        for a run sheet, or Excerpts... rows to check for the invoice."""
        out = self.problems() if {"agreement", "mofr"} & set(outputs) else []
        if {"agreement", "mofr"} & set(outputs) and self.speed_question():  # (the speed they name)
            out.append(f"{self.speed_question()}: choose the speed (Minute agreement form details)")
        if "invoice" in outputs and self.invoice_hold():
            out.append(self.invoice_hold())
        elif "invoice" in outputs and not self.invoice_pages():
            out.append(NO_INVOICE)
        if "runsheet" in outputs and not self.transcript_pages():
            out.append(NO_RUNSHEET)
        return out

    def issues(self, outputs) -> list[str]:
        """Everything to check about the job, each once: why Generate failed last time, fields missing or no
        attorney ticked (problems), and what stops the outputs ticked (output_problems). The job list marks a
        job ⚠ when there is any (and it isn't saved), and its tooltip lists them."""
        out = [f"Not saved: {self.error}"] if self.error else []
        return list(dict.fromkeys(out + self.problems() + self.output_problems(outputs)))

    def to_check(self, outputs) -> bool:
        """The job list's ⚠: something to check (issues) on a job not saved yet (a failed Generate counts)."""
        return bool((self.error or not (self.saved or self.invoiced)) and self.issues(outputs))

    def makeable(self, outputs) -> list[str]:
        """The outputs this job can have: all of them, less the run sheet when there is no transcript and the
        invoice when there are no pages to bill (no transcript and no pages typed, or the Pages field says 0).
        An invoice held until Whose pages... says whose pages to bill is kept: it is held, with the reason (see
        invoice_hold). No run sheet either for a case one reporter wrote, when the window ticked the box for
        others (runsheet_unneeded)."""
        return [o for o in outputs if not (o == "runsheet" and (not self.transcript_pages() or self.runsheet_unneeded))
                and not (o == "invoice" and not self.invoice_pages() and not self.ownership_problem())]

    def unneeded(self, output: str) -> bool:
        """makeable() leaves this output out by choice, not for want of anything: the run sheet of a case one
        reporter wrote (runsheet_unneeded). Nothing to tell the user about."""
        return output == "runsheet" and self.runsheet_unneeded and bool(self.transcript_pages())

    def left_out_reason(self, output: str = "invoice") -> str:
        """Why makeable() left an output out: the run sheet, no transcript; the invoice, no pages to bill (the
        Pages field says 0, or there is no transcript and no pages were typed)."""
        if output == "runsheet" and not self.transcript_pages():
            return "no transcript PDF among the inputs"
        if self.transcript_pages() or self.pages_typed():
            return "the Pages field says 0"
        return "no transcript PDF, and no pages typed in Est. number of pages"

    def file_count(self, outputs) -> int:
        """How many agreements and MOFRs generate() makes for this job (output_counts counts the invoices and
        run sheets, which the days of a case share, and the forms made once for several days: case_forms)."""
        per = {"agreement": self.form_count(), "mofr": 1}
        return sum(per.get(o, 0) for o in outputs)


@dataclass
class OrderLine:
    """One attorney's order of one day, as the "Who ordered what" card shows it (see Job.order_lines). spans:
    the stretches of the day's pages it ordered, counted from 1 over every page of the day's transcripts
    ([(20, 40)]); whole: every page of the day; shared: the other attorneys who ordered some of the same
    pages, and which; billed: the billed pages among them (what its invoice counts); pages: the day's pages;
    printed: the printed page numbers of each span ("pp. 120-140", "" when not known); note: instead of spans,
    why there is nothing to show ("not ticked on this day", a choice still to make)."""
    day: str
    key: str
    name: str
    spans: list = field(default_factory=list)
    whole: bool = False
    shared: dict = field(default_factory=dict)
    billed: int = 0
    pages: int = 0
    printed: list = field(default_factory=list)
    note: str = ""
    parties: int = 0  # the Parties number typed for the day (its price is shared that many ways); 0 = not typed
    typed: bool = False  # the pages are the Pages field's number, typed in, not the transcript's pages


def _join(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Stretches of pages that touch made one: [(1, 19), (20, 40)] -> [(1, 40)]."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _printed_span(printed: list, first: int, last: int) -> str:
    """'pp. 120-140': the printed numbers of pages first to last of the day, when known and in order; ""."""
    nums = printed[first - 1:last] if printed else []
    if not nums or None in nums or any(a > b for a, b in zip(nums, nums[1:])):
        return ""
    return f"p. {nums[0]}" if len(nums) == 1 else f"pp. {nums[0]}–{nums[-1]}"


# The choices of a job's invoice that are kept with its records (job_origin) and put back (job_from_origin)
_ORIGIN_CHOICES = ("parties", "invoice_email", "invoice_index", "invoice_show", "invoice_detail", "portions",
                   "page_basis", "front_owner")
# Not put back when a job is opened again: they are worked out afresh (today's date, the delivery date from it)
_ORIGIN_SKIP = ("agreement_date", "delivery_date")


def job_origin(job: Job) -> dict:
    """Where a job came from, kept with the record of each file made from it (deliver.generate adds the case):
    the documents read (their paths; pasted text has none) and the invoice's own choices (Peripherals, Excerpts...,
    Whose pages...)."""
    return {"sources": [d.path for d in job.docs if d.path],
            "job": {**{k: getattr(job, k) for k in _ORIGIN_CHOICES},
                    "proc_touched": job.proc_touched, "att_touched": job.att_touched}}


def job_from_origin(origin: dict, s: Settings) -> Job:
    """A job as it was when a file was made from it (origin: Activity.origin, read from JSON), without its
    documents: the window reads those again (origin["sources"]) when they are still there. Each field comes
    back with the source it had, so what the user typed stays theirs and what was read from the documents is
    read again; the attorneys and proceeding types come back as the user left them (ticks included), the day's
    invoice choices too. The agreement and delivery dates are today's again. Anything in `origin` that isn't as
    expected is left out."""
    job = Job()
    snap = origin.get("case") if isinstance(origin.get("case"), dict) else {}
    choices = origin.get("job") if isinstance(origin.get("job"), dict) else {}

    def listed(name: str) -> list:
        """A list of the case snapshot ([] when it isn't one)."""
        return snap[name] if isinstance(snap.get(name), list) else []

    for key, state in (snap["fields"] if isinstance(snap.get("fields"), dict) else {}).items():
        if key in job.case.fields and key not in _ORIGIN_SKIP and isinstance(state, list) and len(state) == 3 \
                and all(isinstance(x, str) for x in state[:2]) and isinstance(state[2], (int, float)):
            job.case.fields[key] = FieldState(state[0], state[1], float(state[2]), [state[0]])
    known = {f for f in Attorney.__dataclass_fields__}
    for a in listed("attorneys"):
        if isinstance(a, dict):
            text = {k: v for k, v in a.items() if k in known and k != "checked" and isinstance(v, str)}
            job.case.attorneys.append(Attorney(**text, checked=a.get("checked") is True))
    # (touched: the user had changed them, be it to none at all; records from before that was kept say so
    # by having some)
    job.att_touched = bool(job.case.attorneys) or choices.get("att_touched") is True
    job.case.proc_types = {p for p in listed("proc") if isinstance(p, str)}
    job.proc_touched = bool(job.case.proc_types) or choices.get("proc_touched") is True
    if isinstance(choices.get("parties"), int) and not isinstance(choices.get("parties"), bool):
        job.parties = max(0, choices["parties"])
    if isinstance(choices.get("invoice_email"), bool):
        job.invoice_email = choices["invoice_email"]
    if choices.get("invoice_index") in ("auto", "on", "off"):
        job.invoice_index = choices["invoice_index"]
    if isinstance(choices.get("invoice_show"), list):
        job.invoice_show = [k for k in choices["invoice_show"] if isinstance(k, str)]
    job.invoice_detail = choices.get("invoice_detail") is True
    rows = choices.get("portions")
    if isinstance(rows, list) and all(isinstance(r, list) and len(r) == 2 and isinstance(r[0], int)
                                      and isinstance(r[1], list) for r in rows):
        job.portions = [(r[0], [k for k in r[1] if isinstance(k, str)]) for r in rows]
    for name in ("page_basis", "front_owner"):
        value = choices.get(name)
        if isinstance(value, dict):  # (page_basis: a list of reporters too, see Job.basis_of)
            setattr(job, name, {k: v for k, v in value.items() if isinstance(k, str) and (
                isinstance(v, str) or name == "page_basis" and isinstance(v, list)
                and all(isinstance(x, str) for x in v))})
    one_row_per_firm(job)
    job.own = tuple(sorted(my_initials(s.profile.name, s.profile.initials)))
    return job


def one_row_per_firm(job: Job) -> None:
    """Brings a job of a record made by version 2.0 or earlier up to date: then every attorney had a row of
    their own, keyed by the name (Attorney.legacy_keys). Now a firm is one row naming all its attorneys, keyed
    by the firm: the rows of one firm (same_firm) are merged into the first (merge_entry: ticked when any was),
    and the Excerpts... rows (Job.portions) that name an attorney by an old key name the row it is now in (the
    attorneys of a firm in one run become the firm, once). Rows of different firms, and keys that are still
    current, are left alone."""
    rows: list[Attorney] = []
    homes: list[tuple[set[str], Attorney]] = []  # each row's old keys, and the row it is in now
    for a in job.case.attorneys:
        old = {a.key(), *a.legacy_keys()}  # (before merging: the merged name loses "Esq.", "Mr. Jones"...)
        twin = next((b for b in rows if a.firm and b.firm and same_firm(a.firm, b.firm)), None)
        if twin is None:
            rows.append(a)
        else:
            merge_entry(twin, a)
        homes.append((old, twin or a))
    job.case.attorneys = rows
    if not job.portions:
        return
    current = {a.key() for a in rows}

    def now(k: str) -> str:
        """An Excerpts... key as it reads now: the key of the row its attorney was merged into, else of the row
        one of whose attorneys it names; a key no row knows stays as it is."""
        if k in current or k == ORDERED_BY_NOBODY:
            return k
        return next((home.key() for old, home in homes if k in old),
                    next((a.key() for a in rows if k in a.legacy_keys()), k))

    job.portions = [(last, list(dict.fromkeys(now(k) for k in keys))) for last, keys in job.portions]


def make_doc(ing: Ingested, regex: Extraction, s: Settings, path: str = "") -> Doc:
    """A Doc, with what it says about its case worked out from its regex fields, and the case names the user's
    records give its index number (records_extraction)."""
    doc_ident = ident(merge([regex], s))
    return Doc(ing, regex, path=path, ident=doc_ident, records=records_extraction(doc_ident.index, regex))


RECORDS_CONF = 0.55  # a case name from the records alone: offered, marked for review, below any read from the document


def records_extraction(index: str, regex: Extraction) -> Extraction | None:
    """The case names of earlier jobs with this index number in the user's records (records.recorded_cases), as
    a suggestion (SRC_RECORDS): many days of a trial share one case, and the first day's name may have been put
    right by hand. A recorded name that is the same case as one the document gives itself ('Roe v. Poe' and
    'Jane Roe v. Sam Poe', see _same_caption) is added in the document's own words, so that merge.pool counts
    the two as agreeing (+0.1, once per job), and in its own words too when they differ; any other at
    RECORDS_CONF (offered, and marked for review whatever its confidence), below a name the document's caption,
    word index or a "Re:" line gives (0.75 and up), above a guess from a lowercase "roe v poe" or a file name.
    None when the index number is not a number and a year, or the records have none for it."""
    if not re.fullmatch(r"\d+/\d{4}", index or ""):
        return None
    own = [c.value for c in regex.fields.get("case_name", [])]
    out = Extraction()
    for number, name in recorded_cases(index.split("/")[0]):
        if index_key(number) != index:
            continue
        twin = next((v for v in own if _same_caption(_sides(v), _sides(name))), None)
        if twin:
            out.add("case_name", twin, SRC_RECORDS, RECORDS_CONF, "your records: same index number")
            if same_value("case_name", twin, name):
                continue
            # the recorded wording too, as put right by hand once, maybe ('Jane Roe v. Sam Poe, DPM' for the
            # e-mail's 'Roe v. Poe'): offered, not above the document's
        if len(out.fields.get("case_name", [])) < 3:
            out.add("case_name", " ".join(name.split()), SRC_RECORDS, RECORDS_CONF, "your records: same index number")
    return out if out.fields else None


def case_reporters(jobs: list[Job]) -> set[str]:
    """Who wrote the transcripts of these jobs (the days of a case), together: the initials at the foot of the
    pages ("ds"), and the reporters the title pages name whose initials are not among them, by name (a title
    page naming two reporters of a transcript without initials gives two). A run sheet is wanted from two."""
    from .takes import initials_of
    docs = [d for j in jobs for d in j.transcripts()]
    found = {i for d in docs for i in d.reporters()}
    return found | {n.lower() for d in docs for n in d.title_names() if not initials_of(n) & found}


def joint_invoice_sets(group: list[Job]) -> list[tuple[CaseInfo, InvoiceOpts]]:
    """The invoices for a group of days (see joint_invoice): the user's own, then those of each other reporter
    Whose pages... ticks on any of the days, in their name (InvoiceOpts.reporter), each billing that reporter's
    pages only (Job.billed_by). A reporter with no pages to bill gets none (the user's own set is always first
    when the user bills any). One joint_invoice when nobody else is billed."""
    reporters: list[str] = []
    for j in group:
        reporters += [r for r in j.bill_reporters() if r not in reporters]
    reporters.sort(key=lambda r: r != "me")
    if reporters == ["me"]:
        return [joint_invoice(group)]
    out = []
    for r in reporters:
        case, opts = joint_invoice([j.billed_by(r) for j in group])
        opts.reporter = "" if r == "me" else r
        if opts.pages > 0:
            out.append((case, opts))
    return out


def remerge(job: Job, s: Settings) -> None:
    """Rebuilds job.case from its documents, keeping what the user entered: typed fields, and the proceeding
    types and attorneys (with their ticks) once the user has changed them (Job.proc_touched, Job.att_touched).
    The dates of a batch job, its pages, copies, rate and delivery date are then worked out again."""
    prev = job.case
    new = merge([e for d in job.docs for e in d.extractions()], s, previous=prev)
    if job.proc_touched:
        new.proc_types = prev.proc_types
    if job.att_touched:
        known = prev.attorneys
        keys = [(a, a.key(), a.checked) for a in known]
        new.attorneys = dedupe_attorneys(known + list(new.attorneys), s.profile)

        def home_of(a: Attorney) -> Attorney | None:
            """The row a row of the user's table is in now: itself, or the row it was merged into."""
            return a if any(a is b for b in new.attorneys) else \
                next((b for b in new.attorneys if same_entry(a, b)), None)

        homes = [(home_of(a), k, ticked) for a, k, ticked in keys]
        for b in new.attorneys:  # a row is ticked as the user left it: when two of the user's rows became one
            mine = [t for h, _, t in homes if h is b]  # (a firm), ticked when either was; a row only the
            b.checked = any(mine)                      # documents bring is not (though it ticks its sender)
        current = {b.key() for b in new.attorneys}
        # a firm filled in from a duplicate, or a row merged into its firm's: the Excerpts... rows follow its key
        for home, k, _ in homes:
            if k not in current and home is not None and home.key() != k:
                job.rename_in_portions(k, home.key())
    if job.batch:
        _all_dates(new, job)
    job.own = tuple(sorted(my_initials(s.profile.name, s.profile.initials)))
    _all_pages(new, job)
    refresh_copies(new, s, job.copies_parties())  # the attorneys ticked may be the user's, restored above
    refresh_rate(new, s)  # a speed chosen by the user was restored after the defaults were applied
    if s.fill_delivery_date:
        refresh_delivery_date(new, s)
    job.case = new


def split_forgotten(job: Job, keys: set[str], s: Settings) -> bool:
    """Answers about rows that may be one firm were forgotten (keys: those rows' keys, see Attorney.key). A job
    whose table the user changed keeps its rows as they are on a merge (remerge), so a row that joined such
    rows ("Smith Law: Dana Smith, Sam Poe") would stay joined. Its rows are read again from its documents
    instead: the user's ticks go back on the rows they were on (the same key, or same_entry), and rows the user
    added stay. Returns True when the job's rows were read again (False: none of its rows has one of the keys,
    or the user hasn't changed its table, and then the next remerge reads its rows again anyway)."""
    if not job.att_touched or not any(a.key() in keys for a in job.case.attorneys):
        return False
    prev = job.case.attorneys
    job.att_touched = False
    remerge(job, s)
    job.att_touched = True
    rows = job.case.attorneys
    for b in rows:
        b.checked = any(p.checked and (p.key() == b.key() or same_entry(p, b)) for p in prev)
    rows += [p for p in prev if p.source == SRC_USER and not any(p.key() == b.key() for b in rows)]
    job.refresh_copies(s)
    return True


def _date_key(d: str) -> tuple:
    """'6/2/2026' -> (2026, 6, 2), to sort M/D/YYYY dates."""
    m, day, y = (int(x) for x in d.split("/"))
    return y, m, day


def dates_text(texts) -> str:
    """Days as the Dates field of a job or form of several days holds them: '9/28/2026, 9/30/2026, 10/1/2026',
    each once, earliest first (the forms then write days in a row as a range: fill.date_ranges). texts: the
    dates, or texts holding them ("9/28/2026, 9/30/2026"); one in which no date is found is kept as it is, after
    the others."""
    found, other = [], []
    for t in texts:
        days = [d for _, _, d in find_dates(t or "")]
        if days:
            found += days
        elif t and t.strip():
            other.append(t.strip())
    return ", ".join(sorted(dict.fromkeys(found), key=_date_key) + list(dict.fromkeys(other)))


def _all_dates(case: CaseInfo, job: Job) -> None:
    """When the grouped documents name different days, the form lists all of them (to review)."""
    fs = case.fields["dates"]
    if fs.source == SRC_USER:
        return
    have = {d for _, _, d in find_dates(fs.value)}
    every = have.union(*(d.ident.dates for d in job.docs))
    if every - have:
        value = dates_text(every)
        case.fields["dates"] = FieldState(value, fs.source or SRC_REGEX, 0.55,
                                          [value] + [a for a in fs.alternatives if a != value])


def _all_pages(case: CaseInfo, job: Job) -> None:
    """A job with transcript PDFs takes its Est. number of pages from them (SRC_PDF, the "PDF" badge), never
    from a number in a document's words: every page of the transcripts of the job (days, volumes) added up,
    whoever wrote them, without the word index. Each attorney's minute agreement then shows the pages that
    attorney ordered (Job.ordered_pages) and the invoice bills the user's own pages of them (Job.invoice_pages;
    the Invoice panel's Billed row shows how many), so the user's own count is not offered in the ▾ list:
    picked, it would be a number typed in, the day's count on every agreement. A number the user typed stays
    (it is then what the user's invoice bills, and the day's pages on the agreements: Job.pages_typed),
    unless it is the pages the invoice bills anyway (Job.own_pages) or the count itself: then it is the count
    again (the user's own pages billed). Such a "typed" count came from a record of an older version opened again while one of its
    documents had moved, from the suggestion then shown picked from the menu, or from the count typed again.
    A transcript whose readings of its pages disagree lowers the confidence (takes.count_pages: 0.8 when the
    count came from another reading than the scan, 0.55 with a warning, marked for review), and with a warning
    the totals its other counts would give are offered in the ▾ list (the scan's count that the others put
    right is no rival)."""
    counts = [transcript_pages(d.ing) for d in job.transcripts()]
    if not counts:
        return
    fs = case.fields["est_pages"]
    total = sum(counts)
    value = str(total)
    own = str(job.own_pages() or total)
    if fs.source == SRC_USER and not job.is_the_count(fs.value):
        return  # typed by the user: theirs, and what their invoice bills (Job.pages_typed)
    reads = [d.ing.count for d in job.transcripts() if getattr(d.ing, "count", None) is not None]
    rivals = [str(total - c.pages + n) for c in reads if c.warning for n in c.others]
    case.fields["est_pages"] = FieldState(value, SRC_PDF, job.count_confidence(), list(dict.fromkeys(
        [value] + rivals + [a for a in fs.alternatives if a and a != own])))


def group(docs: list[Doc], s: Settings, jobs: list[Job] | None = None) -> list[Job]:
    """Sorts documents into jobs: into one of `jobs` when they match it, else into new ones. A document that
    matches several jobs joins them into one. Returns every job, old and new."""
    jobs = list(jobs or [])
    changed: list[Job] = []
    any_date = s.batch_combine_dates

    def put(doc: Doc, hits: list[Job]) -> None:
        """Adds doc to the first of the jobs it matches, merging the others into it; to a new job when none."""
        if hits:
            job = hits[0]
            for other in hits[1:]:  # this document links jobs that were separate
                job.docs += other.docs
                job.proc_touched |= other.proc_touched
                job.att_touched |= other.att_touched
                # the user's invoice and run sheet choices of either job are kept
                job.parties = job.parties or other.parties
                job.invoice_detail |= other.invoice_detail
                for name in ("invoice_email", "invoice_index", "invoice_show", "portions", "runsheet_to"):
                    if getattr(job, name) is None:
                        setattr(job, name, getattr(other, name))
                job.page_basis = {**other.page_basis, **job.page_basis}
                job.front_owner = {**other.front_owner, **job.front_owner}
                jobs.remove(other)
        else:
            job = Job()
            jobs.append(job)
        job.docs.append(doc)
        job.unbill()  # a new document may mean new pages to bill
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


def _first_day(job: Job) -> tuple:
    """The job's earliest date as a sort key; undated jobs last."""
    days = sorted((d for _, _, d in find_dates(job.case.get("dates"))), key=_date_key)
    return _date_key(days[0]) if days else (9999,)


def invoice_groups(jobs: list[Job], s: Settings) -> list[list[Job]]:
    """The jobs that share an invoice, earliest day first. With Settings.invoice_joint, every day of one case
    (the same index number, or a matching caption); otherwise each job alone. Jobs with no pages to bill (no
    transcript and no pages typed, or the Pages field says 0: Job.invoice_pages) have no invoice and are left
    out."""
    billable = [j for j in jobs if j.invoice_pages()]
    if not s.invoice_joint:
        return [[j] for j in billable]
    groups: list[list[Job]] = []
    for job in billable:
        i = ident(job.case)
        home = next((g for g in groups if i and any(same_case(i, ident(k.case)) for k in g)), None) if i else None
        if home is None:
            groups.append([job])
        else:
            home.append(job)
    for g in groups:
        g.sort(key=_first_day)
    return groups


def firm_questions(jobs: list[Job]) -> list[tuple[Attorney, Attorney]]:
    """The rows of these jobs (the days of a case) that may be one firm or attorney, but it isn't sure
    (extract_regex.maybe_same_entry: 'Smith Law' and 'Smith Law Group', 'Mr. Smith' and Dana Smith of Smith
    Law): the user is asked (the Attorneys card, and Generate). Each pair once; placeholders are left out."""
    rows = [a for j in jobs for a in j.case.attorneys if not a.is_placeholder() and a.key()]
    out: list[tuple[Attorney, Attorney]] = []
    seen: set[frozenset] = set()
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            pair = frozenset((a.key(), b.key()))
            if len(pair) == 2 and pair not in seen and maybe_same_entry(a, b):
                seen.add(pair)
                out.append((a, b))
    return out


def join_entries(jobs: list[Job], a: Attorney, b: Attorney) -> None:
    """The user said a and b are one firm or attorney (and the answer is in extract_regex's answers, so
    same_entry says so from now on): on each of these days their rows become one (merge_entry: the first kept,
    ticked when either was), and its Excerpts... rows and the attorneys already invoiced follow the row kept;
    the days of a case then name it alike (same_entries)."""
    keys = {a.key(), b.key()}
    days = [(j, [x for x in j.case.attorneys if x.key() in keys]) for j in jobs]
    found = [x for _, rows in days for x in rows]
    if not found:
        return
    whole = deepcopy(found[0])  # the entry as all the days name it together
    for x in found[1:]:
        merge_entry(whole, x)
    for j, rows in days:
        if not rows:
            continue
        keep, old = rows[0], [x.key() for x in rows]
        for x in rows[1:]:
            merge_entry(keep, x)
        j.case.attorneys = [x for x in j.case.attorneys if not any(x is y for y in rows[1:])]
        # one key on every day: the firm, or without one the attorney's name ('Mr. Smith' on one day and 'Dana
        # Smith' on another, which same_entries leaves apart, as neither has a firm)
        if whole.firm:
            keep.firm = whole.firm
        else:
            keep.name = whole.name
        for k in dict.fromkeys(old):
            if k != keep.key():
                j.rename_in_portions(k, keep.key())
    if len(jobs) > 1:
        same_entries(jobs)


def same_entries(group: list[Job]) -> None:
    """The same firm or attorney on several days of a case is one entry, with one Attorney.key() on every day
    (the days' invoices, agreements and Excerpts... rows name it by its key): a row of one day that is another
    day's firm written without it ('Dana Smith' when a day's title page says 'Dana Smith' of 'Smith Law'), or
    with its name spelled otherwise ('Smith Law' and 'Smith Law Firm, PLLC'), gets that firm, spelled as on the
    first day that has it (with its address when the row has none), and its Excerpts... rows follow the new key."""
    rows = [(j, a) for j in group for a in j.case.attorneys if not a.is_placeholder() and a.key()]
    for i, (j, a) in enumerate(rows):
        at, home = next(((n, b) for n, (k, b) in enumerate(rows) if k is not j and b.firm and b.key() != a.key()
                         and same_entry(a, b)), (None, None))
        if home is None or (a.firm and at > i):
            continue  # (a firm spelled two ways keeps the spelling of the first day that has it)
        old = a.key()
        a.firm, a.address = home.firm, a.address or home.address
        if a.key() != old:
            j.rename_in_portions(old, a.key())


def group_attorneys(group: list[Job]) -> list[Attorney]:
    """The attorneys ticked on any day of the group who get an invoice, each once (see
    CaseInfo.invoice_orderers: no placeholders or blank rows; the same firm on two days is one, see
    same_entries)."""
    if len(group) > 1:
        same_entries(group)
    out, seen = [], set()
    for job in group:
        for a in job.case.invoice_orderers():
            if a is not None and a.key() not in seen:
                seen.add(a.key())
                out.append(a)
    return out


def joint_invoice(group: list[Job]) -> tuple[CaseInfo, InvoiceOpts]:
    """The case and choices for one invoice covering a group of days (see invoice_groups): the first day's
    case with every date and every attorney ticked on any day, the pages of each day and who ordered them
    (each attorney's invoice then bills only its own days and pages, see invoice.firm_invoices). Each of the
    Peripherals, the granular detail shown and the parties comes from the earliest day that has one of its own, so a
    choice isn't lost when an earlier day joins the group; the granular detail is on when any day has it on."""
    first = group[0]
    if len(group) == 1:
        return first.case, first.invoice_opts()
    same_entries(group)  # (before the days' orders are read: their keys may change)
    case = deepcopy(first.case)
    days = [d for job in group for d in job.invoice_days()]
    case.set("dates", ", ".join(day for day, _ in days if day), case.fields["dates"].source)
    case.attorneys = [deepcopy(a) for a in group_attorneys(group)]
    opts = first.invoice_opts()
    opts.days, opts.pages = days, sum(n for _, n in days)
    opts.orders = [o for job in group for o in job.invoice_orders()]
    opts.my_pages, opts.total_pages = opts.pages, sum(j.transcript_pages() for j in group)
    opts.reporters = reporters_text(day_reporters(opts.orders))
    opts.typed = any(j.pages_typed() for j in group)

    def own(name: str):
        """The job choice `name` of the earliest day that has one (None: none has, as Settings say)."""
        return next((getattr(j, name) for j in group if getattr(j, name) is not None), None)

    opts.email, opts.index, opts.show = own("invoice_email"), own("invoice_index"), own("invoice_show")
    opts.detail = any(j.invoice_detail for j in group)  # ticked on any day of the invoice
    opts.parties = next((j.parties for j in group if j.parties), 0) or max(1, len(case.attorneys))
    # an attorney whose invoice for these days a stopped run made already (on every one of them) isn't billed
    # again; when a day was added since, it is, as its invoice now covers that day too
    opts.skip = [k for k in first.billed_keys() if all(k in j.billed_keys() for j in group)]
    return case, opts


def fill_jobs(jobs: list[Job], s: Settings, progress: Progress | None = None,
              batch: list[Job] | None = None, outputs: list[str] | None = None, ledger=None,
              math: list | None = None) -> list[Path]:
    """Makes the outputs (default: Settings.outputs) of every job; problems are recorded in job.error
    instead of stopping the batch. A job without a transcript gets no run sheet, nor an invoice unless its pages
    were typed in (noted in job.error).
    A job already invoiced (Job.invoiced) is not billed again, nor an attorney a stopped run invoiced already
    (Job.invoiced_keys). A job whose invoice is held (Job.invoice_hold: Excerpts... rows to check, or whose
    pages to bill) gets no invoice, nor the days of a case with a day nobody is ticked on (group_problem): their
    errors say so. Each reporter Whose pages... bills gets their own invoices (joint_invoice_sets). `batch` is the
    whole batch when only some of its jobs are filled: jobs for several days of one case get the date in their
    file names. ledger: the records to number and enter the invoices in (default: the user's; the preview's
    own copy, see Ledger.preview_copy); math: gets (FirmInvoice, number) of each invoice made (see generate).
    The days of a case that share an invoice get their agreements and MOFR once for all of them (form_groups,
    case_forms; made after the days' own files, each listed under the days it covers): a day that is not among
    `jobs` is not on them.
    The days of a case are one piece of work (case_units): its run sheet is written first, with the takes of
    all its days in one go (never a run sheet with a day missing), and when a file of it can't be written (the
    run sheet open in Excel) nothing more is made for any of its days, whose errors say why (HELD): they are
    done again together."""
    outputs = list(s.outputs if outputs is None else outputs)
    ledger = ledger or ledger_for(s)
    names = [j.name_key() for j in batch or jobs]
    unchecked = {id(j) for j in jobs if j.invoice_hold()} if "invoice" in outputs else set()
    to_bill = [j for j in jobs if not j.invoiced and id(j) not in unchecked]
    joint = [g for g in invoice_groups(to_bill, s) if len(g) > 1] if "invoice" in outputs else []
    in_joint = {id(j) for g in joint for j in g}  # these days are billed together, after the loop
    # the days whose agreements and MOFR are made once for all of them (form_groups), after the loop too
    trials = form_groups(jobs, s) if set(FORMS) & set(outputs) else []
    in_trial = {id(j) for g in trials for j in g}
    sheets = sheet_units(jobs, outputs, s)
    unit_of = case_units(jobs, sheets + trials + joint)
    stopped: dict[int, str] = {}  # case unit -> why nothing more is made for its days
    failed: set[int] = set()   # id(job) of the day a held case's problem happened on (its own error says it)
    steps = len(sheets) + len(jobs) + len(trials) + len(joint)
    done: list[Path] = []
    started: list[Path] = []  # run sheets started by this batch: the other days of the trial go on them too
    sheet_of: dict[int, RunSheetOpts] = {}  # id(job) -> the run sheet its takes went on
    for k, unit in enumerate(sheets):  # first: a run sheet open in Excel then stops its case before anything
        if progress:
            progress(k, steps, f"Run sheet for {unit[0].title()}")
        if unit_of[id(unit[0])] in stopped:  # (another run sheet of its case couldn't be saved)
            continue
        sheet = unit_sheet(unit, s, started)
        try:
            made = generate(unit_case(unit), s, out_dir_for(unit[0], s), ["runsheet"], None, ledger,
                            runsheet=sheet, folders=[f for j in unit for f in input_folders(j)],
                            origin=job_origin(unit[0]) if len(unit) == 1 else
                            {"sources": [d.path for j in unit for d in j.docs if d.path], "joint": True})
        except Exception as e:
            stopped[unit_of[id(unit[0])]] = _held_note(unit[0] if len(unit) == 1 else None, e, "the run sheet")
            log_error("could not add the takes to the run sheet", e)
            continue
        for j in unit:
            sheet_of[id(j)] = sheet
            j.saved = list(made)  # (left out when it had every take already)
        done += [p for p in made if p not in done]
        if sheet.created and sheet.path and sheet.path not in started:
            started.append(sheet.path)
    for i, job in enumerate(jobs):
        if progress:
            progress(len(sheets) + i, steps, job.title())
        unit = unit_of[id(job)]
        if unit in stopped:  # its run sheet, or another day of its case, couldn't be saved
            continue
        sheet = sheet_of.get(id(job))
        keys: list[str] = []  # the attorneys invoiced for this job by generate
        try:
            want = job.makeable(outputs)
            billed_elsewhere = job.invoiced or id(job) in in_joint  # billed before, or on the joint invoice
            unchecked_here = id(job) in unchecked and "invoice" in want and not billed_elsewhere
            if billed_elsewhere or unchecked_here:
                want = [o for o in want if o != "invoice"]
            if id(job) in in_trial:  # its agreements and MOFR cover the other days of the case too
                want = [o for o in want if o not in FORMS]
            job.refresh_copies(s)  # (the parties as they are now, whatever changed the ticks)
            ran_sheet = "runsheet" in want  # (written above, for its case)
            want = [o for o in want if o != "runsheet"]
            sheet_made = list(job.saved) if ran_sheet else []
            job.saved = sheet_made + [
                p for p in generate(job.case, s, out_dir_for(job, s), want, job.invoice_sets(), ledger,
                                    dated=names.count(job.name_key()) > 1, folders=input_folders(job),
                                    invoiced=keys, origin=job_origin(job), math=math, ordered=job.ordered_pages())
                if p not in sheet_made] if want else sheet_made
            job.invoiced |= "invoice" in want
            left_out = [o for o in outputs if o not in want and not job.unneeded(o)
                        and not (o == "runsheet" and ran_sheet)
                        and not (o == "invoice" and (billed_elsewhere or unchecked_here))
                        and not (o in FORMS and id(job) in in_trial)]
            why: dict[str, list[str]] = {}  # the outputs left out by reason ("no invoice or run sheet: ...")
            for o in left_out:
                why.setdefault(job.left_out_reason(o), []).append(OUTPUTS[o].lower())
            problems = [f"no {' or '.join(kinds)}: {reason}" for reason, kinds in why.items()]
            if unchecked_here and job.portions_problem():
                problems.append(f"{NOT_INVOICED} check Excerpts… for {job.case.get('dates') or job.title()}")
            elif unchecked_here:
                problems.append(f"{NOT_INVOICED} choose under Whose pages… ({job.ownership_problem()})")
            job.error = "; ".join(problems)
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
        except Exception as e:
            job.saved = (list(job.saved) if sheet else []) + list(getattr(e, "made", []))  # made before it
            job.error = f"{type(e).__name__}: {e}"
            if keys:  # some attorneys' invoices were made: the next run leaves them out
                job.invoiced_keys = list(dict.fromkeys(job.invoiced_keys + keys))
            done += [p for p in job.saved if p not in done]  # one run sheet takes several days
            log_error("could not save the forms of a job", e)
            if len([j for j in jobs if unit_of[id(j)] == unit]) > 1:  # the other days of its case wait for it
                stopped[unit] = _held_note(job, e)
                failed.add(id(job))
    for k, g in enumerate(trials):  # one agreement per attorney and one MOFR for all the days of a case
        if progress:
            progress(len(sheets) + len(jobs) + k, steps, f"Forms for {g[0].title()}")
        if unit_of[id(g[0])] in stopped:
            continue
        forms: list[tuple[CaseForm, list[Job]]] = []
        try:
            forms = case_forms(g, s, outputs)
            made = make_forms([f for f, _ in forms], s, out_dir_for(g[0], s), ledger,
                              origin={"sources": [d.path for j in g for d in j.docs if d.path], "joint": True})
            problem = ""
        except Exception as e:
            made = list(getattr(e, "made", []))
            problem = f"{type(e).__name__}: {e}"  # every day of it says so
            log_error("could not make the forms for several days of a case", e)
            stopped[unit_of[id(g[0])]] = _held_note(None, e, "a minute agreement or the MOFR")
        for (_, covered), path in zip(forms, made):  # each form is listed under the days it covers
            for job in covered:
                if path not in job.saved:
                    job.saved.append(path)
        if problem:
            for job in g:
                job.error = "; ".join(x for x in (job.error, problem) if x)
        done += [p for p in made if p not in done]
    for k, g in enumerate(joint):  # one invoice for all the days of a case
        first = g[0]
        if progress:
            progress(len(sheets) + len(jobs) + len(trials) + k, steps, f"Invoice for {first.title()}")
        if unit_of[id(first)] in stopped:
            continue
        keys = []  # the attorneys invoiced
        held = group_problem(g)
        if held:  # a day with nobody ticked: no invoice until it says who ordered it
            for job in g:
                job.error = "; ".join(x for x in (job.error, f"{NOT_INVOICED} {held}") if x)
            continue
        try:
            sets = joint_invoice_sets(g)
            case = sets[0][0]
            # each set with its own case: another reporter's invoices name the days that reporter wrote
            # (opened again from the records, the days are read from their documents: one job each)
            made = generate(case, s, out_dir_for(first, s), ["invoice"], [o for _, o in sets], ledger,
                            invoiced=keys, math=math, cases=[c for c, _ in sets],
                            origin={"sources": [d.path for j in g for d in j.docs if d.path], "joint": True})
            problem = ""
        except Exception as e:
            made = list(getattr(e, "made", []))
            # every day of it says so (plain text, for the window's message)
            problem = f"invoice: {str(e) or type(e).__name__}"
            log_error("could not make the invoice for several days of a case", e)
        for job in g:  # the invoice is each day's: none of them is billed again
            job.saved += [p for p in made if p not in job.saved]
            if problem:
                job.error = "; ".join(x for x in (job.error, problem) if x)
                # the attorneys whose invoices were made before the problem aren't billed again by the next run
                job.invoiced_keys = list(dict.fromkeys(job.billed_keys() + keys))
            else:
                job.invoiced = True
        done += made
    for job in jobs:  # every day of a held case says why: they stay to be done again, together
        why = stopped.get(unit_of[id(job)])
        if why and id(job) not in failed:  # (the day it happened on says so itself)
            job.error = "; ".join(x for x in (job.error, why) if x)
    log.info("saved %d form(s) for %d job(s)", len(done), len(jobs))
    return done


# how a day's error starts when its case was held (see fill_jobs): "more", as its run sheet may have been written
# before another day's file failed
HELD = "Nothing more made for this case:"


def _held_note(job: Job | None, e: Exception, what: str = "") -> str:
    """Why a case was held, for each of its days: the file that couldn't be saved and why ("Jane Roe - Run
    Sheet.xlsx is open in another program - close it in Excel and try again"). job: the day it happened on
    (None: the case as a whole); what: the file, when the error doesn't name it."""
    reason = str(e) if isinstance(e, PermissionError) and str(e) else f"{type(e).__name__}: {e}"
    where = f" ({job.case.get('dates') or job.title()})" if job is not None else ""
    return f"{HELD} {what or 'a file'}{where} couldn't be saved: {reason}"


def sheet_units(jobs: list[Job], outputs, s: Settings | None = None) -> list[list[Job]]:
    """The jobs whose takes go on one run sheet together, written in one go (fill_jobs): those the window asked
    about together (Job.runsheet_group), else the days of one case (the same index number or case name), unless
    Settings say to start a new run sheet each time (Settings.runsheet_existing "new": a day each). Only jobs
    that can have a run sheet (Job.makeable)."""
    each_new = s is not None and s.runsheet_existing == "new"
    units: list[list[Job]] = []
    for j in jobs:
        if "runsheet" not in j.makeable(outputs):
            continue
        unit = next((u for u in units if (u[0].runsheet_group, j.runsheet_group) != (None, None)
                     and u[0].runsheet_group == j.runsheet_group or
                     u[0].runsheet_group is None and j.runsheet_group is None and not each_new and
                     same_case(ident(u[0].case), ident(j.case))), None)
        if unit is None:
            units.append([j])
        else:
            unit.append(j)
    return units


def unit_sheet(unit: list[Job], s: Settings, started: list[Path]) -> RunSheetOpts:
    """The takes of every day of a run sheet unit (sheet_units), in the order of the days, for one save; where
    they go: as the window chose for the case (Job.runsheet_to), else a run sheet this batch started for the
    case, else as Settings say."""
    rows = [r for j in unit for r in j.runsheet_opts(s).rows]
    rows.sort(key=lambda r: (r.day or date.max, r.start if r.start is not None else -1))
    name_rows(rows)  # a reporter named on one day's title page is named on the other days too
    sheet = RunSheetOpts(rows, unit[0].runsheet_to)
    if sheet.target is None and s.runsheet_existing != "new":
        sheet.target = _started_for(unit[0], started, s)
    return sheet


def unit_case(unit: list[Job]) -> CaseInfo:
    """The case a run sheet unit's record names: the first day's, with every day's dates."""
    if len(unit) == 1:
        return unit[0].case
    case = deepcopy(unit[0].case)
    case.set("dates", dates_text(j.case.get("dates") for j in unit), case.fields["dates"].source)
    return case


def case_units(jobs: list[Job], groups: list[list[Job]]) -> dict[int, int]:
    """id(job) -> the number of its case unit: jobs in any one of `groups` (a run sheet's days, a trial's forms,
    a joint invoice) are one unit with every job they share a group with; the others are a unit each."""
    unit = {id(j): n for n, j in enumerate(jobs)}
    for g in groups:
        ids = [id(j) for j in g if id(j) in unit]
        if not ids:
            continue
        old = {unit[i] for i in ids}
        new = min(old)
        for k, v in unit.items():
            if v in old:
                unit[k] = new
    return unit


def _started_for(job: Job, started: list[Path], s: Settings) -> str | None:
    """A run sheet this batch started that is this job's case too (another day of the trial): the same index
    number, or the same case name when Settings say to add to such run sheets. None: as Settings say."""
    for p in started:
        info = read_info(p)
        why = matches(info, job.case.get("case_name"), job.case.get("index_no")) if info else ""
        if why == "index" or (why == "name" and s.runsheet_existing == "add"):
            return str(p)
    return None


def group_problem(group: list[Job]) -> str:
    """Why the joint invoice of these days can't be made yet ("" when it can): a day with nobody ticked while
    other days have attorneys ticked. Nobody can be billed for its pages until it says who ordered them, so
    the window and the batch warn instead of guessing. Days with no attorney ticked at all (a transcript
    without appearances) still make one invoice with a blank Bill To."""
    if len(group) < 2:
        return ""
    empty = [j for j in group if not j.ticked_keys()]
    if not empty or len(empty) == len(group):
        return ""
    days = ", ".join(j.case.get("dates") or j.title() for j in empty)
    return f"nobody is ticked on {days}: tick who ordered {'that day' if len(empty) == 1 else 'those days'}"


FORMS = ("agreement", "mofr")  # the outputs form_groups makes once for several days


def form_groups(jobs: list[Job], s: Settings) -> list[list[Job]]:
    """The days whose minute agreements and MOFR fill_jobs makes once for all of them (case_forms) instead of a
    set per day: with Settings.forms_per_case and the joint invoice (Settings.invoice_joint), the days of a case
    that share an invoice (invoice_groups: those with pages to bill), less a day whose invoice is held
    (Excerpts... to check, or whose pages to bill: what its attorneys ordered isn't known yet) and a case with a
    day nobody is ticked on (group_problem: it gets no joint invoice either). Days already invoiced count, as
    every run makes the forms of its days again. Each group has two days or more; the other days keep a set of
    forms of their own."""
    if not (s.forms_per_case and s.invoice_joint):
        return []
    days = [j for j in jobs if not j.invoice_hold()]
    return [g for g in invoice_groups(days, s) if len(g) > 1 and not group_problem(g)]


def _latest(jobs: list[Job], key: str) -> FieldState | None:
    """The field `key` of the job whose date in it is the latest (None when no job has a date there)."""
    dated = [(_date_key(found[0][2]), i) for i, j in enumerate(jobs) if (found := find_dates(j.case.get(key)))]
    return deepcopy(jobs[max(dated)[1]].case.fields[key]) if dated else None


def case_forms(group: list[Job], s: Settings, outputs) -> list[tuple[CaseForm, list[Job]]]:
    """The minute agreements and the MOFR of a group of days (form_groups), with the days each covers (it is
    listed under them, see fill_jobs), as `outputs` asks for them. One agreement per attorney ticked on any of
    the days who ordered pages (CaseInfo.invoice_orderers: the same attorney entered twice gets one; with nobody
    ticked on any day, one with a blank attorney block): it lists only the days that attorney ordered something
    on (with Excerpts..., a run of pages), earliest first, and its Est. number of pages is the pages it ordered
    on them, every page whoever wrote it (Job.ordered_days: the whole day, or its runs of an excerpt; the Pages
    field's number when typed); its invoices bill only their reporter's pages of them. One MOFR lists every
    day, its pages those ordered by anyone on each day, added up. The rest is the first day's case, as on the
    joint invoice (court, part, judge, case name, index, speed, rate), with No. of copies on every one of these
    forms the ordering parties (the Parties number set on the earliest day that has one and isn't split under
    Excerpts..., else every attorney ticked on any of the days; unless typed on the first day), the proceeding types of all the days covered
    and the latest estimated delivery date among them (every day's transcript is promised by then)."""
    want = [o for o in FORMS if o in outputs]
    if not want:
        return []
    if len(group) > 1:
        same_entries(group)  # (before the days' orders are read: their keys may change)
    reporters = [r for j in group for r in j.bill_reporters()]
    who = "me" if "me" in reporters else reporters[0]  # (another reporter's pages only: theirs, as generate does)
    # each day of the group with the pages each attorney ordered on it (every page, whoever wrote it)
    days: list[tuple[Job, str, dict[str, int]]] = [(j, d, got) for j in group for d, got in j.ordered_days()]
    base = deepcopy(group[0].case)
    base.attorneys = [deepcopy(a) for a in group_attorneys(group)]
    # every form of the trial: the Parties number of the earliest day set by hand, else every attorney ticked on any
    # of the days (a day split under Excerpts... has no say: its own count is only its own firms, see copies_parties)
    refresh_copies(base, s, next((j.parties for j in group if j.parties and j.valid_portions() is None), 0))

    def covering(picked: list[int]) -> list[Job]:
        """The days (jobs) of these entries of `days`, each once, in order (a job of several days has several)."""
        out: list[Job] = []
        for i in picked:
            if not any(days[i][0] is j for j in out):
                out.append(days[i][0])
        return out

    def form_case(jobs: list[Job], dates: str, pages: int) -> CaseInfo:
        """The case as a form of these days shows it: `base` with their dates and pages, their proceeding types
        (and Other text) together, and the latest delivery date among them."""
        case = deepcopy(base)
        case.set("dates", dates or base.get("dates"), base.fields["dates"].source)
        case.set("est_pages", str(pages), SRC_DERIVED)
        case.proc_types = set().union(*(j.case.proc_types for j in jobs))
        other = [j.case.get("proc_other").strip() for j in jobs if j.case.get("proc_other").strip()]
        if other:
            case.set("proc_other", ", ".join(dict.fromkeys(other)), base.fields["proc_other"].source)
        latest = _latest(jobs, "delivery_date")
        if latest is not None:
            case.fields["delivery_date"] = latest
        return case

    def mine(jobs: list[Job]) -> int:
        """The pages of these days the user's invoices bill (another reporter's, when the user bills none), for
        the records."""
        return sum(j.billed_by(who).invoice_pages() for j in jobs)

    out: list[tuple[CaseForm, list[Job]]] = []
    if "agreement" in want:
        orderers = base.invoice_orderers()
        for atty in orderers:
            k = "" if atty is None else atty.key()
            picked = [i for i, (_, _, got) in enumerate(days) if got.get(k, 0) > 0]
            if not picked:
                continue  # ordered no pages on any of the days: no agreement (nor invoice)
            pages = sum(days[i][2][k] for i in picked)
            jobs = covering(picked)
            form = CaseForm("agreement", form_case(jobs, dates_text(days[i][1] for i in picked), pages), atty,
                            pages, mine(jobs), sum(j.transcript_pages() for j in jobs))
            out.append((form, jobs))
    if "mofr" in want:
        pages = sum(got.get("", 0) for _, _, got in days)
        case = form_case(group, dates_text(j.case.get("dates") for j in group), pages)
        out.append((CaseForm("mofr", case, None, pages, mine(group), sum(j.transcript_pages() for j in group)),
                    list(group)))
    return out


def output_counts(jobs: list[Job], outputs, s: Settings | None = None, again: bool = False) -> dict[str, int]:
    """How many files fill_jobs will make for these jobs, by output: {"agreement": 2, "mofr": 1, "invoice": 2,
    "detailed": 0, "runsheet": 1}. The days of one case share a run sheet, and (as Settings.invoice_joint says)
    the invoices, one for each attorney ticked on any of its days who ordered pages and was not invoiced yet (and
    the set of each other reporter Whose pages... bills, see joint_invoice_sets). Days already invoiced
    (Job.invoiced), and days whose invoice is held (Excerpts... rows to check, or Whose pages... to choose:
    Job.invoice_hold), get none. again: a day already invoiced counts as one not invoiced yet, as Generate this
    job bills it again (Job.billed_keys); Generate all doesn't. "detailed": with Settings.invoice_detailed_copy,
    the detailed copy of each invoice without the granular detail. The days whose agreements and MOFR are made
    once for all of them (form_groups) count those of case_forms. The window says them under each output
    (MainWindow._show_counts)."""
    s = s or Settings()
    out = dict.fromkeys(("agreement", "mofr", "invoice", "detailed", "runsheet"), 0)
    trials = form_groups(jobs, s) if set(FORMS) & set(outputs) else []
    in_trial = {id(j) for g in trials for j in g}
    for j in jobs:
        for o in outputs:
            if o in FORMS and id(j) not in in_trial:
                out[o] += j.file_count([o])
    for g in trials:
        for form, _ in case_forms(g, s, outputs):
            out[form.kind] += 1
    if "invoice" in outputs:
        groups = invoice_groups([j for j in jobs if (again or not j.invoiced) and not j.invoice_hold()], s)
        for g in groups:
            if not group_problem(g):
                for case, opts in joint_invoice_sets(g):  # (each reporter billed)
                    n = invoice_count(case, opts)
                    out["invoice"] += n
                    if s.invoice_detailed_copy and not opts.detail:
                        out["detailed"] += n
    cases: list[Ident] = []
    for j in jobs:
        if "runsheet" in j.makeable(outputs):
            i = ident(j.case)
            if not i or not any(same_case(i, k) for k in cases):
                cases.append(i)
    out["runsheet"] = len(cases)
    return out


def files_to_make(jobs: list[Job], outputs, s: Settings | None = None) -> int:
    """How many files fill_jobs will make for these jobs: output_counts added up (an invoice's detailed copy is a
    file too)."""
    return sum(output_counts(jobs, outputs, s).values())
