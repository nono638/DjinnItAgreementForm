"""Makes what the user asked for - minute agreements, a MOFR, invoices, the run sheet - and records each file.

Both the window's Generate button and the batch use generate(), so the records see every file made. The
outputs and the code that makes each come from the courthouse (courthouses.OutputSpec.maker: fill.Agreements,
mofr.Mofrs, invoice.Invoices, runsheet.RunSheets, each handed a Making); generate runs them in order and
records what they made. The batch also calls it for a joint invoice: the case of several days, with "invoice"
as the only output (see batch.fill_jobs). Each ticked attorney (once, however many times it is entered) gets an
invoice for the pages it ordered (invoice.firm_invoices), with Settings.invoice_detailed_copy a copy of it
showing the granular detail, and its math saved as Settings.save_math says. On a transcript of several
reporters, the invoices can be made for each reporter Whose pages... ticks: one set of InvoiceOpts each, those
of another reporter in their name (InvoiceOpts.reporter). Each attorney's minute agreement shows the pages that
attorney ordered, whoever wrote them (batch.Job.ordered_pages), and the speed(s) it ordered them at when set;
the MOFR the pages anyone ordered; an invoice bills only its reporter's pages of them. make_forms fills and
records the minute agreements and the MOFR the batch makes once for several days of a case (CaseForm, see
batch.case_forms).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import courthouses
from .invoice import NO_INVOICE, InvoiceOpts  # noqa: F401 (NO_INVOICE: batch and the tests take it from here)
from .log import error as log_error
from .models import Attorney, CaseInfo, SRC_USER, to_int
from .records import Ledger
from .runsheet import RunSheetOpts
from .settings import Settings


def ledger_for(s: Settings) -> Ledger:
    """The records database, with its CSV copies kept in the user's records folder."""
    return Ledger(mirror_dir=s.records_folder())


def backup_folder(s: Settings) -> Path:
    """Where the daily copies of the records database go: Backups in the records folder."""
    return s.records_folder() / "Backups"


def backup_records(folder: Path) -> Path | None:
    """Today's copy of the records database into `folder` (see Ledger.backup), made when the app starts; None
    when there is one already, or no records yet. A problem is logged, never raised: the app must still start."""
    from .records import default_db
    try:
        return Ledger().backup(folder) if default_db().exists() else None
    except Exception as e:
        log_error("could not back up the records", e)
        return None


def case_snapshot(case: CaseInfo) -> dict:
    """A case as the records keep it, to open the job again: each field's value, source and confidence (a
    field the user emptied too: it must not come back from the documents), the proceeding types and the
    attorneys (with their ticks)."""
    return {"fields": {k: [f.value, f.source, f.confidence] for k, f in case.fields.items()
                       if f.value or f.source == SRC_USER},
            "proc": sorted(case.proc_types), "attorneys": [a.to_dict() for a in case.attorneys]}


def billed_pages(sets: list[InvoiceOpts]) -> int:
    """The pages a job's invoices bill, from its invoice sets (batch.Job.invoice_sets), for the records (and the
    MOFR when generate isn't told the pages ordered): the user's own set's (not always the first set: a user
    who bills no pages of their own has none, as joint_invoice_sets leaves out a set with nothing to bill),
    else the most any other reporter's set bills; 0 when there is nothing to bill (no transcript and no pages
    typed, or the Pages field typed as 0)."""
    own = next((o for o in sets if o is not None and not o.reporter), None)
    billed = own.pages if own and own.pages > 0 else 0
    if not billed and any(o.pages > 0 for o in sets if o is not None):  # (only other reporters' pages are billed)
        billed = max(o.pages for o in sets if o is not None)
    return billed


def _where_from(origin: dict | None, case: CaseInfo) -> str:
    """The origin kept with a file's record (see generate), as JSON with the case snapshot; "" when it can't be
    written (the files are still made)."""
    try:
        return json.dumps({**(origin or {}), "case": case_snapshot(case)})
    except (TypeError, ValueError) as e:  # (something in it that JSON can't hold)
        log_error("could not note where a job came from", e)
        return ""


def _record(ledger: Ledger, kind: str, path: Path, on: CaseInfo, atty: Attorney | None, number: str, count: int,
            my: int, total: int, where_from: str) -> None:
    """Logs a file made in the records' activity (a problem is logged, never raised: the file is made)."""
    try:
        ledger.log_activity(kind, case_name=on.get("case_name"), index_no=on.get("index_no"),
                            dates=on.get("dates"), judge=on.get("judge"), part=on.get("part"),
                            attorney=atty.name if atty else "", firm=atty.firm if atty else "", pages=count,
                            file_path=str(path), invoice_no=number, my_pages=my, transcript_pages=total,
                            origin=where_from)
    except Exception as e:  # the files are made; a records problem must not lose them
        log_error("could not add to the records", e)


