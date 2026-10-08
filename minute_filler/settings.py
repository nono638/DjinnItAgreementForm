"""Persistent user settings stored as JSON in %APPDATA%\\YinItAgreementForm.

Settings holds every choice (the defaults are what a new user gets) and load() brings older files up to date.
The tables here name the choices the window and the Settings dialog offer: the outputs (OUTPUTS), the speeds
(SPEEDS) and which one the agreement form names when its first choice isn't offered (AGREEMENT_FALLBACKS),
which days of an invoice get an index (INDEX_RULES), who pays the index of pages several firms ordered together
(INDEX_SHARED), what "Show granular detail" adds to an invoice (DETAIL_ITEMS), and where the
invoice's own text goes and when it is shown (TEXT_PLACES, TEXT_WHEN, default_invoice_texts), when the AI may
tick who ordered (AI_TICKS), and the folders the outputs go to when they have none of their own (OUTPUT_FOLDERS,
see Settings.folder_for).

A settings.json that load() can't read (locked by a sync, damaged) is never written over on the app's own: the
app runs on the defaults (Settings.unreadable), and only the user's own Save does (save(force=True)).

The settings can be saved to a file and read back on another computer (export_to, import_from): everything but
what belongs to this computer (LOCAL), with the rate sheets.

The other reporters of a trial are kept by initials (Settings.reporters, a ReporterProfile each): their name for
the run sheet, and the details of invoices made in their name (Settings.as_reporter, Settings.number_format).

The app was called DjinnItAgreementForm before 2.0: the first start carries its settings folder over
(carry_over), and old exported settings files still import.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

APP_NAME = "YinItAgreementForm"
FILENAME_PATTERN = "Minute Agreement - {case} - {index} - {attorney} - {today}"
OLD_FILENAME_PATTERN = "Minute Agreement - {case} - {index} - {attorney}"  # the default before settings v3
# The app's earlier names, newest first (DjinnItAgreementForm until 2.0): their settings folder is carried over
OLD_APP_NAMES = ("DjinnItAgreementForm", "MinuteAgreementFiller")
# The folders under Documents named after the app, as they were called before the rename -> now
OLD_DOCUMENT_FOLDERS = {"DjinnIt Records": "YinIt Records", "DjinnIt Run Sheets": "YinIt Run Sheets"}
RECENT_MAX = 10  # how many documents and folders File -> Open recent keeps
# What belongs to this computer and this copy of the app: left out of an exported settings file, and kept as
# it is when one is imported
LOCAL = ("window_geometry", "recent_files", "update_checked", "recap_month", "recap_year", "welcomed",
         "signature_image", "opened_year", "new_year_day")
# What says who the user is: left out of a settings file exported "without my details" (for a colleague), and
# kept as it is when such a file is imported. The invoice's payment text (who to pay, and how) is left out
# with them. The folders are among them: their paths name the user's Windows account.
# The answers about firms that may be one (firm_answers) name the user's clients: left out with them.
PERSONAL = ("profile", "reporters", "output_dir", "output_dirs", "records_dir", "rate_sheets_dir", "firm_answers")


_CARRY_TRIED: set[str] = set()  # the settings folders a carry-over was tried for (once a run: it copies a lot)


def settings_dir() -> Path:
    """%APPDATA%\\YinItAgreementForm (the home folder when APPDATA is unset), created when missing. The old
    app's folder is carried over (carry_over) while the new one has neither settings nor records: a carry-over
    that failed (a file locked) is tried again at the next start."""
    base = Path(os.environ.get("APPDATA") or str(Path.home()))
    d = base / APP_NAME
    if str(d) not in _CARRY_TRIED and not _has_own(d):
        old = next((base / n for n in OLD_APP_NAMES if (base / n).is_dir()), None)
        if old is not None and (not d.exists() or _has_own(old)):
            _CARRY_TRIED.add(str(d))
            carry_over(old, d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _has_own(folder: Path) -> bool:
    """A settings folder that holds the user's settings or records (not only logs made at a start)."""
    return (folder / "settings.json").exists() or (folder / "records.db").exists()


