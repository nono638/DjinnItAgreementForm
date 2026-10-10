"""Main window: drop zone, job list and paste box on the left, the current job's editable fields on the right,
and the Outputs box at the bottom: the Generate buttons and a panel per output with its options (greyed out
while the output is unticked) and how many files Generate makes of it, kept up to date (_show_counts). The
panels are the courthouse's outputs (courthouses.panel_order), their options laid out by gui/panels.py.
The job list is a tree: a row per job (Case / file, Date, Pages, Yours) and, under a job of several documents,
a row per document; a document with no index number, or with another than its job's, gets a ⚠ there and a note
under the list (_label_docs, _show_index_notes), and Generate asks first about documents whose index numbers
don't match (_ask_index_numbers).
The "Minute agreement form details" card holds the rate sheet (one missing prices is warned about:
_check_rate_sheet) and the one speed and rate the agreement form names (the Settings rule's,
Settings.agreement_speed, or the job's own choice; when an e-mail asks for another speed, the user is asked
which), among the speeds the invoice offers (ticked in the Invoice panel, each at its price). A firm whose speed
is set in the "Who ordered what" card gets its own; firms that ordered the same pages commit to one (Generate
asks: _ask_split_speeds).
Under the attorneys, "Who pays what" shows each firm's invoice in short as things are ticked; it and the Invoice
panel link to the whole math. The Invoice panel's Peripherals... (the e-mailed copy and the index, for this job
only) and Customize... set what the current job's invoice shows, Excerpts... which firm ordered which pages of
each day of the case (its own window, gui/excerpts.py), and Whose pages... whose pages of a transcript of
several reporters are billed (the user's own, by default; other reporters' on invoices in their name); with
Generate all, the days of one case share one invoice per attorney (batch.joint_invoice). The files of a case go
into a folder of its own inside Save to (Settings.case_folders), asked about when it has files in it already
(_case_folder). The Run sheet box is ticked by how many reporters wrote a case's transcripts until the user
clicks it (_auto_runsheet); New job is greyed out while there is nothing to clear. File -> Lock finished PDFs
saves copies whose fields can no longer be changed.
Generate and Generate all show the files as pictures first (preview.PreviewDialog), with "The math" of the
invoices, unless that is turned off (then the math comes after saving, preview.MathDialog, unless
Settings.show_math is off), and the math is saved as PDFs with the invoices as Settings.save_math says (its
"Save the math" list is under the math too: _math_saves_kept); the box that says what was saved can print the
files. File -> Open recent lists the documents opened lately, and Export / Import settings carry the settings
to another computer. A job once made can be opened again from the Records window (open_past_job). When the
window opens it makes the day's backup of the records and, once a day, asks whether there is a newer version
(update.py); a new user is asked a few questions first (preview.WelcomeDialog), a settings file that couldn't
be read is said so instead, and an incomplete rate sheet is warned about (MainWindow._startup). On the first day
the app is used in a new year, the yin-yang's "done" picture is the New Year one (note_opened).

Documents are read and batches are made on a thread pool (workers.Runner), and the AI is asked on a pool of its
own, one question at a time (MainWindow.ai_pool). Their results are applied on the UI thread; the results of
work started before "New job" are dropped (see MainWindow.gen), and AI questions still waiting are never asked."""
from __future__ import annotations

import html
import json
import re
import tempfile
import textwrap
import time
from copy import deepcopy
from datetime import date
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, QSignalBlocker, Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QKeySequence, QMovie, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QGridLayout, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTableWidget,
    QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import log as logfile
from ..log import log
from ..batch import (BATCH_EXT, HELD, MISMATCH_LEAD, NOT_INVOICED, Doc, Job, case_folders, case_reporters, expand_paths,
                     files_to_make, fill_jobs, firm_questions, group, ident, index_number_text, input_folders,
                     job_from_origin, job_origin, join_entries, out_dir_for, read_loaders, remerge, same_case,
                     split_forgotten)
from ..extract_llm import OllamaExtractor
from ..extract_regex import find_dates, use_firm_answers
from ..dates import quick_date
from ..deliver import backup_folder, backup_records, generate, ledger_for
from ..ingest import ingest_file, ingest_pil, ingest_text
from ..merge import apply_defaults, apply_speed_rule, refresh_delivery_date, refresh_rate
from ..models import (Attorney, CaseInfo, FIELD_LABELS, FieldState, PROC_TYPES, REQUIRED_KEYS, SRC_AI,
                      SRC_DEFAULT, SRC_DERIVED, SRC_PDF, SRC_RECORDS, SRC_USER)
from ..runsheet import SheetUnreadable, find_sheets, run_sheet_summary, runsheets_folder, transcript_pages
from .. import courthouses
from ..courthouses.base import resolve
from ..settings import OUTPUTS, Settings
from .dialogs import ClarifyDialog, RunSheetDialog, SettingsDialog
from .theme import apply_theme
from .widgets import (ASSETS, QuietCombo, open_path, open_url, plural, refill_combo, repolish, round_corners, rounded,
                      set_checks, show_save_error)
from .workers import Runner
from . import zoom as zooming
from .zoom import sized, z

# every kind of file a dropped folder takes (batch.BATCH_EXT), so Browse... offers the same
FILE_FILTER = (f"Documents ({' '.join('*' + e for e in sorted(BATCH_EXT, key=lambda e: (e != '.pdf', e)))});;"
               "All files (*.*)")
ATT_COLS = ["", "Name", "Firm", "Address", "Phone", "Fax", "Email", "Party / role", "Source"]
ATT_FIELDS = [None, "name", "firm", "address", "phone", "fax", "email", "party", "source"]
ORDER_COLS = ["Day", "Attorney", "Pages ordered", "Speed", "Printed page numbers", "Also ordered",
              "Your pages billed"]
ORDER_SPEED = ORDER_COLS.index("Speed")  # (a box to choose it in, see _speed_box)
ORDER_TIPS = {  # the headings' tooltips
    "Pages ordered": "The pages of the day this attorney ordered: every page, or an excerpt (Excerpts…)",
    "Speed": "The speed this attorney ordered its pages of the day at. Firms that ordered the same pages commit "
             "to a speed (Generate asks when one isn't set) and are billed it alone: at different speeds the "
             "original is billed once, at the fastest, and each pays its share at its own speed. A firm "
             "ordering alone may leave it to choose from its invoice. Different speeds for different pages: "
             "Excerpts…",
    "Printed page numbers": "The page numbers printed on those pages of the transcript",
    "Also ordered": "The other attorneys who also ordered some of the same pages, and which (they split part "
                    "of the cost of those pages, the original among it: see Who pays what)",
    "Your pages billed": "How many of those pages its invoice bills: on a transcript of several reporters only "
                         "yours (or those chosen under Whose pages…)",
}
# the list of jobs on the left: a row per job, and under a job of several documents a row per document
JOB_COLS = ["Case / file", "Date", "Pages", "Yours"]
JOB_DATE, JOB_PAGES, JOB_YOURS = 1, 2, 3
JOB_TIPS = {
    "Case / file": "One job per case and date. Click a job to check or edit it; untick the ones \"Generate all\"\n"
                   "should leave out. A job read from several documents lists them under it: right-click one to\n"
                   "see its text, move it to a job of its own or remove it.",
    "Date": "The date of the minutes (a document's own date, or what kind of document it is)",
    "Pages": "The transcript's own pages (the word index after them left out)",
    "Yours": "The pages you wrote (those with your initials; every page when you wrote it alone,\n"
             "or when no page has initials).\n"
             "A number typed in Est. number of pages shows as typed.\n"
             "? = can't tell yet: your initials aren't set (Settings → My info), or Whose pages…\n"
             "hasn't said whose the first pages are.",
}
# the Who ordered what row of a day nobody is ticked on, when other days of its case have attorneys ticked
NOBODY_DAY = "⚠ nobody ticked on this day: no invoice for the case until you tick who ordered it"


SPEEDS_TIP = ("The speeds the invoice offers, each at its own price (the attorney chooses one). One ticked:\n"
              "the invoice bills that speed alone. They are kept for the next job too.\n"
              "The minute agreement form names one of them (Minute agreement form details → Speed), so they\n"
              "can be ticked while Invoice is unticked too. A firm whose speed is set in Who ordered what\n"
              "is billed that speed alone.")
EST_PAGES_TIP = ("Counted from the transcript PDF: every page, whoever wrote it (without the word index).\n"
                 "It is read four ways (the pages up to the index, the line numbers, the printed page numbers,\n"
                 "the index from the end); when two of them agree on another count, or the index would take\n"
                 "more than a quarter of the PDF (rounded up; 5 pages of a short one), the field turns amber\n"
                 "and a warning under it says so.\n"
                 "Each attorney's minute agreement shows the pages that attorney ordered (the whole day,\n"
                 "or its excerpt under Excerpts…); the MOFR the pages anyone ordered. Your invoice bills\n"
                 "only your own pages of them (Invoice panel: Billed).\n"
                 "A number you type is yours: your invoice bills it, and it is the day's pages on every\n"
                 "agreement (an excerpt counts its pages of it). Typed below the PDF's count, Generate asks\n"
                 "about the index when the two would decide it differently. No transcript (a caption page\n"
                 "only)? Type the pages here and the invoice bills them (a number read from an e-mail isn't\n"
                 "billed).")
AGREEMENT_SPEED_TIP = ("The speed written on the minute agreement forms (and the MOFR), at its rate per page, out\n"
                       "of the speeds offered (ticked in the Invoice panel). Settings → Invoice picks it (first\n"
                       "choice Expedited, else the slowest offered); choose another here for this job (↺ goes\n"
                       "back to the Settings rule). A firm whose speed is set in Who ordered what gets its own\n"
                       "speed on its form, and the MOFR ticks every speed ordered.\n"
                       "When an e-mail asks for another speed, you are asked which.")
# (the tips below say what firms ordering the same pages split, and what each pays for its own, as
# Settings.invoice_index_shared says: format them with _who_splits)
PARTIES_TIP = ("How many parties ordered: they split {shared},\n"
               "and each pays for its own {own} (normally the number of ticked attorneys).\n"
               "No. of copies on the forms follows it.")
WHO_TIP = ("Excerpts: one attorney ordered the whole transcript and another only pages 20 to 40?\n"
           "A table of every day of the case on that invoice: type each run of pages and tick the firms\n"
           "that ordered it, with the prices as you go. Pages ordered together split {shared}.")
WHOSE_TIP = ("Several reporters wrote this transcript (the initials at the foot of the pages change).\n"
             "The invoice bills your own pages. Tick another reporter here to also make invoices in their name\n"
             "for their pages, or bill the whole transcript.")


PAYS_TIP = ("What each firm's invoice comes to, as things stand: its pages, how it ordered them (alone, or\n"
            "shared with other firms: they split {shared}) and what it pays at each speed\n"
            "the invoice offers, with what that comes to a page (its copy, the e-mailed copy and the index\n"
            "counted in). It follows every tick at once; the link spells out the whole math.")


def _who_splits(s) -> dict[str, str]:
    """What firms ordering the same pages split, and what each pays for its own, as Settings say, for the
    tooltips above: {"shared": "the original and the judge's index", "own": "copy, e-mailed copy and index"}
    (no e-mailed copy, or no index, when Settings → Invoice charges none)."""
    from ..invoice import own_words, shared_words
    return {"shared": shared_words(s), "own": own_words(s)}


def _own_words(firms, s) -> str:
    """What each firm pays for its own on these invoices (invoice.firm_invoices), from the lines they charge:
    "copy, e-mailed copy and index", or "copy" for a 30-page day without an index and the e-mailed copy turned
    off for the job. Settings' index on shared pages "split" makes the index one the firms split, not their own."""
    have = {l.charge for f in firms for q in f.quotes for l in q.lines if l.amount > 0}
    own = [w for label, w in (("Copy", "copy"), ("E-mailed copy", "e-mailed copy"), ("Index", "index"))
           if label in have and (label != "Index" or s.invoice_index_shared == "each")]
    return _and(own) or "copy"

WARN_WIDTH = 72  # the longest line of a warning in the Invoice panel (characters; see MainWindow._warn)
# the longest line of the Invoice panel's Billed and Includes lines (characters): they are broken into lines
# here, not word-wrapped by the label, as a word-wrapped label in a row the form shows later (Billed) was given
# the height of one line less than it needed, its last line cut off
LINE_WIDTH = 40


def _lines(text: str, width: int = LINE_WIDTH) -> str:
    """Each line of the text broken into lines of at most `width` characters (between words)."""
    return "\n".join(textwrap.fill(line, width) or line for line in text.split("\n"))

def _count_text(key: str, counts: dict | None, everyone: dict | None = None, held: str = "") -> str:
    """The line under an output's heading: "Will generate 2 minute agreement forms", "Will generate 3 invoices
    (and 3 detailed copies)", "Will fill in 1 run sheet", "Will generate no invoice" ("No invoice until Whose
    pages… is chosen", "... until the speeds are checked": held, why the job's invoice waits, Job.invoice_hold;
    see _show_counts). With several jobs, what Generate all makes, on a line of its own ("Generate all: 6 (+6
    detailed)"): at the end of the first line it made the panels wider than their other rows, and the Outputs box
    went from four panels a row to three. Digits, not words: "two minute agreement forms" reads as
    "two-minute". counts, everyone: batch.output_counts of the job
    shown (None: an empty job, only Generate all's line) and of every job ticked. The words are the output's
    (courthouses.OutputSpec.count_words); a run sheet (group "sheet") is filled in."""
    spec = courthouses.output(key)
    one, many = spec.count_words()
    lines = []
    if counts is not None:
        verb = "Will fill in" if spec.group == "sheet" else "Will generate"
        n = counts.get(key, 0)
        text = f"{verb} {n} {one if n == 1 else many}" if n else f"No {one} until {held}" if held else \
            f"{verb} no {one}"
        copies = counts.get("detailed", 0) if key == "invoice" else 0
        if copies:
            text += f" (and {copies} detailed cop{'y' if copies == 1 else 'ies'})"
        lines.append(text)
    if everyone is not None:
        extra = everyone.get("detailed", 0) if key == "invoice" else 0
        lines.append(f"Generate all: {everyone.get(key, 0)}" + (f" (+{extra} detailed)" if extra else ""))
    return "\n".join(lines)


def _named(keys) -> str:
    """The outputs in a sentence, by their labels: "minute agreement, MOFR, invoice and/or run sheet"."""
    names = [o.label if o.label.isupper() else o.label[:1].lower() + o.label[1:]
             for o in courthouses.outputs() if o.key in set(keys)]
    return ", ".join(names[:-1]) + " and/or " + names[-1] if len(names) > 1 else "".join(names)


def _previewed(outputs) -> list[str]:
    """The outputs a preview shows: those whose files are PDFs the app marks (OutputSpec.mark). The run sheet is
    a spreadsheet, and making it for the preview would add to the real one."""
    return [o for o in outputs if (spec := courthouses.output(o)) is not None and spec.mark]


def _not_previewed(outputs, several: bool = False) -> str:
    """The preview's note on the outputs it doesn't show: "The run sheet is not shown here; it is saved with the
    rest." ("" when it shows them all); several: of Generate all ("The run sheets are ..., they are ...")."""
    left = [spec.count_words()[1 if several else 0] for o in outputs
            if (spec := courthouses.output(o)) is not None and not spec.mark]
    if not left:
        return ""
    many = several or len(left) > 1
    return f"The {' and the '.join(left)} {'are' if many else 'is'} not shown here; " \
           f"{'they are' if many else 'it is'} saved with the rest."


def _price_lines(firms, form_speed: str = "", asked=frozenset()) -> tuple[str, bool]:
    """The Invoice panel's prices, two speeds a line so the panel stays narrow enough for a small window:
    "Regular $63.00 · Expedite $76.00 each" when every attorney pays the same, else a line per invoice, named
    by Attorney.label ("Alex B. Counsel: Regular $815.50 · Expedite $977.00"), and whether it is so. With
    several speeds, the one the minute agreement form names (form_speed) is marked: "Expedite (form) $76.00".
    A firm of a split order whose speed Generate still asks (asked: Attorney.key()s, batch.speeds_to_ask) is
    priced at the form's speed, with a question mark: "Expedite? $98.00", as the Excerpts window shows it.
    firms: invoice.firm_invoices."""
    from ..invoice_calc import fmt
    from ..rates import speed_key

    def pairs(quotes, ask=False) -> list[str]:
        mark = len(quotes) > 1 and form_speed
        each = [f"{q.speed}{'?' if ask else ''}"
                f"{' (form)' if mark and speed_key(q.speed) == speed_key(form_speed) else ''} "
                f"{fmt(q.per_party)}" for q in quotes]
        return [" · ".join(each[i:i + 2]) for i in range(0, len(each), 2)]

    def asks(f) -> bool:
        return f.atty is not None and f.atty.key() in asked

    if not firms:
        return "", False
    if len({tuple(q.per_party for q in f.quotes) for f in firms}) == 1 and len({asks(f) for f in firms}) == 1:
        shared = len(firms) > 1 or any(q.parties > 1 for q in firms[0].quotes)
        return "\n".join(pairs(firms[0].quotes, asks(firms[0]))) + (" each" if shared else ""), False
    lines, seen = [], set()
    for f in firms:
        name = f.atty.label() if f.atty else "(no attorney)"
        if (name, id(f.quotes)) in seen:
            continue
        seen.add((name, id(f.quotes)))
        name = name if len(name) <= 24 else name[:23] + "…"
        rows = pairs(f.quotes, asks(f))
        lines.append(f"{name}: {rows[0]}" if rows else name)
        lines += ["      " + r for r in rows[1:]]
    return "\n".join(lines), True


def _and(words: list[str]) -> str:
    """['Regular'] -> 'Regular'; ['Regular', 'Expedite', 'Daily'] -> 'Regular, Expedite and Daily'."""
    return " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]


def _or(words: list[str]) -> str:
    """['Email'] -> 'Email'; ['Copy', 'Email', 'Index'] -> 'Copy, Email or Index'."""
    return " or ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " or " + words[-1]


def _pay_html(rows, form_speed: str, form_rate: str, days: int = 1, rates: str = "", own: str = "copy",
              firm_speeds: bool = False) -> str:
    """The "Who pays what" card as rich text: the minute agreement form's one speed and rate, then a row per
    invoice: the firm, its pages, how it ordered them and what it pays at each speed the invoice offers
    ("$408.50 ($3.40/pg)"; the agreement form's speed marked "(form)" when there are several), what a page of
    the original costs alone and shared (rates: invoice_math.page_rates) and what each firm pays for its own
    (own: "copy, e-mailed copy and index"), and a link to the whole math. When every invoice bills one speed
    (the speed its firm ordered: a split order commits to one), a Speed and an Amount column instead.
    rows: invoice_math.who_pays; days: how many days the invoices cover (Generate all); firm_speeds: some firms
    have a speed of their own (Job.speeds), which their agreements name."""
    import html
    from ..invoice_calc import fmt
    from ..rates import speed_key
    single = bool(rows) and all(len(r.prices) == 1 for r in rows)
    speeds = list(dict.fromkeys(sp for r in rows for sp, _, _ in r.prices)) if rows else []
    form = speed_key(form_speed)
    rate = f" at ${form_rate.lstrip('$')} a page" if form_rate and form_speed != "Other" else ""
    offer = (f"Each firm is billed the speed it ordered" if single and len(speeds) > 1 else
             f"The invoices offer {html.escape(_and(speeds))}, each at its own price" if len(speeds) > 1 else
             f"The invoices bill {html.escape(speeds[0])}" if speeds else "")
    span = f" for these {days} days" if days > 1 else ""
    forms = (f"each firm's own speed (<b>{html.escape(form_speed or '(no speed)')}{rate}</b> for a firm without "
             "one)" if firm_speeds else
             f"<b>{html.escape(form_speed or '(no speed)')}{rate}</b> (one speed, one rate)")
    out = [f"Minute agreement forms: {forms}. {offer}{span}:"]
    heads = ["Firm", "Pages", "How it ordered them"] + (["Speed", "Amount"] if single else [
        html.escape(sp) + (" (form)" if len(speeds) > 1 and speed_key(sp) == form else "") for sp in speeds])
    table = ["<table cellspacing='0' cellpadding='3'>",
             "<tr>" + "".join(f"<th align='left'>{h}&nbsp;&nbsp;</th>" for h in heads) + "</tr>"]
    for r in rows:
        cells = [html.escape(r.name), f"{r.pages:,}", html.escape(r.how)]
        if single:
            sp, pays, each = r.prices[0]
            cells += [html.escape(sp), f"{fmt(pays)} ({fmt(each)}/pg)"]
        else:
            got = {sp: (pays, each) for sp, pays, each in r.prices}
            cells += [f"{fmt(got[sp][0])} ({fmt(got[sp][1])}/pg)" if sp in got else "—" for sp in speeds]
        table.append("<tr>" + "".join(f"<td>{c}&nbsp;&nbsp;</td>" for c in cells) + "</tr>")
    table.append("</table>")
    out.append("".join(table))
    if rates:
        out.append(f"{html.escape(rates.rstrip('.'))}. Each firm also pays for its own {html.escape(own)}; /pg is everything "
                   "on its invoice divided by its pages.")
    out.append("<a href='math'>How these amounts are worked out…</a>")
    return "<br>".join(out)


def _pages_changed(job, copy) -> bool:
    """The job was given other pages to bill while Generate all worked on its copy (made when the batch
    started): other documents, another page count, other Excerpts... rows or other pages that are the user's
    (Whose pages..., or initials corrected under My info: as many pages, but not the same ones)."""
    return ([id(d) for d in job.docs] != [id(d) for d in copy.docs] or job.portions != copy.portions
            or job.invoice_pages() != copy.invoice_pages() or job.page_basis != copy.page_basis
            or job.front_owner != copy.front_owner or job.own != copy.own)


def _billing_changed(job, copy) -> bool:
    """The job was given what changes its invoice while Generate all worked on its copy: other pages
    (_pages_changed), other attorneys ticked, another Parties number, another speed for the form or for a firm
    (Job.speeds). What the batch billed is then not all there is to bill (an attorney ticked meanwhile was not
    invoiced)."""
    return (_pages_changed(job, copy) or job.ticked_keys() != copy.ticked_keys() or job.parties != copy.parties
            or job.case.get("delivery") != copy.case.get("delivery") or job.speeds != copy.speeds)


def _billed_by_copy(copy) -> list[str]:
    """The attorneys Generate all invoiced for a day (its copy, billed in full): each one ticked on it, as
    InvoiceOpts.skip_key names them ("key", and "key@ds" for another reporter's invoices)."""
    keys = copy.ticked_keys()
    return keys + [f"{k}@{r}" for r in copy.bill_reporters() if r != "me" for k in keys]


def _entry_text(a: Attorney) -> str:
    """An attorney row in a few words, for a question: "Smith Law Group (Dana Smith)", "Mr. Smith"."""
    return f"{a.firm} ({a.name})" if a.firm and a.name else a.firm or a.name


def _kind_word(doc: Doc) -> str:
    """What a document of a job is, for its row in the list (where a transcript shows its day): "e-mail",
    "invoice", "photo", "PDF", "text" ("transcript" for a photo of one)."""
    kind = doc.regex.doc_kind
    if kind in ("transcript", "invoice"):
        return kind
    if kind == "email" or doc.ing.kind == "email":
        return "e-mail"
    return {"image": "photo", "pdf": "PDF"}.get(doc.ing.kind, "text")


def _cases(jobs: list[Job]) -> list[list[Job]]:
    """These jobs by case (batch.same_case: the same index number or case name), in their order."""
    cases: list[list[Job]] = []
    for j in jobs:
        same = next((g for g in cases if same_case(ident(g[0].case), ident(j.case))), None)
        if same is None:
            cases.append([j])
        else:
            same.append(j)
    return cases


def _held_cases(jobs) -> dict[str, str]:
    """The cases Generate all held (batch.HELD: a file of one of their days couldn't be saved), each with why:
    {"Jane Roe v. Sam Poe": "June 2026 ... Run Sheet.xlsx is open in another program - close it in Excel and try
    again"}, in full (the reason is what the user must act on)."""
    out: dict[str, str] = {}
    for j in jobs:
        for part in j.error.split("; "):
            if part.startswith(HELD) and j.title() not in out:
                out[j.title()] = part[len(HELD):].strip().split("couldn't be saved: ", 1)[-1]
    return out


def _reason(error: str, width: int = 160) -> str:
    """An error for the batch's message: without "PermissionError: " in front, and long enough to say why."""
    error = re.sub(r"^(?:PermissionError|OSError|ValueError): ", "", error)
    return error if len(error) <= width else error[:width - 1] + "…"


# ------------------------------------------------------------------ widgets

class FieldRow(QWidget):
    """Editor + suggestions menu + source badge for one form field.

    Typing makes the value the user's own (source "you") and calls on_edit(key). Values set by the program
    (set_text, set_state) do not call on_edit.
    """

    def __init__(self, key: str, on_edit, multiline: bool = False):
        super().__init__()
        self.key, self.on_edit, self.multiline = key, on_edit, multiline
        self.state = FieldState()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if multiline:
            self.edit = QPlainTextEdit()
            sized(self.edit, "setFixedHeight", 58)
            self.edit.textChanged.connect(self._changed)
        else:
            self.edit = QLineEdit()
            self.edit.textEdited.connect(self._changed)
        # QPlainTextEdit has no textEdited (user-only) signal, so set_text raises this flag instead.
        self._loading = False
        lay.addWidget(self.edit, 1)
        self.alts = QToolButton()
        self.alts.setText("▾")
        self.alts.setPopupMode(QToolButton.InstantPopup)
        self.alts.setMenu(QMenu(self.alts))
        sized(self.alts, "setFixedWidth", 28)
        lay.addWidget(self.alts)
        self.badge = QLabel("")
        self.badge.setObjectName("badge")
        sized(self.badge, "setFixedSize", 58, 20)
        self.badge.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.badge, 0, Qt.AlignTop if multiline else Qt.AlignVCenter)

    def text(self) -> str:
        """What the box shows, without leading or trailing spaces."""
        return self.edit.toPlainText().strip() if self.multiline else self.edit.text().strip()

    def set_text(self, t: str) -> None:
        """Shows t without treating it as typed by the user."""
        shown = self.edit.toPlainText() if self.multiline else self.edit.text()
        if shown == t or (self.edit.hasFocus() and shown.strip() == t):
            return  # unchanged: leave the cursor (and a space just typed) where they are
        self._loading = True
        if self.multiline:
            self.edit.setPlainText(t)
        else:
            self.edit.setText(t)
        self._loading = False

    def set_state(self, st: FieldState) -> None:
        """Shows a field's value, source badge and other suggestions."""
        self.state = st
        self.set_text(st.value)
        self._refresh()

    def _refresh(self) -> None:
        """Updates the badge, the review/missing highlight and the suggestions menu from self.state.
        A value not typed by the user is marked for review when the AI suggested it or its confidence is
        below 0.6."""
        st = self.state
        self.badge.setText(st.source if st.value else "")
        self.badge.setProperty("src", st.source if st.value else "")
        tip = {"regex": "Found in the document", "AI": "Suggested by the AI model - please check",
               "default": "Your default setting", "derived": "Calculated",
               "PDF": "Counted from the transcript PDF, without the word index (the line under it says which pages)",
               "records": "From your records: an earlier job with the same index number - please check",
               "you": "Entered by you"}
        self.badge.setToolTip(tip.get(st.source, ""))
        review = st.value and st.source != SRC_USER and (st.confidence < 0.6 or st.source in (SRC_AI, SRC_RECORDS))
        self.edit.setProperty("review", bool(review))
        self.edit.setProperty("missing", self.key in REQUIRED_KEYS and not st.value)
        others = [a for a in st.alternatives if a != st.value]
        menu = self.alts.menu()
        menu.clear()
        for alt in st.alternatives:
            act = menu.addAction(alt if len(alt) < 110 else alt[:107] + "...")
            act.triggered.connect(lambda _=False, v=alt: self.pick(v))
        self.alts.setVisible(bool(others))
        self.alts.setToolTip(f"{len(others)} other suggestion(s)")
        for w in (self.badge, self.edit):
            repolish(w)

    def pick(self, value: str) -> None:
        """A suggestion picked from the menu: the user's own value (choose), unless it is the value already
        shown, which changes nothing and keeps its source (the transcript's page count picked again stays
        "PDF": as "you" the invoice would bill "the Pages field" instead of the pages counted)."""
        if value == self.state.value and self.text() == value:
            return
        self.choose(value)

    def choose(self, value: str) -> None:
        """Takes a value (a quick delivery date), as if the user had typed it."""
        self.set_text(value)
        self._changed()

    def _changed(self) -> None:
        """The user changed the text: it becomes their value (source "you", full confidence)."""
        if self._loading:
            return
        self.state = FieldState(self.text(), SRC_USER, 1.0, self.state.alternatives)
        self._refresh()
        self.on_edit(self.key)


COMPACT_BELOW = 960  # before the window is shown: window height (px at 100 % zoom) under which the drop zone is small


SWIRLS = ("idle", "working")  # the drop zone's moods that show the swirling yin-yang
LINGER_MS = 10000  # how long the swirl goes on at least, once begun: one full turn of the loop