@dataclass
class Making:
    """What generate hands the maker of each output it makes (courthouses.OutputSpec.maker): the job's case and
    settings, where files go, the pages counted, and record(), which every maker calls for each file it made
    (the file goes in `made` and in the records). check() gets it before anything is made, without `ledger`
    and `record` yet. One object for every output, so generate's loop is the same whatever the output: one
    that needs something more of the job gets a field here, and no other maker changes.

    pages: the pages a file counts in the records unless it says (billed, else the Est. number of pages);
    billed: the pages the invoices bill (billed_pages); ordered: the pages each attorney ordered, speeds the
    speeds it ordered them at (see generate);
    pairs: each invoice set with the case it bills; runsheet, folders: the takes and where else to look for the
    case's run sheet; invoiced, math, detailed: generate's lists to add to (None: not wanted)."""
    case: CaseInfo
    s: Settings
    out_dir: Path
    dated: bool = False
    billed: int = 0
    ordered: dict[str, int] | None = None
    speeds: dict[str, list] | None = None
    pairs: list[tuple[InvoiceOpts, CaseInfo]] = field(default_factory=list)
    runsheet: RunSheetOpts | None = None
    folders: list[Path] = field(default_factory=list)
    invoiced: list[str] | None = None
    math: list | None = None
    detailed: list | None = None
    pages: int = 0
    ledger: Ledger | None = None
    record: Callable[..., None] | None = None
    made: list[Path] = field(default_factory=list)

    def folder(self, key: str) -> Path:
        """Where the output `key` is saved (Settings.folder_for: its own folder, else the job's)."""
        return self.s.folder_for(key, self.out_dir)


@dataclass
class CaseForm:
    """A minute agreement or MOFR covering several days of a case (see batch.case_forms), made by its output's
    make_form: kind, the output ("agreement", for atty, None = the blank attorney block, or "mofr"); case: the
    case as the form shows it (its days, pages, speed...); pages: the pages it counts (for the records, as on
    the form); my_pages, total_pages: the user's pages of its days' transcripts and all their pages."""
    kind: str
    case: CaseInfo
    atty: Attorney | None = None
    pages: int = 0
    my_pages: int = 0
    total_pages: int = 0


def make_forms(forms: list[CaseForm], s: Settings, out_dir: Path, ledger: Ledger | None = None,
               origin: dict | None = None) -> list[Path]:
    """Fills the agreements and MOFRs of several days (one per attorney, one MOFR), each by its output's
    make_form (courthouses.OutputSpec.maker), and records each once, with its pages, as generate does (dated
    file names: the first and last day, see pdfout.output_name). Returns the files in the order of `forms`;
    when one can't be made, the error raised carries the files made before it as `made`."""
    ledger = ledger or ledger_for(s)
    made: list[Path] = []
    try:
        for f in forms:
            where_from = _where_from(origin, f.case)
            path = courthouses.output(f.kind).maker_impl().make_form(f, s, s.folder_for(f.kind, out_dir))
            made.append(path)
            _record(ledger, f.kind, path, f.case, f.atty, "", f.pages, f.my_pages, f.total_pages, where_from)
    except Exception as e:
        e.made = made  # what was saved before the problem, so the caller can say so
        raise
    return made


