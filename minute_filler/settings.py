"""Persistent user settings stored as JSON in %APPDATA%\\DjinnItAgreementForm."""
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


def reporter_key(initials: str) -> str:
    """Initials as they are looked up: 'D.S.' and 'd s' are 'ds'."""
    return initials.lower().replace(".", "").replace(" ", "")
INVOICE_FOOTER = ("I am in the courtroom during the day, so e-mail is the best way to reach me. "
                  "Please send a short e-mail after paying so I can start on your transcript.")


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
    settings_version: int = 4         # bumped when a default changes for existing users
    output_dir: str = ""              # blank = next to the first input file, else Documents\Minute Agreements
    filename_pattern: str = FILENAME_PATTERN  # {case} {index} {attorney} {date} (of the minutes) {today}
    batch_combine_dates: bool = False  # batch: all days of a case on one form instead of one form per day
    outputs: list = field(default_factory=lambda: ["agreement"])  # ticked by default: keys of OUTPUTS

    # MOFR (Minute Order Form/Receipt)
    mofr_division: str = "civil"      # "civil" or "criminal" box
    mofr_filename_pattern: str = MOFR_FILENAME_PATTERN

    # Invoices (made from transcripts only: they need the page count)
    invoice_choice: bool = True       # list every offered speed so the attorney can choose
    invoice_speeds: list = field(default_factory=lambda: ["Regular", "Expedited", "Daily"])
    invoice_include_email: bool = True  # each party also gets an e-mailed copy (Email column of the rate sheet)
    invoice_include_index: bool = True  # long transcripts get an index (Index column), plus one for the judge
    invoice_index_threshold: int = 50
    invoice_turnaround: dict = field(default_factory=lambda: dict(INVOICE_TURNAROUND))
    invoice_payment_text: str = INVOICE_PAYMENT_TEXT
    invoice_footer: str = INVOICE_FOOTER
    invoice_number_format: str = "{year}-{seq:04}"
    invoice_filename_pattern: str = INVOICE_FILENAME_PATTERN
    records_dir: str = ""             # blank = Documents\DjinnIt Records (CSV copies and exports)

    # Run sheets (who wrote which pages of a trial; see runsheet.py)
    runsheet_dir: str = ""            # blank = Documents\DjinnIt Run Sheets
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
        s.invoice_speeds = [x for x in s.invoice_speeds if isinstance(x, str)] or cls().invoice_speeds
        s.invoice_turnaround = {k: v for k, v in s.invoice_turnaround.items()
                                if isinstance(k, str) and isinstance(v, str)}
        s.invoice_index_threshold = max(1, s.invoice_index_threshold)
        s.reporters = {reporter_key(k): v for k, v in s.reporters.items()
                       if isinstance(k, str) and isinstance(v, str) and reporter_key(k)}
        if s.runsheet_existing not in RUNSHEET_EXISTING:
            s.runsheet_existing = "ask"
        s.settings_version = cls.settings_version
        return s