class Picture(QLabel):
    """The drop zone's yin-yang: a click on the picture itself (not the space beside it) calls on_click."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click

    def mousePressEvent(self, e) -> None:
        """A left click on the picture calls on_click."""
        pix = self.pixmap()
        if e.button() == Qt.LeftButton and pix is not None and not pix.isNull():
            size = pix.deviceIndependentSize().toSize()
            shown = QRect(QPoint(0, 0), size)
            shown.moveCenter(self.rect().center())
            if shown.contains(e.position().toPoint()):
                self.on_click()
        super().mousePressEvent(e)


class DropZone(QFrame):
    """The big drop target. Dropped files go to on_files(paths), text to on_text(str), a picture to
    on_image(QImage); the Browse button calls on_browse(). It also shows the yin-yang pictures."""

    def __init__(self, on_files, on_text, on_image, on_browse):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.on_files, self.on_text, self.on_image = on_files, on_text, on_image
        self.setMinimumHeight(z(215))
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self.icon = icon = QLabel("⭳")
        icon.setObjectName("dropIcon")
        icon.setAlignment(Qt.AlignCenter)
        self.yin = Picture(self.encore)
        self.yin.setAlignment(Qt.AlignCenter)
        self.yin.setVisible(False)
        lay.addWidget(self.yin)
        self.mood = None     # (mood, width) of the picture loaded now, so it is not reloaded for nothing
        self.wanted = None   # the mood asked for last: shown again by set_compact and rezoom, and after a swirl
        self.compact = False
        # the first day of the year the app was used ("2027-01-07", see note_opened): "done" is the New Year
        # yin-yang on that day only (checked each time, as the app may be left open overnight)
        self.new_year_day = ""
        # (mood, width) -> picture, made once: the window's _size_drop switches between the sizes as it measures
        self._pixmaps: dict = {}
        # "idle" and "working" swirl: the moving yin-yang (a loop of the video), played only while it is shown
        self.movie = QMovie(str(ASSETS / "yin_working.webp"), QByteArray(), self)  # (goes with the window)
        # A document is usually read in a blink: the swirl then goes on for one turn (linger_ms from when it
        # began) before the "ready" picture. A problem ("stumped") shows at once.
        self.linger_ms = LINGER_MS
        self._swirl_began = 0.0
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(lambda: self.set_mood(self.wanted))
        self.movie.frameChanged.connect(self._frame)
        t = QLabel("Drop documents here")
        t.setObjectName("dropText")
        t.setAlignment(Qt.AlignCenter)
        sub = QLabel("PDF, photo or text file - or many at once, or a whole folder: "
                     "documents about the same case and date become one form.")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        self.sub = sub
        b = QPushButton("Browse...")
        b.clicked.connect(on_browse)
        sized(b, "setFixedWidth", 120)
        for w in (icon, t, sub):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(b, 0, Qt.AlignCenter)

    def set_mood(self, mood: str | None) -> None:
        """Shows the yin-yang: swirling while the app waits for documents ('idle') and reads them ('working'),
        then 'done' (after the swirl has gone on for linger_ms) or 'stumped' (None hides it). On the year's first
        day of use, 'done' is the New Year yin-yang ('newyear')."""
        self.wanted = mood
        self._settle.stop()
        if mood == "done" and self.new_year_day == date.today().isoformat():
            mood = "newyear"
        if mood in ("done", "newyear") and self.mood and self.mood[0] == "working" \
                and self.movie.state() == QMovie.Running:
            left = self.linger_ms - (time.monotonic() - self._swirl_began) * 1000
            if left > 20:
                self._swirl_size()  # (the layout may have changed meanwhile: set_compact)
                self._settle.start(int(left))  # (then set_mood(self.wanted) again)
                return
        width, height = self._size()
        self.sub.setVisible(not self.compact)
        if mood not in SWIRLS:
            self.movie.stop()
        if mood is None:
            self.mood = None  # (else the same mood asked for again is taken as shown, and doesn't swirl)
            self.yin.setVisible(False)
            self.icon.setVisible(not self.compact)
            self.setMinimumHeight(z(110) if self.compact else z(215))
            return
        self.icon.setVisible(False)
        self.yin.setVisible(True)
        self.setMinimumHeight(height)
        if mood in SWIRLS and self.movie.isValid() and self.movie.state() != QMovie.Running:
            self.movie.start()
        if mood == "working" and (not self.mood or self.mood[0] != "working"):
            self._swirl_began = time.monotonic()
        if (mood, width) == self.mood and self.yin.pixmap() and not self.yin.pixmap().isNull():
            return
        self.mood = (mood, width)
        if (mood, width) not in self._pixmaps:  # (idle swirls too: its first picture is the working one)
            picture = "working" if mood == "idle" else mood
            self._pixmaps[(mood, width)] = rounded(ASSETS / f"yin_{picture}.jpg", width, z(12))
        self.yin.setPixmap(self._pixmaps[(mood, width)])
        self.yin.setToolTip({"idle": "Drop a document to begin", "working": "Working on it…",
                             "done": "Ready to fill!", "stumped": "Something needs your attention",
                             "newyear": "Happy New Year!"}.get(mood, ""))

    def encore(self) -> None:
        """A click on a still picture (ready, stumped, New Year): the yin-yang swirls for one turn, then the
        picture it showed comes back (unless the app asked for another one meanwhile)."""
        if self.mood is None or self.mood[0] in SWIRLS or not self.movie.isValid():
            return
        wanted = self.wanted
        self.set_mood("working")
        self.wanted = wanted
        self._settle.start(max(1, self.linger_ms))  # (then set_mood(self.wanted): the picture it was)

    def _frame(self, _n: int) -> None:
        """Shows the moving picture's next frame, at the size of the still one (not while the window is
        minimised: nobody sees it)."""
        if self.mood and self.mood[0] in SWIRLS and self.yin.isVisible() and not self.window().isMinimized():
            self.yin.setPixmap(round_corners(self.movie.currentPixmap(), self.mood[1], z(12)))

    def _size(self) -> tuple[int, int]:
        """The picture's width and the drop zone's least height, in the layout it has now (set_compact)."""
        return (z(150), z(170)) if self.compact else (z(300), z(330))

    def _swirl_size(self) -> None:
        """While the swirl goes on a while yet (after a read, or a click: _settle), the layout of now: the swirl
        at its width, the line under it, the least height. (Before, a smaller window or another zoom then kept
        the big layout, and the window measured the wrong one.)"""
        width, height = self._size()
        self.sub.setVisible(not self.compact)
        self.setMinimumHeight(height)
        self.mood = ("working", width)

    def rezoom(self) -> None:
        """Shows the picture again at the new zoom's size (the swirl that goes on a while yet goes on)."""
        if self._settle.isActive():
            self._swirl_size()
            return
        self.mood = None
        self.set_mood(self.wanted)

    def set_compact(self, on: bool) -> None:
        """Switches to the small layout (on) or back: a smaller picture leaves room for the list of jobs, or
        for the inputs in a short window."""
        if on != self.compact:
            self.compact = on
            self.set_mood(self.wanted)

    def _hover(self, on: bool):
        """Highlights the drop zone while something is dragged over it (the style sheet's [hover="true"])."""
        self.setProperty("hover", on)
        repolish(self)

    def dragEnterEvent(self, e):
        """Takes a drag of files, text or a picture, and lights the zone up while it is over it."""
        md = e.mimeData()
        if md.hasUrls() or md.hasText() or md.hasImage():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        """The drag left the zone without a drop: its highlight goes."""
        self._hover(False)

    def dropEvent(self, e):
        """Passes what was dropped to its handler (handle_mime)."""
        self._hover(False)
        handle_mime(e.mimeData(), self.on_files, self.on_text, self.on_image)
        e.acceptProposedAction()


def note_opened(s: Settings, today: date | None = None) -> bool:
    """Notes the app was opened today, and says whether today is its first day in a new year (the New Year
    yin-yang shows all that day, however often the app is opened again). The very first opening (no year noted
    yet: a new user, or one who just updated) is not one, nor is a year that went back (a clock set wrong). Saves
    the settings when they change (once a year), unless the settings file couldn't be read (Settings.unreadable:
    the defaults must not be written over it)."""
    today = today or date.today()
    year = str(today.year)
    if s.opened_year != year:
        # (none yet, or typed oddly in the file: "26", "2026.0" - not a year gone by)
        newer = len(s.opened_year) == 4 and s.opened_year.isdigit() and int(s.opened_year) < today.year
        if newer:
            s.new_year_day = today.isoformat()
        s.opened_year = year
        if not s.unreadable:
            try:
                s.save()
            except OSError as e:
                logfile.error("could not save the settings", e)
    return s.new_year_day == today.isoformat()


def handle_mime(md, on_files, on_text, on_image) -> bool:
    """Passes dropped or pasted data to one handler: local files first, then a picture, then text.
    Returns False when there was nothing it could use."""
    files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
    if files:
        on_files(files)
        return True
    if md.hasImage():
        img = md.imageData()
        if img is not None and not img.isNull():
            on_image(img)
            return True
    if md.hasText() and md.text().strip():
        on_text(md.text())
        return True
    return False


def _path_key(path: str) -> str:
    """One spelling per file, to tell whether it is loaded already."""
    return str(Path(path).resolve()).lower()


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """A section card (with a heading when title is given) and the layout to put its contents in."""
    fr = QFrame()
    fr.setObjectName("card")
    lay = QVBoxLayout(fr)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("section")
        lay.addWidget(lbl)
    return fr, lay


