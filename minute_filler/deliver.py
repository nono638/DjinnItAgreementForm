"""Makes what the user asked for - minute agreements, a MOFR, invoices, the run sheet - and records each file.

Both the window's Generate button and the batch use generate(), so the records see every file made. The
batch also calls it for a joint invoice: the case of several days, with "invoice" as the only output (see
batch.fill_jobs). Each ticked attorney (once, however many times it is entered) gets an invoice for the pages
it ordered (invoice.firm_invoices), and with Settings.invoice_detailed_copy a copy of it showing the granular
detail. On a transcript of several reporters, the invoices can be made for each reporter Whose pages... ticks:
one set of InvoiceOpts each, those of another reporter in their name (InvoiceOpts.reporter). Each attorney's
minute agreement shows the pages that attorney ordered, whoever wrote them (batch.Job.ordered_pages), the MOFR
the pages anyone ordered; an invoice bills only its reporter's pages of them. make_forms fills
and records the minute agreements and the MOFR the batch makes once for several days of a case (CaseForm, see
batch.case_forms).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from .fill import agreement_case, agreement_orderers, fill
from .invoice import InvoiceOpts, firm_invoices, make_invoice, render, settings_for
from .log import error as log_error
from .models import Attorney, CaseInfo, SRC_USER, to_int
from .mofr import fill_mofr
from .records import Ledger
from .runsheet import NO_RUNSHEET, RunSheetOpts, add_takes
from .settings import OUTPUTS, Settings

NO_INVOICE = "an invoice needs pages to bill: a transcript PDF, or the pages typed in Est. number of pages"


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
class CaseForm:
    """A minute agreement or MOFR covering several days of a case (see batch.case_forms): kind "agreement" (for
    atty, None = the blank attorney block) or "mofr"; case: the case as the form shows it (its days, pages,
    speed...); pages: the pages it counts (for the records, as on the form); my_pages, total_pages: the user's
    pages of its days' transcripts and all their pages."""
    kind: str
    case: CaseInfo
    atty: Attorney | None = None
    pages: int = 0
    my_pages: int = 0
    total_pages: int = 0