def carry_over(old: Path, new: Path) -> None:
    """The first start after a rename: a copy of the old settings folder (settings, rate sheets, records,
    signature, logs) becomes the new one; the old is left as it was. The folders under Documents named after
    the app (OLD_DOCUMENT_FOLDERS) are renamed too, and the paths kept in the settings and in the records follow
    both. A folder that can't be renamed (open in another program) is kept where it is, named in the settings,
    so nothing is lost.
    The copy is made beside the new folder ("<name>.partial-<process id>") and renamed into place when done, so
    a start that stops half way, or another copy of the app starting at the same time, never finds half a folder.
    Files that can't be copied (a log open in another program) are left out; when the settings or the records
    are among them nothing is carried over and the next start tries again."""
    import shutil
    import time
    for left in new.parent.glob(new.name + ".partial-*"):  # left by a start that stopped half way
        try:
            if time.time() - left.stat().st_mtime > 3600:  # (not one another copy of the app is making now)
                shutil.rmtree(left, ignore_errors=True)
        except OSError:
            pass
    temp = new.with_name(f"{new.name}.partial-{os.getpid()}")  # (each copy of the app its own)
    shutil.rmtree(temp, ignore_errors=True)
    try:
        shutil.copytree(old, temp)
    except shutil.Error as e:  # the files that could be copied are; e.args[0] lists the others
        lost = {Path(str(item[0])).name.lower() for item in e.args[0] if isinstance(item, tuple) and item}
        if lost & {"settings.json", "records.db"}:
            shutil.rmtree(temp, ignore_errors=True)
            return
    except OSError:
        shutil.rmtree(temp, ignore_errors=True)
        return
    if not _into_place(temp, new):
        return
    moved = {str(old): str(new)}
    docs = Path.home() / "Documents"
    stayed = {}
    for was, now in OLD_DOCUMENT_FOLDERS.items():
        if (docs / was).is_dir() and not (docs / now).exists():
            try:
                (docs / was).rename(docs / now)
                moved[str(docs / was)] = str(docs / now)
            except OSError:
                stayed[now] = str(docs / was)
    _repath_records(new / "records.db", moved)
    f = new / "settings.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    data = _repath(data, moved)
    if "YinIt Records" in stayed and not data.get("records_dir"):
        data["records_dir"] = stayed["YinIt Records"]
    dirs = data.get("output_dirs") if isinstance(data.get("output_dirs"), dict) else {}
    if "YinIt Run Sheets" in stayed and not dirs.get("runsheet") and not data.get("runsheet_dir"):
        data["output_dirs"] = {**dirs, "runsheet": stayed["YinIt Run Sheets"]}
    try:
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _renamed_folders() -> dict[str, str]:
    """The folders of this computer the rename moved (old -> new): the old settings folders and the folders
    under Documents (when the old one is gone, or the new one is there)."""
    base = Path(os.environ.get("APPDATA") or str(Path.home()))
    out = {str(base / n): str(base / APP_NAME) for n in OLD_APP_NAMES}
    docs = Path.home() / "Documents"
    for was, now in OLD_DOCUMENT_FOLDERS.items():
        if (docs / now).is_dir() or not (docs / was).is_dir():
            out[str(docs / was)] = str(docs / now)
    return out


def _into_place(temp: Path, new: Path) -> bool:
    """The finished copy `temp` becomes the folder `new`. A folder there already with the user's settings or
    records (another copy of the app carried over first) wins: ours is dropped (False). One with only what a
    failed start left (its logs) is taken into ours first."""
    import shutil
    try:
        if new.exists():
            if _has_own(new):
                raise FileExistsError(str(new))
            shutil.copytree(new, temp, dirs_exist_ok=True)
            shutil.rmtree(new)
        os.rename(temp, new)  # (one step: never half a folder; fails when another copy of the app got there first)
        return True
    except OSError:
        if not _has_own(new):  # (the folder couldn't be emptied and renamed: copied over it instead)
            try:
                shutil.copytree(temp, new, dirs_exist_ok=True)
                shutil.rmtree(temp, ignore_errors=True)
                return True
            except OSError:
                pass
        shutil.rmtree(temp, ignore_errors=True)
        return False


def _repath_records(db_path: Path, moved: dict[str, str]) -> None:
    """The file paths kept in the records (each file made, each invoice, and the documents a job was read
    from) follow the folders moved (old -> new). A database that can't be opened is left as it is."""
    import sqlite3
    if not db_path.is_file():
        return
    try:
        db = sqlite3.connect(db_path)
    except sqlite3.Error:
        return
    try:
        with db:  # (one transaction: every path changed, or none)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, cols in (("activity", ("file_path", "origin")), ("invoices", ("file_path",))):
                if table not in tables:
                    continue
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for col in (c for c in cols if c in have):
                    rows = db.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall()
                    for rowid, value in rows:
                        new = _repath_text(value, moved, col == "origin")
                        if new != value:
                            db.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (new, rowid))
    except sqlite3.Error:
        pass
    finally:
        db.close()


def _repath_text(value, moved: dict[str, str], is_json: bool):
    """A path (or, is_json, the JSON of a record's origin) with its folders moved."""
    if not isinstance(value, str):
        return value
    if not is_json:
        return _repath(value, moved)
    try:
        data = json.loads(value)
    except ValueError:
        return value
    new = _repath(data, moved)
    return value if new == data else json.dumps(new)


def _repath(value, moved: dict[str, str]):
    """value (a setting, or a table or list of them) with any path in a folder of `moved` (old -> new) pointing
    into the new folder instead."""
    if isinstance(value, dict):
        return {k: _repath(v, moved) for k, v in value.items()}
    if isinstance(value, list):
        return [_repath(v, moved) for v in value]
    if isinstance(value, str):
        for was, now in moved.items():
            low, key = os.path.normcase(value), os.path.normcase(was)
            if low == key or low.startswith(key + os.sep):
                return now + value[len(was):]
    return value


@dataclass
class Profile:
    """The court reporter's own details, for the forms and invoices (and the initials, for run sheets)."""
    name: str = ""
    title: str = "Court Reporter"
    address1: str = ""
    address2: str = ""
    phone: str = ""
    fax: str = ""
    email: str = ""
    website: str = ""
    initials: str = ""  # as on the transcript pages the reporter writes ("pr"); blank = from the name


@dataclass
class ReporterProfile:
    """Another reporter of the transcripts (Settings.reporters, by initials): the name the run sheets give them
    and the details on the invoices the user makes for their pages (Whose pages... ticks them)."""
    name: str = ""          # as the run sheet's Reporter column says it ("Dana")
    full_name: str = ""     # at the top of their invoices ("Dana Smith"); blank = name
    title: str = "Court Reporter"
    address1: str = ""
    address2: str = ""
    phone: str = ""
    email: str = ""
    payment: str = ""       # how to pay them: their invoices' Payment text (the user's own is left off)
    prefix: str = ""        # their invoice numbers begin with it ("DS" -> DS-2026-0001); blank = the initials

    def profile(self, initials: str) -> Profile:
        """These details as the invoice's header takes them (a Profile)."""
        return Profile(name=self.full_name or self.name or initials.upper(), title=self.title,
                       address1=self.address1, address2=self.address2, phone=self.phone, email=self.email,
                       initials=initials)

    def has_details(self) -> bool:
        """Enough for an invoice in their name: a name, and a way to reach them (an address, phone or e-mail)."""
        return bool((self.full_name or self.name) and (self.address1 or self.phone or self.email))