# -------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    """The app's window. It holds a list of jobs (one per case and date; more than one is a batch) and shows
    one of them, self.cur, in the editor on the right.

    Background work and the state that guards it:
      gen       bumped by "New job"; a result whose gen is old is dropped.
      work      reads and batches still running (the progress bar shows while it is above 0).
      ai_pending  AI questions not answered yet (waiting their turn on ai_pool, one at a time, or being asked).
      filling   "Generate all" is making files on another thread. It works on copies of the jobs and the
                settings. Until it is done, Generate, Generate all and New job only say "one moment".
      _loading  files being read now, so dropping the same file again does not load it twice.
    """

    def __init__(self, settings: Settings, app: QApplication):
        super().__init__()
        self.s, self.app = settings, app
        # the user's answers about rows that may be one firm: kept ones (Settings.firm_answers) and those for now
        # only (Remember unticked: until the app is closed)
        self._session_answers: list[list] = []
        self._use_answers()
        self.runner = Runner()
        # The AI's questions go on a pool of their own, one at a time: Ollama answers one at a time anyway, and
        # on the global pool each document's question held a thread for up to a minute, so reads and Generate
        # all waited behind them on a laptop with few cores
        self.ai_pool = QThreadPool(self)
        self.ai_pool.setMaxThreadCount(1)
        self.ai_runner = Runner(self.ai_pool)
        self.ai = OllamaExtractor(settings)
        self.ai_ok = False
        self.gen = 0  # bumped by "New job": results of work started before it are dropped
        self.jobs: list[Job] = [Job()]  # more than one = a batch
        self.cur = self.jobs[0]         # the job shown in the editor
        self.ai_pending = 0
        self.work = 0                   # documents being read / batches being made in the background
        self.filling = False            # "Generate all" is running on another thread
        self._loading: set[str] = set()  # files being read right now (so a second drop doesn't add them twice)
        # the incomplete rate sheets warned about this session, by rates.sheet_fingerprint (one edited and read
        # again is warned about again): see _check_rate_sheet
        self._sheets_warned: set[str] = set()
        # the Run sheet box was ticked or unticked by the user since the last New job: it is no longer ticked by
        # how many reporters wrote the transcripts (see _auto_runsheet)
        self._runsheet_touched = False
        self._runsheet_auto: bool | None = None  # what _auto_runsheet decided; None = as Settings.outputs say
        self._math_previewed = False  # the last preview showed "The math" (see _preview)
        self.excerpts = None  # the Excerpts window, made when first opened (see _who_ordered)

        self.setWindowTitle("YinItAgreementForm")
        if zooming.zoom() != settings.zoom:  # (main() sets it before the style sheet is made)
            zooming.set_zoom(settings.zoom)
            apply_theme(app, settings.theme)
        self._fit_minimum()
        self._build()
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
        else:
            self.resize(z(1320), z(860))
        self._fit_screen()
        self._wheel = 0  # Ctrl + wheel turned since the last zoom step (see eventFilter)
        app.installEventFilter(self)  # Ctrl + the mouse wheel zooms, and the form follows its width (eventFilter)
        self._show_case()
        self._set_status("Drop a document to begin", "")
        QTimer.singleShot(50, self._startup)

    # The editor always works on the current job.
    @property
    def case(self) -> CaseInfo:
        """The current job's case (what the editor shows)."""
        return self.cur.case

    @case.setter
    def case(self, value: CaseInfo) -> None:
        self.cur.case = value

    @property
    def proc_touched(self) -> bool:
        """The user changed the current job's proceeding types, so a new merge must keep them."""
        return self.cur.proc_touched

    @proc_touched.setter
    def proc_touched(self, value: bool) -> None:
        self.cur.proc_touched = value

    @property
    def att_touched(self) -> bool:
        """The user changed the current job's attorney table, so a new merge must keep it."""
        return self.cur.att_touched

    @att_touched.setter
    def att_touched(self, value: bool) -> None:
        self.cur.att_touched = value

    # ---------------------------------------------------------- layout
    def _build(self):
        """Builds the window: header, the left column (drop zone, job list, paste box), the scrolling form
        on the right, the Outputs box (with the Generate buttons) at the bottom, the menu and the shortcuts."""
        central = QWidget()
        central.setObjectName("central")
        # On a small screen (or a large zoom) the whole window scrolls, rather than the Outputs box and the
        # Generate buttons being pushed off the bottom of the screen
        self.page_scroll = outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QFrame.NoFrame)
        outer.setWidget(central)
        self.setCentralWidget(outer)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # header
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t = QLabel("YinItAgreementForm")
        t.setObjectName("title")
        sub = QLabel("Drop a document, check the details, fill the form.")
        sub.setObjectName("subtitle")
        title_box.addWidget(t)
        title_box.addWidget(sub)
        head.addLayout(title_box)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("status")
        head.addWidget(self.status)
        self.new_btn = new_btn = QPushButton("New job")
        new_btn.setToolTip("Clear everything and start over (Ctrl+N)")
        new_btn.clicked.connect(self.new_job)
        set_btn = QPushButton("⚙  Settings")
        set_btn.clicked.connect(self.open_settings)
        self.rec_btn = rec_btn = QPushButton("Records")
        rec_btn.setObjectName("records")  # a hue of its own: the records are apart from making the forms
        rec_btn.setToolTip("Invoices (mark them paid) and everything made so far (Ctrl+R)")
        rec_btn.clicked.connect(self.open_records)
        head.addWidget(rec_btn)
        head.addWidget(new_btn)
        head.addWidget(set_btn)
        root.addLayout(head)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)
        root.addWidget(self.busy)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # left column
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(10)
        self.drop = DropZone(self.add_files, self.add_text, self.add_qimage, self.browse)
        note_opened(self.s)
        self.drop.new_year_day = self.s.new_year_day
        ll.addWidget(self.drop)
        self.jobs_label = QLabel("Jobs")
        self.jobs_label.setObjectName("fieldLabel")
        self.jobs_label.setToolTip("⚠ = something to check before Generate (a field missing, no attorney ticked,\n"
                                   "Whose pages… or Excerpts… to choose, a speed to choose, documents whose\n"
                                   "index numbers don't match, more than two speeds on the same pages, no pages\n"
                                   "to bill...). Hover over a job to see what. ✓ = saved.")
        ll.addWidget(self.jobs_label)
        # the jobs (a batch) or the job on screen, a row each, and under a job of several documents a row per
        # document (their own pages, the right-click to move one out); see _refresh_jobs
        self.job_list = QTreeWidget()
        self.job_list.setColumnCount(len(JOB_COLS))
        self.job_list.setHeaderLabels(JOB_COLS)
        for c, name in enumerate(JOB_COLS):
            self.job_list.headerItem().setToolTip(c, JOB_TIPS[name])
        hh = self.job_list.header()
        hh.setSectionsClickable(False)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.job_list.setUniformRowHeights(True)
        self.job_list.setAllColumnsShowFocus(True)
        sized(self.job_list, "setIndentation", 14)
        self.job_list.currentItemChanged.connect(lambda item, _: self._job_selected(self._job_of(item)))
        self.job_list.itemChanged.connect(self._job_ticked)
        self.job_list.itemCollapsed.connect(lambda item: self._job_folded(item, True))
        self.job_list.itemExpanded.connect(lambda item: self._job_folded(item, False))
        self.job_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.job_list.setTextElideMode(Qt.ElideRight)
        self.job_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.job_list.customContextMenuRequested.connect(self._job_menu)
        self._collapsed: set[Job] = set()  # the jobs whose documents the user folded away (kept on a rebuild)
        ll.addWidget(self.job_list, 3)
        # what the documents say about the index number that needs a look (Job.index_number_notes): one with
        # none, or documents whose numbers don't match, with a link to keep them together
        self.index_note = QLabel("")
        self.index_note.setObjectName("pagesWarn")
        self.index_note.setWordWrap(True)
        self.index_note.setTextFormat(Qt.RichText)
        self.index_note.linkActivated.connect(lambda _: self._keep_index_numbers())
        self.index_note.setVisible(False)
        ll.addWidget(self.index_note)
        lbl = QLabel("…or paste an e-mail or notes")
        lbl.setObjectName("fieldLabel")
        ll.addWidget(lbl)
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("e.g. \"Please send the minutes for Smith v. Jones, Index 712345/2024, "
                                      "before Justice Lopez on 9/14/2026, expedited…\"")
        sized(self.paste, "setMinimumHeight", 52)
        # it takes the room left over, but doesn't ask for any: otherwise the left column's scroll area counts
        # its preferred 190 px and shows a scroll bar in a window with room to spare
        self.paste.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.paste.textChanged.connect(self._update_header_buttons)
        ll.addWidget(self.paste, 1)
        pb = QPushButton("Extract from text")
        pb.clicked.connect(self._extract_paste)
        ll.addWidget(pb)
        self.ai_label = QLabel("Checking AI…")
        self.ai_label.setObjectName("muted")
        self.ai_label.setWordWrap(True)
        self.ai_label.setTextFormat(Qt.RichText)
        self.ai_label.linkActivated.connect(self._ai_help)
        ll.addWidget(self.ai_label)
        # In a short window the column scrolls, rather than its parts being drawn over each other or the
        # Outputs box being squeezed (the column needs about 620 px in a batch).
        self.left_scroll = left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(left)
        left_scroll.setMinimumWidth(left.minimumSizeHint().width() + 12)  # (room for the scroll bar)
        split.addWidget(left_scroll)

        # right column (scrollable form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        rl = QVBoxLayout(inner)
        rl.setContentsMargins(8, 0, 8, 0)
        rl.setSpacing(12)
        scroll.setWidget(inner)
        split.addWidget(scroll)
        self.form_scroll = scroll
        self._pairs: list[tuple[QGridLayout, QWidget, QWidget]] = []  # the cards' two columns (see two_cols)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 940])

        self.rows: dict[str, FieldRow] = {}

        def row(form: QFormLayout, key: str, multiline=False):
            """The FieldRow of the case field `key`, with its label, added to `form` and kept in self.rows."""
            r = FieldRow(key, self._field_edited, multiline)
            self.rows[key] = r
            lab = QLabel(FIELD_LABELS[key])
            lab.setObjectName("fieldLabel")
            form.addRow(lab, r)
            return r

        def form_in(lay: QVBoxLayout) -> QFormLayout:
            """A form (labels right-aligned beside their fields) added to `lay`."""
            f = QFormLayout()
            f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f.setHorizontalSpacing(12)
            f.setVerticalSpacing(8)
            lay.addLayout(f)
            return f

        def two_cols(lay: QVBoxLayout) -> tuple[QVBoxLayout, QVBoxLayout]:
            """Two columns side by side in a card, one above the other when the form is too narrow for both
            (a small screen, Windows scaling or a zoom: see _place_pairs)."""
            holder = QWidget()
            g = QGridLayout(holder)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(18)
            g.setVerticalSpacing(8)
            wa, wb = QWidget(), QWidget()
            cols = []
            for w in (wa, wb):
                v = QVBoxLayout(w)
                v.setContentsMargins(0, 0, 0, 0)
                cols.append(v)
            g.addWidget(wa, 0, 0)  # side by side until _place_pairs measures the room
            g.addWidget(wb, 0, 1)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 1)
            lay.addWidget(holder)
            self._pairs.append((g, wa, wb))
            return cols[0], cols[1]

        # Case card: two columns
        c, cl = card("Case")
        colA, colB = two_cols(cl)
        fa, fb = form_in(colA), form_in(colB)
        for k in ("court", "county", "part"):
            row(fa, k)
        for k in ("index_no", "judge", "dates"):
            row(fb, k)
        fc = form_in(cl)
        row(fc, "case_name", multiline=True)
        rl.addWidget(c)

        # Proceeding card
        c, cl = card("Type of proceeding")
        pr = QHBoxLayout()
        self.proc_boxes: dict[str, QCheckBox] = {}
        for p in PROC_TYPES:
            cb = QCheckBox(p)
            cb.toggled.connect(self._proc_toggled)
            self.proc_boxes[p] = cb
            pr.addWidget(cb)
        pr.addStretch(1)
        cl.addLayout(pr)
        f = form_in(cl)
        row(f, "proc_other")
        rl.addWidget(c)

        # Minute agreement form details: the rate sheet, the one speed and one rate the form names (among the
        # speeds the invoice offers, ticked in the Invoice panel), No. of copies, the pages and the dates
        c, cl = card("Minute agreement form details")
        colA, colB = two_cols(cl)
        fa, fb = form_in(colA), form_in(colB)

        # Rate sheet, with its folder and reload buttons
        sheet_row = QHBoxLayout()
        self.sheet_box = QuietCombo()
        self.sheet_box.setMinimumContentsLength(18)
        self.sheet_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sheet_box.currentIndexChanged.connect(self._sheet_changed)
        folder = QToolButton()
        folder.setText("📂")
        folder.setToolTip("Open the rate sheets folder (add or edit CSV files, then click ⟳)")
        folder.clicked.connect(self._open_sheets_folder)
        reload_btn = QToolButton()
        reload_btn.setText("⟳")
        reload_btn.setToolTip("Reload rate sheets")
        reload_btn.clicked.connect(self._reload_sheets)
        sheet_row.addWidget(self.sheet_box, 1)
        sheet_row.addWidget(folder)
        sheet_row.addWidget(reload_btn)
        lab = QLabel("Rate sheet")
        lab.setObjectName("fieldLabel")
        fa.addRow(lab, sheet_row)
        # the one speed the minute agreement form names, at its one rate per page
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        self.delivery = QuietCombo()
        # (its items are long, "Expedite · $5.40/pg · copy $1.10 · 7 days": sized to them, the card's two
        # columns no longer fitted side by side in a window of an ordinary width)
        self.delivery.setMinimumContentsLength(20)
        self.delivery.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.delivery.setToolTip(AGREEMENT_SPEED_TIP)
        self.delivery.currentIndexChanged.connect(self._delivery_changed)
        speed_row.addWidget(self.delivery, 1)
        self.speed_reset = QToolButton()
        self.speed_reset.setText("↺")
        self.speed_reset.clicked.connect(self._speed_to_rule)
        speed_row.addWidget(self.speed_reset)
        self.speed_badge = QLabel("")
        self.speed_badge.setObjectName("badge")
        sized(self.speed_badge, "setFixedSize", 58, 20)
        self.speed_badge.setAlignment(Qt.AlignCenter)
        speed_row.addWidget(self.speed_badge)
        lab = QLabel("Speed")
        lab.setObjectName("fieldLabel")
        lab.setToolTip(AGREEMENT_SPEED_TIP)
        fa.addRow(lab, speed_row)
        # an e-mail asks for another speed than the rule's: the user is asked to choose (Job.speed_question)
        self.speed_ask_row = QHBoxLayout()
        self.speed_ask_row.setSpacing(8)
        self.speed_ask = QLabel("")
        self.speed_ask.setObjectName("speedAsk")
        self.speed_ask.setWordWrap(True)
        self.speed_ask_row.addWidget(self.speed_ask, 1)
        self.speed_keep = QPushButton("Keep")
        self.speed_keep.setToolTip("Keep the speed shown for this job (the e-mail's request is then answered)")
        self.speed_keep.clicked.connect(self._keep_speed)
        self.speed_ask_row.addWidget(self.speed_keep, 0, Qt.AlignTop)
        fa.addRow("", self.speed_ask_row)
        fa.setRowVisible(self.speed_ask_row, False)
        self.speed_form = fa
        row(fa, "rate")
        self.rate_info = QLabel("")
        self.rate_info.setObjectName("muted")
        self.rate_info.setWordWrap(True)
        fa.addRow("", self.rate_info)
        for k in ("copies", "est_pages"):
            row(fb, k)
        fb.labelForField(self.rows["est_pages"]).setToolTip(EST_PAGES_TIP)
        self.rows["est_pages"].edit.setToolTip(EST_PAGES_TIP)
        # what the number counts ("Transcript pages 378–460 · excludes the 13 word-index pages after them"), and
        # a warning when the PDF's readings of its pages disagree (Job.pages_help); hidden without a transcript
        help_box = QVBoxLayout()
        help_box.setSpacing(4)
        self.pages_help = QLabel("")
        self.pages_help.setObjectName("muted")
        self.pages_help.setWordWrap(True)
        self.pages_help.setToolTip(EST_PAGES_TIP)
        help_box.addWidget(self.pages_help)
        self.pages_warn = QLabel("")
        self.pages_warn.setObjectName("pagesWarn")
        self.pages_warn.setWordWrap(True)
        help_box.addWidget(self.pages_warn)
        fb.addRow("", help_box)
        self.pages_help_row = help_box
        self.order_form = fb
        row(fb, "delivery_date")
        # three buttons a row: six in one row would make the card too wide for a small screen
        quick = QGridLayout()
        quick.setHorizontalSpacing(4)
        quick.setVerticalSpacing(4)
        for n, (label, days, months) in enumerate((("Today", 0, 0), ("Tomorrow", 1, 0), ("1 week", 7, 0),
                                                   ("2 weeks", 14, 0), ("3 weeks", 21, 0), ("1 month", 0, 1))):
            b = QToolButton()
            b.setText(label)
            b.setObjectName("quick")
            when = quick_date(days, months)
            b.setToolTip(f"Estimated delivery {when}" + ("" if label in ("Today", "Tomorrow") else " - from today")
                         + "\n(a Saturday or Sunday becomes the Monday after)")
            b.clicked.connect(lambda _=False, d=days, m=months: self.rows["delivery_date"].choose(quick_date(d, m)))
            quick.addWidget(b, n // 3, n % 3)
        quick.setColumnStretch(3, 1)
        fb.addRow("", quick)
        row(fb, "agreement_date")
        rl.addWidget(c)
        self._fill_sheet_box()

        # Attorneys card
        c, cl = card("Attorneys  —  one form is made for each checked row")
        cl.itemAt(0).widget().setToolTip(  # (the heading)
            "Each checked attorney gets a minute agreement (and an invoice) of its own: the agreement\n"
            "shows the pages that attorney ordered, whoever wrote them; the invoice bills your pages of them.")
        self.att = QTableWidget(0, len(ATT_COLS))
        self.att.setHorizontalHeaderLabels(ATT_COLS)
        self.att.verticalHeader().setVisible(False)
        self.att.setAlternatingRowColors(True)
        self.att.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.att.setWordWrap(True)
        hh = self.att.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for i, w in enumerate([30, 140, 190, 230, 115, 105, 190, 170, 60]):
            sized(self.att, "setColumnWidth", i, w, keep=1)
        sized(self.att, "setMinimumHeight", 240)
        self.att.itemChanged.connect(self._att_changed)
        cl.addWidget(self.att)
        # two rows that may be one firm or attorney ("Smith Law" and "Smith Law Group"): the user says (Same firm,
        # Not the same), and the answer is kept for later documents too (batch.firm_questions, Settings.firm_answers),
        # or with Remember unticked only until the app is closed
        self.firm_ask_box = QWidget()
        fq = QHBoxLayout(self.firm_ask_box)
        fq.setContentsMargins(0, 0, 0, 0)
        fq.setSpacing(8)
        self.firm_ask = QLabel("")
        self.firm_ask.setObjectName("speedAsk")
        self.firm_ask.setWordWrap(True)
        fq.addWidget(self.firm_ask, 1)
        self.firm_same = QPushButton("Same firm")
        self.firm_same.setToolTip("They are one firm (or one attorney): one row, one invoice, one agreement")
        self.firm_same.clicked.connect(lambda: self._answer_firm(True))
        self.firm_apart = QPushButton("Not the same")
        self.firm_apart.setToolTip("They are two firms (or two attorneys): two rows, each billed on its own")
        self.firm_apart.clicked.connect(lambda: self._answer_firm(False))
        fq.addWidget(self.firm_same, 0, Qt.AlignTop)
        fq.addWidget(self.firm_apart, 0, Qt.AlignTop)
        self.firm_remember = QCheckBox("Remember")
        self.firm_remember.setChecked(True)
        self.firm_remember.setToolTip("Keep the answer for later documents naming these two (Settings → Invoice →\n"
                                      "Firms you answered lists them, to forget one). Unticked: for now only.")
        fq.addWidget(self.firm_remember, 0, Qt.AlignTop)
        self.firm_ask_box.setVisible(False)
        self._firm_q = None  # (the days asked about, (row, row)) of the question shown
        cl.addWidget(self.firm_ask_box)
        br = QHBoxLayout()
        add = QPushButton("+ Add attorney")
        add.clicked.connect(self._add_att_row)
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_att_rows)
        hint = QLabel("Double-click a cell to edit. Separate address lines with  /")
        hint.setObjectName("muted")
        br.addWidget(add)
        br.addWidget(rem)
        br.addStretch(1)
        br.addWidget(hint)
        cl.addLayout(br)
        rl.addWidget(c)

        # Who pays what: each invoice in short, right under the ticks that change it, kept up to date as
        # attorneys, speeds, Parties, Peripherals or Excerpts change (shown when an invoice is to be made)
        self.pays_card, cl = card("Who pays what (the invoices)")
        self.pays_title = cl.itemAt(0).widget()
        self.pays_title.setToolTip(PAYS_TIP.format(**_who_splits(self.s)))
        self.pays = QLabel("")
        self.pays.setObjectName("payTable")
        self.pays.setTextFormat(Qt.RichText)
        self.pays.setWordWrap(True)
        self.pays.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.pays.setOpenExternalLinks(False)
        self.pays.linkActivated.connect(self._show_live_math)
        self.pays.setToolTip(PAYS_TIP.format(**_who_splits(self.s)))
        cl.addWidget(self.pays)
        self._pay_firms: list = []  # the invoices the card shows (invoice.firm_invoices), for its math
        self.pays_card.setVisible(False)
        rl.addWidget(self.pays_card)

        # Who ordered what: every attorney's order of every day the invoice covers, spelled out (shown while
        # Invoice is ticked and the job has a transcript or pages to bill)
        self.orders_card, cl = card("Who ordered what")
        top = QHBoxLayout()
        self.orders_info = QLabel("")
        self.orders_info.setObjectName("muted")
        self.orders_info.setWordWrap(True)
        top.addWidget(self.orders_info, 1)
        self.orders_edit = QPushButton("Edit excerpts…")
        self.orders_edit.clicked.connect(self._edit_orders)
        top.addWidget(self.orders_edit)
        cl.addLayout(top)
        self.orders = QTableWidget(0, len(ORDER_COLS))
        self.orders.setHorizontalHeaderLabels(ORDER_COLS)
        for c, name in enumerate(ORDER_COLS):
            if name in ORDER_TIPS:
                self.orders.horizontalHeaderItem(c).setToolTip(ORDER_TIPS[name])
        self.orders.verticalHeader().setVisible(False)
        self.orders.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.orders.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.orders.setWordWrap(True)
        self.orders.horizontalHeader().setStretchLastSection(True)
        self.orders.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # as tall as its rows (see _show_orders)
        self.orders.setToolTip("Double-click a row to open Excerpts… at that day (who ordered which pages of the "
                               "case)")
        self.orders.itemDoubleClicked.connect(self._order_row_clicked)
        # the scroll bar comes and goes with the window's width: the table's height makes room for it
        self.orders.horizontalScrollBar().rangeChanged.connect(lambda *_: self._fit_orders_height())
        cl.addWidget(self.orders)
        self.orders_card.setVisible(False)
        rl.addWidget(self.orders_card)
        rl.addStretch(1)

        # footer: the Outputs box - the Generate buttons by its title, then a column per output with its own options
        foot = QFrame()
        foot.setObjectName("card")
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(16, 10, 16, 10)
        fv.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel("Outputs")
        title.setObjectName("section")
        top.addWidget(title)
        top.addStretch(1)
        self.fill_btn = QPushButton("Generate")
        self.fill_btn.setObjectName("generate")  # (the theme makes it stand out: the one thing to click at the end)
        self.fill_btn.clicked.connect(self.fill)
        top.addWidget(self.fill_btn)
        self.fill_all_btn = QPushButton("Generate all")
        self.fill_all_btn.setObjectName("generate")
        self.fill_all_btn.setToolTip("Make the ticked outputs for every ticked job (Ctrl+Shift+Enter)")
        self.fill_all_btn.clicked.connect(self.fill_all_jobs)
        top.addWidget(self.fill_all_btn)
        fv.addLayout(top)
        self.output_grid = QGridLayout()  # a panel per output, in a row or the last two stacked (_place_output_cols)
        self.output_grid.setHorizontalSpacing(10)
        self.output_grid.setVerticalSpacing(10)
        fv.addLayout(self.output_grid)
        self._output_cols: list[QWidget] = []
        self._cols_per_row = 0
        self.output_boxes: dict[str, QCheckBox] = {}
        self.output_opts: dict[str, QWidget] = {}  # each output's options, greyed out while it is unticked
        self.output_counts: dict[str, QLabel] = {}  # how many files Generate makes of each (_show_counts)

        def panel(spec) -> QFormLayout:
            """A bordered panel headed by the output's make-box (its label and tooltip: courthouses.OutputSpec),
            with a line and the count of what Generate makes of it under it (_show_counts); returns the form for
            its options (label: value rows), which gui/panels.py fills."""
            key = spec.key
            holder = QFrame()
            holder.setObjectName("outputPanel")
            col = QVBoxLayout(holder)
            col.setContentsMargins(12, 8, 12, 10)
            col.setSpacing(6)
            cb = QCheckBox(spec.label)
            cb.setObjectName("outputHead")
            cb.setChecked(key in self.s.outputs)
            cb.setToolTip(spec.tip)
            cb.toggled.connect(lambda _=False, k=key: self._outputs_changed(k))
            self.output_boxes[key] = cb
            col.addWidget(cb)
            rule = QFrame()
            rule.setObjectName("outputRule")
            rule.setFixedHeight(1)
            col.addWidget(rule)
            count = QLabel("")
            count.setObjectName("outputCount")
            count.setToolTip("What Generate makes of it for this job, as things are ticked now")
            count.setVisible(False)
            col.addWidget(count)
            self.output_counts[key] = count
            body = QWidget()
            form = QFormLayout(body)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(6)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
            col.addWidget(body)
            col.addStretch(1)
            self.output_opts[key] = body
            self._output_cols.append(holder)
            return form

        for spec in courthouses.panel_order():  # each output's panel, with its options (gui/panels.py)
            form = panel(spec)
            if spec.panel:
                resolve(spec.panel)(self, form)
        self._outputs_built = True  # (_refresh_outputs and _place_output_cols wait for every panel)
        if "invoice" in self.output_boxes:
            self._fill_invoice_speeds()
        self._place_output_cols()
        root.addWidget(foot)
        self._refresh_jobs()

        self._build_menu()
        QShortcut(QKeySequence.Paste, self, activated=self._paste_shortcut)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_job)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.browse)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.fill)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, activated=self.fill_all_jobs)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.fill)  # (the Enter of the number keys)
        QShortcut(QKeySequence("Ctrl+Shift+Enter"), self, activated=self.fill_all_jobs)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.open_records)
        QShortcut(QKeySequence("F1"), self, activated=self.show_guide)
        for keys, step in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.zoom_step(s))

    def _build_menu(self):
        """The File and Help menus (zoom has no menu: Ctrl + / Ctrl - / Ctrl 0 and Ctrl + wheel)."""
        from .dialogs import AboutDialog, FEEDBACK_URL, WEBSITE_URL
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self.new_action = m.addAction("&New job", self.new_job)  # Ctrl+N handled by the window shortcut
        m.addAction("&Open documents…", self.browse)
        m.addAction("Open a &folder of documents (batch)…", self.browse_folder)
        self.recent_menu = m.addMenu("Open r&ecent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addAction("Open &rate sheets folder", self._open_sheets_folder)
        m.addAction("&Settings…", self.open_settings)
        m.addAction("Ex&port settings (for another computer)…", self.export_settings)
        m.addAction("&Import settings…", self.import_settings)
        m.addSeparator()
        m.addAction("&Records (invoices and history)…", self.open_records)
        m.addAction("Save the invoice &spreadsheet template…", self._save_invoice_template)
        m.addAction("&Lock finished PDFs (no more changes)…", self.lock_pdfs)
        m.addSeparator()
        m.addAction("E&xit", self.close)
        m = mb.addMenu("&Help")
        m.addAction("&How to use YinItAgreementForm…\tF1", self.show_guide)
        m.addAction("The &website (pictures and the latest version)", lambda: open_url(WEBSITE_URL))
        m.addAction("Check for a &newer version", lambda: self._check_update(asked=True))
        m.addSeparator()
        m.addAction("Set up the &AI helper (Ollama)…", self._ai_help)
        m.addAction("Send &feedback…", lambda: open_url(FEEDBACK_URL))
        m.addAction("Open the &log folder", self._open_log_folder)
        m.addAction("&Copy details for a problem report", self._copy_diagnostics)
        m.addSeparator()
        m.addAction("&About YinItAgreementForm", lambda: AboutDialog(self).exec())

    # --------------------------------------------------------- startup
    def _startup(self):
        """Runs once the window is on screen: the welcome questions on the first run (no name yet; asked once),
        then the AI check, the day's backup of the records, once a day the look for a newer version, and a warning
        when the rate sheet in use is incomplete and the user hasn't said to use it as it is (_check_rate_sheet).
        A settings file that couldn't be read is said so instead of the welcome: the app runs on the defaults, and
        nothing is written over the file until the user presses Save in Settings or imports settings
        (Settings.save(force=True))."""
        if self.s.unreadable:
            QMessageBox.warning(self, "Your settings couldn't be read",
                                "Your settings file couldn't be read (it may be damaged, or held by another "
                                "program), so the app starts with its defaults for now. Nothing is saved over it "
                                "until you press Save in Settings, which replaces it with the settings you see "
                                f"there; to keep what it holds, close the app and look at the file first:\n\n"
                                f"{self.s.path}")
        elif not self.s.profile.name and not self.s.welcomed:
            self.s.welcomed = True
            self.welcome()
        self._check_ai()
        self.runner.start(backup_records, backup_folder(self.s))  # off the UI thread: a big database takes a moment
        if self.s.check_updates and self.s.update_checked != date.today().isoformat():
            self._check_update()
        self._check_rate_sheet()  # (edited since the app last ran, or incomplete and never answered)

    def welcome(self) -> None:
        """The first-run questions (name, address, rate sheet, where files go). Skipped: asked no more;
        Settings has all of it."""
        from .preview import WelcomeDialog
        if WelcomeDialog(self.s, self).exec():
            self._settings_changed()
        else:
            self._save_settings()

    def _save_settings(self) -> None:
        """Saves the settings; a problem is logged, not shown (they are saved again when the window closes)."""
        try:
            self.s.save()
        except OSError as e:
            logfile.error("could not save the settings", e)

    def _check_update(self, asked: bool = False) -> None:
        """Asks GitHub, in the background, whether there is a newer version (see update.py) and shows a line
        with a link in the status bar when there is. asked: Help -> Check for a newer version, which also says
        so when there is none, or when GitHub can't be reached; the daily look says nothing then."""
        from .. import update

        def done(found):
            self.s.update_checked = date.today().isoformat()
            self._save_settings()
            if found:
                self._show_update(*found)
                if asked:
                    QMessageBox.information(self, "A newer version", f"Version {found[0]} is available. The link "
                                            "at the bottom of the window opens its download page.")
            elif asked:
                QMessageBox.information(self, "Up to date", f"You have the latest version ({update.__version__}).")

        def failed(msg):
            if asked:
                QMessageBox.information(self, "Could not check", "Could not reach GitHub to look for a newer "
                                        f"version (are you online?).\n\n{msg}")

        self.runner.start(update.newer, on_done=done, on_error=failed)

    def _show_update(self, version: str, page: str) -> None:
        """A newer version is out: a line in the status bar that stays, with a link to its download page."""
        if getattr(self, "update_label", None) is None:
            self.update_label = QLabel()
            self.update_label.linkActivated.connect(open_url)
            self.statusBar().addPermanentWidget(self.update_label)
        self.update_label.setText(f'Version {version} is available: <a href="{page}">get it</a>')
        self.update_label.setToolTip("Opens the download page in your browser. Nothing is installed by itself.\n"
                                     "Settings → Options turns this daily check off.")

    # ------------------------------------------------------ recent files, settings to and from a file
    def _fill_recent(self) -> None:
        """File -> Open recent, filled as it opens: the documents and folders opened lately, newest first
        (one that is gone is greyed out)."""
        m = self.recent_menu
        m.clear()
        for p in self.s.recent_files:
            path = Path(p)
            act = m.addAction(f"{path.name}   ·   {path.parent}".replace("&", "&&"),
                              lambda p=p: self.add_files([p], batch=Path(p).is_dir()))
            act.setEnabled(path.exists())
        if not self.s.recent_files:
            m.addAction("(nothing opened yet)").setEnabled(False)
        m.addSeparator()
        m.addAction("Clear this list", lambda: self._set_opt("recent_files", [])).setEnabled(bool(self.s.recent_files))

    def export_settings(self) -> None:
        """File -> Export settings: saves the settings and the rate sheets as one file (Settings.export_to),
        after asking whether the user's own details go into it (yes for another computer of theirs, no for a
        colleague)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Export settings")
        box.setText("Include your personal details in the file?\n\n"
                    "They are: your name, title, address, phone, fax, e-mail, website and initials (My info), the "
                    "payment text of your invoices, the names of other reporters (Run sheet) and your folders.\n\n"
                    "•  Include them to move to another computer of your own.\n"
                    "•  Leave them out to give the file to a colleague: they keep their own details, and get "
                    "your options, invoice wording and rate sheets.\n\n"
                    "Your signature picture and your records are never in the file.")
        with_mine = box.addButton("Include my details", QMessageBox.YesRole)
        without = box.addButton("Leave them out", QMessageBox.NoRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(without)  # (the careful choice, should Enter be pressed without reading)
        box.exec()
        if box.clickedButton() not in (with_mine, without):
            return
        personal = box.clickedButton() is with_mine
        name = "YinIt settings.json" if personal else "YinIt settings (no personal details).json"
        dest, _ = QFileDialog.getSaveFileName(self, "Export settings", str(Path.home() / "Documents" / name),
                                              "Settings file (*.json)")
        if not dest:
            return
        try:
            self.s.export_to(dest, personal=personal)
        except OSError as e:
            show_save_error(self, e, "Could not export the settings")
            return
        holds = ("your details (name, address, contact, payment text), your options and your rate sheets"
                 if personal else "your options, invoice wording and rate sheets, and none of your personal details")
        QMessageBox.information(self, "Settings exported", f"Saved as\n{dest}\n\nOn the other computer: File → "
                                f"Import settings.\n\nThe file holds {holds}. Your signature picture and your "
                                "records are not in it.")

    def import_settings(self) -> None:
        """File -> Import settings: replaces the settings with those of a file made by Export settings, after
        asking; the settings as they were are kept next to the settings file as "settings before import.json"."""
        if self._batch_running():
            return
        src, _ = QFileDialog.getOpenFileName(self, "Import settings", str(Path.home() / "Documents"),
                                             "Settings file (*.json)")
        if not src:
            return
        try:
            new = Settings.import_from(src, self.s)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Could not import", str(e))
            return
        who = (f"reporter: {new.profile.name or '(no name)'}" if new.imported_personal else
               "it has no personal details: your name, contact details, payment text and folders stay as they are")
        if QMessageBox.question(self, "Import settings", f"Replace your settings with those of this file "
                                f"({who})?\n\nYour settings as they are now are kept as "
                                "\"settings before import.json\" in the app's settings folder. "
                                "Your records are not changed.") != QMessageBox.Yes:
            return
        sheet = self.s.sheet().name  # (another sheet imported, and incomplete: its warning can go back to this one)
        try:
            new.write_imported_sheets()  # (only now: answering No leaves the rate sheet folder as it was)
            if self.s.path.exists():
                self.s.path.with_name("settings before import.json").write_bytes(self.s.path.read_bytes())
            # the Settings object is shared (the Records window, the AI helper): filled in place
            for name in Settings.__dataclass_fields__:
                setattr(self.s, name, getattr(new, name))
            self.s.save(force=True)  # (the user's own choice: written even over a settings file that couldn't be read)
        except OSError as e:
            show_save_error(self, e, "Could not import the settings")
            return
        self._settings_changed(sheet=sheet)
        self._toast("Settings imported.")

    def show_guide(self):
        """Help -> How to use (F1): what goes in, what comes out, the steps and tips."""
        from .dialogs import GuideDialog
        GuideDialog(self).exec()

    def _ai_help(self, _link=""):
        """Shows the Ollama setup help, then checks the AI again (a model may have been installed)."""
        from .dialogs import OllamaHelpDialog
        OllamaHelpDialog(self.s.ollama_model, self.s.ollama_host, self).exec()
        self._check_ai()

    def _ai_text(self, text: str):
        """Shows the AI's state under the paste box. A longer or shorter line changes the left column's height,
        so the drop zone is sized again."""
        self.ai_label.setText(text)
        self._size_drop_later()

    def _check_ai(self):
        """Asks Ollama in the background whether the model is ready and shows the answer under the paste box.
        Documents of a single job dropped while it was checking are then sent to the AI."""
        if not self.s.use_ai:
            self.ai_ok = False
            self._ai_text("AI is off (Settings → AI). Using rules only.")
            return
        self._ai_text("Checking AI…")
        self.ai = ai = OllamaExtractor(self.s)

        def done(res):
            if ai is not self.ai or not self.s.use_ai:  # settings changed since (another model, or AI off)
                return
            self.ai_ok, msg = res
            link = "" if self.ai_ok else '  <a href="setup">How to set up</a>'
            self._ai_text(("● " if self.ai_ok else "○ ") + html.escape(msg, quote=False) + link)  # (the label is rich text)
            # inputs dropped while the check was still running were not sent to the AI
            waiting = [d for d in self.cur.docs if d.ai is None]
            if self.ai_ok and waiting and self.ai_pending == 0 and len(self.jobs) == 1:
                self._maybe_ai(self.cur, waiting)

        self.runner.start(self.ai.status, on_done=done, on_error=lambda m: done((False, m)))

    # ------------------------------------------------------ inputs
    def browse(self):
        """Asks for one or more documents and reads them (Ctrl+O)."""
        files, _ = QFileDialog.getOpenFileNames(self, "Choose document(s)", "", FILE_FILTER)
        if files:
            self.add_files(files)

    def browse_folder(self):
        """Asks for a folder and reads every document in it as a batch."""
        folder =QFileDialog.getExistingDirectory(self, "Choose a folder of documents")
        if folder:
            self.add_files([folder], batch=True)

    def _paste_shortcut(self):
        """Ctrl+V. In a text box it pastes as usual; anywhere else the clipboard's files, picture or text
        are read as a new input."""
        if self.focusWidget() in (self.paste,) or isinstance(self.focusWidget(), (QLineEdit, QPlainTextEdit)):
            fw = self.focusWidget()
            fw.paste()
            return
        md = QApplication.clipboard().mimeData()
        if not handle_mime(md, self.add_files, self.add_text, self.add_qimage):
            self._toast("Nothing to paste.")

    def _extract_paste(self):
        """Extract from text: reads what is in the paste box as an input."""
        text = self.paste.toPlainText().strip()
        if text:
            self.add_text(text)

    def add_text(self, text: str):
        """Reads pasted or dropped text as an input named "Pasted text 1", "Pasted text 2"… and shows it in the
        paste box."""
        n =sum(1 for j in self.jobs for d in j.docs if d.ing.name.startswith("Pasted text")) + 1
        self._ingest([lambda: ingest_text(text, f"Pasted text {n}")], [f"Pasted text {n}"])
        if self.paste.toPlainText().strip() != text.strip():
            self.paste.setPlainText(text)

    def add_qimage(self, qimg):
        """Reads a pasted or dropped picture (a QImage), e.g. a screenshot of an e-mail, with OCR."""
        from PIL import Image
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        qimg.save(buf, "PNG")
        import io
        pil = Image.open(io.BytesIO(bytes(buf.data())))
        self._ingest([lambda: ingest_pil(pil, "Pasted image")], ["Pasted image"])

    def add_files(self, paths: list[str], batch: bool = False, same_job: bool = False):
        """Reads files, and the documents in folders. Files already loaded or still being read are skipped.
        Several documents about different cases (or batch=True) start a batch; same_job: they all go to the job
        on screen whatever they say (a past job opened again: that is how it was put together). What was opened
        is kept for File -> Open recent."""
        gen = self.gen
        for p in reversed(paths):
            self.s.remember_file(p)
        self._save_settings()

        def find():  # off the UI thread: a big folder takes a while to go through
            return [(p, _path_key(p)) for p in expand_paths(paths)]

        def found(everything):
            if not self._work_done(gen):
                return
            # judged now, not when dropped: the same file dropped twice in a row is still being read
            loaded = {_path_key(d.path) for j in self.jobs for d in j.docs if d.path} | self._loading
            fresh = [(p, key) for p, key in everything if key not in loaded]
            if len(fresh) < len(everything):
                self._toast(f"Skipped {len(everything) - len(fresh)} document(s) that are already loaded.")
            if fresh:
                files = [p for p, _ in fresh]
                self._ingest([(lambda p=p: ingest_file(p)) for p in files], [Path(p).name for p in files],
                             files, batch, {key for _, key in fresh}, same_job)
            else:
                self._update_status()
                if not everything:
                    self._toast("No documents found there.")

        def failed(msg):
            if not self._work_done(gen):
                return
            self._update_status()
            QMessageBox.warning(self, "Could not open that", msg)

        self.work += 1
        if any(Path(p).is_dir() for p in paths):
            self._set_status("Looking for documents…", "busy")
            self.busy.setVisible(True)
        self.runner.start(find, on_done=found, on_error=failed)

    def _work_done(self, gen: int) -> bool:
        """A background read or batch has ended. False when "New job" was clicked since it started:
        its result is then dropped (and the counters were already reset)."""
        if gen != self.gen:
            return False
        self.work = max(0, self.work - 1)
        self._idle()
        return True

    def _ingest(self, loaders, names, paths=None, batch=False, keys=frozenset(), same_job=False):
        """Reads inputs in the background and adds them to the jobs.

        loaders: one function per input that returns the Ingested document; names: what to call each in
        messages; paths: the files (None for pasted text or pictures); keys: the files' _path_key, held in
        self._loading until the read ends. With a single job and documents all about one case, they are
        added to that job (with same_job, whatever they are about); otherwise (or with batch=True) they are
        sorted into jobs by case and date. A cell of the attorney table being typed in when the read ends is
        taken as typed, and opened again afterwards while the same job is on screen (_resume_edit).
        """
        # the reading thread gets its own copy of the settings: Settings may change them meanwhile
        gen, target, s = self.gen, self.cur, deepcopy(self.s)
        self.work += 1
        self._loading |= keys
        self._set_status(f"Reading {names[0]}…" if len(names) == 1 else f"Reading {len(names)} documents…", "busy")
        self.busy.setVisible(True)

        def work(progress):
            return read_loaders(loaders, names, s, paths, progress)

        def step(i, n, name):
            if gen == self.gen and n > 1:
                self.busy.setRange(0, n)
                self.busy.setValue(i)
                self._set_status(f"Reading {i + 1} of {n}:  {name[:40]}", "busy")

        def done(result):
            if not self._work_done(gen):
                return
            self._loading -= keys
            docs, errors = result
            for d in docs:
                for w in d.ing.warnings:
                    self._toast(w)
            if docs:
                typing = self._sync_from_ui()
                shown = self.cur
                one_job = len(self.jobs) == 1 and target in self.jobs and not batch
                if one_job and (same_job or len(docs) == 1 or len(group(docs, self.s)) == 1):
                    # the usual way: everything dropped is about the job on screen
                    target.docs += docs
                    target.unbill()  # new documents may mean new pages to bill
                    remerge(target, self.s)
                    self._auto_runsheet()
                    self._show_job()
                    self._maybe_ai(target, docs)
                else:
                    before = [j for j in self.jobs if not j.is_empty()]
                    count = len(before)
                    self.jobs = group(docs, self.s, before) or [Job()]
                    if self.cur not in self.jobs:
                        self.cur = self.jobs[0]
                    self._auto_runsheet()
                    self._refresh_jobs()
                    self._show_job()
                    new = len(self.jobs) - count
                    self._toast(f"{len(docs)} document(s) read:  {new} new job(s), "
                                f"{len(self.jobs)} in total.")
                if self.cur is shown:  # (still the job the user was typing in)
                    self._resume_edit(typing)
            else:
                self._update_status()
            log.info("read %d of %d document(s)%s", len(docs), len(loaders), " (batch)" if batch else "")
            if errors:
                self._unreadable(errors, len(loaders))

        def failed(msg):
            if not self._work_done(gen):
                return
            self._loading -= keys
            self._set_status("Could not read that input", "warn")
            QMessageBox.warning(self, "Could not read input", msg)

        self.runner.start(work, on_done=done, on_error=failed, on_progress=step)

    def _unreadable(self, errors: list[str], total: int):
        """Says which inputs could not be read: the error itself for a single input, else a list (first 8)."""
        if total == 1:
            self._set_status("Could not read that input", "warn")
            QMessageBox.warning(self, "Could not read input", errors[0])
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Some documents could not be read")
        box.setText(f"{len(errors)} of {total} documents could not be read and were left out:\n\n"
                    + "\n".join(e[:110] for e in errors[:8]) + ("\n…" if len(errors) > 8 else ""))
        box.setDetailedText("\n".join(errors))
        box.exec()

    def _maybe_ai(self, job: Job, docs: list):
        """Asks the model about documents added to a single job (batches use the rules only): pictures always,
        e-mails and text when Settings say so, PDFs only while a required field is still blank (or the case name
        only a suggestion from the records). The questions
        are asked one at a time (ai_runner, on ai_pool). Each answer merges the job the document is in by then
        again (a later read may have merged its job into another), unless "New job" was clicked, the document
        was removed or the AI turned off meanwhile."""
        if not (self.s.use_ai and self.ai_ok):
            return
        todo = []
        for d in docs:
            if d.ing.kind == "image":
                todo.append(d)
            elif d.ing.kind in ("email", "text") and self.s.ai_for_text:
                todo.append(d)
            elif d.ing.kind == "pdf" and (job.case.missing_required()
                                          or job.case.fields["case_name"].source == SRC_RECORDS):
                todo.append(d)
        if not todo:
            return
        gen = self.gen
        self.ai_pending += len(todo)
        self.busy.setVisible(True)
        self._update_status()
        for d in todo:
            def done(ex, d=d):
                if gen != self.gen:
                    return
                self.ai_pending -= 1
                holder = next((j for j in self.jobs if any(x is d for x in j.docs)), None)
                if holder is not None and self.s.use_ai:
                    d.ai = ex
                    self._remerge(holder)
                self._update_status()

            def failed(msg, d=d):
                if gen != self.gen:
                    return
                self.ai_pending -= 1
                self._ai_text(f"○ AI failed on {html.escape(d.ing.name, quote=False)}: {html.escape(msg[:120], quote=False)}")
                self._update_status()

            self.ai_runner.start(self.ai.extract, d.ing, on_done=done, on_error=failed)

    # ------------------------------------------------------------ jobs
    # The list on the left: a row per job of a batch (or the job on screen alone), and under a job of several
    # documents a row per document. The rows keep their job (and document) in these roles.
    _JOB, _DOC, _DOCS = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2

    def _shown_jobs(self) -> list[Job]:
        """The jobs the list shows, a row each: every job of a batch; the job on screen alone once it has
        documents (nothing dropped yet: no row)."""
        if len(self.jobs) > 1:
            return self.jobs
        return [self.cur] if self.cur.docs else []

    def _job_of(self, item: QTreeWidgetItem | None) -> Job | None:
        """The job of a row of the list: its own, or the job of a document's row; None when it is gone."""
        if item is None:
            return None
        job = (item.parent() or item).data(0, self._JOB)
        return job if job in self.jobs else None

    def _job_item(self, job: Job) -> QTreeWidgetItem | None:
        """The row of a job in the list (None when it has none)."""
        lst = self.job_list
        return next((lst.topLevelItem(i) for i in range(lst.topLevelItemCount())
                     if lst.topLevelItem(i).data(0, self._JOB) is job), None)

    def _select_job(self, job: Job) -> None:
        """Selects a job's row, which shows the job (_job_selected)."""
        item = self._job_item(job)
        if item is not None:
            self.job_list.setCurrentItem(item)

    def _job_text(self, job: Job) -> str:
        """A job's name in the list (its first cell), e.g. "✓ Jane Roe v. Sam Poe" (the column cuts it short).
        ✓ = saved (or billed on another day's invoice), ⚠ = it failed or needs a look: something missing for
        the outputs ticked, or an invoice held until a choice is made (its tooltip says which, see _job_tip)."""
        done = (job.saved or job.invoiced) and not job.error
        mark = "✓ " if done else "⚠ " if job.to_check(self._outputs()) else ""
        return mark + job.title()

    @staticmethod
    def _job_date(job: Job) -> str:
        """A job's date in the list: the first date of its Date(s) of Minutes ("6/3/2026", "6/3/2026 +1" with
        another), or the field as it is when no date is read in it."""
        text = job.case.get("dates")
        found = list(dict.fromkeys(d for _, _, d in find_dates(text)))
        return found[0] + (f" +{len(found) - 1}" if len(found) > 1 else "") if found else text

    def _job_tip(self, job: Job) -> str:
        """A job's tooltip: the case, its dates and index number in full, its documents, the pages typed (when
        Yours shows them), what to check (the reasons for its ⚠, see Job.issues, the index numbers that don't
        match among them), the documents with no index number (Job.index_number_notes), who the form is for and
        what was saved."""
        head = [job.case.get("case_name") or job.title(), job.case.get("dates"), job.case.get("index_no")]
        tip = ["  ·  ".join(dict.fromkeys(v for v in head if v))]
        names = [d.ing.name for d in job.docs]
        tip += ([f"{len(names)} documents:"] if len(names) > 1 else []) + (names or ["(no documents)"])
        if job.pages_typed():
            tip.append(f"Yours: the {job.invoice_pages()} pages typed in Est. number of pages")
        issues = job.issues(self._outputs())
        if issues:
            tip.append("To check before Generate:")
            tip += ["⚠ " + p for p in issues]
        tip += [n for n in job.index_number_notes() if not n.startswith(MISMATCH_LEAD)]  # (a mismatch: in issues)
        who = [a.label() for a in job.case.attorneys if a.checked]
        tip.append("Form for: " + ("; ".join(who) if who else "(blank attorney block)"))
        tip += [f"Saved: {p.name}" for p in job.saved]
        if job.invoiced:
            tip.append("Invoiced: Generate all won't bill this day again")
        return "\n".join(tip)

    def _refresh_jobs(self):
        """Rebuilds the list on the left: a row per job (_shown_jobs), its documents' rows under it (_sync_job_row),
        then their text (_refresh_job_labels), the job on screen selected. A batch gets a tick per job, Generate
        all and "Generate this job"; a single job a short list, leaving room for the big picture (see
        _size_drop). The jobs whose documents the user folded away stay folded."""
        multi = len(self.jobs) > 1
        self.fill_all_btn.setVisible(multi)
        # (set here, not with sized(): a zoom change must not give a single job the batch's height)
        self.job_list.setMinimumHeight(z(140) if multi else z(56))
        self.job_list.setMaximumHeight(16777215 if multi else z(110))
        self._size_drop()
        self.paste.setMaximumHeight(z(64) if multi else 16777215)
        self.fill_btn.setText("Generate this job" if multi else "Generate")
        self.fill_btn.setToolTip("Make the ticked outputs for the day shown alone: its own agreements, MOFR and "
                                 "invoice (Ctrl+Enter)" if multi else "Make the ticked outputs (Ctrl+Enter)")
        self.fill_btn.setObjectName("" if multi else "generate")  # (Generate all is the one to click then)
        repolish(self.fill_btn)
        self._collapsed &= set(self.jobs)
        lst = self.job_list
        blocked = lst.blockSignals(True)
        lst.clear()
        for job in self._shown_jobs():
            item = QTreeWidgetItem(lst, [""] * len(JOB_COLS))
            item.setData(0, self._JOB, job)
            for c in (JOB_PAGES, JOB_YOURS):
                item.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            self._sync_job_row(job, item)
        cur = self._job_item(self.cur)
        if cur is not None:
            lst.setCurrentItem(cur)
        lst.blockSignals(blocked)
        self._refresh_job_labels()
        self._size_drop_later()  # measured again once the column's new contents are laid out

    def _sync_job_row(self, job: Job, item: QTreeWidgetItem | None = None) -> None:
        """Puts a row per document under a job of several (none under a job of one), rebuilt only when its
        documents changed: a click that shows a job never deletes the row clicked. When a document's row was the
        current one, the job's row becomes it. item: the job's row (_refresh_jobs has it); found here when
        not given, and the whole list is rebuilt when the job's row is missing or shouldn't be there (a single
        job's row comes with its first document and goes with its last)."""
        if item is None:
            item = self._job_item(job)
            if (item is None) == (job in self._shown_jobs()):
                self._refresh_jobs()
                return
            if item is None:
                return
        docs = job.docs if len(job.docs) > 1 else []
        keys = " ".join(str(id(d)) for d in docs)  # (text: Qt would hand a tuple back as a list)
        if item.data(0, self._DOCS) == keys and item.childCount() == len(docs):
            return
        lst = self.job_list
        blocked = lst.blockSignals(True)
        was_child = lst.currentItem() is not None and lst.currentItem().parent() is item
        item.takeChildren()
        for d in docs:
            child = QTreeWidgetItem(item, [""] * len(JOB_COLS))
            child.setData(0, self._DOC, d)
            for c in (JOB_PAGES, JOB_YOURS):
                child.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        item.setData(0, self._DOCS, keys)
        item.setExpanded(job not in self._collapsed)
        if was_child:
            lst.setCurrentItem(item)
        # the strip for the arrows only when a job has documents to fold: room for the case's name otherwise
        lst.setRootIsDecorated(any(lst.topLevelItem(i).childCount() for i in range(lst.topLevelItemCount())))
        lst.blockSignals(blocked)
        self._label_docs(job, item)

    def _job_folded(self, item: QTreeWidgetItem, folded: bool) -> None:
        """A job's documents were folded away (or shown again) by the user: kept so when the list is rebuilt."""
        job = self._job_of(item)
        if job is not None:
            (self._collapsed.add if folded else self._collapsed.discard)(job)

    def _refresh_job_labels(self, counts: dict | None = None):
        """Updates each row of the list (the job's name and date, its transcripts' pages and the user's, its
        tooltip, its tick and its documents' rows: _label_docs), the heading above it and the Generate all button
        (its number counts every file, the math PDFs too). Pages and Yours are hidden when no job has a transcript
        or pages typed in. A single job has no tick, and its heading says "This job".
        counts: batch.output_counts of the jobs Generate all makes, when just counted (_show_counts)."""
        multi = len(self.jobs) > 1
        lst = self.job_list
        blocked = lst.blockSignals(True)  # setting the ticks must not look like the user clicking them
        pages_shown = False
        for i in range(lst.topLevelItemCount()):
            item = lst.topLevelItem(i)
            job = self._job_of(item)
            if job is None:
                continue
            has = bool(job.transcripts()) or job.pages_typed()
            pages_shown |= has
            yours = job.your_pages()
            cells = {0: self._job_text(job), JOB_DATE: self._job_date(job),
                     JOB_PAGES: str(job.transcript_pages()) if job.transcripts() else "",
                     JOB_YOURS: ("?" if yours is None else str(yours)) if has else ""}
            tip = self._job_tip(job)
            for c, text in cells.items():
                item.setText(c, text)
                item.setToolTip(c, tip)
            if multi:
                item.setCheckState(0, Qt.Checked if job.include else Qt.Unchecked)
            self._label_docs(job, item)
        lst.setColumnHidden(JOB_PAGES, not pages_shown)
        lst.setColumnHidden(JOB_YOURS, not pages_shown)
        lst.blockSignals(blocked)
        if not multi:
            self.jobs_label.setText("This job")
            return
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        outputs = self._outputs()
        need = sum(1 for j in self.jobs if j.to_check(outputs))
        saved = sum(1 for j in self.jobs if j.saved or j.invoiced)
        text = f"Jobs: {len(self.jobs)}"
        if need:
            text += f"  ·  {need} to check (⚠)"
        if saved:
            text += f"  ·  {saved} saved (✓)"
        self.jobs_label.setText(text)
        # days of a case share a run sheet, invoices too
        files = sum(counts.values()) if counts is not None else files_to_make(chosen, outputs, self.s)
        self.fill_all_btn.setText(f"Generate all  ({plural(files, 'file')})")
        self.fill_all_btn.setEnabled(bool(chosen) and bool(outputs) and not self.filling)

    def _label_docs(self, job: Job, item: QTreeWidgetItem) -> None:
        """The rows of a job's documents (under it, for a job of several): the name, a transcript's own day or
        what kind of document it is (_kind_word), a transcript's pages and the user's of them (Job.your_pages_of).
        A ⚠ before a document with no index number (as Job.index_number_notes lists them), and, while Generate
        would ask about them (Job.index_number_asks), before one whose index number isn't the job's: the job's are
        those of the group of documents (Job.index_number_mismatch) with the index number the case shows, else
        the first group's. Its tooltip says why above the document's text."""
        if not item.childCount():
            return
        from ..batch import index_key
        mismatch = job.index_number_mismatch() if job.index_number_asks() else []
        own = index_key(job.case.get("index_no"))
        ref = next((g for g in mismatch if any(own in d.index_numbers() for d in g)), mismatch[0]) \
            if mismatch else []
        odd = {id(d) for g in mismatch if g is not ref for d in g}
        theirs = ", ".join(sorted({n for d in ref for n in d.index_numbers()}))
        transcripts = {id(d) for d in job.transcripts()}
        lst = self.job_list
        blocked = lst.blockSignals(True)
        for i in range(item.childCount()):
            child = item.child(i)
            d = child.data(0, self._DOC)
            if d is None:
                continue
            why = ("No index number found in this document" if not d.index_numbers() else
                   f"Its index number ({', '.join(sorted(d.index_numbers()))}) isn't this job's ({theirs})"
                   if id(d) in odd else "")
            if id(d) in transcripts:
                yours = job.your_pages_of(d)
                cells = [job.day_text(d) or "transcript", str(transcript_pages(d.ing)),
                         "?" if yours is None else str(yours)]
            else:
                cells = [_kind_word(d), "", ""]
            child.setText(0, ("⚠ " if why else "") + d.ing.name)
            for c, text in zip((JOB_DATE, JOB_PAGES, JOB_YOURS), cells):
                child.setText(c, text)
            text = d.ing.text[:1500] or "(no text)"
            if d.ing.ocr_used:
                text = "(read with OCR)\n" + text
            tip = f"⚠ {why}\n\n{text}" if why else text
            for c in range(len(JOB_COLS)):
                child.setToolTip(c, tip)
        lst.blockSignals(blocked)

    def _show_job(self):
        """Puts the current job in the editor, and its documents in the list (_sync_job_row)."""
        self._sync_job_row(self.cur)
        self._show_case()
        self._update_status()

    def _show_index_notes(self) -> None:
        """What the current job's documents say about the index number that needs a look (Job.index_number_notes),
        under the list: a document with none, documents whose numbers don't match (with "Keep them together");
        and a ⚠ on each such document's row (_label_docs), its tooltip saying why. Kept up to date with the case
        (_show_case), as the AI's answer can bring a number."""
        job = self.cur
        notes = job.index_number_notes()
        mismatch = job.index_number_mismatch() if job.index_number_asks() else []
        item = self._job_item(job)
        if item is not None:
            self._label_docs(job, item)
        lines = ["⚠ " + html.escape(n) for n in notes]
        if mismatch:
            lines.append("<a href='keep'>Keep them together</a> (or right-click a document → Move to a job of its "
                         "own)")
        self.index_note.setText("<br>".join(lines))
        self.index_note.setVisible(bool(lines))

    def _keep_index_numbers(self) -> None:
        """"Keep them together": the job's documents are about one case though their index numbers don't match
        (Job.index_numbers_ok); Generate won't ask, until another document comes."""
        self.cur.index_numbers_ok = [d.key() for d in self.cur.docs]
        self._show_index_notes()
        self._refresh_job_labels()

    def _job_selected(self, job: Job | None):
        """A row was clicked in the list (a job, or one of its documents): keep the edits made to the job on
        screen, then show that job."""
        if job is None or job is self.cur:
            return
        self._sync_from_ui()
        self.cur = job
        self._show_job()

    def _job_ticked(self, item: QTreeWidgetItem, column: int = 0):
        """A job's tick changed: Generate all includes or leaves out that job (and the days it bills together,
        shown in the Invoice panel and the Who ordered what card, change with it)."""
        job = self._job_of(item)
        if column != 0 or item.parent() is not None or job is None or len(self.jobs) < 2:
            return
        job.include = item.checkState(0) == Qt.Checked
        self._refresh_outputs()
        self._refresh_job_labels()

    def _job_menu(self, pos):
        """Right-click on the list. A document's row: show its text, move it to a job of its own, remove it. A
        job's row: show the text of its document, for a job of one; Remove this job in a batch (a single job:
        Remove from job, for its one document); Tick all and Untick all in a batch. The jobs can change while the
        menu is open (a read that ends merges them): what is chosen acts on the job and document it was opened
        on, if they are still there (_remove_doc, _remove_job)."""
        item = self.job_list.itemAt(pos)
        job = self._job_of(item)
        doc = item.data(0, self._DOC) if item is not None and item.parent() is not None else None
        multi = len(self.jobs) > 1
        if job is not None and doc is None and len(job.docs) == 1:
            doc = job.docs[0]  # (a job of one document: its row is the document's too)
        m = QMenu(self)
        view = m.addAction("Show extracted text") if doc is not None else None
        split = m.addAction("Move to a job of its own") if doc is not None and len(job.docs) > 1 else None
        rem_doc = m.addAction("Remove from job") if doc is not None and (item.parent() is not None or not multi) \
            else None
        rem = m.addAction("Remove this job") if job is not None and multi and item.parent() is None else None
        tick = untick = None
        if multi:
            m.addSeparator()
            tick = m.addAction("Tick all")
            untick = m.addAction("Untick all")
        if m.isEmpty():
            return
        act = m.exec(self.job_list.viewport().mapToGlobal(pos))
        if act is None:
            return
        if act in (tick, untick):
            for j in self.jobs:
                j.include = act == tick
            self._refresh_outputs()  # the days Generate all bills together
            self._refresh_job_labels()
        elif act == view:
            self._doc_text(doc)
        elif act in (split, rem_doc):
            self._remove_doc(job, doc, own_job=act == split)
        elif act == rem:
            self._remove_job(job)

    def _doc_text(self, doc: Doc) -> None:
        """Show extracted text: the text read from a document, in a box."""
        box = QMessageBox(self)
        box.setWindowTitle(doc.ing.name)
        box.setText("Text read from this document:")
        box.setDetailedText(doc.ing.text or "(no text)")
        box.exec()

    def _remove_doc(self, job: Job, doc: Doc, own_job: bool = False) -> None:
        """Remove from job, or (own_job) Move to a job of its own, right after it: a document taken out of its
        job, which is merged again without it. The job may be another than the one on screen. A job of a batch
        left with no document goes, as with Remove this job; a single job stays, empty."""
        if job not in self.jobs or doc not in job.docs:  # (the list may have changed while the menu was open)
            return
        self._sync_from_ui()
        job.docs.remove(doc)
        remerge(job, self.s)
        if own_job:
            new = Job(docs=[doc], batch=True)
            remerge(new, self.s)
            self.jobs.insert(self.jobs.index(job) + 1, new)
        elif not job.docs and len(self.jobs) > 1:
            # its last document, in a batch (a menu opened on the job alone, then a read made it a batch): the
            # job goes, as with Remove this job, rather than stay in the list empty
            self.jobs.remove(job)
            if self.cur not in self.jobs:
                self.cur = self.jobs[0]
        self._auto_runsheet()
        self._refresh_jobs()
        self._show_job()

    def _remove_job(self, job: Job) -> None:
        """Remove this job: a job of a batch taken out of the list (and of Generate all); the last one leaves a
        blank job."""
        if job not in self.jobs:  # (the list may have changed while the menu was open)
            return
        self._sync_from_ui()
        self.jobs.remove(job)
        if not self.jobs:
            self.jobs = [Job()]
        if self.cur not in self.jobs:
            self.cur = self.jobs[0]
        self._auto_runsheet()
        self._refresh_jobs()
        self._show_job()

    # ----------------------------------------------------- merge/show
    def _remerge(self, job: Job | None = None):
        """Merges a job's documents again (default: the current job). For the job on screen, the edits in
        the editor are taken first and the editor shows the result, with a cell of the attorney table that was
        being typed in opened again (_resume_edit: an AI answer arrives while the user types)."""
        job = job or self.cur
        typing = self._sync_from_ui() if job is self.cur else None
        remerge(job, self.s)
        if job is self.cur:
            self._show_case()
            self._resume_edit(typing)

    def _show_case(self):
        """Shows the current job's case in the editor: fields, speed, proceeding boxes, attorneys, invoice line.
        No. of copies is worked out again first (the ticks may have changed while another job was shown), and
        the agreement form's speed by the Settings rule unless chosen (a new job gets the rule's speed and rate,
        not those the window showed last)."""
        self.cur.refresh_copies(self.s)
        apply_speed_rule(self.case, self.s)
        for key, r in self.rows.items():
            r.set_state(self.case.fields[key])
        self._fill_speeds()
        for p, cb in self.proc_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in self.case.proc_types)
            cb.blockSignals(False)
        self._show_attorneys()
        self._refresh_outputs()
        self._show_index_notes()

    def _show_attorneys(self):
        """Fills the attorney table from the current case (without counting as an edit by the user)."""
        self.att.blockSignals(True)
        self.att.setRowCount(0)
        for a in self.case.attorneys:
            self._append_att(a)
        self.att.resizeRowsToContents()
        self.att.blockSignals(False)

    def _append_att(self, a: Attorney):
        """Adds a table row for one attorney. Address lines show joined by " / "; a placeholder row is greyed."""
        r =self.att.rowCount()
        self.att.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if a.checked else Qt.Unchecked)
        self.att.setItem(r, 0, chk)
        for c, f in enumerate(ATT_FIELDS[1:], 1):
            val = getattr(a, f) or ""
            if f == "address":
                val = " / ".join(l for l in val.splitlines() if l.strip())
            it = QTableWidgetItem(val)
            if f == "source":
                it.setFlags(Qt.ItemIsEnabled)
            if a.is_placeholder():
                it.setForeground(self.palette().placeholderText())
            self.att.setItem(r, c, it)

    def _read_attorneys(self) -> list[Attorney]:
        """The attorney table as Attorney objects, with " / " in the address turned back into line breaks."""
        out = []
        for r in range(self.att.rowCount()):
            vals = {}
            for c, f in enumerate(ATT_FIELDS[1:], 1):
                it = self.att.item(r, c)
                vals[f] = it.text().strip() if it else ""
            # " / " separates lines; "c/o" and "12-1/2" are left alone
            vals["address"] = "\n".join(p.strip() for p in re.split(r"\s+/\s*|\s*/\s+", vals["address"])
                                        if p.strip())
            a = Attorney(**vals)
            a.checked = self.att.item(r, 0).checkState() == Qt.Checked if self.att.item(r, 0) else False
            out.append(a)
        return out

    def _sync_from_ui(self, commit: bool = True) -> tuple[int, str, int] | None:
        """Copies the user's edits in the editor into the current job's case: typed fields, the proceeding
        boxes and the attorney table. Call it before the case is merged again, switched or filled.

        commit: a cell still being typed in is taken as typed (an AI answer or a second document arriving
        must not throw it away), and its editor closes; returns what _commit_edit returns, for _resume_edit.
        With commit=False (the status pill, which only reads the fields) an open editor is left alone."""
        typing = self._commit_edit() if commit else None
        for key, r in self.rows.items():
            if r.state.source == SRC_USER:
                self.case.fields[key] = FieldState(r.text(), SRC_USER, 1.0, r.state.alternatives)
        # (the speed has no FieldRow: _delivery_changed, Keep and ↺ put it in the case as it is chosen)
        self.case.proc_types = {p for p, cb in self.proc_boxes.items() if cb.isChecked()}
        self.case.attorneys = self._read_attorneys()
        return typing

    # ---------------------------------------------------- UI events
    def _field_edited(self, key: str):
        """The user typed in a field (or picked a suggestion): it goes into the case straight away. The
        transcripts' own page count typed in Est. number of pages stays the count (badge PDF): as the user's
        number it would bill the other reporters' pages too (see Job.pages_typed). (Only the whole count: this
        runs at each key, and a field emptied or a number half typed must stay as it is. A blank field, or the
        user's own pages typed, are no number of the user's either: Job.is_the_count.)"""
        st = self.rows[key].state
        whole = self.cur.transcript_pages() if key == "est_pages" else 0
        if whole and st.source == SRC_USER and st.value.strip() == str(whole):
            st = FieldState(str(whole), SRC_PDF, self.cur.count_confidence(), st.alternatives)
            self.rows[key].set_state(st)
        self.case.fields[key] = st
        self._update_status()

    def _proc_toggled(self, _):
        """A proceeding box was clicked: the tick goes into the case straight away (an answer about the firms
        shows the case again from it), and later merges keep the user's choice."""
        self.proc_touched = True
        self.case.proc_types = {p for p, cb in self.proc_boxes.items() if cb.isChecked()}

    def _att_changed(self, _item):
        """A cell or tick of the attorney table changed: later merges keep the user's table."""
        self.att_touched = True
        self._attorneys_edited()

    def _attorneys_edited(self):
        """A tick or an edit in the attorney table: No. of copies (the ordering parties, unless typed), the
        invoice's parties and the file counts follow, and so do the Excerpts... rows when an attorney's name
        (or firm) was changed."""
        old, new = self.case.attorneys, self._read_attorneys()
        if len(old) == len(new):  # the same rows (none added or removed): the attorney of each row is the same
            keys = {a.key() for a in new}
            for before, after in zip(old, new):
                if before.key() != after.key() and before.key() not in keys:
                    self.cur.rename_in_portions(before.key(), after.key())
        self.case.attorneys = new
        self._show_copies()
        self._update_status()

    def _show_copies(self, jobs: list[Job] | None = None):
        """No. of copies follows the ordering parties (Job.refresh_copies: the Parties number when set, else the
        attorneys ticked) of these jobs (default: the current one), unless typed; the editor shows the current
        job's."""
        for job in jobs or [self.cur]:
            if job is self.cur and self.rows["copies"].state.source == SRC_USER:
                continue  # typed in the editor (and perhaps not yet in the case)
            job.refresh_copies(self.s)
            if job is self.cur:
                self.rows["copies"].set_state(self.case.fields["copies"])

    def _add_att_row(self):
        """Adds a ticked, empty attorney row and starts editing its Name cell."""
        self.att.blockSignals(True)
        self._append_att(Attorney(source=SRC_USER, checked=True))
        self.att.blockSignals(False)
        self.att_touched = True
        self._attorneys_edited()
        self.att.editItem(self.att.item(self.att.rowCount() - 1, 1))

    def _remove_att_rows(self):
        """Removes the selected attorney rows."""
        rows = sorted({i.row() for i in self.att.selectedIndexes()}, reverse=True)
        for r in rows:
            self.att.removeRow(r)
        if rows:
            self.att_touched = True
            self._attorneys_edited()

    # ------------------------------------------------ rate sheets / speed
    def _fill_sheet_box(self):
        """Lists the rate sheets (each tooltip shows its speeds, the headings read as another price's and the prices
        it is missing), says which files were skipped, then the speeds."""
        from ..rates import missing_prices
        sheets, problems = self.s.sheets()
        current = self.s.sheet()
        shown = sheets or [current]

        def missing(sh) -> str:
            """'\n⚠ Missing: Regular: Email, Index; Realtime: Original' ('' for a complete sheet)."""
            gaps = missing_prices(sh)
            return ("\n⚠ Missing: " + "; ".join(f"{name}: {', '.join(what)}" for name, what in gaps)) if gaps else ""

        tips = ["\n".join(sp.label() + (f"  ·  {sp.days} days" if sp.days is not None else "") for sp in sh.speeds)
                + (f"\nRates last updated {sh.updated}" if sh.updated else "")
                + "".join(f'\nThe "{heading}" column is read as the {label} price'
                          for heading, label in sh.read_as.items())
                + missing(sh) for sh in shown]
        refill_combo(self.sheet_box, [(sh.name, sh.name) for sh in shown], current.name, tips)
        for p in problems:
            self._toast(f"Rate sheet skipped - {p}")
        self._fill_speeds()

    def _fill_speeds(self):
        """Fills the agreement form's Speed box: the speeds the invoice offers, cheapest first as the boxes above
        (with their turnaround days; every speed of the sheet when none is ticked), plus "Other", and shows the
        job's speed (else the Settings rule's: never the box's last choice, which was another job's)."""
        keep = self.case.get("delivery") or self.s.agreement_speed()
        items = []
        for sp in self.s.offered_speeds():
            days = self.s.days_for(sp.name)
            items.append((sp.label() + (f"  ·  {plural(days, 'day')}" if days is not None else ""), sp.name))
        refill_combo(self.delivery, items + [("Other (type the rate yourself)", "Other")])
        self._select_speed(keep)

    def _select_speed(self, name: str):
        """Selects `name` (matched loosely, e.g. 'Expedited' -> 'Expedite') without firing change events. A speed
        the box doesn't list shows as the Settings rule's ("Other" when the sheet doesn't have it at all)."""
        sp = self.s.sheet().find(name)
        target = sp.name if sp else ("Other" if name else self.s.agreement_speed())
        if self.delivery.findData(target) < 0 and target != "Other":
            target = self.s.agreement_speed()
        with QSignalBlocker(self.delivery):
            self.delivery.setCurrentIndex(max(0, self.delivery.findData(target)))
        self._show_rate_info()
        self._show_speed_state()

    def _show_speed_state(self):
        """The agreement form speed's badge ("default": the Settings rule's; "you": chosen for this job), its ↺
        (back to the rule, shown for a speed chosen) and the question when an e-mail asks for another speed
        than the one shown (Job.speed_question): the box is marked and Keep answers it."""
        f = self.case.fields["delivery"]
        src = f.source if f.value else ""
        self.speed_badge.setText(src)
        self.speed_badge.setProperty("src", src)
        self.speed_badge.setToolTip({SRC_USER: "Chosen by you for this job",
                                     SRC_DEFAULT: "Picked by the rule in Settings → Invoice"}.get(src, ""))
        repolish(self.speed_badge)
        rule = self.s.delivery_name(self.s.agreement_speed())
        self.speed_reset.setVisible(src == SRC_USER)
        self.speed_reset.setToolTip(f"Back to the speed Settings → Invoice picks ({rule})")
        ask = self.cur.speed_question()
        if ask:
            asked, _, _ = self.cur.asked_speed()
            name = self.s.delivery_name(asked)
            text = f"⚠ {ask[0].upper()}{ask[1:]}. Please choose a speed for this job:"
            if not self.s.speed_offered(asked):
                text += f" ({name} isn't offered on the invoice: tick it under Speeds offered to choose it)"
            self.speed_ask.setText(text)
            self.speed_keep.setText(f"Keep {self.delivery.currentData() or rule}")
        self.speed_form.setRowVisible(self.speed_ask_row, bool(ask))
        self.delivery.setProperty("ask", bool(ask))
        repolish(self.delivery)

    def _ask_speeds(self, jobs: list[Job]) -> bool:
        """Before agreement forms or MOFRs are made: for the jobs whose e-mail asks for another speed than the
        one their form names (Job.speed_question), asks which speed (SpeedDialog: the e-mail's preselected when
        it is offered). Each answer is the user's choice for the job, its rate and delivery date following.
        False when Go back was clicked, or Generate all started meanwhile (nothing is made then)."""
        from .dialogs import SpeedDialog
        ask = [j for j in jobs if j.speed_question()]
        if not ask:
            return True
        speeds = [(sp.label(), sp.name) for sp in self.s.offered_speeds()]
        rows = []
        for j in ask:
            asked, _, _ = j.asked_speed()
            pre = self.s.delivery_name(asked) if self.s.speed_offered(asked) else j.case.get("delivery")
            title = ", ".join(x for x in (j.title(), j.case.get("dates")) if x)
            rows.append((f"{title}: {j.speed_question()}", speeds, pre))
        dlg = SpeedDialog(rows, self)
        if dlg.exec() != SpeedDialog.Accepted or self._batch_running():
            return False
        for j, name in zip(ask, dlg.chosen()):
            if name and j in self.jobs:  # (a read that ended meanwhile may have merged it into another job)
                j.case.fields["delivery"] = FieldState(name, SRC_USER, 1.0, [name])
                refresh_rate(j.case, self.s)
                if self.s.fill_delivery_date:
                    refresh_delivery_date(j.case, self.s)
        if any(j is self.cur for j in ask):
            self._fill_speeds()
            self._apply_speed()
        else:
            self._update_status()
        return True

    def _ask_parties(self, jobs: list[Job]) -> bool:
        """Before anything is made: a day split under Excerpts... whose Parties number, set by hand, isn't the
        number of firms that ordered (Job.parties_mismatch) may have a firm left unticked by mistake. The
        invoices and No. of copies count the firms that ordered; Go back leaves it to be checked (False), Go on
        makes the files so (True)."""
        odd = [j for j in jobs if j.parties_mismatch()]
        if not odd:
            return True
        lines = "\n".join(f"•  {', '.join(x for x in (j.title(), j.case.get('dates')) if x)}: {j.parties_mismatch()}"
                           for j in odd[:8]) + ("\n…" if len(odd) > 8 else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Parties and Excerpts disagree")
        box.setText(f"{lines}\n\nThe invoices and No. of copies count the firms that ordered pages under "
                    "Excerpts…, not the Parties number. If a firm that ordered is missing, go back and tick it "
                    "under Excerpts….")
        back = box.addButton("Go back", QMessageBox.RejectRole)
        go_on = box.addButton("Go on (count the firms ticked)", QMessageBox.AcceptRole)
        box.setDefaultButton(back)
        box.setEscapeButton(back)
        box.exec()
        return box.clickedButton() is go_on and not self._batch_running()

    def _ask_split_speeds(self, jobs: list[Job], button: str = "Generate") -> bool:
        """Before the files are made: the firms of a split order whose speed isn't set (batch.speeds_to_ask:
        firms that ordered the same pages commit to a speed, as the courthouse says) are asked about at once
        (dialogs.SplitSpeedsDialog, each set to the firm's speed on its other pages of the case, else the agreement
        form's). Each answer is the firm's speed for all its pages of the day that have none of their own
        (Job.speeds; one set meanwhile is kept). Generate asks again after the Clarify box, for the firms ticked
        there. False when Go back was clicked, or Generate all started meanwhile (nothing is made then)."""
        from ..batch import speeds_to_ask
        from .dialogs import SplitSpeedsDialog
        asks = speeds_to_ask(jobs, self.s)
        if not asks:
            return True
        dlg = SplitSpeedsDialog(asks, [(sp.label(), sp.name) for sp in self.s.sheet().speeds], button, self)
        if dlg.exec() != SplitSpeedsDialog.Accepted or self._batch_running():
            return False
        from ..extract_regex import same_entry
        for a, name in zip(asks, dlg.chosen()):
            if name and a.job in self.jobs:  # (a read that ended meanwhile may have merged it into another job)
                rows = a.job.case.attorneys
                key = a.key
                if a.attorney is not None and not any(x.key() == key for x in rows):
                    # (a document read meanwhile renamed the firm: "Dana Smith" of "Smith Law" now)
                    key = next((x.key() for x in rows if same_entry(a.attorney, x)), key)
                a.job.speeds.setdefault(key, name)
        self._update_status()
        return True

    def _ask_index(self, groups: list[list[Job]]) -> bool:
        """Before invoices are made: for each set of days billed together (groups) where a Pages number typed
        below the transcript's own count changes whether there is an index (batch.index_question), asks whether
        to include one: the user made the count their own, so the app doesn't decide. The answer is kept on the
        typed days (Job.index_on_typed: No judges them on the number typed, Yes on the transcript), so it isn't
        asked again; Peripherals... shows it. When the rate sheet has no index price for the invoice's speeds
        (IndexQuestion.priced), no index is charged either way: a warning says so instead, and nothing is kept, so
        Generate asks once a price is added. False when Go back was clicked (nothing is made), or Generate all
        started meanwhile. Answers given before a Go back are kept, as the box says."""
        from ..batch import index_question
        asked = False
        try:
            for group in groups:
                group = [j for j in group if j in self.jobs]  # (a read that ended meanwhile may have merged one)
                q = index_question(group, self.s) if group else None
                if q is None:
                    continue
                dates = [d for j in group if (d := j.case.get("dates"))]
                title = ", ".join(x for x in (group[0].title(), ", ".join(dates)) if x)
                several = len(group) > 1
                # under the rule "each" day on its own, the answer is about the days typed only
                whom = ("those days" if len(q.jobs) > 1 else "that day") \
                    if several and self.s.invoice_index_rule == "each" else "the invoice"
                which = f" for {', '.join(d for j in q.jobs if (d := j.case.get('dates')))}" if several else ""
                has = "the transcripts have" if q.transcripts > 1 else "the transcript has"
                gets, it = ("get", "they get") if whom == "those days" else ("gets", "it gets")
                box = QMessageBox(self)
                if not q.priced:  # no index can be charged either way: nothing to choose, but say why there's none
                    box.setIcon(QMessageBox.Warning)
                    box.setWindowTitle("No index price")
                    box.setText(f"{title}\n\nYou typed {q.typed:,} pages in Est. number of pages{which}; {has} "
                                f"{q.counted:,}.\n\nJudged on the transcript, {whom} {gets} an index "
                                f"({q.pdf_why}), but your rate sheet has no index price for the speeds this invoice "
                                "offers, so no index is charged.")
                    box.setInformativeText("To charge one, add a price in the Index column of the rate sheet (File → "
                                           "Open rate sheets folder); Generate then asks about the index.")
                    go = box.addButton("Go on without an index", QMessageBox.AcceptRole)
                    back = box.addButton("Go back", QMessageBox.RejectRole)
                    box.setDefaultButton(go)
                    box.setEscapeButton(back)
                    box.exec()
                    if box.clickedButton() is not go or self._batch_running():  # (closed: as Go back)
                        return False
                    continue
                box.setIcon(QMessageBox.Question)
                box.setWindowTitle("Index for this invoice?")
                box.setText(f"{title}\n\nYou typed {q.typed:,} pages in Est. number of pages{which}; {has} "
                            f"{q.counted:,}.\n\nJudged on the transcript, {whom} {gets} an index ({q.pdf_why}); "
                            f"judged on the pages you typed, {it} none ({q.typed_why}).\n\nInclude an index (and the "
                            f"judge's) {'on this invoice' if whom == 'the invoice' else 'for ' + whom}?")
                box.setInformativeText("Your answer is kept for this job: Peripherals… in the Invoice panel "
                                       "changes it.")
                yes = box.addButton("Yes, an index", QMessageBox.YesRole)
                no = box.addButton("No index", QMessageBox.NoRole)
                back = box.addButton("Go back", QMessageBox.RejectRole)
                box.setDefaultButton(back)
                box.setEscapeButton(back)
                box.exec()
                if box.clickedButton() not in (yes, no) or self._batch_running():  # (closed: as Go back)
                    return False
                for j in q.jobs:
                    if j in self.jobs:
                        j.index_on_typed = box.clickedButton() is no
                asked = True
            return True
        finally:
            if asked:  # (the Includes line and the prices follow the answers, a later Go back too)
                self._update_status()

    def _speed_to_rule(self):
        """↺: the job's speed goes back to the one Settings → Invoice picks; the rate and delivery date follow."""
        self.case.fields["delivery"] = FieldState()
        self._apply_speed_rule()

    def _keep_speed(self):
        """Keep: the speed shown is the user's choice for this job (answers an e-mail's other speed)."""
        name = self.delivery.currentData() or ""
        if name:
            self.case.fields["delivery"] = FieldState(name, SRC_USER, 1.0, [name])
        self._apply_speed()

    def _show_rate_info(self):
        """The line under the Rate field (Minute agreement form details): the copy rate, the speed's other prices
        and when the rates changed."""
        sheet = self.s.sheet()
        sp = sheet.find(self.delivery.currentData() or "")
        bits = []
        if sp and sp.copy:
            bits.append(f"Copies ${sp.copy}/pg")
        if sp:
            bits += [f"{k} ${v.lstrip('$')}" for k, v in sp.extras.items()]
        if sheet.updated:
            bits.append(f"rates updated {sheet.updated}")
        self.rate_info.setText("  ·  ".join(bits))

    def _sheet_changed(self, _idx):
        """Another rate sheet was picked: it becomes the default, and every job is priced again from it (the
        agreement form's speed by the Settings rule again, unless chosen and still offered: _sheet_in_use). An
        incomplete one is warned about first (_check_rate_sheet), before anything is priced, with the one in use
        before to go back to: the jobs are then as they were (a speed chosen that the new sheet lacks isn't
        reset)."""
        name = self.sheet_box.currentData()
        if not name or name == self.s.rate_sheet:
            return
        # (the one in use: the sheet Settings names may be gone, another being used in its place)
        previous = self.s.sheet().name
        picked = next((sh for sh in self.s.sheets()[0] if sh.name == name), None)
        # checked before Settings names it: a read or an AI answer that ends while the box is open is priced from
        # the sheet in use, which Go back keeps
        if picked is None or self._check_rate_sheet(previous, again=True, priced=False, sheet=picked):
            self.s.rate_sheet = name
            self._sheet_in_use()
            if picked is None:
                self._check_rate_sheet(previous, again=True)

    def _sheet_in_use(self) -> None:
        """The rate sheet in use changed: saved, and every job priced again from it."""
        self._save_settings()
        self._fill_invoice_speeds()
        self._apply_speed_rule()
        self._remerge_others()

    def _check_rate_sheet(self, previous: str = "", again: bool = False, priced: bool = True,
                          sheet=None) -> bool:
        """Warns when the rate sheet in use leaves out prices (rates.missing_prices): a speed left off the sheet is
        one not offered, which is fine, but every speed on it needs its Original, Copy, Email and Index price, or
        invoices come out wrong (the user, 2026-10-09). Called whenever the sheet in use may have changed: at start
        (it may have been edited meanwhile), when another is picked or the sheets are read again (⟳), and after
        Settings or an import. Use it anyway remembers the sheet as it was read (Settings.rate_sheets_ok, by
        rates.sheet_fingerprint, the last RATE_SHEETS_OK_KEPT): not asked again until its file is changed and read
        again. Go back to <previous> (the sheet in use before; offered when it is another, still in the folder)
        picks that one again, which is checked in turn; Open the rate sheets folder leaves this one in use, to be
        filled in. Asked once a session for the same file as read unless `again` (picked, ⟳, another chosen in
        Settings). priced: the jobs were priced from the sheet already (they are priced from `previous` again on
        Go back). sheet: the one to check, when it isn't in use yet (picked in the dropdown: Settings names it
        only once it is kept). False = Go back was clicked (the sheet before is in use again)."""
        from ..rates import missing_prices, sheet_fingerprint
        from ..settings import RATE_SHEETS_OK_KEPT
        sheet = sheet or self.s.sheet()
        gaps = missing_prices(sheet)
        mark = sheet_fingerprint(sheet)
        if not gaps or not mark or mark in self.s.rate_sheets_ok or (mark in self._sheets_warned and not again):
            return True
        self._sheets_warned.add(mark)
        lines = "\n".join(f"•  {name}: no {_or(what)} price"
                          + (" (so it isn't offered at all)" if what == ["Original"] else "") for name, what in gaps[:8])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Your rate sheet is incomplete")
        box.setText(f'The rate sheet "{sheet.name}" is missing some prices:\n\n{lines}'
                    + ("\n…" if len(gaps) > 8 else "") + "\n\nLeaving off a speed you don't offer is fine, but every "
                    "speed on the sheet needs all four prices: Original, Copy, Email and Index. Without them, "
                    "invoices can come out wrong (no index charged, or copies at $0.00)."
                    + (' The Email and Index prices are read from the columns headed so, or close to it ("E-mail", '
                       '"Index Price") when only one column is.' if any({"Email", "Index"} & set(what)
                                                                        for _, what in gaps) else "")
                    + "\n\nUse this rate sheet anyway?")
        use = box.addButton("Use it anyway", QMessageBox.AcceptRole)
        names = [sh.name for sh in self.s.sheets()[0]]
        back = box.addButton(f"Go back to {previous}", QMessageBox.RejectRole) \
            if previous and previous != sheet.name and previous in names else None
        fix = box.addButton("Open the rate sheets folder", QMessageBox.ActionRole)
        box.setDefaultButton(back or fix)
        if back is not None:
            box.setEscapeButton(back)
        box.exec()
        if box.clickedButton() is use:
            self.s.rate_sheets_ok = [m for m in self.s.rate_sheets_ok if m != mark][-(RATE_SHEETS_OK_KEPT - 1):] + [mark]
            self._save_settings()
        elif back is not None and box.clickedButton() is back:
            self.s.rate_sheet = previous
            self._fill_sheet_box()
            if priced:
                self._sheet_in_use()
            self._check_rate_sheet()  # (that one may be incomplete too)
            return False
        elif box.clickedButton() is fix:
            self._open_sheets_folder()
        return True

    def _remerge_others(self):
        """New rates or settings also apply to the batch's other jobs."""
        for job in self.jobs:
            if job is not self.cur and job.docs:
                remerge(job, self.s)

    def _delivery_changed(self, _idx):
        """The user picked a speed: it counts as their choice (it also answers an e-mail's other speed), and the
        rate and delivery date follow it."""
        name = self.delivery.currentData() or ""
        self.case.fields["delivery"] = FieldState(name, SRC_USER, 1.0, [name])
        self._apply_speed()

    def _apply_speed(self):
        """Re-derives rate and delivery date from the job's speed (unless typed by the user), and shows them
        and what follows (the prices, the job list's ⚠ of a speed to choose). The case's speed is what counts:
        the box only shows it (it was once copied from the box, and so a stale choice of another job's, or the
        box's first item, became the job's speed)."""
        if self.case.get("delivery") == "Other" and self.rows["rate"].state.source != SRC_USER:
            self.case.fields["rate"] = FieldState()  # (no sheet rate for a speed of the user's own)
        refresh_rate(self.case, self.s)
        if self.s.fill_delivery_date:
            refresh_delivery_date(self.case, self.s)
        for k in ("rate", "delivery_date"):
            if self.rows[k].state.source != SRC_USER:
                self.rows[k].set_state(self.case.fields[k])
        self._show_rate_info()
        self._show_speed_state()
        self._refresh_outputs()
        self._refresh_job_labels()  # (a job's ⚠ of a speed to choose comes or goes)

    def _apply_speed_rule(self):
        """What decides the agreement form's speed changed (the speeds ticked, the rate sheet, the settings, ↺):
        every job's speed is the Settings rule's again unless the user chose one still offered, its rate and
        delivery date follow (merge.apply_speed_rule), and the window shows the current job's."""
        for job in self.jobs:
            apply_speed_rule(job.case, self.s)
        self._fill_speeds()
        self._apply_speed()

    def _open_log_folder(self):
        open_path(logfile.log_dir())

    def _copy_diagnostics(self):
        """Copies the app and system details for a problem report (no case details) to the clipboard."""
        QApplication.clipboard().setText(logfile.diagnostics(self.s))
        self._toast("Copied: paste it into your message. It has no case details.")

    def _open_sheets_folder(self):
        from ..rates import sheets_dir
        open_path(sheets_dir(self.s.rate_sheets_dir))

    def _reload_sheets(self):
        """⟳: reads the rate sheet files again (after they were edited) and prices every job again. The sheet in
        use, still incomplete or newly so, is warned about again, unless the user said to use it as it is
        (_check_rate_sheet)."""
        self.s.reload_rates()
        self._fill_sheet_box()
        self._fill_invoice_speeds()  # the sheet's speeds may have changed
        self._apply_speed_rule()
        self._remerge_others()
        self._toast(f"Loaded {len(self.s.sheets()[0])} rate sheet(s).")
        self._check_rate_sheet(again=True)  # (edited in Excel: still incomplete, or newly so)

    def _set_opt(self, name, value):
        """Sets a setting and saves it: an option changed in the Outputs box (the outputs ticked and each one's
        options), the zoom, the list of recent files cleared. A file that can't be written is logged, not shown
        (the window must follow the option all the same; the settings are saved again when it closes)."""
        setattr(self.s, name, value)
        self._save_settings()

    # ------------------------------------------------------- status
    def _set_status(self, text: str, state: str, mood: str | None = None):
        """Sets the status pill at the top. state: "" (plain), "busy", "ok" or "warn". When the yin-yang is on
        (Settings → Options, Settings.show_yin), state also picks its picture unless mood names one: it swirls
        while busy ("working") and while the app waits (plain: "idle"); ok shows "done" once the swirl's turn is
        over, warn "stumped" at once (DropZone.set_mood)."""
        self.status.setText(text)
        self.status.setProperty("state", state)
        repolish(self.status)
        if self.s.show_yin:
            self.drop.set_mood(mood or {"busy": "working", "ok": "done", "warn": "stumped"}.get(state, "idle"))
        else:
            self.drop.set_mood(None)
        self._update_header_buttons()  # (the status changes whenever work starts or ends, or jobs come or go)

    def _update_status(self):
        """Brings the Outputs box and the cards under the attorneys (_refresh_outputs, which also brings the
        Excerpts window along), the status pill, the question about rows that may be one firm and the list
        of jobs on the left up to date."""
        self._all_counts = None
        self._refresh_outputs()  # (it counts what Generate all makes: _all_counts)
        self._show_firm_question()
        self._job_status()
        self._refresh_job_labels(self._all_counts)

    def _show_firm_question(self) -> None:
        """The Attorneys card's question when two rows of the job, or of its case's other days, may be one firm or
        attorney (batch.firm_questions: "Smith Law" and "Smith Law Group"): Same firm or Not the same answers it
        (_answer_firm); hidden when there is none."""
        group = self._invoice_group() if self.cur in self.jobs else [self.cur]
        qs = firm_questions(group)
        self._firm_q = (group, qs[0]) if qs else None
        if qs:
            a, b = qs[0]
            more = f"  ({len(qs)} to answer)" if len(qs) > 1 else ""
            self.firm_ask.setText(f"⚠ Are these one firm?{more}\n•  {_entry_text(a)}\n•  {_entry_text(b)}")
        self.firm_ask_box.setVisible(bool(qs))

    def _answer_firm(self, same: bool) -> None:
        """Same firm / Not the same, for the question shown (see _show_firm_question): kept for later documents
        while the card's Remember is ticked, else until the app is closed."""
        if self._firm_q is None or self._batch_running():
            return
        _, (a, b) = self._firm_q
        self._commit_edit()  # (a cell still being typed in: the table is shown again from the case)
        self._record_firm_answer(a, b, same, self.firm_remember.isChecked())
        self._show_case()
        self._update_status()

    def _use_answers(self) -> None:
        """The answers about rows that may be one firm, as same_entry reads them: those kept in Settings and those
        given for now only (extract_regex.use_firm_answers)."""
        use_firm_answers(self.s.firm_answers + self._session_answers)

    def _record_firm_answer(self, a: Attorney, b: Attorney, same: bool, remember: bool = True) -> None:
        """The user's answer about rows a and b: kept (remember: Settings.firm_answers, saved, with the names as
        written, so later documents naming them are read so too) or for now only (until the app is closed).
        When they are one, they become one row on each day of every job loaded (batch.join_entries, a case at a
        time); No. of copies follows."""
        pair = {a.key(), b.key()}
        row = [a.key(), b.key(), same, _entry_text(a), _entry_text(b)]
        self._session_answers = [r for r in self._session_answers if {r[0], r[1]} != pair]
        if remember:
            self.s.firm_answers = [r for r in self.s.firm_answers if {r[0], r[1]} != pair] + [row]
            self._save_settings()
        else:
            self._session_answers.append(row)
        self._use_answers()
        if same:  # on every job loaded, not only the case asked about: it won't be asked about again
            for case in _cases(self.jobs):
                join_entries(case, a, b)
        for j in self.jobs:
            j.refresh_copies(self.s)

    def _ask_firms(self, groups: list[list[Job]]) -> bool:
        """Before anything is made: rows of these days (each list the days of a case) that may be one firm or
        attorney and haven't been answered are asked about, one pair at a time (Same firm, Not the same). The
        Remember box starts as the card's and carries from one question to the next, and back to the card. False
        when Go back was clicked (nothing is made), or Generate all started meanwhile."""
        asked = False
        ticked = self.firm_remember.isChecked()
        for group in groups:
            # the questions are worked out again after each answer: an answer joins rows, and a read that ends
            # while the box is open may merge the job again (new rows); an answered pair isn't asked again
            while qs := firm_questions([j for j in group if j in self.jobs]):
                a, b = qs[0]
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Question)
                box.setWindowTitle("One firm or two?")
                case = ", ".join(x for x in (group[0].title(), group[0].case.get("dates")) if x)
                box.setText(f"{case}: are these one firm (or one attorney)?\n\n•  {_entry_text(a)}\n"
                            f"•  {_entry_text(b)}\n\nOne firm gets one invoice and one minute agreement.")
                remember = QCheckBox("Remember my answer for later documents")
                remember.setChecked(ticked)
                remember.setToolTip("Unticked: the answer holds until you close the app. Settings → Invoice →\n"
                                    "Firms you answered lists your answers (those for now too), to forget one.")
                box.setCheckBox(remember)
                same = box.addButton("Same firm", QMessageBox.YesRole)
                apart = box.addButton("Not the same", QMessageBox.NoRole)
                back = box.addButton("Go back", QMessageBox.RejectRole)
                box.setEscapeButton(back)
                box.exec()
                clicked = box.clickedButton()
                ticked = remember.isChecked()
                self.firm_remember.setChecked(ticked)
                if clicked not in (same, apart) or self._batch_running():
                    return False
                self._record_firm_answer(a, b, clicked is same, ticked)
                asked = True
        if asked:
            self._show_case()
            self._update_status()
        return True

    def _follow_excerpts(self) -> None:
        """The Excerpts window, when open, shows the current job's days as they are now (it is called from
        _refresh_outputs, which every change reaches: a speed, the parties, a job ticked, the delivery). When
        the job has no days to show (a new job, one without a transcript, a job removed), the window closes
        (ExcerptsWindow.clear): else it would keep showing, and changing, days no longer among the jobs."""
        w = self.excerpts
        if w is None or not w.isVisible():
            return
        if self.cur not in self.jobs or not self.cur.invoice_days():
            w.clear()
            return
        w.load(self._excerpt_group())

    def _job_status(self):
        """The status pill for the job on screen: reading (documents still being read, whatever the job on
        screen says: a single file read while a folder is still coming in), asking the AI, fields missing or to
        review, or ready."""
        busy = self.ai_pending > 0
        self.busy.setVisible(busy or self.work > 0)
        if self.work > 0:
            if self.status.property("state") != "busy":  # ("Reading 2 of 5: ..." is kept as it is)
                self._set_status("Reading…", "busy")
            return
        if not self.cur.docs:
            self._set_status("Drop a document to begin", "")
            return
        self._sync_from_ui(commit=False)  # (a name being typed in the attorney table stays open)
        missing = [FIELD_LABELS[k] for k in self.case.missing_required()]
        review = [k for k, r in self.rows.items() if r.edit.property("review")]
        if busy:
            self._set_status(f"Asking {self.s.ollama_model}…  (you can keep editing)", "busy")
        elif missing or review:
            parts = []
            if missing:
                parts.append(f"{len(missing)} missing")
            if review:
                parts.append(f"{len(review)} to review")
            # the yin-yang only cracks when something required is missing
            self._set_status("Ready to fill  ·  " + ", ".join(parts), "warn", "stumped" if missing else "done")
            self.status.setToolTip("Missing: " + ", ".join(missing) if missing else "Highlighted fields are guesses")
        else:
            self._set_status("✓  Ready to fill", "ok")
            self.status.setToolTip("")

    def _idle(self):
        """A piece of background work is done: the progress bar goes back to 'busy' style, and stays only
        while the AI or another read is still going."""
        self.busy.setRange(0, 0)
        self.busy.setVisible(self.ai_pending > 0 or self.work > 0)

    def _saved_box(self, title: str, text: str, folders: list, details: str = "", warn: bool = False,
                   files: list | None = None):
        """The 'files saved' message, with a button that opens the folder(s) and, when `files` are given,
        one that prints them (see preview.print_files)."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning if warn else QMessageBox.Information)
        box.setText(text)
        if details:
            box.setDetailedText(details)
        print_btn = box.addButton("Print…", QMessageBox.ActionRole) if files else None
        open_folder = box.addButton("Open folder", QMessageBox.ActionRole) if folders else None
        box.setDefaultButton(box.addButton(QMessageBox.Ok))
        box.exec()
        if open_folder is not None and box.clickedButton() == open_folder:
            for f in folders[:3]:
                open_path(f)
        elif print_btn is not None and box.clickedButton() == print_btn:
            from .preview import print_files
            print_files(self, files)

    def _toast(self, msg: str):
        """A short note at the bottom of the window that goes away after 8 seconds."""
        self.statusBar().showMessage(msg, 8000)

    # --------------------------------------------------------- fill
    def _batch_running(self) -> bool:
        """True (with a note to the user) while Generate all is still making files."""
        if self.filling:
            self._toast("Still making the files of the batch - one moment.")
        return self.filling

    def _commit_edit(self) -> tuple[int, str, int] | None:
        """A cell of the attorney table still being typed in (Ctrl+Enter pressed in it, a merge, an answer about
        the firms) is taken as typed. The editor is found in the table itself, not by the keyboard focus: a
        background answer arrives while the focus may be anywhere. Returns (column, text, cursor position) when
        the user was typing in it (it had the focus), for _resume_edit, else None."""
        if self.att.state() != QAbstractItemView.EditingState or getattr(self, "_committing", False):
            return None  # (committing fires itemChanged, whose handlers sync the editor again: once is enough)
        editor = QApplication.focusWidget()
        if editor is None or editor is self.att or not self.att.isAncestorOf(editor):
            viewport = self.att.viewport()
            # (the editor is the viewport's own child; one closed before is hidden until Qt deletes it)
            editor = next((w for w in viewport.findChildren(QWidget) if w.parent() == viewport
                           and w.isVisibleTo(viewport)), None)
        if editor is None:
            return None
        typing = None  # (the window's focus, kept while another program is in front; elsewhere = typing elsewhere)
        if isinstance(editor, QLineEdit) and self.focusWidget() is editor:
            typing = (self.att.currentIndex().column(), editor.text().strip(), editor.cursorPosition())
        self._committing = True
        try:
            self.att.commitData(editor)
            self.att.closeEditor(editor, QAbstractItemDelegate.NoHint)
        finally:
            self._committing = False
        return typing

    def _resume_edit(self, typing: tuple[int, str, int] | None) -> None:
        """Opens the cell editor again after a merge from the background (a document read, an AI answer) closed
        it, on the row that now holds the text typed (or starts with it: the merge may have added the firm's other
        names), with the cursor where it was: the next key the user types goes on with "Dana Smi" instead of
        replacing it. Nothing happens when that row can't be told."""
        if not typing or not typing[1]:
            return
        col, text, cursor = typing
        cells = [(r, it.text()) for r in range(self.att.rowCount()) if (it := self.att.item(r, col))]
        rows = [r for r, t in cells if t == text] or [r for r, t in cells if t.startswith(text)]
        if len(rows) != 1:
            return
        index = self.att.model().index(rows[0], col)
        self.att.setCurrentIndex(index)
        self.att.edit(index)
        viewport = self.att.viewport()
        editor = next((w for w in viewport.findChildren(QLineEdit) if w.parent() == viewport
                       and w.isVisibleTo(viewport)), None)
        if editor is not None:
            editor.setFocus()
            editor.deselect()
            editor.setCursorPosition(min(cursor, len(editor.text())))

    def fill(self):
        """Generate: makes the ticked outputs for the job on screen, on the UI thread (Ctrl+Enter).

        Asks first, as needed, in this order: whether documents whose index numbers don't match belong together
        (_ask_index_numbers); whether to go on without the invoice (no pages to bill: no transcript and none
        typed) or the run sheet (no transcript); whose pages to bill, or whether to go on without an invoice
        that is held (_without_unchecked_invoice); whether rows that may be one firm are one (_ask_firms); which
        speed, when an e-mail asks for another one (_ask_speeds); a Parties number that isn't the firms that
        ordered (_ask_parties); the speed of each firm of a split order that has none (_ask_split_speeds), then
        whether to go on without the invoice when the speeds hold it (more on the same pages than are billed for
        now); whether there is an index, when a page count typed below the transcript's changes it (_ask_index);
        about blank required fields, competing values and a case name only the records suggest, and who ordered
        when no attorney is ticked (ClarifyDialog), then the split-order speeds of the firms ticked there; which
        run sheet the takes go on, and to close it first when it is open in Excel (nothing is made while it
        can't be read or written); the index numbers again, for a document read meanwhile; and, when
        the case's own folder is there with files in it, whether to add to it (_case_folder). Then the preview
        (_preview, when Settings.preview_before_saving), and the files are made.
        Background work can finish while a question is on screen, so after each one the job is checked to still
        exist and is taken as it is now (an invoice held meanwhile is asked about again before the files are
        made). Go back at the preview leaves the case folder to be asked about next time.
        """
        if self._batch_running():
            return
        self._commit_edit()
        self._sync_from_ui()
        job = self.cur
        case = job.case
        outputs = self._outputs()
        if not outputs:
            QMessageBox.information(self, "Nothing to make", f"Tick what to make first: {_named(OUTPUTS)} "
                                    "(in the Outputs box).")
            return
        # documents whose index numbers don't match may be about different cases: asked before anything else
        if not self._ask_index_numbers([job]) or job not in self.jobs:
            return
        missing = [o for o in outputs if o not in job.makeable(outputs) and not job.unneeded(o)]
        if missing:
            left_out = " or ".join(OUTPUTS[o].lower() for o in missing)
            reasons = {"invoice": "An invoice needs pages to bill, and the Pages field says 0."
                       if job.transcript_pages() or job.pages_typed() else
                       "An invoice needs pages to bill, and this job has no transcript PDF: type the pages to bill "
                       "in Est. number of pages (Minute agreement form details) to make one.",
                       "runsheet": "A run sheet needs a transcript PDF (the initials on its pages), and this job "
                                   "has none."}
            why = "\n".join(reasons[o] for o in missing if o in reasons)
            if QMessageBox.question(
                    self, f"No {left_out}", f"{why}\n\nMake the other outputs without the {left_out}?"
            ) != QMessageBox.Yes:
                return
            if job not in self.jobs or self._batch_running():
                return
            case = job.case  # an AI answer that came in meanwhile merged the job again
        outputs = job.makeable(outputs)  # (a run sheet one reporter's case doesn't need goes without a word)
        if not outputs:
            return
        outputs = self._without_unchecked_invoice(job, outputs)
        if not outputs:
            return
        # rows that may be one firm: asked before the speeds and the parties (one firm is one agreement, one
        # invoice; the run sheet doesn't name them). Each question is asked when an output chosen asks it
        # (courthouses.OutputSpec.asks).
        if courthouses.asks(outputs, "firms") and (not self._ask_firms([[job]]) or job not in self.jobs):
            return
        # the e-mail asks for another speed than the Settings rule's: which one the forms name
        if courthouses.asks(outputs, "speed") and (not self._ask_speeds([job]) or job not in self.jobs):
            return
        if courthouses.asks(outputs, "parties") and (not self._ask_parties([job]) or job not in self.jobs):
            return
        # the firms of a split order commit to a speed: the speeds not set yet (they change the prices)
        if courthouses.asks(outputs, "speeds") and (not self._ask_split_speeds([job]) or job not in self.jobs):
            return
        if "invoice" in outputs and job.invoice_hold():  # (more speeds answered on the same pages than are billed)
            outputs = self._without_unchecked_invoice(job, outputs)
            if not outputs or job not in self.jobs:
                return
        case = job.case  # (Whose pages... or a read that ended meanwhile may have merged the job again)
        # Ask about required fields that are blank, fields with competing values, and a case name only the records
        # suggest (only the case name when neither the agreement nor the MOFR is made)
        questions = []
        for key in REQUIRED_KEYS if courthouses.asks(outputs, "fields") else ["case_name"]:
            fs = case.fields[key]
            if not fs.value or fs.source == SRC_RECORDS or (
                    fs.source != SRC_USER and len([a for a in fs.alternatives if a != fs.value]) > 0
                    and fs.confidence < 0.8):
                questions.append((key, fs.value, fs.alternatives))
        real = [a for a in case.attorneys if not a.is_placeholder() and (a.name or a.firm)]
        # who ordered: only agreements and invoices are addressed to an attorney
        ask_att = real and not any(a.checked for a in case.attorneys) and courthouses.asks(outputs, "attorney")
        if questions or ask_att:
            asked = list(case.attorneys)
            dlg = ClarifyDialog(questions, asked if ask_att else None, self)
            if dlg.exec() != ClarifyDialog.Accepted or job not in self.jobs or self._batch_running():
                return
            # While the question was on screen an AI answer may have come in (the job was merged again) or
            # documents still being read may have put another job in the editor: the answers go to the job
            # they were asked about, as it is now.
            case = job.case
            for key, val in dlg.answers().items():
                case.fields[key] = FieldState(val, SRC_USER, 1.0, case.fields[key].alternatives)
            chosen = dlg.checked_attorneys()
            if chosen is not None:
                # the rows as asked about, never by their place: an AI answer may have put them in another order
                # (or filled in a firm, a new key: then by key, or as the same entry)
                from ..extract_regex import same_entry
                rows = [asked[i] for i in chosen]
                keys = {x.key() for x in rows if x.key()}
                for a in case.attorneys:
                    a.checked = any(a is x for x in rows) or a.key() in keys or any(same_entry(x, a) for x in rows)
                job.att_touched = True  # a later AI answer must not undo the choice
                job.refresh_copies(self.s)  # No. of copies: the parties just ticked
            if job is self.cur:
                self._show_case()
            # the firms just ticked may share pages: their speeds are asked now (no box when there's none to ask)
            if chosen is not None and courthouses.asks(outputs, "speeds") and (
                    not self._ask_split_speeds([job]) or job not in self.jobs):
                return
            if "invoice" in outputs and job.invoice_hold():  # (more speeds answered on the same pages)
                outputs = self._without_unchecked_invoice(job, outputs)
                if not outputs or job not in self.jobs:
                    return
        # a Pages number typed below the transcript's count that changes whether there is an index: which. Asked
        # once the firms who ordered and their speeds are known: whether an index can be charged is their speeds'
        if "invoice" in outputs and (not self._ask_index([[job]]) or job not in self.jobs):
            return

        sheet = None
        if "runsheet" in outputs:
            target = self._run_sheet_for(job)
            if target is None or job not in self.jobs or self._batch_running():
                return
            if not self._sheets_free([target], "Generate"):
                return
            sheet = job.runsheet_opts(self.s)
            sheet.target = target
        # the files are made from the job as it is now: an answer from the AI or a read that ended while a
        # question was on screen merged it again (and what is on screen with it)
        if "invoice" in outputs and job.invoice_hold():  # (an attorney was unticked meanwhile)
            outputs = self._without_unchecked_invoice(job, outputs)
            if not outputs or job not in self.jobs:
                return
        # (a document read while a question was on screen, with an index number of its own: asked about too)
        if not self._ask_index_numbers([job]) or job not in self.jobs:
            return
        # the case's own folder inside Save to (Settings.case_folders): asked about when it has files already
        folder_was = job.folder, job.folder_named  # (put back when nothing is saved: asked about next time)
        if not self._case_folder([job], outputs) or job not in self.jobs:
            return
        case = job.case
        out_dir = out_dir_for(job, self.s)
        opts, origin = job.invoice_sets(), job_origin(job)  # (the user's invoices, and other reporters' ticked)
        ordered = job.ordered_pages()  # each attorney's agreement: the pages it ordered, whoever wrote them
        speeds = job.ordered_speeds()  # and the speed(s) it ordered them at, where set
        docs = list(job.docs)
        previewed = False  # the preview showed the math
        if self.s.preview_before_saving:
            # what is saved is what was shown: the case as it is now, whatever comes in while the preview is up
            case = deepcopy(case)
            if not self._preview(case, outputs, opts, ordered, speeds):
                job.folder, job.folder_named = folder_was
                return
            previewed = self._math_previewed
            if job not in self.jobs:  # (a read that ended meanwhile put its documents with another job)
                self._toast("The job changed while the preview was open: nothing was saved. Generate again.")
                return
            if self._batch_running():
                return
        keys: list[str] = []  # the attorneys invoiced
        math: list = []  # (FirmInvoice, number) of each invoice made: "The math" shows how its amounts were reached
        detailed: list[Path] = []  # the invoices' detailed copies: kept for later, not opened or printed now
        try:
            paths = generate(case, self.s, out_dir, outputs, opts, runsheet=sheet, folders=input_folders(job),
                             invoiced=keys, origin=origin, math=math, detailed=detailed, ordered=ordered,
                             speeds=speeds)
        except Exception as e:
            made = list(getattr(e, "made", []))
            job.saved, job.error = made, f"{type(e).__name__}: {e}"
            if keys:
                # some attorneys' invoices were made: trying again doesn't bill them twice. A day invoiced before
                # is billable again for the others only (billed_keys() is empty while it is invoiced).
                job.invoiced_keys = list(dict.fromkeys(job.billed_keys() + keys))
                job.invoiced = False
            show_save_error(self, e, "Could not make the files", "\n\nSaved before the problem:\n"
                            + "\n".join(p.name for p in made) if made else "")
            self._update_status()
            self._records_changed()
            return
        log.info("made %d file(s): %s, form type %s", len(paths), "+".join(outputs), self.s.form_choice)
        job.saved, job.error = paths, ""
        # Generate all won't bill this day again, unless a document was added while the preview was open: its
        # pages are not on the invoice just made
        job.invoiced |= "invoice" in outputs and job.docs == docs
        # the detailed copies and the math PDFs are kept for when they're asked for: not opened or printed now
        from ..invoice_math import is_math_file
        kept = [p for p in paths if p in detailed or is_math_file(p)]
        if self.s.open_after:
            for p in paths:
                if p not in kept:
                    open_path(p)
        if math and self.s.show_math and not previewed:  # (the preview showed it, with the files)
            self._show_math(math, self.s.folder_for("invoice", out_dir), [p for p in kept if is_math_file(p)])
        made = [p for p in paths if not (sheet and p == sheet.path)]
        folders = list(dict.fromkeys(p.parent for p in made))  # an output may have a folder of its own
        where = f"\n\nin {folders[0]}" if len(folders) == 1 else "\n\nin " + ", ".join(map(str, folders))
        text = f"Saved {plural(len(made), 'file')}:\n\n" + "\n".join(p.name for p in made) + where if made else ""
        if sheet and sheet.path:
            text += ("\n\n" if text else "") + run_sheet_summary(sheet)
            folders.append(sheet.path.parent)
        # (Print...: the PDFs but those kept; the run sheet is a workbook, printed from Excel)
        self._saved_box("Saved", text, list(dict.fromkeys(folders)),
                        files=[p for p in paths if p.suffix.lower() == ".pdf" and p not in kept])
        self._update_status()
        self._set_status(f"✓  Saved {len(paths)} file(s)", "ok")
        self._records_changed()

    def _show_math(self, made: list, folder: Path, saved: list | None = None) -> None:
        """The math of the invoices just made (preview.MathDialog), to glance at and close; `saved`: the PDFs of
        it Generate saved (Settings.save_math), which it names."""
        from .preview import MathDialog
        try:
            dlg = MathDialog(made, self.s, self, folder, saved=saved)
        except Exception as e:  # the invoices are saved; this is only an aside
            logfile.error("could not spell out the math", e)
            return
        before = self.s.save_math
        dlg.exec()
        self._math_saves_kept(before)

    def _math_saves_kept(self, before: str) -> None:
        """After a window showing the math, whose "Save the math" list may have changed Settings.save_math
        (before: as it was): the counts follow, Generate all's number counting the math PDFs (output_counts)."""
        if self.s.save_math != before:
            self._show_counts()
            self._refresh_job_labels()

    def _preview(self, case: CaseInfo, outputs: list[str], opts, ordered: dict | None = None,
                 speeds: dict | None = None) -> bool:
        """Shows the files Generate is about to make, as pictures (preview.PreviewDialog); True = save them.
        They are made in a temporary folder with a records database of its own (Ledger.preview_copy: the
        invoice shows the number it will get, and none is taken). The run sheet is not shown: it is a
        spreadsheet, and making it would add to the real one. True too when there is nothing to show. When the
        preview can't be made, the user is asked whether to save without one (a problem with the files
        themselves is then said as usual). With invoices among them, "The math" is a tab of the preview (unless
        Settings.show_math is off); self._math_previewed then says it was shown, so it isn't shown again after
        saving. ordered, speeds: each attorney's pages and speeds (Job.ordered_pages, Job.ordered_speeds), as
        generate() takes them."""
        from .preview import PreviewDialog
        self._math_previewed = False
        show = _previewed(outputs)
        if not show:
            return True
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            s = deepcopy(self.s)
            s.output_dir, s.output_dirs = tmp, {}
            s.save_math = "off"  # (its tab shows the math; Save saves it with the invoices)
            math: list = []
            try:
                files = generate(deepcopy(case), s, Path(tmp), show, opts, math=math, ordered=ordered, speeds=speeds,
                                 ledger=ledger_for(self.s).preview_copy(Path(tmp) / "records"))
                dlg = PreviewDialog([f for f in files if f.suffix.lower() == ".pdf"], self.s, self,
                                    _not_previewed(outputs), math if self.s.show_math else None)
                self._math_previewed = dlg.math is not None
            except Exception as e:
                logfile.error("could not make the preview", e)
                return QMessageBox.question(self, "No preview", "The preview could not be made "
                                            f"({type(e).__name__}).\n\nSave the files without it?") == QMessageBox.Yes
            before = self.s.save_math
            accepted = dlg.exec() == PreviewDialog.Accepted
            self._math_saves_kept(before)
            return accepted

    def _without_unchecked_invoice(self, job: Job, outputs: list[str]) -> list[str]:
        """The outputs to make, less the invoice when it is held (Job.invoice_hold): whose pages to bill is asked
        first (Whose pages...); Excerpts... rows that need checking, and more speeds on the same pages than are
        billed for now (Job.speed_problems), are not. Asks whether to make the others without it ([] = make
        nothing)."""
        if "invoice" in outputs and job.ownership_problem() and not job.portions_problem():
            self._whose_pages(job, job.ownership_problem())
            if job not in self.jobs or self._batch_running():
                return []
        if "invoice" not in outputs or not job.invoice_hold():
            return outputs
        others = [o for o in outputs if o != "invoice"]
        title = "Excerpts… needs checking" if job.portions_problem() else "Whose pages to bill" \
            if job.ownership_problem() else "The speeds need checking"
        if job.portions_problem():
            why = (f"{job.portions_check()}.\n\nNo invoice is made for this day until you check it: open "
                   "Excerpts… in the Invoice panel and tick who ordered which pages, or choose \"Remove this "
                   "day's excerpts\".")
        elif not job.ownership_problem():  # more speeds on the same pages than are billed for now
            why = (f"{job.invoice_hold()}.\n\nNo invoice is made for this day until you change a firm's speed "
                   "(Who ordered what, or Excerpts… for a run of pages).")
        else:
            why = (f"{job.ownership_problem()[0].upper()}{job.ownership_problem()[1:]}.\n\nNo invoice is made "
                   "for this job until you choose whose pages to bill (Whose pages… in the Invoice panel).")
        if not others:
            QMessageBox.warning(self, title, why)
            return []
        if QMessageBox.question(self, title, why + "\n\nMake the other outputs without the invoice?") \
                != QMessageBox.Yes:
            return []
        if job not in self.jobs or self._batch_running():
            return []
        return others

    def _sheets_free(self, targets: list[str], button: str) -> bool:
        """Before anything is made: the run sheets the takes go on can be written. One open in another program
        (Excel) is named in a message saying to close it, and False is returned: nothing is made, so that the
        takes of a trial never go on its run sheet with a day missing. targets: the run sheets chosen ("" or
        None: a new one, nothing to check); button: "Generate" or "Generate all", to press again."""
        from ..runsheet import is_locked
        locked = list(dict.fromkeys(t for t in targets if t and is_locked(t)))
        if not locked:
            return True
        names = "\n".join(f"•  {Path(t).name}" for t in locked)
        box = QMessageBox(QMessageBox.Warning, "Close the run sheet first",
                          f"{'This run sheet is' if len(locked) == 1 else 'These run sheets are'} open in another "
                          f"program (probably Excel), so the takes can't be added:\n\n{names}\n\n"
                          f"Close {'it' if len(locked) == 1 else 'them'} (or the other copy of YinItAgreementForm "
                          f"that has {'it' if len(locked) == 1 else 'them'} open), then press {button} again. "
                          "Nothing was made.", QMessageBox.Ok, self)
        box.exec()
        return False

    def _run_sheet_for(self, job: Job, button: str = "Generate") -> str | None:
        """The run sheet to add the job's takes to ("" = a new one), as Settings → Run sheet says; asks when the
        case seems to have one already. None = cancelled, also when a workbook named for the case can't be
        opened just now (runsheet.SheetUnreadable: the user is told which, and to press `button` again; nothing
        is made)."""
        if self.s.runsheet_existing == "new":
            return ""
        try:
            found = find_sheets(job.case, [runsheets_folder(self.s), *input_folders(job)])
        except SheetUnreadable as e:
            QMessageBox.warning(self, "Run sheet", f"{e}\n\nNothing was made. Press {button} again once it can "
                                                   "be opened.")
            return None
        if not found:
            return ""
        if self.s.runsheet_existing == "add":
            return str(found[0].path)
        dlg = RunSheetDialog(found, job.title(), self)
        return dlg.choice() if dlg.exec() == RunSheetDialog.Accepted else None

    def _ask_index_numbers(self, jobs: list[Job], button: str = "Generate") -> bool:
        """Generate's first question, and again just before the files are made (a document read meanwhile): when
        a job's documents have index numbers that don't match (Job.index_number_asks: they may be about
        different cases), says which document has which, the jobs of Generate all in one box, and asks whether to
        go on ("<button> anyway"). Going on keeps them together (Job.index_numbers_ok: not asked again until
        another document comes). The answer covers the documents the box named: one read while it was open is
        asked about again. True = go on."""
        while True:
            # (after an answer: the jobs still there, a document that came meanwhile asking again)
            asking = [j for j in jobs if j.index_number_asks() and j in self.jobs]
            if not asking:
                return True
            shown = {id(j): [d.key() for d in j.docs] for j in asking}
            if len(asking) == 1:
                text = index_number_text(asking[0].index_number_mismatch()) + "."
            else:
                text = (f"In {len(asking)} jobs the documents' index numbers don't match, so they may not be about "
                        "the same case:\n\n" + "\n".join(f"•  {j.title()}: "
                                                         f"{index_number_text(j.index_number_mismatch(), '')}"
                                                         for j in asking[:8]) + ("\n…" if len(asking) > 8 else ""))
            box = QMessageBox(QMessageBox.Warning, "Different index numbers",
                              text + "\n\nIf a document belongs to another case, go back, right-click it in the "
                              "list on the left (under its job) and choose Move to a job of its own.", parent=self)
            back = box.addButton("Go back", QMessageBox.RejectRole)
            go = box.addButton(f"{button} anyway", QMessageBox.AcceptRole)
            box.setDefaultButton(back)
            box.setEscapeButton(back)
            box.exec()
            if box.clickedButton() is not go or self._batch_running():
                return False
            for j in asking:
                j.index_numbers_ok = shown[id(j)]
            if self.cur in asking:
                self._show_index_notes()
            self._refresh_job_labels()  # (the ⚠ and "don't match" of the list go too)

    def _case_folder(self, jobs: list[Job], outputs: list[str], button: str = "Generate") -> bool:
        """Chooses each job's own case folder (Settings.case_folders: batch.case_folders, Job.folder). One that is
        already there with files in it is added to, given a new one beside it ("712345-2021 (2)"), or asked
        about, as Settings.case_folder_existing says; the question can say not to ask again. A job that chose its
        folder at an earlier Generate keeps it (until its folder's name changes). The caller puts the folders back
        when nothing is saved after all (Go back at the preview). False = Go back (nothing chosen)."""
        folders = case_folders(jobs, self.s, outputs)
        there = [f for f in folders if f.files]
        new = self.s.case_folder_existing == "new"
        if there and self.s.case_folder_existing == "ask":
            answer = self._ask_case_folders(there, button)
            if answer is None or self._batch_running():
                return False
            new = answer
        for f in folders:
            f.use(new=new)
        return True

    def _ask_case_folders(self, there: list, button: str) -> bool | None:
        """The question of _case_folder: these case folders (batch.CaseFolder) are already there with files in
        them; add the new files to them (False), or make new ones beside them (True)? None = Go back. "Don't ask
        again" makes the answer Settings.case_folder_existing."""
        one = len(there) == 1
        if one:
            f = there[0]
            text = (f"A folder for this case is already there, with {plural(f.files, 'file')} in it:\n\n{f.path}\n\n"
                    f"Add the new files to it, or make a new folder beside it (\"{f.new_name()}\")?")
        else:
            text = (f"{len(there)} case folders are already there, with files in them:\n\n"
                    + "\n".join(f"•  {f.path}  ({plural(f.files, 'file')})" for f in there[:8])
                    + ("\n…" if len(there) > 8 else "")
                    + "\n\nAdd the new files to them, or make a new folder beside each (\"… (2)\")?")
        box = QMessageBox(QMessageBox.Question, "The case folder is there" if one else "The case folders are there",
                          text, parent=self)
        add = box.addButton("Add to it" if one else "Add to them", QMessageBox.AcceptRole)
        new = box.addButton(f"New folder \"{there[0].new_name()}\"" if one else "New folders", QMessageBox.ActionRole)
        back = box.addButton("Go back", QMessageBox.RejectRole)
        box.setDefaultButton(add)
        box.setEscapeButton(back)
        never = QCheckBox("Don't ask again (Settings → Options → Folders)")
        box.setCheckBox(never)
        box.exec()
        if box.clickedButton() not in (add, new):
            return None
        if never.isChecked():
            self.s.case_folder_existing = "new" if box.clickedButton() is new else "add"
            self._save_settings()
        return box.clickedButton() is new

    def fill_all_jobs(self):
        """Generate all: makes the outputs of every ticked job on another thread (Ctrl+Shift+Enter).

        Asks first, as needed, in this order: the jobs whose documents' index numbers don't match, in one box
        (_ask_index_numbers); whose pages to bill, once per job that needs it; rows of a case's days that may be
        one firm (_ask_firms); the speed an e-mail asks for (_ask_speeds); a Parties number that isn't the firms
        that ordered (_ask_parties); the speeds of the firms of split orders that have none, every case in one
        box (_ask_split_speeds). Then the days whose invoice still waits for a choice (Excerpts..., whose pages,
        the speeds, a case with a day nobody is ticked on) are listed, to go back or go on without them. Blank
        fields are not asked about, but incomplete jobs are listed, to leave out or make anyway; then, for each
        invoice of the days made, whether there is an index when a page count typed below the transcript's
        changes it (_ask_index). Where the takes go is asked once per case when it has a run sheet already
        (Settings → Run sheet; the jobs of one case share a run sheet group, so its days go on one sheet), and
        those open in Excel are to be closed first. Last, the case folders already there with files in them, in
        one box (_case_folder), and the index numbers again, for a document read meanwhile.
        The batch works on copies of the jobs and the settings (shown first in a preview when
        Settings.preview_before_saving: _preview_batch, whose Go back leaves the case folders to be asked again);
        when it ends, each job's saved files, error and whether it was invoiced are copied back (_run_batch).
        With a single job this is the same as Generate.
        """
        if len(self.jobs) < 2:
            return self.fill()
        if self._batch_running():
            return
        self._commit_edit()
        self._sync_from_ui()
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        if not chosen:
            return
        outputs = self._outputs()
        if not outputs:
            return
        # jobs whose documents' index numbers don't match (they may be about different cases): asked first
        if not self._ask_index_numbers(chosen, "Generate all"):
            return
        chosen = [j for j in chosen if j in self.jobs]
        if "invoice" in outputs:  # whose pages to bill is asked now, once per job that needs it
            for j in [j for j in chosen if j.ownership_problem() and not j.portions_problem()]:
                if j in self.jobs:
                    self._whose_pages(j, f"{j.title()}: {j.ownership_problem()}")
                if self._batch_running():
                    return
            chosen = [j for j in chosen if j in self.jobs]
        # Rows of a case's days that may be one firm: asked now (one firm is one agreement, one invoice; the run
        # sheet doesn't name them). Each question is asked when an output chosen asks it (OutputSpec.asks).
        if courthouses.asks(outputs, "firms") and not self._ask_firms(_cases(chosen)):
            return
        chosen = [j for j in chosen if j in self.jobs]
        # The speed of the jobs whose e-mail asks for another speed than the Settings rule's: asked now, at once
        if courthouses.asks(outputs, "speed"):
            if not self._ask_speeds(chosen):
                return
            chosen = [j for j in chosen if j in self.jobs]
        if courthouses.asks(outputs, "parties") and not self._ask_parties(chosen):
            return
        chosen = [j for j in chosen if j in self.jobs]
        # The firms of split orders commit to a speed: those not set yet, asked at once for every case
        if courthouses.asks(outputs, "speeds") and not self._ask_split_speeds(chosen, "Generate all"):
            return
        chosen = [j for j in chosen if j in self.jobs]
        # Days whose invoice still waits for a choice (Excerpts..., whose pages to bill, the speeds): say which,
        # before anything is made. Going on bills the other days of their case without them. A case with a day
        # nobody is ticked on gets no invoice at all (batch.group_problem): said too, as fill_jobs groups them.
        held = [j for j in chosen if "invoice" in outputs and not j.invoiced and j.invoice_hold()]
        stuck: list[tuple[list[Job], str]] = []  # (the days of a case, why none of them is invoiced)
        if "invoice" in outputs:
            from ..batch import group_problem, invoice_groups
            billed = [j for j in chosen if not j.invoiced and not j.invoice_hold()]
            stuck = [(g, group_problem(g)) for g in invoice_groups(billed, self.s) if group_problem(g)]
        if held or stuck:
            lines = [f"•  {j.title()} {j.case.get('dates')}: {j.invoice_hold()}" for j in held]
            lines += [f"•  {g[0].title()} ({plural(len(g), 'day')}): {why}; going on makes no invoice for this "
                      "case" for g, why in stuck]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Not every day is decided")
            box.setText("Some days still need your choice before they can be invoiced:\n\n"
                        + "\n".join(lines[:8]) + ("\n…" if len(lines) > 8 else "")
                        + "\n\nGo back and make the choices (Whose pages… or Excerpts… in the Invoice panel, a "
                        "firm's speed in Who ordered what, or tick who ordered a day in its attorney table), or go "
                        "on anyway: the days listed get no "
                        "invoice now, and the other days of their case are invoiced without them, unless the case "
                        "has a day nobody is ticked on (they can be invoiced later).")
            back = box.addButton("Go back and decide", QMessageBox.RejectRole)
            go_on = box.addButton("Go on anyway", QMessageBox.AcceptRole)
            box.setDefaultButton(back)
            box.setEscapeButton(back)
            box.exec()
            if box.clickedButton() is not go_on or self._batch_running():  # (closed: as Go back)
                # show the first day to decide (for a case, its day with nobody ticked)
                first = held[0] if held else next((j for j in stuck[0][0] if not j.ticked_keys()), None)
                if first is not None and first in self.jobs and first is not self.cur and not self._batch_running():
                    self._select_job(first)
                return
            chosen = [j for j in chosen if j in self.jobs]

        def problems(j: Job) -> list[str]:
            """What is incomplete about a job, less the invoice choices just warned about."""
            return [p for p in j.output_problems(outputs) if not (j in held and p == j.invoice_hold())]

        gaps = [j for j in chosen if problems(j)]
        if gaps:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Some jobs are incomplete")
            box.setText(f"{len(gaps)} of {len(chosen)} jobs (marked ⚠) are incomplete:\n\n"
                        + "\n".join(f"•  {j.title()}:  {', '.join(problems(j))}" for j in gaps[:8])
                        + ("\n…" if len(gaps) > 8 else ""))
            ready = box.addButton(f"Do the {len(chosen) - len(gaps)} complete ones", QMessageBox.AcceptRole)
            everything = box.addButton("Do all (blanks left, no invoice without pages, no run sheet without a "
                                       "transcript)", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            ready.setEnabled(len(chosen) > len(gaps))
            box.exec()
            if box.clickedButton() == ready:
                chosen = [j for j in chosen if not problems(j)]
            elif box.clickedButton() != everything:
                return
        if "invoice" in outputs:
            # a typed page count that changes whether there is an index: asked per invoice, of the days that are
            # made (after the incomplete ones were left out: an invoice of fewer days may decide it another way)
            from ..batch import group_problem, invoice_groups
            chosen = [j for j in chosen if j in self.jobs]
            billed = [j for j in chosen if not j.invoiced and not j.invoice_hold()]
            if not self._ask_index([g for g in invoice_groups(billed, self.s) if not group_problem(g)]):
                return
        # Where each case's takes go: asked once per case (the days of a trial share one run sheet)
        sheet_for: dict[int, tuple[str, int]] = {}  # id(job) -> (the run sheet chosen, its case's number)
        asked: list[tuple[Job, str]] = []
        for j in chosen:
            if "runsheet" not in j.makeable(outputs):
                continue
            case_no = next((n for n, (k, _) in enumerate(asked) if same_case(ident(k.case), ident(j.case))), None)
            if case_no is None:
                target = self._run_sheet_for(j, "Generate all")
                if target is None or self._batch_running():
                    return
                case_no = len(asked)
                asked.append((j, target))
            sheet_for[id(j)] = (asked[case_no][1], case_no)
        # A read that ended while a question was on screen may have joined two jobs into one
        chosen = [j for j in chosen if j in self.jobs]
        if not chosen:
            return
        sheet_for = {k: v for k, v in sheet_for.items() if k in {id(j) for j in chosen}}
        if not self._sheets_free([t for t, _ in sheet_for.values()], "Generate all"):
            return
        # each case's own folder inside Save to: asked about once for all those with files in them already, before
        # the jobs are copied (the copies carry the folder chosen: Job.folder)
        folder_was = {id(j): (j.folder, j.folder_named) for j in chosen}
        if not self._case_folder(chosen, outputs, "Generate all") or self._batch_running():
            return
        # (a document read while a question was on screen, with an index number of its own: asked about too)
        if not self._ask_index_numbers(chosen, "Generate all"):
            for j in chosen:  # (Go back: nothing saved, the folders asked about next time)
                j.folder, j.folder_named = folder_was.get(id(j), (j.folder, j.folder_named))
            return
        chosen = [j for j in chosen if j in self.jobs]
        if not chosen:
            return
        # The batch is made on another thread while the window stays usable, so it gets its own copy of the
        # jobs and the settings: editing a job, an AI answer or a change in Settings can't reach files half made.
        def copy_jobs() -> dict:
            return {id(j): replace(j, case=deepcopy(j.case), docs=list(j.docs), saved=[], error="",
                                   invoiced_keys=list(j.invoiced_keys), portions=deepcopy(j.portions),
                                   speeds=dict(j.speeds),
                                   page_basis=deepcopy(j.page_basis), front_owner=dict(j.front_owner),
                                   runsheet_to=sheet_for.get(id(j), (None, None))[0],
                                   runsheet_group=sheet_for.get(id(j), (None, None))[1])
                    for j in self.jobs}
        copies = copy_jobs()
        todo, settings = [copies[id(j)] for j in chosen], deepcopy(self.s)
        if self.s.preview_before_saving and _previewed(outputs):
            # the files are shown first, with the math, made from copies of their own: what is saved is what was
            # shown (the copies above), and Go back saves nothing
            def save(previewed: bool) -> None:
                # (the preview's math tab may have just changed them: the math is saved as it was last shown)
                settings.save_math, settings.math_layout = self.s.save_math, self.s.math_layout
                for j in chosen:  # (the folders chosen: kept, so the same jobs generating again use them)
                    j.folder, j.folder_named = copies[id(j)].folder, copies[id(j)].folder_named
                self._run_batch(chosen, copies, todo, settings, outputs, previewed)
            shown = copy_jobs()
            for j in chosen:  # (until Save: Go back saves nothing, and the folders are asked about next time)
                j.folder, j.folder_named = folder_was.get(id(j), (j.folder, j.folder_named))
            self._preview_batch(chosen, shown, outputs, save)
        else:
            self._run_batch(chosen, copies, todo, settings, outputs, False)

    def _preview_batch(self, chosen: list[Job], copies: dict, outputs: list[str], then) -> None:
        """Generate all's preview before saving: the batch made on another thread into a temporary folder, with a
        records database of its own (Ledger.preview_copy: the numbers the invoices will get, none taken), then
        shown in one PreviewDialog with "The math" of every invoice. Save calls then(math shown); Go back saves
        nothing. The run sheets are not made for it (they are spreadsheets, and would add to the real ones)."""
        from .preview import PreviewDialog
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        s = deepcopy(self.s)
        s.output_dir, s.output_dirs = tmp.name, {}
        s.save_math = "off"  # (its tab shows the math; Save saves it with the invoices)
        shown = _previewed(outputs)
        math: list = []
        try:  # (before the window is marked busy: a records database that can't be opened left it busy for good)
            ledger = ledger_for(self.s).preview_copy(Path(tmp.name) / "records")
        except Exception as e:
            tmp.cleanup()
            logfile.error("could not make the preview of the batch", e)
            if QMessageBox.question(self, "No preview", f"The preview could not be made ({type(e).__name__})."
                                    "\n\nSave the files without it?") == QMessageBox.Yes \
                    and not self._batch_running():
                then(False)
            return
        self.filling = True
        self.work += 1
        self._update_header_buttons()
        self.fill_all_btn.setEnabled(False)
        self.fill_btn.setEnabled(False)
        self.busy.setVisible(True)
        self._set_status("Making the preview…", "busy")

        def ended():
            self.filling = False
            self.work = max(0, self.work - 1)
            self._update_header_buttons()
            self._idle()
            self.fill_btn.setEnabled(True)
            self._refresh_job_labels()

        def done(paths):
            ended()
            try:
                try:
                    dlg = PreviewDialog([p for p in paths if p.suffix.lower() == ".pdf"], self.s, self,
                                        _not_previewed(outputs, several=True),
                                        math if self.s.show_math else None)
                except Exception as e:  # (a page that can't be drawn: as for one job, see _preview)
                    logfile.error("could not show the preview of the batch", e)
                    self._job_status()
                    if QMessageBox.question(self, "No preview", f"The preview could not be made ({type(e).__name__})."
                                            "\n\nSave the files without it?") == QMessageBox.Yes \
                            and not self._batch_running():
                        then(False)
                    return
                before = self.s.save_math
                save = dlg.exec() == PreviewDialog.Accepted
                previewed = dlg.math is not None
                self._math_saves_kept(before)
            finally:
                tmp.cleanup()
            self._job_status()
            if save and not self._batch_running():
                then(previewed)

        def failed(msg):
            ended()
            tmp.cleanup()
            self._job_status()
            logfile.error("could not make the preview of the batch", RuntimeError(msg))
            if QMessageBox.question(self, "No preview", "The preview could not be made.\n\nSave the files "
                                    "without it?") == QMessageBox.Yes and not self._batch_running():
                then(False)

        self.runner.start(fill_jobs, [copies[id(j)] for j in chosen], s, batch=list(copies.values()),
                          outputs=shown, ledger=ledger, math=math, on_done=done, on_error=failed)

    def _run_batch(self, chosen: list[Job], copies: dict, todo: list[Job], settings, outputs: list[str],
                   previewed: bool) -> None:
        """Makes Generate all's files on another thread from the copies of the jobs, and copies what happened
        back to the jobs when it ends. previewed: the preview showed the math (else it is shown after, when
        Settings.show_math is on)."""
        math: list = []
        self.filling = True
        self.work += 1
        self._update_header_buttons()
        self.fill_all_btn.setEnabled(False)
        self.fill_btn.setEnabled(False)
        self.busy.setVisible(True)

        # No self.gen check here: New job is refused while self.filling, so these jobs are still the window's.
        def ended():
            self.filling = False
            self.work = max(0, self.work - 1)
            self._update_header_buttons()
            self._idle()
            self.fill_btn.setEnabled(True)

        def step(i, n, name):
            self.busy.setRange(0, n)
            self.busy.setValue(i)
            self._set_status(f"Making {i + 1} of {n}…", "busy")

        def done(paths):
            ended()
            changed = set()  # (ids of) jobs whose invoice changed while the batch ran (_billing_changed): ticked still
            for j in chosen:
                copy = copies[id(j)]
                j.saved, j.error = copy.saved, copy.error
                if _billing_changed(j, copy):
                    changed.add(id(j))
                    if copy.invoiced and not _pages_changed(j, copy):
                        # only who orders (or the speed, the parties) changed: those just invoiced aren't billed
                        # again by the next run, but an attorney ticked meanwhile is
                        j.invoiced_keys = list(dict.fromkeys(j.billed_keys() + _billed_by_copy(copy)))
                        j.invoiced = False
                else:
                    j.invoiced, j.invoiced_keys = copy.invoiced, copy.invoiced_keys
            # days whose only "error" is the invoice held back, as the user chose before it started (see
            # batch.fill_jobs: those messages come last, so an error that starts with one has nothing else)
            held_days = [j for j in chosen if HELD in j.error]  # (a held case's days: listed once, under the case)
            unbilled = [j for j in chosen if j.error.startswith(NOT_INVOICED) and not any(j is h for h in held_days)]
            failed = [j for j in chosen if j.error and not j.saved and j not in unbilled]
            partial = [j for j in chosen if j.error and j.saved and j not in unbilled]
            made = len(chosen) - len(failed) - sum(1 for j in unbilled if not j.saved)
            for j in chosen:
                if (j.saved or j.invoiced) and not j.error and id(j) not in changed:
                    j.include = False  # "Generate all" again only does what is left
            changed = [j for j in chosen if id(j) in changed and any(j is k for k in self.jobs)]
            self._update_status()
            folders = list(dict.fromkeys(str(p.parent) for p in paths))
            text = f"Saved {len(paths)} file{'s' if len(paths) != 1 else ''} for {plural(made, 'job')}"
            text += f" in\n{folders[0]}" if len(folders) == 1 else f" in {len(folders)} folders." if folders else "."
            held = _held_cases(held_days)  # a file of a case couldn't be saved: nothing (more) made for its days
            if held:
                # ("more": a trial's run sheet is written first, and may be saved before another of its files fails)
                text += (f"\n\nNothing more was made for {plural(len(held), 'case')} once a file couldn't be saved:\n"
                         + "\n".join(f"•  {case}: {why}" for case, why in held.items())
                         + "\n\nClose the file (in Excel, a PDF viewer or another copy of YinItAgreementForm), then "
                         "press Generate all again: those days are still ticked.")
                # (the day it happened on, whose own error doesn't say HELD, is among them: the same case)
                in_held = [ident(h.case) for h in held_days]
                failed = [j for j in failed if not any(same_case(ident(j.case), k) for k in in_held)]
                partial = [j for j in partial if not any(same_case(ident(j.case), k) for k in in_held)]
            if unbilled:
                text += f"\n\n{plural(len(unbilled), 'day')} not invoiced, as you chose:\n" + "\n".join(
                    f"•  {j.title()} {j.case.get('dates')}: {_reason(j.error[len(NOT_INVOICED):].strip())}"
                    for j in unbilled[:6])
            if failed:
                text += f"\n\n{len(failed)} could not be saved (is a file - a PDF or the Excel run sheet - open in " \
                        "another program?):\n" + "\n".join(f"•  {j.title()}: {_reason(j.error)}" for j in failed[:6])
            if partial:
                text += f"\n\n{len(partial)} job(s) are not complete:\n" + \
                        "\n".join(f"•  {j.title()}: {_reason(j.error)}" for j in partial[:6])
            if changed:
                text += (f"\n\n{len(changed)} job(s) changed while the files were made (a document was added, "
                         "the pages, Excerpts…, the attorneys ticked, the parties or the speed changed), so they "
                         "are still ticked: Generate all again to bill "
                         "them as they are now:\n" + "\n".join(f"•  {j.title()}" for j in changed[:6]))
            if math and self.s.show_math and not previewed:
                from ..invoice_math import is_math_file
                self._show_math(math, Path(folders[0]) if folders else self.s.folder_for("invoice"),
                                [p for p in paths if is_math_file(p)])
            self._saved_box("Batch finished", text, folders, "\n".join(str(p) for p in paths),
                            warn=bool(failed or held))
            self._set_status(f"✓  Saved {len(paths)} file(s)", "warn" if failed or held else "ok")
            self._records_changed()

        def crashed(msg):
            ended()
            self._update_status()
            QMessageBox.critical(self, "Could not fill the forms", msg)

        self.runner.start(fill_jobs, todo, settings, batch=list(copies.values()), outputs=outputs, math=math,
                          on_done=done, on_error=crashed, on_progress=step)

    # ------------------------------------------------------- misc
    def new_job(self):
        """Clears every job and starts over (Ctrl+N), asking first in a batch. Work already running in the
        background is not stopped, but its results are dropped (self.gen goes up), and AI questions still
        waiting their turn are never asked (_clear_jobs). Nothing happens when there is nothing to clear (the
        button is greyed out then)."""
        if not self._anything_to_clear():
            return
        if self._batch_running():
            return
        if len(self.jobs) > 1 and QMessageBox.question(
                self, "New job", f"Clear all {len(self.jobs)} jobs of this batch and start over?"
        ) != QMessageBox.Yes:
            return
        self._clear_jobs()

    def _anything_to_clear(self) -> bool:
        """Whether New job would do anything: a job with documents or typed details, pasted text, or documents
        still being read."""
        return not all(j.is_empty() for j in self.jobs) or bool(self.paste.toPlainText().strip()) or self.work > 0

    def _update_header_buttons(self) -> None:
        """New job (button and menu) is greyed out while there is nothing to clear, or Generate all runs."""
        if not hasattr(self, "new_action"):  # still building the window
            return
        on = self._anything_to_clear() and not self.filling
        self.new_btn.setEnabled(on)
        self.new_action.setEnabled(on)

    def _clear_jobs(self, job: Job | None = None) -> None:
        """Drops every job and starts with one (a blank one, or `job`); results of work still running are
        dropped (self.gen goes up), and AI questions still waiting their turn are never asked."""
        self.gen += 1
        self.ai_runner.drop_queued()
        if self.excerpts is not None:  # (it shows days of the jobs dropped)
            self.excerpts.clear()
        self.jobs = [job or Job()]
        self.cur = self.jobs[0]
        self._runsheet_touched = False  # the Run sheet box goes back to the saved choice (see _auto_runsheet)
        self._runsheet_auto = None
        self._auto_runsheet()
        self.ai_pending = self.work = 0
        self._loading.clear()
        self.busy.setRange(0, 0)
        self._refresh_jobs()
        self.paste.clear()
        self._show_case()
        self.busy.setVisible(False)
        self._set_status("Drop a document to begin", "")

    def open_past_job(self, origin: str) -> bool:
        """Opens a job again from its record (Records -> Open this job again), for a corrected invoice or
        another day of the case. origin: Activity.origin (JSON), or what the Records window put together from
        an older record's columns. The case comes back as it was filled in (batch.job_from_origin) and the
        documents it was read from are read again when they are still where they were. An invoice of several
        days is read from its documents alone, a job per day. Asks first when there is work on screen; False
        when nothing was opened (during Generate all a box says so, over the Records window the click came from:
        this window's status bar is behind it)."""
        if self.filling:
            QMessageBox.information(getattr(self, "_records_win", None) or self, "Open this job again",
                                    "Still making the files of the batch - one moment, then try again.")
            return False
        try:
            data = json.loads(origin)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            QMessageBox.information(self, "Open this job again", "This record doesn't say what it was made from.")
            return False
        if any(not j.is_empty() for j in self.jobs) and QMessageBox.question(
                self, "Open this job again", "Clear what is in the window now and open this job instead?"
        ) != QMessageBox.Yes:
            return False
        if self._batch_running():
            return False
        sources = [p for p in data.get("sources") or [] if isinstance(p, str)]
        there = [p for p in sources if Path(p).is_file()]
        several_days = data.get("joint") is True and there
        if several_days:
            self._clear_jobs()
        else:
            try:
                job = job_from_origin(data, self.s)
            except Exception as e:  # (a record damaged in a way job_from_origin doesn't expect)
                logfile.error("could not open a past job", e)
                QMessageBox.information(self, "Open this job again", "This record can't be opened again.")
                return False
            if len(there) < len(sources) or not sources:
                # Not every document will be read again: what was read from the missing ones would be lost
                # when the others are merged, so it is kept as if typed (not the defaults: they are worked out).
                # Not a page count of a transcript (SRC_PDF) when every PDF is read again: it is counted again
                # from the transcript. Kept as typed, the invoice would bill it as "the Pages field"
                # (Job.pages_typed) instead of the user's own pages of a transcript of several reporters. (When
                # a PDF is missing, it may be the transcript: an order letter saved as a PDF is no count.)
                # Nor a speed a document named: the agreement form's speed is the Settings rule's unless the user
                # chose one (merge.refresh_speed), and an e-mail's speed was never the user's choice.
                pdfs = [p for p in sources if p.lower().endswith(".pdf")]
                pdf_back = bool(pdfs) and all(p in there for p in pdfs)
                for key, f in job.case.fields.items():
                    if f.value and f.source not in (SRC_DEFAULT, SRC_DERIVED) and not (
                            f.source == SRC_PDF and pdf_back) and key != "delivery":
                        f.source = SRC_USER
            apply_defaults(job.case, self.s)
            self._clear_jobs(job)
            self._show_job()
        if there:
            self.add_files(there, batch=bool(several_days), same_job=not several_days)
        gone = len(sources) - len(there)
        if gone:
            self._toast(f"{plural(gone, 'document')} of this job {'is' if gone == 1 else 'are'} no longer where "
                        f"{'it was' if gone == 1 else 'they were'}: drop {'it' if gone == 1 else 'them'} here to "
                        "make an invoice again.")
        self.raise_()
        self.activateWindow()
        return True

    def open_settings(self, _checked: bool = False):
        """Opens Settings (not while Generate all is making files: it reads the settings, and the answers about
        the firms, on its thread). After Save, the theme, the window's options, the rate sheets and the AI check
        are refreshed and every job is merged and priced again, with the answers about rows that may be one firm
        as they are now (one forgotten, for now or for good, splits its rows again and is asked about again)."""
        if self._batch_running():
            return
        def pairs() -> set[frozenset]:
            return {frozenset(r[:2]) for r in self.s.firm_answers + self._session_answers}
        before, sheet = pairs(), self.s.sheet().name  # (the one in use: the one Settings names may be gone)
        dlg = SettingsDialog(self.s, self, self._session_answers)
        if dlg.exec():
            self._session_answers = dlg.session_answers
            self._settings_changed({k for pair in before - pairs() for k in pair}, sheet)

    def _settings_changed(self, forgotten: set[str] | frozenset = frozenset(), sheet: str | None = None) -> None:
        """The settings were changed (Settings saved, the welcome questions answered, settings imported): the
        theme, the window's options, the rate sheets and the AI check are refreshed and every job is merged
        and priced again (with the answers about rows that may be one firm as they are now). forgotten: the
        keys of rows whose answer was forgotten: a job that joined them reads its rows again (split_forgotten).
        sheet: the rate sheet Settings named before (Settings, an import); the one in use now is checked
        (_check_rate_sheet), with Go back to that one, and asked about even if it was this session when it is
        another."""
        self._use_answers()
        if forgotten:
            self._sync_from_ui()
            for job in self.jobs:
                if split_forgotten(job, forgotten, self.s) and job is self.cur:
                    self._show_attorneys()  # (the merge below starts from the table)
        if self.s.zoom != zooming.zoom():
            self.set_zoom(self.s.zoom)  # (the theme too)
        apply_theme(self.app, self.s.theme)
        self.per_email.setChecked(self.s.per_email)
        self.sign_rep.setChecked(self.s.sign_reporter)
        for combo, value in ((self.form_choice, self.s.form_choice), (self.mofr_division, self.s.mofr_division),
                             (self.rs_existing, self.s.runsheet_existing)):
            with QSignalBlocker(combo):
                combo.setCurrentIndex(max(0, combo.findData(value)))
        set_checks(self.output_boxes, self.s.outputs)
        self._auto_runsheet()
        self.s.reload_rates()
        # an incomplete sheet is warned about before the jobs are priced from it: Go back leaves them as they were
        if not self._check_rate_sheet(sheet or "", again=sheet is not None and sheet != self.s.rate_sheet,
                                      priced=False):
            self._save_settings()  # (Settings, or the import, saved the sheet gone back from)
        self._fill_sheet_box()
        self._fill_invoice_speeds()
        self._check_ai()
        if self.cur.docs:
            self._remerge()
        self._apply_speed_rule()  # (a job without documents too: the rule may be another now)
        self._remerge_others()
        self._update_status()

    # ------------------------------------------------------- outputs and records
    def _auto_runsheet(self) -> None:
        """Ticks the Run sheet box when two or more reporters wrote the transcripts of a case (the initials at
        the foot of the pages, the names on the title pages: batch.case_reporters, the days of a case together), and
        unticks it when one did. Only for these jobs: Settings.outputs keeps the user's own choice, and once the
        user clicks the box it is left as they set it (until New job). Without a transcript the box is as saved."""
        days: dict[tuple, list[Job]] = {}
        for job in self.jobs:
            if job.transcripts():
                days.setdefault(job.name_key(), []).append(job)
        cases = {k: case_reporters(js) for k, js in days.items()}
        for job in self.jobs:  # ticked for a case of several reporters, the box leaves out the others
            job.runsheet_unneeded = not self._runsheet_touched and len(cases.get(job.name_key(), ())) == 1
        self._runsheet_auto = any(len(who) > 1 for who in cases.values()) if cases else None
        box = getattr(self, "output_boxes", {}).get("runsheet")  # (none while the window is built)
        if self._runsheet_touched or box is None:
            return
        want = "runsheet" in self._outputs()
        if box.isChecked() != want:
            with QSignalBlocker(box):
                box.setChecked(want)
            self._refresh_outputs()
            self._refresh_job_labels()

    def _ticked(self, key: str) -> bool:
        """The output's box in the Outputs box is ticked (False for an output the courthouse doesn't make)."""
        box = self.output_boxes.get(key)
        return box is not None and box.isChecked()

    def _outputs(self) -> list[str]:
        """The outputs to make (keys of OUTPUTS): Settings.outputs, the boxes ticked in the Outputs box, but for
        the run sheet, ticked or not for these jobs by how many reporters wrote them (_auto_runsheet) until the
        user clicks its box."""
        out = [k for k in OUTPUTS if k in self.s.outputs and k != "runsheet"]
        auto = None if self._runsheet_touched else self._runsheet_auto
        if "runsheet" in OUTPUTS and (("runsheet" in self.s.outputs) if auto is None else auto):
            out.append("runsheet")
        return out

    def _outputs_changed(self, key: str = ""):
        """The user ticked or unticked an output: the outputs ticked are saved straight away, as the default for
        next time. A run sheet ticked by _auto_runsheet keeps the saved choice for it until the user clicks it."""
        if key == "runsheet":
            self._runsheet_touched = True
            self._auto_runsheet()  # (every case gets the run sheet the user asked for)
        saved = [k for k in OUTPUTS if self.output_boxes[k].isChecked()]
        if not self._runsheet_touched and self._runsheet_auto is not None:
            saved = [k for k in saved if k != "runsheet"] + (["runsheet"] if "runsheet" in self.s.outputs else [])
        self._set_opt("outputs", [k for k in OUTPUTS if k in saved])
        self._refresh_outputs()
        self._refresh_job_labels()

    def _refresh_outputs(self):
        """Greys out the options of the outputs not ticked. For the current job: the tooltips and warnings of
        the invoice (it needs pages to bill: a transcript, or pages typed) and the run sheet (it needs a
        transcript), the minute agreement's speed, the Invoice panel's parties and prices, the Who pays what
        and Who ordered what cards and, when open, the Excerpts window (_follow_excerpts)."""
        if not getattr(self, "_outputs_built", False):  # still building the window
            return
        for key, body in self.output_opts.items():
            body.setEnabled(self.output_boxes[key].isChecked())
        job = self.cur
        pages = job.invoice_pages()
        if "invoice" in self.output_boxes:
            self.output_boxes["invoice"].setToolTip(
                "An invoice for each ticked attorney, priced from the rate sheet" if pages else
                "Needs pages to bill: a transcript PDF among the inputs, or the pages\n"
                "typed in Est. number of pages (a number read from an e-mail isn't billed).")
        if "runsheet" in self.output_boxes:
            self.output_boxes["runsheet"].setToolTip(
                "Adds the transcript's takes (who wrote which pages, from the initials on each page)\n"
                "to the case's run sheet in Excel, or starts one (Settings → Run sheet)" if job.transcript_pages()
                else "Needs a transcript PDF among the inputs (the initials on its pages).\n"
                     "Jobs without one get no run sheet.")
            self.rs_info.setText("" if job.transcript_pages() or not job.docs else
                                 "⚠ no transcript PDF: no run sheet for this job")
            self.rs_info.setVisible(bool(self.rs_info.text()))
        self._show_pages_help(job)
        self._show_form_speed()
        self._show_who_pays(*self._show_invoice_prices())  # (the invoices priced once for both)
        self._show_counts()
        self._show_orders()
        self._place_output_cols()  # the prices may need more room than the panels have
        self._update_header_buttons()
        self._follow_excerpts()

    def _show_invoice_prices(self) -> tuple[list | None, int]:
        """The Invoice panel's parties, Includes line (Peripherals…) and prices for the current job. When Generate
        all bills it with other days of its case, the first line says so and the prices are the joint invoice's.
        When the attorneys don't all pay the same (they ordered different days or pages), a line per attorney. A
        firm of a split order whose speed Generate still asks is priced at the form's speed, with a "?"
        (_price_lines). No prices while the invoice is held (whose pages, Excerpts... to check, more speeds on the
        same pages than are billed, a day of the case nobody is ticked on). Returns the invoices priced
        (invoice.firm_invoices; None when there are none to price, the panel saying why) and how many days they
        cover, for "Who pays what"."""
        from ..batch import joint_invoice
        job = self.cur
        group = self._invoice_group()
        case, opts = joint_invoice(group)
        split = job.portions is not None  # Excerpts... decides this day's parties (or needs checking)
        check = job.portions_check()
        with QSignalBlocker(self.inv_parties):
            # never fewer parties than the attorneys ticked on a day: each would pay a whole original
            self.inv_parties.setMinimum(max([1] + [len(j.ticked_keys()) for j in group]))
            self.inv_parties.setValue(opts.parties)
        self.inv_parties.setEnabled(not split)
        self.inv_parties.setToolTip("This day's pages are split between the attorneys under Excerpts…" if split
                                    else PARTIES_TIP.format(**_who_splits(self.s)))
        why = self._who_ordered_why(job)
        self.inv_who.setEnabled(not why or split)  # rows that need checking can always be opened, to fix them
        self.inv_who.setText("⚠ Excerpts…" if check else "✓ Excerpts…" if split else "Excerpts…")
        self.inv_who.setToolTip(check + "\n(click to check it)" if check else why or WHO_TIP.format(**_who_splits(self.s)))
        owned = self._show_whose_pages(job)
        with QSignalBlocker(self.inv_detail):
            self.inv_detail.setChecked(job.invoice_detail)  # this job's own (Generate this job uses it)
        from ..batch import index_question, typed_days
        from ..invoice import index_priced
        on_typed = any(j.index_on_typed for j in typed_days(group))
        self.inv_peripherals_info.setText(_lines(self._peripherals_text(
            opts, index_question(group, self.s), on_typed, len(group) > 1, index_priced(case, self.s, opts))))
        self.inv_info.setToolTip("")
        if not job.invoice_pages() and not owned:
            if not job.docs:
                self.inv_info.setText("")  # an empty job has nothing to warn about yet
            elif job.transcript_pages() or job.pages_typed():
                self._warn("⚠ the Pages field says 0: no invoice for this job")
            else:
                self._warn("⚠ no transcript PDF: type the pages to bill in Est. number of pages")
            return None, 1
        if owned:  # whose pages to bill isn't known: no prices until Whose pages... says
            # (the reason names the file, which can be very long: it goes in the tooltip)
            self.inv_info.setText("⚠ Choose whose pages to bill (Whose pages…)")
            self.inv_info.setToolTip(f"{owned[0].upper()}{owned[1:]}\n\n"
                                     "No invoice is made for this job until you say whose pages to bill.")
            return None, 1
        if check:  # no prices: nothing is billed for this day until the rows are checked
            self._warn(f"⚠ {check}")
            self.inv_info.setToolTip("No invoice is made for this day until Excerpts… is checked:\n"
                                     "tick who ordered which pages, or choose \"Remove this day's excerpts\".")
            return None, 1
        if job.speed_problems():  # more speeds on the same pages than are billed for now: no prices either
            self._warn(f"⚠ {job.invoice_hold()}")
            self.inv_info.setToolTip("No invoice is made for this day until a firm's speed is changed\n"
                                     "(Who ordered what, or Excerpts… for a run of pages).")
            return None, 1
        from ..batch import group_problem
        held = group_problem(group)
        if held:  # nobody ticked on a day of the joint invoice: no prices until it says who ordered it
            self._warn(f"⚠ Generate all: no invoice for these {len(group)} days yet:\n{held}")
            self.inv_info.setToolTip("Tick the attorneys who ordered that day's pages (in its attorney table);\n"
                                     "until then Generate all makes the other outputs but no invoice for the case.")
            return None, 1
        try:
            from ..invoice import firm_invoices
            firms = firm_invoices(case, self.s, opts)
        except Exception as e:  # a broken rate sheet must not break the window
            self._warn(f"⚠ {e}")
            return None, 1
        if not firms:  # (every attorney ticked was invoiced already by a run that stopped, or ordered no pages)
            self._warn("No invoice left to make: every attorney ticked is invoiced already or ordered no pages")
            return None, 1
        from ..batch import speeds_to_ask
        asked = {a.key for a in speeds_to_ask(group, self.s)}  # (priced at the form's speed meanwhile)
        prices, per_firm = _price_lines(firms, self.case.get("delivery"), asked)
        ask_tip = ("A speed with \"?\" is not set yet: firms that order the same pages commit to a speed.\n"
                   "Generate asks for it (set it beforehand in Who ordered what)." if asked else "")
        self.inv_info.setToolTip(ask_tip)
        if len(group) > 1:
            self.inv_info.setText(f"Generate all: one invoice for {len(group)} days ({opts.pages} pp.)\n{prices}")
            forms = ("It also makes one minute agreement per attorney (its days and pages) and one MOFR\n"
                     "for these days (Settings → Options).\n" if self.s.forms_per_case else
                     "The minute agreements and MOFRs are made for each day (Settings → Options).\n")
            self.inv_info.setToolTip(
                f"Generate all bills these {len(group)} days of the case on one invoice for each attorney\n"
                f"(the days it is ticked on, and the pages it ordered).\n{forms}"
                f"\"Generate this job\" makes the day shown alone ({job.invoice_pages()} pp.)."
                + (f"\n\n{ask_tip}" if ask_tip else ""))
        else:
            self.inv_info.setText(f"{opts.pages} pp." + ("\n" if per_firm else " · ") + prices)
        return firms, len(group)

    def _warn(self, text: str) -> None:
        """A warning in the Invoice panel's Prices row, its long lines broken (a file name too): a label is as
        wide as its longest line, and a long one made the panel, and the window, wider than the screen."""
        self.inv_info.setText("\n".join(textwrap.fill(line, WARN_WIDTH) for line in text.split("\n")))

    def _form_speed_text(self) -> str:
        """The one speed and rate the minute agreement form names: 'Expedite · $5.65 a page'; with firms that
        have a speed of their own (Who ordered what), 'Each firm's own speed (Expedite · $5.65 a page without
        one)'."""
        speed, rate = self.case.get("delivery"), self.case.get("rate")
        if not speed:
            text = "(none: choose one under Minute agreement form details)"
        elif not rate:
            text = f"{speed} · no rate (type it under Minute agreement form details)"
        else:
            text = f"{speed} · ${rate.lstrip('$')} a page"
        return f"Each firm's own speed ({text} without one)" if self.cur.ordered_speeds() else text

    def _show_form_speed(self) -> None:
        """The Minute agreement panel's Speed row (see _form_speed_text)."""
        self.ag_speed.setText(self._form_speed_text())

    def _show_who_pays(self, firms: list | None, days: int = 1) -> None:
        """The "Who pays what" card: a row per invoice about to be made (the firm, its pages, how it ordered
        them, what it pays at each speed and what that comes to a page, or its one speed and amount when every
        invoice bills one; invoice_math.who_pays), under the speed and rate the minute agreement forms name (each
        firm's own, when some have one: _pay_html). Shown while Invoice is ticked and the job has pages to bill
        (or whose pages to bill is to be chosen); when no invoice can be priced yet, the Invoice panel's
        warning says why. firms: what _show_invoice_prices priced (nothing is priced twice)."""
        import html
        from ..invoice_math import page_rates, who_pays
        job = self.cur
        on = self._ticked("invoice") and bool(job.invoice_pages() or job.ownership_problem())
        self.pays_card.setVisible(on)
        self._pay_firms = list(firms or []) if on else []
        self.inv_form.setRowVisible(self.inv_math, bool(self._pay_firms))  # (the Invoice panel's link to the math)
        if not on:
            return
        tip = PAYS_TIP.format(**_who_splits(self.s))  # (as Settings say now)
        self.pays_title.setToolTip(tip)
        self.pays.setToolTip(tip)
        if firms:
            text = _pay_html(who_pays(firms), self.case.get("delivery"), self.case.get("rate"), days,
                             page_rates(firms), _own_words(firms, self.s),
                             any(j.speeds or j.ordered_speeds() for j in self._invoice_group()))
        else:
            why = self.inv_info.text() or "Nothing to bill yet."
            text = html.escape(why).replace("\n", "<br>")
        if text != self.pays.text():  # (set only when it changes: it is worked out at every keystroke)
            self.pays.setText(text)

    def _show_counts(self) -> None:
        """Under each ticked output's heading, how many files Generate makes of it for the job shown ("Will
        generate 2 minute agreement forms", batch.output_counts) and, with several jobs, how many Generate all
        makes; it follows every tick (attorneys, outputs, Excerpts...). Hidden while the output is unticked, and
        for an empty job when it is the only one. What Generate all makes is kept in _all_counts for the job
        list's button (_refresh_job_labels), so a keystroke counts every job once, not twice."""
        from ..batch import output_counts
        outputs, job = self._outputs(), self.cur
        shown = not job.is_empty()
        # (again: Generate this job bills a day invoiced already once more; Generate all doesn't)
        counts = output_counts([job], outputs, self.s, again=True) if shown else None
        everyone = None
        if len(self.jobs) > 1:
            everyone = output_counts([j for j in self.jobs if j.include and not j.is_empty()], outputs, self.s)
        self._all_counts = everyone
        held = ""
        if shown and "invoice" in outputs and job.invoice_hold():  # (Generate asks first: see invoice_hold)
            held = "Excerpts… is checked" if job.portions_problem() else "Whose pages… is chosen" \
                if job.ownership_problem() else "the speeds are checked"
        for key, label in self.output_counts.items():
            on = key in outputs and (shown or everyone is not None)
            label.setText(_count_text(key, counts, everyone, held if key == "invoice" else "") if on else "")
            label.setVisible(on)

    def _show_live_math(self, _link: str = "") -> None:
        """The links of the Who pays what card and the Invoice panel: the whole math of the invoices the card
        shows, as things stand (preview.MathDialog)."""
        if not self._pay_firms:
            return
        from .preview import MathDialog
        try:
            dlg = MathDialog([(f, "") for f in self._pay_firms], self.s, self, live=True)
        except Exception as e:  # (as _show_math: the math of an odd invoice must not break the window)
            logfile.error("could not show the math", e)
            self._toast(f"The math could not be shown ({type(e).__name__}).")
            return
        before = self.s.save_math
        dlg.exec()
        self._math_saves_kept(before)

    def _show_pages_help(self, job: Job) -> None:
        """The lines under Est. number of pages: what the number counts, and a warning when the transcript PDF's
        readings of its pages disagree (Job.pages_help); the row is hidden for a job without a transcript."""
        line, warning = job.pages_help()
        # (the row first: showing it shows every label in it)
        self.order_form.setRowVisible(self.pages_help_row, bool(line or warning))
        self.pages_help.setText(line)
        self.pages_help.setVisible(bool(line))
        self.pages_warn.setText(warning)
        self.pages_warn.setVisible(bool(warning))

    def _show_whose_pages(self, job: Job) -> str:
        """The Invoice panel's Billed row, shown for a transcript of several reporters: "You wrote 65 of 153 total
        pages" (the whole transcripts' own pages, the word index left out; "Billing 153 of 153 total pages (chosen
        under Whose pages…)" when Whose pages... chose others, "The Pages field: 40 of 153 total pages" when typed),
        and the Whose pages... button. Returns why the invoice is held until Whose pages... says ("" when it
        isn't)."""
        shared = job.shared_transcripts()
        held = job.ownership_problem()
        self.inv_form.setRowVisible(self.inv_pages_row, bool(shared or held))
        if not (shared or held):
            return ""
        whole = job.transcript_pages()
        mine = all(job.basis_of(d)[0] == "me" for d in job.transcripts())
        if held:
            text = "Whose pages?"
        elif job.pages_typed():
            text = f"The Pages field: {job.invoice_pages()} of {whole} total pages"
        elif mine:
            text = f"You wrote {job.invoice_pages()} of {whole} total pages"
        else:
            text = f"Billing {job.invoice_pages()} of {whole} total pages\n(chosen under Whose pages…)"
        others = [r for r in job.bill_reporters() if r != "me"]
        if others and not held:  # invoices in other reporters' names too (Whose pages... ticked them)
            if "me" not in job.bill_reporters():
                text = "Not yours"
            text += "\nalso " + ", ".join(f"{r.upper()}'s ({job.billed_by(r).invoice_pages()})" for r in others)
            bare = [r.upper() for r in others if not self.s.reporter(r).has_details()]
            if bare:
                text += f"\n⚠ no invoice details for {', '.join(bare)}: Settings → Run sheet → Invoices for"
        self.inv_pages_info.setText(_lines(text))
        self.inv_whose.setText("⚠ Whose pages…" if held else "Whose pages…")
        self.inv_whose.setToolTip(held or WHOSE_TIP)
        return held

    def _whose_pages(self, job: Job, reason: str = "") -> bool:
        """Whose pages…: which pages of the job's transcripts of several reporters (or of the one held, see
        Job.ownership_problem) are billed. Returns True when OK was clicked and the job is still there. The job
        is merged again, so the Pages field shows the pages billed."""
        from .dialogs import PagesOwnerDialog
        docs = [d for d in job.transcripts() if len(d.reporters()) > 1 or (
            d.reporters() and "*" not in job.basis_of(d) and not job.billed_pages_of(d))]
        if not docs:
            QMessageBox.information(self, "Whose pages", "The transcripts of this job have one reporter's pages "
                                    "each (or no initials at all): they are billed in full.")
            return False
        dlg = PagesOwnerDialog([(d.key(), d.ing.name, d.owners()) for d in docs], set(job.own), job.page_basis,
                               job.front_owner, reason, self, self.s)
        if not dlg.exec() or not any(job is j for j in self.jobs):
            return False
        basis, front = dlg.values()
        job.page_basis = {**job.page_basis, **basis}
        job.front_owner = {**job.front_owner, **front}
        self._remerge(job)  # the Pages field shows the pages billed
        self._update_status()
        return True

    def _show_orders(self):
        """The "Who ordered what" card: for every day the current job's invoice covers (the days of the case
        Generate all bills together, else the day shown), a row per ordering attorney (one per Attorney.key: a
        firm's row naming several attorneys is one) saying which pages it ordered (the whole day, or which
        stretch), the speed it ordered them at (a box to choose it in: _speed_box), who also ordered them and how
        many of your pages that bills. Nothing is left to
        guess: an attorney not ticked on a day says it orders nothing, a day waiting for a choice says so, a day
        nobody is ticked on holds the whole case's invoice back (batch.group_problem), and a row that bills
        none of the pages says it gets no invoice for them. Shown only while Invoice is ticked and the job has
        a transcript or pages to bill; never an empty table (a row says why there is nothing to bill)."""
        job = self.cur
        on = self._ticked("invoice") and bool(job.transcript_pages() or job.invoice_pages())
        self.orders_card.setVisible(on)
        if not on:
            return
        group = self._invoice_group()
        from ..batch import OrderLine, group_attorneys, group_problem
        everyone = group_attorneys(group) if len(group) > 1 else []
        stuck = group_problem(group)  # a day nobody is ticked on: no invoice for the case (see batch.fill_jobs)
        lines: list[tuple[Job, OrderLine]] = []
        for j in group:
            if stuck and not j.ticked_keys():
                lines.append((j, OrderLine(j.case.get("dates"), "", "(nobody ticked)", note=NOBODY_DAY)))
            else:
                lines += [(j, line) for line in j.order_lines(everyone)]
        if not lines:  # say why there is nothing to bill rather than show an empty table
            why = (job.invoice_hold() or ("the Pages field says 0: no invoice for this job"
                                          if not job.invoice_pages() else
                                          "tick the attorneys who ordered in the Attorneys table"))
            lines = [(job, OrderLine(job.case.get("dates"), "", "—" if job.ticked_keys() else "(nobody ticked)",
                                     note="⚠ " + why))]
        mine = all(j.basis_of(d)[0] == "me" for j in group for d in j.transcripts())
        none = "none of these pages are yours" if mine else "none of these pages are the ones billed"
        t = self.orders
        t.setRowCount(0)
        for j, line in lines:
            r = t.rowCount()
            t.insertRow(r)
            if line.note:
                ordered, printed, shared, billed = line.note, "", "", ""
            else:
                part = ", ".join(f"{a}–{b}" if a != b else str(a) for a, b in line.spans)
                if line.typed:  # the Pages field's number is billed, not the transcript's pages: say so
                    ordered = (f"every page (Pages field typed: {line.pages} billed)" if line.whole else
                               f"excerpt: {part} of the {line.pages} pages typed in the Pages field")
                else:
                    ordered = f"every page, 1–{line.pages}" if line.whole else f"excerpt: {part} of {line.pages}"
                printed = ", ".join(p for p in line.printed if p)
                shared = "; ".join(f"{self._name_of(group, o)} ({', '.join(f'{a}–{b}' for a, b in spans)})"
                                   for o, spans in line.shared.items()) or "nobody (alone)"
                if line.parties:
                    shared = f"split {line.parties} ways (the Parties number)"
                billed = str(line.billed)
                if not line.billed:  # an attorney with no pages billed gets no invoice for them (see firm_pages)
                    elsewhere = any(o.key == line.key and o.billed and not o.note for _, o in lines)
                    billed = f"0 ({none})" if elsewhere else f"0 (no invoice: {none})"
                elif stuck:
                    billed += " (held: no invoice yet, see ⚠)"
            for c, text in enumerate((line.day, line.name, ordered, "", printed, shared, billed)):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, id(j))
                if (c == 2 and line.note.startswith("⚠")) or (c == 6 and "no invoice" in text):
                    it.setForeground(QColor("#d97706"))  # (the amber of the warnings, light and dark)
                t.setItem(r, c, it)
            if not line.note and line.key:
                t.setCellWidget(r, ORDER_SPEED, self._speed_box(j, line.key))
        t.resizeColumnsToContents()
        for c in range(2, len(ORDER_COLS)):  # long notes wrap instead of making the table wider than the card
            t.setColumnWidth(c, min(t.columnWidth(c), z(320)))
        t.resizeRowsToContents()
        self._fit_orders_height()
        whose = bool(job.ownership_problem()) and not job.portions_problem()
        self.orders_edit.setText("⚠ Whose pages…" if whose else "Edit excerpts…")
        self.orders_edit.setToolTip(
            "Choose whose pages of the day shown are billed (the same as Whose pages… in the Invoice panel)"
            if whose else "Who ordered which pages of each day of the case, as one table (the same as Excerpts… "
                          "in the Invoice panel)")
        days = len(group)
        if stuck:
            where = f"⚠ No invoice for these {days} days of the case yet: {stuck}."
        elif days > 1:
            where = f"Generate all bills these {days} days of the case on one invoice per attorney" + (
                ", with one minute agreement per attorney and one MOFR for them." if self.s.forms_per_case else ".")
        elif job.invoiced:
            where = "The day shown, invoiced already: Generate all won't bill it again (Generate this job does)."
        elif len(self.jobs) > 1 and not job.include:
            where = "The day shown, unticked in the job list: Generate all leaves it out."
        elif len(self.jobs) > 1 and not job.invoice_hold():
            where = "The day shown. Generate all may bill it with other days."
        else:
            where = "The day shown."
        self.orders_info.setText(where + " Each attorney is billed for the pages written in its row and nothing "
                                 "else; \"Your pages billed\" counts the pages billed among them: on a transcript "
                                 "of several reporters only yours (or those chosen under Whose pages…), and the "
                                 "Pages field's number when you typed one. Firms that ordered the same pages "
                                 "commit to a speed (Generate asks when one isn't set); a firm ordering alone may "
                                 "choose one from its invoice.")

    def _speed_box(self, job: Job, key: str) -> QuietCombo:
        """The "Who ordered what" card's Speed box of one firm on one day: the speed it ordered (Job.speeds, or the
        one set on its only run in Excerpts...), the rate sheet's speeds, cheapest first, and "Chooses on the
        invoice" while it orders alone (a firm sharing pages commits to a speed: until one is set it reads "Speed?
        (Generate asks)", in amber). "Several (set in Excerpts…)" when its runs of the day are at different speeds
        (or some have none); a speed the rate sheet doesn't have is kept, and says so. Choosing one sets it for
        all the firm's pages of the day (Job.set_speed)."""
        box = QuietCombo()
        split = key in job.split_keys()
        runs = {sp for _, keys, speeds in job.runs_ordered() for k, sp in zip(keys, speeds) if k == key}
        # (the speed in effect on its runs: one set on its only run in Excerpts... is the firm's too)
        now = next(iter(runs)) if len(runs) == 1 else job.speeds.get(key, "")
        if len(runs) > 1:
            box.addItem("Several (set in Excerpts…)", "*")
        elif not now and split:
            box.addItem("Speed? (Generate asks)", "?")
            box.setStyleSheet("QComboBox { color: #d97706; }")
        if not split:
            box.addItem("Chooses on the invoice", "")
        for sp in self.s.sheet().speeds:
            box.addItem(sp.name, sp.name)
        sheet = self.s.sheet()
        if now and sheet.find(now) is None:  # (a speed the rate sheet doesn't have: kept, and said)
            box.addItem(f"{now} (not on the rate sheet)", now)
        at = box.findData("*") if len(runs) > 1 else box.findData(sheet.find(now).name if sheet.find(now) else now)
        box.setCurrentIndex(max(0, at))
        box.setToolTip(ORDER_TIPS["Speed"])
        box.activated.connect(lambda _i, j=job, k=key, b=box: self._speed_chosen(j, k, b.currentData()))
        return box

    def _speed_chosen(self, job: Job, key: str, speed) -> None:
        """A speed chosen in the "Who ordered what" card: the firm's speed for all its pages of the day (or none,
        "Chooses on the invoice"); the card, the prices and the Excerpts window follow (after the box's own
        signal: the card is filled again, the box with it)."""
        if speed in ("*", "?", None) or job not in self.jobs:
            return
        job.set_speed(key, speed)
        QTimer.singleShot(0, self._update_status)

    def _fit_orders_height(self) -> None:
        """The card's table as tall as its rows (it doesn't scroll up and down), with room for the horizontal
        scroll bar when the columns are wider than the card: else it covers the last row."""
        t = self.orders
        head = t.horizontalHeader()
        bar = t.horizontalScrollBar()
        height = (max(head.height(), head.sizeHint().height()) + sum(t.rowHeight(r) for r in range(t.rowCount()))
                  + 2 * t.frameWidth() + (bar.sizeHint().height() if bar.maximum() > 0 else 0))
        t.setFixedHeight(max(height, z(60)))

    def _edit_orders(self) -> None:
        """The card's button (and a row double-clicked): Excerpts… for the day shown, or Whose pages… when that
        is what holds its invoice back (Excerpts… has no pages to split until it is chosen)."""
        job = self.cur
        if job.ownership_problem() and not job.portions_problem():
            self._whose_pages(job, job.ownership_problem())
        else:
            self._who_ordered()

    @staticmethod
    def _name_of(group: list[Job], key: str) -> str:
        """An attorney's Attorney.label (the attorney, or the firm of a row naming several) by Attorney.key(),
        from the days of an invoice; the key itself when none of them has it."""
        for j in group:
            for a in j.case.attorneys:
                if a.key() == key:
                    return a.label()
        return key

    def _order_row_clicked(self, item) -> None:
        """A row of "Who ordered what" double-clicked: shows its day and opens its Excerpts window (or Whose
        pages…, see _edit_orders)."""
        job = next((j for j in self.jobs if id(j) == item.data(Qt.UserRole)), None)
        if job is None:
            return
        if job is not self.cur:
            self._select_job(job)
        if job is self.cur:
            self._edit_orders()

    def _who_ordered_why(self, job: Job) -> str:
        """Why Excerpts… can't be used for this job ("" when it can): it needs pages to bill (see
        Job.portions_unavailable; a job of several days is ordered whole in it)."""
        return job.portions_unavailable()

    def _who_ordered(self, job: Job | None = None):
        """Excerpts…: who ordered which pages of every day of the case, as one table (gui/excerpts.py). It stays
        open beside the window: what is changed there is kept at once, and what is changed here shows there."""
        from .excerpts import ExcerptsWindow
        job = job or self.cur
        why = self._who_ordered_why(job)
        if why:
            QMessageBox.information(self, "Excerpts", why)
            return
        if self.excerpts is None:
            self.excerpts = ExcerptsWindow(self.s, self._excerpts_changed, self)
        self.excerpts.load(self._excerpt_group(job))
        rows = [i for i, r in enumerate(self.excerpts.runs) if r.day.job is job]
        if rows:
            self.excerpts.table.selectRow(rows[0])
        self.excerpts.show()
        self.excerpts.raise_()
        self.excerpts.activateWindow()

    def _excerpt_group(self, job: Job | None = None) -> list[Job]:
        """The days Excerpts… shows with a job: those Generate all bills on one invoice with it (as
        _invoice_group), its days waiting for a choice included (Excerpts… is where they are fixed)."""
        from ..batch import invoice_groups
        cur = job or self.cur
        days = [j for j in self.jobs if j.include and not j.invoiced and not j.is_empty() and j.invoice_days()]
        if cur in days:
            for g in invoice_groups(days, self.s):
                if any(j is cur for j in g):
                    return g
        return [cur]

    def _excerpts_changed(self) -> None:
        """A change kept in the Excerpts window: the attorney table, No. of copies (each day's ordering
        parties, see excerpts.store), the prices and the card follow it."""
        if self.excerpts is not None and any(r.day.job is self.cur for r in self.excerpts.runs):
            self._show_attorneys()
            self._show_copies()
        self._update_status()

    def _invoice_group(self, job: Job | None = None) -> list[Job]:
        """The days Generate all bills on one invoice with a job (default: the current one), as
        Settings.invoice_joint says: the ticked jobs of its case not invoiced yet whose invoice isn't held
        (Job.invoice_hold: Excerpts... rows to check, or whose pages to bill). Just the job itself when Generate
        all leaves it out, or it has no pages to bill."""
        from ..batch import invoice_groups
        cur = job or self.cur
        billed = [j for j in self.jobs if j.include and not j.invoiced and not j.is_empty()
                  and not j.invoice_hold()]
        if cur.invoice_pages() and cur in billed:
            for g in invoice_groups(billed, self.s):
                if any(j is cur for j in g):
                    return g
        return [cur]

    def _peripherals_text(self, opts, question=None, on_typed: bool = False, several: bool = False,
                          priced: bool = True) -> str:
        """What the invoice includes besides the original and the copies, and whether there is an index and why
        (invoice.index_reason, on every page of the transcripts), why on a line of its own: 'E-mailed copy,
        index\n(83 total pages, 50 or more)', 'E-mailed copy, no index\n(40 total pages, under 50)', 'No e-mailed
        copy (this job), index\n(turned on for this job)'. With no pages yet, the rule: 'E-mailed copy, index from
        50 pages'. question: a typed page count that changes whether there is an index (batch.index_question):
        'E-mailed copy, index? Generate asks\n(40 typed, the transcript has 83)' ('Generate all asks' for an
        invoice of several days: several). on_typed: a day of it is judged on its typed count, as answered
        (Job.index_on_typed): '(on the pages you typed: 40 total pages, under 50)'. priced False: the rate sheet has
        no index price for the invoice's speeds (those offered and those firms ordered at: invoice.index_priced),
        so days that get an index are charged none: 'E-mailed copy, no index charged\n(the rate sheet has no index
        price)'."""
        from ..invoice import index_mode, index_reason, indexed_days
        s = self.s
        email = s.invoice_include_email if opts.email is None else opts.email
        text = ("E-mailed copy" if email else "No e-mailed copy") + (" (this job)" if opts.email is not None else "")
        if not priced and opts.pages and any(indexed_days(opts, s)):
            return f"{text}, no index charged\n(the rate sheet has no index price)"
        if question is not None:
            has = "the transcripts have" if question.transcripts > 1 else "the transcript has"
            return (f"{text}, index? Generate{' all' if several else ''} asks\n"
                    f"({question.typed:,} typed, {has} {question.counted:,})")
        if index_mode(opts, s) == "auto" and not opts.pages:
            return f"{text}, index from {s.invoice_index_threshold} pages"
        mine = "on the pages you typed: " if on_typed and index_mode(opts, s) == "auto" else ""
        why = f"({mine}{index_reason(opts, s)})"  # (on a line of its own)
        if len(why) > LINE_WIDTH and ", " in why:  # broken after its comma, not with "more)" left on a line alone
            head, _, tail = why.rpartition(", ")
            why = f"{head},\n{tail}"
        return f"{text}, {'index' if any(indexed_days(opts, s)) else 'no index'}\n{why}"

    def _invoice_peripherals(self):
        """Peripherals…: the e-mailed copy and the index for this job's invoice, and the other days on it; and,
        for its days typed below their transcripts' count (batch.typed_days), the answer to Generate's question
        (Job.index_on_typed)."""
        from ..batch import joint_invoice, typed_days
        from .dialogs import InvoicePeripheralsDialog
        job = self.cur
        group = self._invoice_group(job)
        opts = joint_invoice(group)[1]
        typed = typed_days(group)
        answers = {j.index_on_typed for j in typed}
        dlg = InvoicePeripheralsDialog(
            opts.email, opts.index, self.s, len(group), self,
            typed=(sum(j.invoice_pages() for j in typed), sum(j.transcript_pages() for j in typed)) if typed else None,
            on_typed=answers.pop() if len(answers) == 1 else None)
        if dlg.exec():
            email, index = dlg.values()
            group = self._invoice_group(job)  # as it is now: a read may have ended meanwhile
            for day in group:
                day.invoice_email, day.invoice_index = email, index
            if typed:
                for day in typed_days(group):
                    day.index_on_typed = dlg.on_typed()
            self._update_status()

    def _invoice_show(self):
        """Customize…: what granular detail shows on this job's invoice, and the other days on it."""
        from ..batch import joint_invoice
        from .dialogs import InvoiceShowDialog
        job = self.cur
        dlg = InvoiceShowDialog(joint_invoice(self._invoice_group(job))[1].show, self.s, self)
        if dlg.exec():
            items = dlg.values()
            for day in self._invoice_group(job):  # as it is now: a read may have ended meanwhile
                day.invoice_show = items

    def _place_output_cols(self):
        """Lays the Outputs panels out all in a row or, when the window is too narrow for that, with the last two
        (the short ones: MOFR and run sheet) one above the other in the last column. Panels side by side are as
        tall as each other."""
        if not getattr(self, "_outputs_built", False):  # still building the window
            return
        cols = self._output_cols
        widths = [c.sizeHint().width() for c in cols]
        room = self.width() - 36 - 32 - 8 - z(10)  # the window's and the box's margins (and a scroll bar)
        per_row = len(cols) if len(cols) < 3 or sum(widths) + 10 * (len(cols) - 1) <= room else len(cols) - 1
        if per_row == self._cols_per_row:
            return
        self._cols_per_row = per_row
        for c in cols:
            self.output_grid.removeWidget(c)
        if per_row == len(cols):
            for i, c in enumerate(cols):
                self.output_grid.addWidget(c, 0, i)
        else:
            for i, c in enumerate(cols[:-2]):
                self.output_grid.addWidget(c, 0, i, 2, 1)
            self.output_grid.addWidget(cols[-2], 0, per_row - 1)
            self.output_grid.addWidget(cols[-1], 1, per_row - 1)
        for col in range(len(cols) + 1):
            self.output_grid.setColumnStretch(col, 1 if col < per_row else 0)  # the panels share the width

    def _place_pairs(self) -> None:
        """Puts the two columns of the Case and Minute agreement form details cards side by side when the form has
        room for both, else one above the other, so the form never needs scrolling sideways."""
        scroll = getattr(self, "form_scroll", None)
        if scroll is None:
            return
        room = scroll.viewport().width() - z(16) - z(32)  # the form's and the card's margins
        for g, wa, wb in self._pairs:
            side = wa.minimumSizeHint().width() + wb.minimumSizeHint().width() + 18 <= room
            if (g.itemAtPosition(0, 1) is not None) == side and g.count() == 2:
                continue
            g.removeWidget(wa)
            g.removeWidget(wb)
            g.addWidget(wa, 0, 0)
            g.addWidget(wb, 0, 1) if side else g.addWidget(wb, 1, 0)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 1 if side else 0)

    def _size_drop(self):
        """The drop zone shows the small picture for a batch (room for the list of jobs), and the big one when
        the left column has room for it above the list and the paste box. It is tried and measured, since the
        column's height depends on fonts and wrapped text."""
        if len(self.jobs) > 1:
            self.drop.set_compact(True)
            return
        scroll = getattr(self, "left_scroll", None)
        if scroll is None or not scroll.isVisible():
            self.drop.set_compact(self.height() < z(COMPACT_BELOW))
            return
        self.drop.set_compact(False)
        left = scroll.widget()
        left.layout().activate()
        width = scroll.viewport().width()
        # the height the column asks for at that width (wrapped text included)
        need = left.heightForWidth(width) if left.hasHeightForWidth() else left.minimumSizeHint().height()
        if max(need, left.minimumSizeHint().height()) > scroll.viewport().height():
            self.drop.set_compact(True)

    def _size_drop_later(self):
        """_size_drop once Qt has laid the window out (sizes measured before that are not the final ones). The
        window is the timer's context: a window closed meanwhile is not measured."""
        QTimer.singleShot(0, self, self._size_drop)

    def resizeEvent(self, e):
        """The Outputs columns follow the window's width, and the drop zone its height."""
        super().resizeEvent(e)
        self._place_output_cols()
        self._place_pairs()
        if hasattr(self, "drop"):
            self._size_drop()

    def showEvent(self, e):
        """When the window first shows, the drop zone is sized again once the window is laid out: measured
        earlier, the left column looks shorter than it is."""
        super().showEvent(e)
        self._place_pairs()
        self._size_drop_later()

    def _fill_invoice_speeds(self):
        """The Invoice panel's Speeds offered: a box per speed of the current rate sheet with its price
        ("Regular  $4.30/pg"), cheapest first as on the invoice, two a row, ticked for the speeds invoices offer
        (Settings.invoice_speeds)."""
        from ..rates import speed_key
        while self.inv_speeds_grid.count():
            item = self.inv_speeds_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.inv_speed_boxes = {}
        offered = {speed_key(x) for x in self.s.invoice_speeds}
        try:
            speeds = list(self.s.sheet().speeds)
        except Exception:  # a broken rate sheet must not break the window
            speeds = []
        names = [sp.name for sp in speeds]
        per_row = 2  # (more in a row would make the Invoice panel, and the Outputs box, too wide)
        for i, sp in enumerate(speeds):  # cheapest first: the order of the Speed box and of the invoice
            name = sp.name
            cb = QCheckBox(f"{name}  ${sp.original}/pg" if sp.original else name)
            days = self.s.days_for(name)
            cb.setToolTip(sp.label() + (f"  ·  {plural(days, 'day')}" if days is not None else ""))
            cb.setChecked(speed_key(name) in offered)
            cb.toggled.connect(self._invoice_speeds_changed)
            self.inv_speed_boxes[name] = cb
            self.inv_speeds_grid.addWidget(cb, i // per_row, i % per_row)
        if not names:
            self.inv_speeds_grid.addWidget(QLabel("(none on the rate sheet)"), 0, 0)
        # (the Invoice panel's prices list each speed offered: the panels may fit in a row now, or no longer)
        self._place_output_cols()

    def _invoice_speeds_changed(self, _=None):
        """The speeds ticked are saved straight away. Speeds of other rate sheets stay as they were. With one
        speed ticked the invoice bills that speed alone; with none, the agreement form's speed. Every job's
        agreement form speed is the Settings rule's among them again, unless the user chose one still offered
        (Expedite unticked and ticked again: back to Expedite, not left on Regular)."""
        from ..rates import speed_key
        here = {speed_key(n) for n in self.inv_speed_boxes}
        keep = [x for x in self.s.invoice_speeds if speed_key(x) not in here]
        self._set_opt("invoice_speeds", keep + [n for n, cb in self.inv_speed_boxes.items() if cb.isChecked()])
        self._apply_speed_rule()

    def _open_run_sheets_folder(self):
        """Opens the run sheets folder, making it first if needed (it is made with the first run sheet)."""
        folder = runsheets_folder(self.s)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Could not open the folder", str(e))
            return
        open_path(folder)

    def lock_pdfs(self):
        """File → Lock finished PDFs: a copy of each PDF picked, with its fields flattened so the values can no
        longer be changed ('<name> (locked).pdf' next to it). Only PDFs this app made that still have fields
        are locked."""
        from ..pdfout import has_fields, is_generated, lock_pdf
        start = self.s.output_dirs.get("agreement") or self.s.output_dir or str(Path.home() / "Documents")
        files, _ = QFileDialog.getOpenFileNames(self, "Lock finished PDFs", start, "PDF files (*.pdf)")
        if not files:
            return
        made, skipped, no_fields, failed = [], [], [], []
        for f in map(Path, files):
            if not is_generated(f):
                skipped.append(f.name)
                continue
            try:
                if not has_fields(f):  # flattened when saved, or a locked copy: nothing to lock
                    no_fields.append(f.name)
                    continue
                made.append(lock_pdf(f))
            except Exception as e:
                failed.append(f"{f.name}: {e}")
        copies = "1 locked copy" if len(made) == 1 else f"{len(made)} locked copies"
        lines = [f"Saved {copies} next to the originals."] if made else []
        if skipped:
            lines.append("Not made by this app, so left alone:\n  " + "\n  ".join(skipped))
        if no_fields:
            lines.append("Already locked or flattened (no fields), so left alone:\n  " + "\n  ".join(no_fields))
        if failed:
            lines.append("Could not be locked (is it open in a PDF viewer?):\n  " + "\n  ".join(failed))
        QMessageBox.information(self, "Lock finished PDFs", "\n\n".join(lines))

    def _invoice_detail_changed(self, on: bool):
        """"Show granular detail" was clicked: it applies to this job's invoice (and the other days on it) only;
        a new job starts with it off."""
        for job in self._invoice_group():
            job.invoice_detail = on
        self._refresh_outputs()

    def _invoice_parties_changed(self, n: int):
        """A number typed here stays; set back to the number of ticked attorneys, it follows them again. It
        applies to the other days on the same invoice too, but only those several attorneys ordered: a day one
        attorney ordered alone is that attorney's to pay. No. of copies follows it (unless typed)."""
        from ..batch import group_attorneys
        group = self._invoice_group()
        ticked = max(1, len(group_attorneys(group)) if len(group) > 1 else len(self.cur.ticked_keys()))
        for job in group:
            alone = len(group) > 1 and len(job.ticked_keys()) < 2
            job.parties = 0 if n == ticked or alone else n
        self._show_copies(group)  # No. of copies is the number of ordering parties
        self._refresh_outputs()

    def open_records(self):
        """Shows the Records window (Ctrl+R), made once and reloaded each time it is opened."""
        from .records_window import RecordsWindow
        win = getattr(self, "_records_win", None)
        if win is None:
            win = self._records_win = RecordsWindow(self.s, self)
            win.on_reopen = self.open_past_job
        win.reload()
        win.show()
        win.raise_()
        win.activateWindow()
        self._recap(win)

    def _recap(self, win, today: date | None = None) -> None:
        """The first time Records is opened in a month: what last month came to ("Last month (September 2026)
        you made $870.00 with 243 pages"), and the first time in a year, last year too (records.recap). The box
        has OK and a tick to show no more recaps (Settings -> Options turns them back on). A month without
        invoices says nothing. On April 1 the recap of last month is first a joke (records.april_fools)."""
        if not self.s.recaps:
            return
        from ..records import april_fools, recap
        try:
            lines, month, year = recap(win.ledger.invoices(), today, self.s.recap_month, self.s.recap_year)
        except Exception as e:  # the records still open
            logfile.error("could not sum up last month", e)
            return
        if (month, year) != (self.s.recap_month, self.s.recap_year):
            self.s.recap_month, self.s.recap_year = month, year
            self._save_settings()
        if not lines:
            return
        joke = april_fools(today)
        if joke and lines[0].startswith("Last month"):
            fool = QMessageBox(win)
            fool.setIcon(QMessageBox.Information)
            fool.setWindowTitle("Recap")
            fool.setText(joke)
            fool.addButton("Really?!", QMessageBox.AcceptRole)
            fool.exec()
            lines = ["April Fools! Here's what really happened:"] + lines
        box = QMessageBox(win)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Recap")
        box.setText("\n\n".join(lines))
        stop = QCheckBox("Don't show me monthly or annual recaps anymore")
        stop.setToolTip("Settings → Options turns them back on.")
        box.setCheckBox(stop)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if stop.isChecked():
            self.s.recaps = False
            self._save_settings()

    def _records_changed(self):
        """Files were made: an open Records window shows them."""
        win = getattr(self, "_records_win", None)
        if win is not None and win.isVisible():
            win.reload()

    def _save_invoice_template(self):
        """Saves a copy of the invoice spreadsheet template that ships with the app where the user picks."""
        from ..invoice import TEMPLATE
        if not TEMPLATE.is_file():
            QMessageBox.warning(self, "Not found", "The invoice spreadsheet template is missing from this install.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save the invoice spreadsheet template",
                                              str(Path.home() / "Documents" / TEMPLATE.name),
                                              "Excel workbook (*.xlsx)")
        if dest:
            import shutil
            try:
                shutil.copyfile(TEMPLATE, dest)
            except OSError as e:
                QMessageBox.warning(self, "Could not save", str(e))
                return
            open_path(Path(dest).parent)

    # ------------------------------------------------------------- zoom and screen
    def zoom_step(self, step: int) -> None:
        """Zoom in (1), out (-1) or back to 100 % (0): Ctrl + / Ctrl - / Ctrl 0, Ctrl + wheel."""
        now = zooming.zoom()
        self.set_zoom(1.0 if step == 0 else now + step * zooming.STEP)

    def set_zoom(self, f: float) -> None:
        """Shows everything at zoom f (see zoom.py), saves it as the default and says so in the status line."""
        f = zooming.set_zoom(f)
        apply_theme(self.app, self.s.theme)
        zooming.reapply()
        self.drop.rezoom()
        self._cols_per_row = 0  # lay the Outputs panels out again: they are wider or narrower now
        self._place_output_cols()
        self._place_pairs()
        QTimer.singleShot(0, self._place_pairs)  # again once the new sizes are laid out
        self._fit_minimum()
        self._refresh_jobs()
        # the Who ordered what table is as tall as its rows, which are taller or shorter now
        self._show_orders()
        QTimer.singleShot(0, self, self._show_orders)  # again once the new sizes are laid out
        self._set_opt("zoom", f)
        self._toast(f"Zoom {round(f * 100)} %")

    def _avail(self):
        """The part of the window's screen not taken by the taskbar (logical pixels, after Windows scaling)."""
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _fit_minimum(self) -> None:
        """The window's smallest size: 1080 x 720 at 100 % zoom, but never more than the screen has room for.
        At 150 % Windows scaling a 1920 x 1080 screen is 1280 x 690 or so in Qt's pixels: a fixed 720 would put
        the bottom of the window off the screen."""
        avail = self._avail()
        w, h = z(1080), z(720)
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.95)), min(h, int(avail.height() * 0.9))
        self.setMinimumSize(max(480, w), max(360, h))

    def _fit_screen(self) -> None:
        """Brings the window back within its screen: a size saved on a bigger screen (or at another scaling) is
        made smaller, and a window off the screen is moved onto it."""
        avail = self._avail()
        if avail is None or self.isMaximized() or self.isFullScreen():
            return
        frame = self.frameGeometry()
        extra_w, extra_h = frame.width() - self.width(), frame.height() - self.height()
        w = min(self.width(), avail.width() - extra_w)
        h = min(self.height(), avail.height() - extra_h)
        if (w, h) != (self.width(), self.height()):
            self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
        frame = self.frameGeometry()
        x = min(max(frame.x(), avail.x()), avail.right() - frame.width() + 1)
        y = min(max(frame.y(), avail.y()), avail.bottom() - frame.height() + 1)
        if (x, y) != (frame.x(), frame.y()):
            self.move(max(x, avail.x()), max(y, avail.y()))

    def eventFilter(self, obj, e):
        """Ctrl + the mouse wheel over this window zooms (a text box would otherwise zoom only its own text):
        a step per notch of the wheel (120), as a touchpad sends a notch in many small turns. And when the form
        gets wider or narrower (the splitter dragged), its cards' columns are placed again (_place_pairs)."""
        if e.type() == QEvent.Wheel and e.modifiers() & Qt.ControlModifier:
            w = obj if isinstance(obj, QWidget) else None
            if w is not None and w.window() is self:
                dy = e.angleDelta().y()
                if dy and (dy > 0) != (self._wheel > 0):
                    self._wheel = 0  # turned the other way: start counting again
                self._wheel += dy
                steps = int(self._wheel / 120)  # (towards 0: the rest is kept for the next turn)
                if steps:
                    self._wheel -= steps * 120
                    self.set_zoom(zooming.zoom() + steps * zooming.STEP)
                return True
        elif (e.type() == QEvent.Resize and getattr(self, "form_scroll", None) is not None
              and obj is self.form_scroll.viewport()):
            self._place_pairs()
        return super().eventFilter(obj, e)

    def closeEvent(self, e):
        """Asks first while a batch is being made; remembers the window's size and place."""
        if self.filling and QMessageBox.question(
                self, "Still working", "The files of the batch are still being made. Close anyway?\n\n"
                "(The file being written may be left incomplete.)") != QMessageBox.Yes:
            e.ignore()
            return
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self._save_settings()  # (a file that can't be written must not keep the window from closing)
        super().closeEvent(e)