def make_forms(forms: list[CaseForm], s: Settings, out_dir: Path, ledger: Ledger | None = None,
               origin: dict | None = None) -> list[Path]:
    """Fills the agreements and MOFRs of several days (one per attorney, one MOFR) and records each once, with
    its pages, as generate does (dated file names: the first and last day, see fill.output_name). Returns the
    files in the order of `forms`; when one can't be made, the error raised carries the files made before it as
    `made`."""
    ledger = ledger or ledger_for(s)
    made: list[Path] = []
    try:
        for f in forms:
            where_from = _where_from(origin, f.case)
            if f.kind == "mofr":
                path = fill_mofr(f.case, s, s.folder_for("mofr", out_dir), True, pages=str(f.pages) if f.pages else "")
            else:
                path = fill(f.case, f.atty, s, s.folder_for("agreement", out_dir), True)
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
             ordered: dict[str, int] | None = None) -> list[Path]:
    """Writes the chosen outputs (keys of settings.OUTPUTS) and logs them in the records. Each output goes to
    its own folder when Settings -> Options -> Folders gives it one (Settings.folder_for), else into out_dir.
    An invoice needs `invoice` with the page count (a list: the invoices of each reporter billed, see
    batch.Job.invoice_sets; the user's own set, when there is one, gives the pages the records count), the run
    sheet `runsheet` with the takes of the transcripts;
    raises ValueError otherwise. The run sheet goes to the run sheets folder (an existing one of the case is
    looked for there and in `folders`, see runsheet.choose). It is written first: when it is open in Excel,
    nothing is made, and trying again doesn't make the invoices twice. A run sheet that already had every
    take is left as it was and is not among the files returned. `invoiced`, when given, gets the
    InvoiceOpts.skip_key ("" for a blank Bill To) of each invoice made, so a run that stops part way can be tried
    again without billing them twice (see InvoiceOpts.skip). `origin`: where the job came from (the documents
    read and its invoice choices, see batch.job_origin); it is kept with each file's record, together with the
    case as it is now, so the job can be opened again from the Records window. `math`, when given, gets a
    (FirmInvoice, number) pair for each invoice made, to spell out its math (invoice_math.explain). With
    Settings.invoice_detailed_copy each invoice that doesn't show the granular detail gets a copy that does
    ("... (detailed).pdf", the same number and date; it is among the files returned, and in `detailed` when given,
    but not recorded again). A copy that can't be made is logged and left out: it doesn't stop the run.
    `cases`: the case of each invoice set, in the same order (a joint invoice's sets each name their own days,
    see batch.joint_invoice_sets); default: `case` for every set.
    `ordered`: the pages each attorney ordered, whoever wrote them (batch.Job.ordered_pages: by Attorney.key(),
    "" = the pages anyone ordered): each attorney's minute agreement shows its own (fill.agreement_case), the
    MOFR those anyone ordered. Without it (no transcript and no pages typed, or the Pages field typed as 0),
    they show the Est. number of pages field as it is (the MOFR the pages billed, when there are any).
    When a file can't be made, the error raised carries the files made before it as `made`."""
    outputs = [o for o in OUTPUTS if o in outputs]
    given = invoice if isinstance(invoice, list) else [invoice]
    cases = list(cases) if cases is not None else [case] * len(given)
    pairs = [(o, c) for o, c in zip(given, cases) if o is not None]
    sets = [o for o, _ in pairs]
    own = next((o for o in sets if not o.reporter), None)
    billed = billed_pages(sets)
    if "invoice" in outputs and not billed:
        raise ValueError(NO_INVOICE)
    if "runsheet" in outputs and not (runsheet and runsheet.rows):
        raise ValueError(NO_RUNSHEET)
    ledger = ledger or ledger_for(s)
    pages = billed or to_int(case.get("est_pages"))
    made: list[Path] = []

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

    try:
        if "runsheet" in outputs:
            path = add_takes(case, runsheet, s, folders or [])
            if runsheet.created or runsheet.added:
                record("runsheet", path, count=runsheet.added_pages)  # the pages of the takes added
        if "agreement" in outputs:
            for atty in agreement_orderers(case, ordered):
                on = agreement_case(case, atty, ordered)  # (its Est. number of pages: the pages it ordered)
                record("agreement", fill(on, atty, s, s.folder_for("agreement", out_dir), dated), atty,
                       count=to_int(on.get("est_pages")) if on is not case else pages)
        if "mofr" in outputs:
            mofr = (ordered or {}).get("") or billed  # the pages anyone ordered, else the pages billed
            record("mofr", fill_mofr(case, s, s.folder_for("mofr", out_dir), dated,
                                     pages=str(mofr) if mofr else ""), count=mofr or pages)
        if "invoice" in outputs:
            # one per attorney, for the pages it ordered; for each reporter billed
            for f in [f for opts, c in pairs if opts.pages > 0 for f in firm_invoices(c, s, opts)]:
                # the invoice's date, given to its detailed copy too: made a moment later, past midnight, the
                # copy would be dated a day after it
                day = date.today()
                path, number = make_invoice(f.case, f.atty, s, s.folder_for("invoice", out_dir), f.opts, ledger,
                                            dated, f.quotes, today=day)
                if invoiced is not None:
                    invoiced.append(f.opts.skip_key(f.atty))
                if math is not None:
                    math.append((f, number))
                record("invoice", path, f.atty, number, f.opts.pages, f.case, f.opts.my_pages or f.opts.pages,
                       f.opts.total_pages or f.opts.my_pages or f.opts.pages)
                if s.invoice_detailed_copy and not f.opts.detail:
                    try:  # only an aside: the invoice is made and recorded, and the others must still be made
                        copy = render(f.case, f.atty, settings_for(s, f.opts), f.quotes, number,
                                      path.with_name(f"{path.stem} (detailed).pdf"), replace(f.opts, detail=True),
                                      when=day)
                    except Exception as e:
                        log_error("could not make the detailed copy of an invoice", e)
                    else:
                        made.append(copy)
                        if detailed is not None:
                            detailed.append(copy)
    except Exception as e:
        e.made = made  # what was saved before the problem, so the caller can say so
        raise
    return made