OUTPUTS = {  # what Generate can make: key -> label
    "agreement": "Minute agreement",
    "mofr": "MOFR",
    "invoice": "Invoice",
    "runsheet": "Run sheet",
}
# Where each output is saved when Settings.output_dirs doesn't say: a folder of its own under Documents, or
# (not listed) the general "Save to" folder. A new output only needs its key in OUTPUTS and its file written to
# Settings.folder_for(key, ...): it then gets its own row under Settings -> Options -> Folders as well.
OUTPUT_FOLDERS = {"runsheet": "YinIt Run Sheets"}
SPEEDS = ("Regular", "Expedited", "Daily", "Immediate")  # the delivery speeds, as named in Settings
# When the agreement form's first-choice speed isn't offered: key -> label (see Settings.agreement_speed)
AGREEMENT_FALLBACKS = {"slowest": "the slowest speed offered", "fastest": "the fastest speed offered"}
MOFR_FILENAME_PATTERN = "MOFR - {case} - {index} - {today}"
INVOICE_FILENAME_PATTERN = "Invoice {number} - {case} - {attorney}"
INVOICE_TURNAROUND = {
    "Immediate": "Delivered at the time of trial.",
    "Daily": "1 business day from receipt of payment.",
    "Expedited": "1 week from receipt of payment.",
    "Regular": "2-4 weeks from receipt of payment.",
}
INVOICE_PAYMENT_TEXT = (
    "Zelle: (555) 555-0100\n"
    "Check: payable to Pat Reporter, mailed to\n"
    "    Pat Reporter, Court Reporter, 123 Example Street, Room 100, Anytown, NY 10000\n"
    "The transcript is sent after the check clears.")
RUNSHEET_FILENAME_PATTERN = "{month} {year} {index} {case} - Run Sheet"
RUNSHEET_EXISTING = ("ask", "add", "new")  # when the case has a run sheet: ask / add to it / start a new one
# When the AI's answer may tick an attorney as the one who ordered: key -> label (see Settings.ai_ticks)
AI_TICKS = {
    "text": "From e-mails and pasted text only",
    "never": "Never",
    "any": "From any input, transcripts and photos too",
}
# Which days of an invoice get an index, when the job doesn't say: key -> what it means
INDEX_RULES = {
    "any": "If any day reaches the threshold, every day gets one",
    "each": "Each day that reaches the threshold gets one",
    "total": "Every day gets one when the days together reach the threshold",
}
# Who pays for the index of pages several firms ordered together: key -> label (see invoice_calc)
INDEX_SHARED = {
    "split": "Split between the firms",
    "each": "Each firm pays its own",
}
# What "Show granular detail" can add to an invoice: key -> label
DETAIL_ITEMS = {
    "pages": "Page count",
    "days": "Pages for each day",
    "per_page": "Price per page",
    "charges": "The charges in each amount",
    "split": "The split between parties",
}


INVOICE_FOOTER = ("I am in the courtroom during the day, so e-mail is the best way to reach me. "
                  "Please send a short e-mail after paying so I can start on your transcript.")
# The invoice's own text (Settings.invoice_texts): rows of {"where": a key of TEXT_PLACES, "when": a key of
# TEXT_WHEN, "text": what is written, with {placeholders} (see invoice.text_values)}
TEXT_PLACES = {
    "top": "At the top, above Bill To",
    "transcript": "Under the transcript details",
    "amounts": "Under the amounts",
    "payment": "Payment",
    "footer": "Footer (small print)",
}
TEXT_WHEN = {
    "always": "Always",
    "parties": "More than one party ordered",
    "one_party": "One party ordered",
    "speeds": "More than one speed is offered",
    "one_speed": "One speed is offered",
    "days": "It covers several days",
    "one_day": "It covers one day",
    "excerpt": "The attorney ordered an excerpt",
    "whole": "The attorney ordered every page",
    "email": "An e-mailed copy is charged",
    "no_email": "No e-mailed copy is charged",
    "index": "An index is charged",
    "no_index": "No index is charged",
    "shared": "Several reporters wrote the transcript",
    "split_share": "Granular detail shows the split; the firms ordered different pages",
    "split_even": "Granular detail shows the split; every party ordered every page",
}
TEXT_PLACEHOLDERS = ("{case} {index} {dates} {pages} {total_pages} {parties} {number} {name} {bill_to} "
                     "{transcript} (\"transcript\" or \"transcripts\") {is} (\"is\" or \"are\") "
                     "{shared} (\"the original, the index and the judge's index\", what firms ordering the same pages "
                     "split)")


def default_invoice_texts(payment: str = INVOICE_PAYMENT_TEXT, footer: str = INVOICE_FOOTER) -> list[dict]:
    """The invoice's text as it has always been: the notes under the amounts, the payment details and the
    footer (payment, footer: the ones to use, e.g. a user's own from before the text could be changed)."""
    rows = [
        {"where": "amounts", "when": "speeds",
         "text": "Please choose one delivery option and pay the amount shown for it."},
        {"where": "amounts", "when": "split_share",
         "text": "Amounts are your share: {shared} of pages ordered together are split between the parties who "
                 "ordered them."},
        {"where": "amounts", "when": "split_even", "text": "Amounts are per party ({parties} parties ordered)."},
        {"where": "amounts", "when": "parties", "text": "The {transcript} {is} delivered once every party has paid."},
        {"where": "amounts", "when": "one_party", "text": "The {transcript} {is} sent after payment is received."},
        {"where": "payment", "when": "always", "text": payment},
        {"where": "footer", "when": "always", "text": footer},
    ]
    return [r for r in rows if r["text"].strip()]


