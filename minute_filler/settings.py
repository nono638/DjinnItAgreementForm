"""Persistent user settings stored as JSON in %APPDATA%\\DjinnItAgreementForm.

Settings holds every choice (the defaults are what a new user gets) and load() brings older files up to date.
The tables here name the choices the window and the Settings dialog offer: the outputs (OUTPUTS), the speeds
(SPEEDS), which days of an invoice get an index (INDEX_RULES), who pays the index of pages several firms
ordered together (INDEX_SHARED), what "Show granular detail" adds to an invoice (DETAIL_ITEMS), and where the
invoice's own text goes and when it is shown (TEXT_PLACES, TEXT_WHEN, default_invoice_texts), and the folders
the outputs go to when they have none of their own (OUTPUT_FOLDERS, see Settings.folder_for).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

APP_NAME = "DjinnItAgreementForm"
FILENAME_PATTERN = "Minute Agreement - {case} - {index} - {attorney} - {today}"
OLD_FILENAME_PATTERN = "Minute Agreement - {case} - {index} - {attorney}"  # the default before settings v3
OLD_APP_NAME = "MinuteAgreementFiller"  # folder used before the rename


def settings_dir() -> Path:
    """%APPDATA%\\DjinnItAgreementForm (the home folder when APPDATA is unset), created when missing."""
    base = Path(os.environ.get("APPDATA") or str(Path.home()))
    d = base / APP_NAME
    old = base / OLD_APP_NAME
    if not d.exists() and old.is_dir():  # carry over settings and rate sheets from the old name
        import shutil
        try:
            shutil.copytree(old, d)
        except OSError:
            pass
    d.mkdir(parents=True, exist_ok=True)
    return d


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


OUTPUTS = {  # what Generate can make: key -> label
    "agreement": "Minute agreement",
    "mofr": "MOFR",
    "invoice": "Invoice",
    "runsheet": "Run sheet",
}
# Where each output is saved when Settings.output_dirs doesn't say: a folder of its own under Documents, or
# (not listed) the general "Save to" folder. A new output only needs its key in OUTPUTS and its file written to
# Settings.folder_for(key, ...): it then gets its own row under Settings -> Options -> Folders as well.
OUTPUT_FOLDERS = {"runsheet": "DjinnIt Run Sheets"}
SPEEDS = ("Regular", "Expedited", "Daily", "Immediate")  # the delivery speeds, as named in Settings
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
                     "{shared} (\"the original and the index\", what firms ordering the same pages split)")


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


def reporter_key(initials: str) -> str:
    """Initials as they are looked up: 'D.S.' and 'd s' are 'ds'."""
    return initials.lower().replace(".", "").replace(" ", "")


@dataclass
class Settings:
    """Everything the Settings dialog sets, kept in settings.json. The defaults here are what a new user
    gets; load() brings files from older versions up to date."""
    profile: Profile = field(default_factory=Profile)

    # Defaults used when the input doesn't say.
    default_court: str = "Supreme"
    default_county: str = "Queens"
    default_delivery: str = "Regular"
    default_copies: str = "1"
    # Rates come from a rate sheet (CSV) - see rates.py
    rate_sheet: str = "Sample Rates"
    rate_sheets_dir: str = ""         # blank = %APPDATA%\DjinnItAgreementForm\Rate Sheets
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
    settings_version: int = 8         # bumped when a default changes for existing users
    output_dir: str = ""              # blank = next to the first input file, else Documents\Minute Agreements
    output_dirs: dict = field(default_factory=dict)  # a folder for one output (key of OUTPUTS); none = as above
    filename_pattern: str = FILENAME_PATTERN  # {case} {index} {attorney} {date} (of the minutes) {today}
    batch_combine_dates: bool = False  # batch: all days of a case on one form instead of one form per day
    outputs: list = field(default_factory=lambda: ["agreement"])  # ticked by default: keys of OUTPUTS

    # MOFR (Minute Order Form/Receipt)
    mofr_division: str = "civil"      # "civil" or "criminal" box
    mofr_filename_pattern: str = MOFR_FILENAME_PATTERN

    # Invoices (made from transcripts only: they need the page count)
    # the speeds an invoice lists so the attorney can choose (one alone: a single-speed invoice)
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
    invoice_turnaround: dict = field(default_factory=lambda: dict(INVOICE_TURNAROUND))
    # the invoice's own text, each row placed and shown as it says (see TEXT_PLACES, TEXT_WHEN); the payment
    # details and the footer note are rows too
    invoice_texts: list = field(default_factory=default_invoice_texts)
    invoice_number_format: str = "{year}-{seq:04}"
    invoice_filename_pattern: str = INVOICE_FILENAME_PATTERN
    records_dir: str = ""             # blank = Documents\DjinnIt Records (CSV copies and exports)
    # the columns shown in the Records window: "invoices" / "activity" -> column keys (see records_window);
    # a table not listed shows its usual columns
    records_columns: dict = field(default_factory=dict)

    # Run sheets (who wrote which pages of a trial; see runsheet.py)
    runsheet_filename_pattern: str = RUNSHEET_FILENAME_PATTERN
    runsheet_existing: str = "ask"    # one of RUNSHEET_EXISTING
    reporters: dict = field(default_factory=dict)  # initials -> the name in the Reporter column ("ds": "Dana")

    # AI
    use_ai: bool = True
    ai_for_text: bool = True          # also ask the model about text inputs with gaps
    ollama_model: str = "gemma4:e2b"
    ollama_host: str = "http://localhost:11434"
    ai_timeout: int = 180

    # UI
    theme: str = "system"             # system / light / dark
    show_djinn: bool = True
    window_geometry: str = ""
    zoom: float = 1.0                 # Ctrl + / Ctrl - zoom (1.0 = 100 %), on top of the Windows display scaling

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
        """The run sheets' own folder ("" = Documents\\DjinnIt Run Sheets): output_dirs["runsheet"]. Kept for
        older callers and the tests; the folders are output_dirs now (see folder_for)."""
        return self.output_dirs.get("runsheet", "")

    @runsheet_dir.setter
    def runsheet_dir(self, folder: str) -> None:
        self.output_dirs = {**self.output_dirs, "runsheet": folder}
        if not folder:
            del self.output_dirs["runsheet"]

    def folder_for(self, kind: str, general: Path | str | None = None) -> Path:
        """The folder an output (a key of OUTPUTS) is saved to: its own folder under Settings -> Options ->
        Folders, else its built-in one (OUTPUT_FOLDERS: run sheets go to Documents\\DjinnIt Run Sheets), else
        `general` (the job's "Save to" folder, see batch.out_dir_for; Documents\\Minute Agreements without one)."""
        own = self.output_dirs.get(kind, "")
        if own:
            return Path(own)
        if kind in OUTPUT_FOLDERS:
            return Path.home() / "Documents" / OUTPUT_FOLDERS[kind]
        return Path(general) if general else Path.home() / "Documents" / "Minute Agreements"

    def records_folder(self) -> Path:
        """Where record CSV copies and exports go: records_dir, else Documents\\DjinnIt Records."""
        return Path(self.records_dir) if self.records_dir else Path.home() / "Documents" / "DjinnIt Records"

    def turnaround(self, speed: str) -> str:
        """The invoice's turnaround wording for a speed ('Expedite' finds 'Expedited')."""
        from .rates import speed_key
        return next((v for k, v in self.invoice_turnaround.items() if speed_key(k) == speed_key(speed)), "")

    def save(self) -> None:
        """Writes settings.json. It goes to a .tmp file first and then replaces the old one, so a crash
        halfway leaves the old settings, not a broken file."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls) -> "Settings":
        """The saved settings; the defaults when there is no file or it can't be read. Unknown keys and
        values of the wrong type are ignored, and files from older versions are brought up to date."""
        s = cls()
        try:
            data = json.loads(s.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        if not isinstance(data, dict):  # a damaged file must not keep the app from starting
            return s
        known = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k == "profile":
                if isinstance(v, dict):
                    pk = {f.name for f in fields(Profile)}
                    s.profile = Profile(**{a: b for a, b in v.items() if a in pk and isinstance(b, str)})
            elif k in known and type(v) is type(getattr(s, k)):  # a value of the wrong kind keeps its default
                setattr(s, k, v)
        if data.get("settings_version", 1) < 2:  # v2: the court's fillable UCS form became the default
            s.form_choice = "ucs"
        if data.get("settings_version", 1) < 3:  # v3: instructions page included, today's date in the file name
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
        s.reporters = {reporter_key(k): v for k, v in s.reporters.items()
                       if isinstance(k, str) and isinstance(v, str) and reporter_key(k)}
        if s.invoice_index_rule not in INDEX_RULES:
            s.invoice_index_rule = "any"
        # v7: "Show granular detail" moved from the settings to each job (batch.Job.invoice_detail, off for every
        # new job; an old invoice_detail setting is ignored) and invoice_index_shared was added, so there is nothing
        # to convert: a missing or unknown value is "split"
        if s.invoice_index_shared not in INDEX_SHARED:
            s.invoice_index_shared = "split"
        s.invoice_detail_items = [k for k in s.invoice_detail_items if isinstance(k, str) and k in DETAIL_ITEMS]
        if s.runsheet_existing not in RUNSHEET_EXISTING:
            s.runsheet_existing = "ask"
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
        s.settings_version = cls.settings_version
        return s