def generate(case: CaseInfo, s: Settings, out_dir: Path, outputs: list[str] | set[str],
             invoice: InvoiceOpts | list[InvoiceOpts] | None = None, ledger: Ledger | None = None, dated: bool = False,
             runsheet: RunSheetOpts | None = None, folders: list[Path] | None = None,
             invoiced: list[str] | None = None, origin: dict | None = None, math: list | None = None,
             detailed: list | None = None, cases: list[CaseInfo] | None = None,
             ordered: dict[str, int] | None = None, speeds: dict[str, list] | None = None) -> list[Path]:
    """Writes the chosen outputs (keys of settings.OUTPUTS; others are left out) and logs them in the records,
    each made by its maker (courthouses.OutputSpec.maker), in the order of courthouses.make_order: the run
    sheet first, the invoices last. Each output goes to its own folder when Settings -> Options -> Folders gives
    it one (Settings.folder_for), else into out_dir.
    An invoice needs `invoice` with the page count (a list: the invoices of each reporter billed, see
    batch.Job.invoice_sets; the user's own set, when there is one, gives the pages the records count) and
    prices for every speed set (invoice.Invoices.check), the run sheet `runsheet` with the takes of the
    transcripts; raises ValueError otherwise, before anything is made. The run sheet goes to the run sheets
    folder (an existing one of the case is looked for there and in `folders`, see runsheet.choose). It is
    written first: when it is open in Excel, nothing is made, and trying again doesn't make the invoices twice.
    A run sheet that already had every take is left as it was and is not among the files returned. `invoiced`, when given, gets the
    InvoiceOpts.skip_key ("" for a blank Bill To) of each invoice made, so a run that stops part way can be tried
    again without billing them twice (see InvoiceOpts.skip). `origin`: where the job came from (the documents
    read and its invoice choices, see batch.job_origin); it is kept with each file's record, together with the
    case as it is now, so the job can be opened again from the Records window. `math`, when given, gets a
    (FirmInvoice, number) pair for each invoice made, to spell out its math (invoice_math.explain). With
    Settings.invoice_detailed_copy each invoice that doesn't show the granular detail gets a copy that does
    ("... (detailed).pdf", the same number and date; it is among the files returned, and in `detailed` when given,
    but not recorded again). A copy that can't be made is logged and left out: it doesn't stop the run. The
    math of the invoices is saved next to them as Settings.save_math says (invoice_math.save_math: among the
    files returned, not recorded; one that can't be saved is logged, as a copy is).
    `cases`: the case of each invoice set, in the same order (a joint invoice's sets each name their own days,
    see batch.joint_invoice_sets); default: `case` for every set.
    `ordered`: the pages each attorney ordered, whoever wrote them (batch.Job.ordered_pages: by Attorney.key(),
    "" = the pages anyone ordered): each attorney's minute agreement shows its own (fill.agreement_case), the
    MOFR those anyone ordered. Without it (no transcript and no pages typed, or the Pages field typed as 0),
    they show the Est. number of pages field as it is (the MOFR the pages billed, when there are any).
    `speeds`: the speeds each attorney committed to, where set (batch.Job.ordered_speeds: by Attorney.key(),
    [(speed, the day or pages at it)]): its agreement names them (fill.agreement_case), and the MOFR ticks every
    speed ordered (mofr.Mofrs); an attorney without one gets the case's speed, as before.
    When a file can't be made, the error raised carries the files made before it as `made`."""
    order = courthouses.make_order(outputs)
    makers = {k: courthouses.output(k).maker_impl() for k in order}
    given = invoice if isinstance(invoice, list) else [invoice]
    cases = list(cases) if cases is not None else [case] * len(given)
    pairs = [(o, c) for o, c in zip(given, cases) if o is not None]
    sets = [o for o, _ in pairs]
    own = next((o for o in sets if not o.reporter), None)
    billed = billed_pages(sets)
    m = Making(case, s, out_dir, dated=dated, billed=billed, ordered=ordered, speeds=speeds, pairs=pairs,
               runsheet=runsheet, folders=folders or [], invoiced=invoiced, math=math, detailed=detailed)
    for k in courthouses.keys():  # (as listed: with no pages to bill and no takes either, the invoice's is said)
        if k in makers and hasattr(makers[k], "check"):
            makers[k].check(m)
    ledger = ledger or ledger_for(s)
    pages = billed or to_int(case.get("est_pages"))
    made = m.made

    where_from = _where_from(origin, case)
    mine = (own.my_pages or own.pages) if own else 0
    whole = (own.total_pages if own else 0) or max((o.total_pages for o in sets), default=0) or mine

    def record(kind: str, path: Path, atty: Attorney | None = None, number: str = "", count: int = pages,
               on: CaseInfo = case, my: int = mine, total: int = whole) -> None:
        """Notes a file made and logs it in the records (count: the pages it covers; on: the case as the file
        names it, an attorney's invoice naming only its own days; my, total: the user's pages of the
        transcripts and all their pages, when there are transcripts)."""
        made.append(path)
        _record(ledger, kind, path, on, atty, number, count, my, total, where_from)

    m.ledger, m.pages, m.record = ledger, pages, record
    try:
        for k in order:
            makers[k].make(m)
    except Exception as e:
        e.made = made  # what was saved before the problem, so the caller can say so
        raise
    return made