def _reporter_profile(v) -> ReporterProfile:
    """A reporter as a settings file keeps them: a name (before 2.0) or their details."""
    if isinstance(v, str):
        return ReporterProfile(name=v)
    known = {f.name for f in fields(ReporterProfile)}
    return ReporterProfile(**{a: b for a, b in v.items() if a in known and isinstance(b, str)})


def reporter_key(initials: str) -> str:
    """Initials as they are looked up: 'D.S.' and 'd s' are 'ds'."""
    return initials.lower().replace(".", "").replace(" ", "")


@dataclass
class Settings:
    """Everything the Settings dialog and the window's own choices set, kept in settings.json. The defaults
    here are what a new user gets; load() brings files from older versions up to date."""
    profile: Profile = field(default_factory=Profile)

    # Defaults used when the input doesn't say.
    default_court: str = "Supreme"
    default_county: str = "Queens"
    # The one speed the minute agreement form names, out of the speeds the invoice offers (invoice_speeds): this
    # one when it is offered, else the slowest (or the fastest) offered; see agreement_speed()
    agreement_speed_first: str = "Expedited"
    agreement_speed_fallback: str = "slowest"  # one of AGREEMENT_FALLBACKS
    default_copies: str = "1"
    # Rates come from a rate sheet (CSV) - see rates.py
    rate_sheet: str = "Sample Rates"
    rate_sheets_dir: str = ""         # blank = %APPDATA%\YinItAgreementForm\Rate Sheets
    # Turnaround used when the rate sheet has no Days column
    days_immediate: int = 0
    days_daily: int = 1
    days_expedited: int = 7
    days_regular: int = 21
    fill_delivery_date: bool = True

    # Checkboxes
    per_email: bool = True            # write "per email" on the attorney signature line
    sign_reporter: bool = False       # sign the reporter signature line: with the image below, else the typed name
    signature_image: str = ""         # prepared signature picture (see signature.py); blank = type the name
    agreement_today: bool = True
    flatten: bool = False
    open_after: bool = True
    title_case_names: bool = True     # "HONORABLE MARIA T. ALVAREZ" -> "Maria T. Alvarez"

    # Output
    form_choice: str = "ucs"          # "ucs", "clean" or "original" (see fill.FORMS)
    include_instructions: bool = True  # keep the UCS form's instructions page (page 2)
    settings_version: int = 10        # bumped when from_dict must change something in older files
    output_dir: str = ""              # blank = next to the first input file, else Documents\Minute Agreements
    output_dirs: dict = field(default_factory=dict)  # a folder for one output (key of OUTPUTS); none = as above
    filename_pattern: str = FILENAME_PATTERN  # {case} {index} {attorney} {date} (of the minutes) {today}
    batch_combine_dates: bool = False  # batch: all days of a case on one form instead of one form per day
    # Generate all: the days of a case that share an invoice (invoice_joint) get one minute agreement per attorney
    # (the days and pages it ordered) and one MOFR for all of them, instead of a set per day (batch.form_groups)
    forms_per_case: bool = True
    outputs: list = field(default_factory=lambda: ["agreement"])  # ticked by default: keys of OUTPUTS

    # MOFR (Minute Order Form/Receipt)
    mofr_division: str = "civil"      # "civil" or "criminal" box
    mofr_filename_pattern: str = MOFR_FILENAME_PATTERN

    # Invoices (they need pages to bill: a transcript's, or a number typed in Est. number of pages)
    # the speeds ticked under "Speeds offered" in the Order card: the invoice lists them so the attorney can choose
    # (one alone: a single-speed invoice), and the agreement form names one of them (agreement_speed)
    invoice_speeds: list = field(default_factory=lambda: ["Regular", "Expedited"])
    invoice_include_email: bool = True  # each party also gets an e-mailed copy (Email column of the rate sheet)
    invoice_include_index: bool = True  # long transcripts get an index (Index column), plus one for the judge
    invoice_index_threshold: int = 50  # pages from which an index is charged (see invoice_index_rule)
    invoice_index_rule: str = "any"   # one of INDEX_RULES: which days of a several-day invoice get an index
    # one of INDEX_SHARED: the index of pages ordered by several firms is split between them (the practice:
    # 100 pages, B orders 10 of them -> A pays 90 at the full rate and 10 at the split rate, B 10 at the split
    # rate), or each pays its own
    invoice_index_shared: str = "split"
    invoice_joint: bool = True        # Generate all bills the days of one case on one invoice (False: one per day)
    invoice_detail_items: list = field(default_factory=lambda: list(DETAIL_ITEMS))  # what granular detail adds
    # also save a copy of each invoice with granular detail ("... (detailed).pdf": the same number, not recorded
    # again), for when the math is asked for later; a job whose invoice already shows the detail gets none
    invoice_detailed_copy: bool = False
    # the Excerpts window: the speeds whose prices it leaves out, and whether it also shows each run's place in
    # the day besides its printed page numbers
    excerpt_hidden_speeds: list = field(default_factory=list)
    excerpt_show_place: bool = False
    # the user's answers when two attorney rows may be one firm or attorney ("Smith Law" and "Smith Law Group"):
    # [[Attorney.key(), Attorney.key(), True (the same) or False, how the first was written, the second], ...]
    # (see extract_regex.use_firm_answers; the names are for Settings → Invoice → Firms you answered; rows saved
    # by 2.2.0 have none, and load with them blank)
    firm_answers: list = field(default_factory=list)
    invoice_turnaround: dict = field(default_factory=lambda: dict(INVOICE_TURNAROUND))
    # the invoice's own text, each row placed and shown as it says (see TEXT_PLACES, TEXT_WHEN); the payment
    # details and the footer note are rows too
    invoice_texts: list = field(default_factory=default_invoice_texts)
    invoice_number_format: str = "{year}-{seq:04}"
    invoice_filename_pattern: str = INVOICE_FILENAME_PATTERN
    records_dir: str = ""             # blank = Documents\YinIt Records (CSV copies and exports)
    # the columns shown in the Records window: "invoices" / "activity" -> column keys (see records_window);
    # a table not listed shows its usual columns
    records_columns: dict = field(default_factory=dict)
    # the Records search's Fuzzy box also matches numbers loosely ("2026-0001" finds "2026-0002"); False: a word
    # with a digit in it must be found as typed (Settings -> Options)
    fuzzy_numbers: bool = True

    # Run sheets (who wrote which pages of a trial; see runsheet.py)
    runsheet_filename_pattern: str = RUNSHEET_FILENAME_PATTERN
    runsheet_existing: str = "ask"    # one of RUNSHEET_EXISTING
    # the other reporters, by initials: their name in the Reporter column and the details of the invoices made
    # for their pages ("ds": ReporterProfile(name="Dana", ...))
    reporters: dict = field(default_factory=dict)

    # AI
    use_ai: bool = True
    ai_for_text: bool = True          # also ask the model about text inputs with gaps
    # when the AI's answer may tick an attorney as the one who ordered (a transcript lists who appeared, not
    # who ordered): one of AI_TICKS
    ai_ticks: str = "text"
    ollama_model: str = "gemma4:e2b"
    ollama_host: str = "http://localhost:11434"
    ai_timeout: int = 180

    # UI
    theme: str = "system"             # system / light / dark
    show_yin: bool = True
    window_geometry: str = ""
    zoom: float = 1.0                 # Ctrl + / Ctrl - zoom (1.0 = 100 %), on top of the Windows display scaling
    recent_files: list = field(default_factory=list)  # documents and folders opened, newest first (Open recent)
    preview_before_saving: bool = True  # Generate shows the files as pictures first; saved only on Save
    welcomed: bool = False            # the first-run "Welcome" questions were shown (once, while there is no name)
    show_math: bool = True            # after Generate makes invoices, a window spells out how each amount was reached
    opened_year: str = ""             # the year the app was last opened in ("2026")
    new_year_day: str = ""            # the day it was first opened in a new year: the New Year yin-yang shows all day

    # The only time the app goes online: once a day it asks GitHub for the number of the latest version
    check_updates: bool = True
    update_checked: str = ""          # the day it last asked ("2026-10-03")

    # Records: "last month you made ..." when Records is first opened in a month (and "last year" in a year)
    recaps: bool = True
    recap_month: str = ""             # the month a recap was last shown in ("2026-10")
    recap_year: str = ""              # the year the yearly one was last shown in ("2026")

    # (not a field, so not saved) load() found a settings file it couldn't read (locked by a sync, damaged): save()
    # skips every save but the user's own Save (force), so that the defaults are never written over it
    unreadable = False

    @property
    def path(self) -> Path:
        """The settings file: settings.json in settings_dir()."""
        return settings_dir() / "settings.json"

    # ---- rates (the active sheet is cached; call reload_rates() after changing sheets)
    def sheets(self):
        """(rate sheets, problems) from the rate sheet folder, as rates.list_sheets gives them; read once."""
        from .rates import list_sheets
        sheets = getattr(self, "_sheets", None)  # read once: another thread may call reload_rates meanwhile
        if sheets is None:
            sheets = self._sheets = list_sheets(self.rate_sheets_dir)
        return sheets

    def reload_rates(self) -> None:
        """Forgets the cached sheets, so the next sheets() reads the folder again."""
        self._sheets = None

    def sheet(self):
        """The rate sheet chosen in Settings (see rates.pick for the fallbacks)."""
        from .rates import pick
        return pick(self.sheets()[0], self.rate_sheet)

    def rate_for(self, delivery: str) -> str:
        """The per-page rate for a speed on the active sheet ('Regular' -> '4.30'); '' when it has none."""
        return self.sheet().rate(delivery)

    def days_for(self, delivery: str) -> int | None:
        """Turnaround days for a speed: the sheet's Days column, else days_regular and the like; None for
        a speed neither knows."""
        from .rates import speed_key
        sp = self.sheet().find(delivery)
        if sp is not None and sp.days is not None:
            return sp.days
        return getattr(self, f"days_{speed_key(delivery)}", None)  # days_regular, days_expedited, ...

    def reporter_names(self) -> dict[str, str]:
        """The other reporters' names as the run sheet gives them, by initials ({"ds": "Dana"})."""
        return {k: p.name for k, p in self.reporters.items() if p.name}

    def reporter(self, initials: str) -> ReporterProfile:
        """Another reporter's details (blank ones, but for nothing, when there are none)."""
        return self.reporters.get(reporter_key(initials)) or ReporterProfile()

    def as_reporter(self, initials: str) -> "Settings":
        """These settings as an invoice made in another reporter's name takes them: their details at the top
        (ReporterProfile.profile), their payment text instead of the user's, no footer (it is the user's own
        note), and their invoice numbers (number_format). A copy: these settings are not changed."""
        from copy import copy
        r = self.reporter(initials)
        out = copy(self)
        out.profile = r.profile(reporter_key(initials))
        out.invoice_texts = [dict(t) for t in self.invoice_texts if t.get("where") not in ("payment", "footer")]
        if r.payment.strip():
            out.invoice_texts.append({"where": "payment", "when": "always", "text": r.payment.strip()})
        out.invoice_number_format = self.number_format(initials)
        out.signature_image, out.sign_reporter = "", False  # (the user's signature is never theirs)
        return out

    def number_format(self, initials: str = "") -> str:
        """How invoice numbers are written: the user's own (invoice_number_format), or another reporter's: the
        same with their prefix in front ("DS-{year}-{seq:04}"). Text the user's own format starts with is the
        user's mark ("PR-{year}-{seq:04}"): the prefix takes its place, rather than "DS-PR-2026-0001"."""
        own = self.invoice_number_format
        if not initials:
            return own
        prefix = (self.reporter(initials).prefix or initials.upper()).replace("{", "").replace("}", "")
        start = own.find("{")
        return f"{prefix}-{own[start:]}" if start > 0 else f"{prefix}-{own}"

    def offered_speeds(self) -> list:
        """The active sheet's speeds ticked under "Speeds offered" (invoice_speeds), in the sheet's order
        (agreement_speed sorts them by turnaround itself); every speed of the sheet when none of the ticked ones
        is on it."""
        from .rates import speed_key
        keys = {speed_key(w) for w in self.invoice_speeds if w}
        speeds = self.sheet().speeds
        return [sp for sp in speeds if sp.key in keys] or list(speeds)

    def agreement_speed(self) -> str:
        """The speed the minute agreement form names, in the sheet's spelling: agreement_speed_first when the
        invoice offers it, else the slowest offered (agreement_speed_fallback "fastest": the fastest). A speed's
        turnaround days decide which is slower (a speed without days counts as the fastest); the cheaper one is
        the slower when the days are the same. "" when the sheet has no speeds."""
        from .rates import speed_key
        pool = self.offered_speeds()
        if not pool:
            return ""
        first = next((sp for sp in pool if sp.key == speed_key(self.agreement_speed_first)), None)
        if first is not None:
            return first.name
        from .rates import parse_money

        def slowness(sp) -> tuple:
            days = self.days_for(sp.name)
            rate = parse_money(sp.original)
            return (days if days is not None else -1, -(rate or 0))
        ordered = sorted(pool, key=slowness)
        return (ordered[0] if self.agreement_speed_fallback == "fastest" else ordered[-1]).name

    def speed_offered(self, delivery: str) -> bool:
        """Whether a speed ('Expedited', 'Expedite') is among those the invoice offers (offered_speeds)."""
        from .rates import speed_key
        return any(sp.key == speed_key(delivery) for sp in self.offered_speeds())

    def delivery_name(self, delivery: str) -> str:
        """The active sheet's spelling of a speed ('Expedited' -> 'Expedite')."""
        sp = self.sheet().find(delivery)
        return sp.name if sp else delivery

    def signature(self) -> str:
        """The signature picture to put on the forms, or "" (not signing, none chosen, or the file is gone)."""
        ok = self.sign_reporter and self.signature_image and Path(self.signature_image).is_file()
        return self.signature_image if ok else ""

    @property
    def runsheet_dir(self) -> str:
        """The run sheets' own folder ("" = Documents\\YinIt Run Sheets): output_dirs["runsheet"]. Kept for
        older callers and the tests; the folders are output_dirs now (see folder_for)."""
        return self.output_dirs.get("runsheet", "")

    @runsheet_dir.setter
    def runsheet_dir(self, folder: str) -> None:
        self.output_dirs = {**self.output_dirs, "runsheet": folder}
        if not folder:
            del self.output_dirs["runsheet"]

    def folder_for(self, kind: str, general: Path | str | None = None) -> Path:
        """The folder an output (a key of OUTPUTS) is saved to: its own folder under Settings -> Options ->
        Folders, else its built-in one (OUTPUT_FOLDERS: run sheets go to Documents\\YinIt Run Sheets), else
        `general` (the job's "Save to" folder, see batch.out_dir_for; Documents\\Minute Agreements without one)."""
        own = self.output_dirs.get(kind, "")
        if own:
            return Path(own)
        if kind in OUTPUT_FOLDERS:
            return Path.home() / "Documents" / OUTPUT_FOLDERS[kind]
        return Path(general) if general else Path.home() / "Documents" / "Minute Agreements"

    def records_folder(self) -> Path:
        """Where record CSV copies and exports go: records_dir, else Documents\\YinIt Records."""
        return Path(self.records_dir) if self.records_dir else Path.home() / "Documents" / "YinIt Records"

    def turnaround(self, speed: str) -> str:
        """The invoice's turnaround wording for a speed ('Expedite' finds 'Expedited')."""
        from .rates import speed_key
        return next((v for k, v in self.invoice_turnaround.items() if speed_key(k) == speed_key(speed)), "")

    def remember_file(self, path: str) -> None:
        """Puts a document or folder just opened at the top of recent_files (once, RECENT_MAX at most)."""
        key = os.path.normcase(os.path.abspath(path))
        rest = [p for p in self.recent_files if os.path.normcase(os.path.abspath(p)) != key]
        self.recent_files = ([os.path.abspath(path)] + rest)[:RECENT_MAX]

    def save(self, force: bool = False) -> None:
        """Writes settings.json. It goes to a .tmp file first and then replaces the old one, so a crash
        halfway leaves the old settings, not a broken file. While `unreadable` (load() found a file it couldn't
        read) nothing is written: the app runs on the defaults, and a save made on its own (a file opened, an
        option ticked, the look for a newer version) would write them over the user's settings. force: the user
        pressed Save in Settings (Start in Welcome, Import settings), which is their choice to replace the file;
        it is written, and the flag cleared."""
        if self.unreadable and not force:
            return
        self.unreadable = False
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def export_to(self, path: Path | str, personal: bool = True) -> Path:
        """Saves the settings as one file to carry to another computer or give to a colleague: everything but
        what belongs to this computer (LOCAL: the window's place, the recent files, the signature picture...),
        and the rate sheets (the CSV files of the rate sheet folder, as text). The records are not in it.
        personal False (for a colleague): the user's own details are left out too (PERSONAL: name, address and
        contact, the reporters' names, the folders) and the payment text of the invoices; whoever imports the
        file keeps their own."""
        from .rates import sheets_dir
        data = {k: v for k, v in asdict(self).items() if k not in LOCAL}
        if not personal:
            for k in PERSONAL:
                data.pop(k, None)
            data["invoice_texts"] = [r for r in data["invoice_texts"] if r.get("where") != "payment"]
        data["personal"] = bool(personal)
        data["app"] = APP_NAME
        sheets = {}
        for p in sorted(sheets_dir(self.rate_sheets_dir).glob("*.csv")):
            try:
                sheets[p.name] = p.read_text(encoding="utf-8-sig")
            except (OSError, ValueError):
                continue  # (a sheet open in Excel, or not text: left out)
        data["rate_sheets"] = sheets
        path = Path(path)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    @classmethod
    def import_from(cls, path: Path | str, current: "Settings") -> "Settings":
        """The settings in a file made by export_to, as new Settings. Nothing is written: the caller saves
        them, after write_imported_sheets() has put the file's rate sheets in the rate sheet folder. What
        belongs to this computer (LOCAL) is taken from `current`, and so is who the user is (PERSONAL, and
        the invoices' payment text) when the file was exported without those (its "personal" is false: see
        `imported_personal` on the result). A folder that isn't on this computer (a colleague's Documents) is
        left blank, so the usual one is used. ValueError when the file is not a settings file."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"this file can't be read as settings: {e}") from None
        if not isinstance(data, dict) or data.get("app") not in (APP_NAME, *OLD_APP_NAMES):
            raise ValueError("this is not a settings file saved by YinItAgreementForm (File → Export settings)")
        data = _repath(data, _renamed_folders())  # (a file saved before the rename names the old folders)
        try:
            s = cls.from_dict(data)
        except (TypeError, AttributeError) as e:  # (a value of a kind from_dict doesn't expect)
            raise ValueError(f"this settings file is damaged: {e}") from None
        for k in LOCAL:
            setattr(s, k, getattr(current, k))
        s.imported_personal = data.get("personal") is not False  # (files from 1.7.0 have no mark: they hold them)
        if not s.imported_personal:
            from copy import deepcopy
            for k in PERSONAL:
                setattr(s, k, deepcopy(getattr(current, k)))
            s.invoice_texts = [r for r in s.invoice_texts if r["where"] != "payment"] + \
                              [dict(r) for r in current.invoice_texts if r.get("where") == "payment"]

        def here(folder: str) -> str:
            """The folder when it, or the folder it is in, is on this computer; else ""."""
            p = Path(folder) if folder else None
            return folder if p is not None and (p.is_dir() or p.parent.is_dir()) else ""

        s.output_dir, s.records_dir, s.rate_sheets_dir = here(s.output_dir), here(s.records_dir), here(s.rate_sheets_dir)
        s.output_dirs = {k: v for k, v in s.output_dirs.items() if here(v)}
        sheets = data.get("rate_sheets")
        s._imported_sheets = {name: text for name, text in (sheets.items() if isinstance(sheets, dict) else ())
                              if isinstance(name, str) and isinstance(text, str) and name.lower().endswith(".csv")}
        return s

    def write_imported_sheets(self) -> None:
        """Writes the rate sheets of the settings file these settings were imported from (import_from) into the
        rate sheet folder, once the user has said yes to the import. A sheet already there with other prices is
        kept, and the file's is saved next to it as "Name (imported)" ("Name (imported 2)" when that is taken by
        yet other prices), and used when it is the one the settings name."""
        from .rates import sheets_dir
        folder = sheets_dir(self.rate_sheets_dir)

        def same(target: Path, text: str) -> bool:
            """Whether a sheet on disk says what `text` does (spaces and line ends aside)."""
            try:
                return target.read_text(encoding="utf-8-sig").split() == text.split()
            except (OSError, ValueError):
                return False

        for name, text in getattr(self, "_imported_sheets", {}).items():
            first = folder / Path(name).name  # (the name only: never a path out of the folder)
            target, n = first, 1
            while target.exists() and not same(target, text):
                target = first.with_name(f"{first.stem} (imported{'' if n == 1 else f' {n}'}).csv")
                n += 1
            if target != first and self.rate_sheet == first.stem:
                self.rate_sheet = target.stem
            if not target.exists():
                target.write_text(text, encoding="utf-8")
        self._imported_sheets = {}
        self.reload_rates()

    @classmethod
    def load(cls) -> "Settings":
        """The saved settings (see from_dict); the defaults when there is no file, and the defaults marked
        `unreadable` when there is one that can't be read."""
        s = cls()
        try:
            data = json.loads(s.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return s
        except (OSError, ValueError):
            s.unreadable = True
            return s
        if not isinstance(data, dict):  # a damaged file must not keep the app from starting
            s.unreadable = True
            return s
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Settings from the contents of a settings file. Unknown keys and values of the wrong type are
        ignored, and files from older versions are brought up to date."""
        s = cls()
        known = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k == "profile":
                if isinstance(v, dict):
                    pk = {f.name for f in fields(Profile)}
                    s.profile = Profile(**{a: b for a, b in v.items() if a in pk and isinstance(b, str)})
            elif k in known and type(v) is type(getattr(s, k)):  # a value of the wrong kind keeps its default
                setattr(s, k, v)
        version = data.get("settings_version", 1)
        if not isinstance(version, int) or isinstance(version, bool):  # ("9", null: a file edited by hand)
            version = 1
        if version < 2:  # v2: the court's fillable UCS form became the default
            s.form_choice = "ucs"
        if version < 3:  # v3: instructions page included, today's date in the file name
            s.include_instructions = True
            if s.filename_pattern == OLD_FILENAME_PATTERN:
                s.filename_pattern = FILENAME_PATTERN
        # v4 added outputs; unknown ones are dropped. Lists and tables keep only entries of the right kind.
        s.outputs = [o for o in s.outputs if isinstance(o, str) and o in OUTPUTS]
        s.invoice_speeds = [x for x in s.invoice_speeds if isinstance(x, str)]  # none: the job's own speed
        if data.get("invoice_choice") is False:  # v5: "offer every speed" off billed the speed chosen under
            s.invoice_speeds = []                 # Order alone, which is what no speed ticked does now
        s.invoice_turnaround = {k: v for k, v in s.invoice_turnaround.items()
                                if isinstance(k, str) and isinstance(v, str)}
        s.invoice_index_threshold = max(1, s.invoice_index_threshold)
        # v9: a reporter's details (ReporterProfile) besides the name; a name alone ("ds": "Dana") is kept as it
        s.reporters = {reporter_key(k): _reporter_profile(v) for k, v in s.reporters.items()
                       if isinstance(k, str) and reporter_key(k) and isinstance(v, (str, dict))}
        # v9: the agreement form names one of the speeds the invoice offers (agreement_speed). A default speed the
        # user had changed from Regular becomes the first choice; Regular, the old default, gives way to Expedited
        old = data.get("default_delivery")
        if version < 9 and isinstance(old, str) and old.strip() and old.strip().lower() != "regular":
            s.agreement_speed_first = old.strip()
        if s.agreement_speed_fallback not in AGREEMENT_FALLBACKS:
            s.agreement_speed_fallback = "slowest"
        if s.invoice_index_rule not in INDEX_RULES:
            s.invoice_index_rule = "any"
        # v7: "Show granular detail" moved from the settings to each job (batch.Job.invoice_detail, off for every
        # new job; an old invoice_detail setting is ignored) and invoice_index_shared was added, so there is nothing
        # to convert: a missing or unknown value is "split"
        if s.invoice_index_shared not in INDEX_SHARED:
            s.invoice_index_shared = "split"
        s.invoice_detail_items = [k for k in s.invoice_detail_items if isinstance(k, str) and k in DETAIL_ITEMS]
        s.excerpt_hidden_speeds = [k for k in s.excerpt_hidden_speeds if isinstance(k, str)]
        # an answer about two firms: [key, key, same, name, name]; a row saved by 2.2.0 ([key, key, same]) gets
        # blank names
        s.firm_answers = [r[:3] + [x if isinstance(x, str) else "" for x in (r[3:5] + ["", ""])[:2]]
                          for r in s.firm_answers if isinstance(r, list) and len(r) in (3, 5)
                          and all(isinstance(k, str) and k for k in r[:2]) and isinstance(r[2], bool)]
        if s.runsheet_existing not in RUNSHEET_EXISTING:
            s.runsheet_existing = "ask"
        if s.ai_ticks not in AI_TICKS:
            s.ai_ticks = "text"
        # v8: a folder for each output. The run sheets' folder (runsheet_dir before) is one of them now.
        s.output_dirs = {k: v.strip() for k, v in s.output_dirs.items()
                         if k in OUTPUTS and isinstance(v, str) and v.strip()}
        old = data.get("runsheet_dir")
        if isinstance(old, str) and old.strip() and "runsheet" not in s.output_dirs:
            s.output_dirs["runsheet"] = old.strip()
        # v8: the invoice's text became rows (Settings -> Invoice -> Invoice text); a payment text and footer
        # written before are kept as the payment and footer rows
        if "invoice_texts" not in data:
            pay, foot = data.get("invoice_payment_text"), data.get("invoice_footer")
            s.invoice_texts = default_invoice_texts(pay if isinstance(pay, str) else INVOICE_PAYMENT_TEXT,
                                                    foot if isinstance(foot, str) else INVOICE_FOOTER)
        s.invoice_texts = [{"where": r["where"], "when": r["when"], "text": r["text"]} for r in s.invoice_texts
                           if isinstance(r, dict) and isinstance(r.get("where"), str) and r["where"] in TEXT_PLACES
                           and isinstance(r.get("when"), str) and r["when"] in TEXT_WHEN
                           and isinstance(r.get("text"), str)]
        s.records_columns = {k: [c for c in v if isinstance(c, str)] for k, v in s.records_columns.items()
                             if k in ("invoices", "activity") and isinstance(v, list)}
        if isinstance(data.get("zoom"), int) and not isinstance(data.get("zoom"), bool):
            s.zoom = float(data["zoom"])  # "zoom": 1 typed by hand
        s.zoom = min(1.6, max(0.7, s.zoom)) if s.zoom == s.zoom else 1.0
        # v10: the app was DjinnItAgreementForm, and its pictures the djinn's (show_djinn)
        if isinstance(data.get("show_djinn"), bool) and "show_yin" not in data:
            s.show_yin = data["show_djinn"]
        s.recent_files = [p for p in s.recent_files if isinstance(p, str) and p.strip()][:RECENT_MAX]
        s.settings_version = cls.settings_version
        return s
