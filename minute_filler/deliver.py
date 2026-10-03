"""Makes what the user asked for - minute agreements, a MOFR, invoices, the run sheet - and records each file.

Both the window's Generate button and the batch use generate(), so the records see every file made. The
batch also calls it for a joint invoice: the case of several days, with "invoice" as the only output (see
batch.fill_jobs). Each ticked attorney (once, however many times it is entered) gets an invoice for the pages
it ordered (invoice.firm_invoices).
"""
from __future__ import annotations

import json
from pathlib import Path

from .fill import fill
from .invoice import InvoiceOpts, firm_invoices, make_invoice
from .log import error as log_error
from .models import Attorney, CaseInfo, SRC_USER, to_int
from .mofr import fill_mofr
from .records import Ledger
from .runsheet import NO_RUNSHEET, RunSheetOpts, add_takes
from .settings import OUTPUTS, Settings

NO_INVOICE = "an invoice needs a transcript PDF (for the page count)"


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


def generate(case: CaseInfo, s: Settings, out_dir: Path, outputs: list[str] | set[str],
             invoice: InvoiceOpts | None = None, ledger: Ledger | None = None, dated: bool = False,
             runsheet: RunSheetOpts | None = None, folders: list[Path] | None = None,
             invoiced: list[str] | None = None, origin: dict | None = None) -> list[Path]:
    """Writes the chosen outputs (keys of settings.OUTPUTS) and logs them in the records. Each output goes to
    its own folder when Settings -> Options -> Folders gives it one (Settings.folder_for), else into out_dir.
    An invoice needs `invoice` with the page count, the run sheet `runsheet` with the takes of the transcripts;
    raises ValueError otherwise. The run sheet goes to the run sheets folder (an existing one of the case is
    looked for there and in `folders`, see runsheet.choose). It is written first: when it is open in Excel,
    nothing is made, and trying again doesn't make the invoices twice. A run sheet that already had every
    take is left as it was and is not among the files returned. `invoiced`, when given, gets the
    Attorney.key() ("" for a blank Bill To) of each invoice made, so a run that stops part way can be tried
    again without billing them twice (see InvoiceOpts.skip). `origin`: where the job came from (the documents
    read and its invoice choices, see batch.job_origin); it is kept with each file's record, together with the
    case as it is now, so the job can be opened again from the Records window.
    When a file can't be made, the error raised carries the files made before it as `made`."""
    outputs = [o for o in OUTPUTS if o in outputs]
    billed = invoice.pages if invoice and invoice.pages > 0 else 0  # 0: no transcript among the inputs
    if "invoice" in outputs and not billed:
        raise ValueError(NO_INVOICE)
    if "runsheet" in outputs and not (runsheet and runsheet.rows):
        raise ValueError(NO_RUNSHEET)
    ledger = ledger or ledger_for(s)
    pages = billed or to_int(case.get("est_pages"))
    made: list[Path] = []

    try:
        where_from = json.dumps({**(origin or {}), "case": case_snapshot(case)})
    except (TypeError, ValueError) as e:  # (something in it that JSON can't hold: the files are still made)
        log_error("could not note where a job came from", e)
        where_from = ""
    mine = invoice.my_pages or billed if invoice else 0
    whole = invoice.total_pages if invoice and invoice.total_pages else mine

    def record(kind: str, path: Path, atty: Attorney | None = None, number: str = "", count: int = pages,
               on: CaseInfo = case, my: int = mine, total: int = whole) -> None:
        """Notes a file made and logs it in the records (count: the pages it covers; on: the case as the file
        names it, an attorney's invoice naming only its own days; my, total: the user's pages of the
        transcripts and all their pages, when there are transcripts)."""
        made.append(path)
        try:
            ledger.log_activity(kind, case_name=on.get("case_name"), index_no=on.get("index_no"),
                                dates=on.get("dates"), judge=on.get("judge"), part=on.get("part"),
                                attorney=atty.name if atty else "", firm=atty.firm if atty else "", pages=count,
                                file_path=str(path), invoice_no=number, my_pages=my, transcript_pages=total,
                                origin=where_from)
        except Exception as e:  # the files are made; a records problem must not lose them
            log_error("could not add to the records", e)

    try:
        if "runsheet" in outputs:
            path = add_takes(case, runsheet, s, folders or [])
            if runsheet.created or runsheet.added:
                record("runsheet", path, count=runsheet.added_pages)  # the pages of the takes added
        if "agreement" in outputs:
            for atty in case.orderers():
                record("agreement", fill(case, atty, s, s.folder_for("agreement", out_dir), dated), atty)
        if "mofr" in outputs:
            record("mofr", fill_mofr(case, s, s.folder_for("mofr", out_dir), dated,
                                     pages=str(billed) if billed else ""))
        if "invoice" in outputs:
            for f in firm_invoices(case, s, invoice):  # one per attorney, for the pages it ordered
                path, number = make_invoice(f.case, f.atty, s, s.folder_for("invoice", out_dir), f.opts, ledger,
                                            dated, f.quotes)
                if invoiced is not None:
                    invoiced.append(f.atty.key() if f.atty else "")
                record("invoice", path, f.atty, number, f.opts.pages, f.case, f.opts.my_pages or f.opts.pages,
                       f.opts.total_pages or f.opts.my_pages or f.opts.pages)
    except Exception as e:
        e.made = made  # what was saved before the problem, so the caller can say so
        raise
    return made
