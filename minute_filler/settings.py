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
    """The court reporter's own details, written on every form."""
    name: str = ""
    title: str = "Court Reporter"
    address1: str = ""
    address2: str = ""
    phone: str = ""
    fax: str = ""
    email: str = ""
    website: str = ""


OUTPUTS = {  # what Generate can make: key -> label
    "agreement": "Minute agreement",
    "mofr": "MOFR",
    "invoice": "Invoice",
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
INVOICE_FOOTER = ("I am in the courtroom during the day, so e-mail is the best way to reach me. "
                  "Please send a short e-mail after paying so I can start on your transcript.")


@dataclass
class Settings:
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
    output_dir: str = ""              # blank = next to first input file, else Documents
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
        return settings_dir() / "settings.json"

    # ---- rates (the active sheet is cached; call reload_rates() after changing sheets)
    def sheets(self):
        from .rates import list_sheets
        if getattr(self, "_sheets", None) is None:
            self._sheets = list_sheets(self.rate_sheets_dir)
        return self._sheets

    def reload_rates(self) -> None:
        self._sheets = None

    def sheet(self):
        from .rates import pick
        return pick(self.sheets()[0], self.rate_sheet)

    def rate_for(self, delivery: str) -> str:
        return self.sheet().rate(delivery)

    def days_for(self, delivery: str) -> int | None:
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
        return Path(self.records_dir) if self.records_dir else Path.home() / "Documents" / "DjinnIt Records"

    def turnaround(self, speed: str) -> str:
        """The invoice's turnaround wording for a speed ('Expedite' finds 'Expedited')."""
        from .rates import speed_key
        return next((v for k, v in self.invoice_turnaround.items() if speed_key(k) == speed_key(speed)), "")

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls) -> "Settings":
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
        s.settings_version = cls.settings_version
        return s
