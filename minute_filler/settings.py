"""Persistent user settings stored as JSON in %APPDATA%\\DjinnItAgreementForm."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

APP_NAME = "DjinnItAgreementForm"
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
    sign_reporter: bool = False       # type reporter name on the reporter signature line
    agreement_today: bool = True
    flatten: bool = False
    open_after: bool = True
    title_case_names: bool = True     # "HONORABLE MARIA T. ALVAREZ" -> "Maria T. Alvarez"

    # Output
    form_choice: str = "ucs"          # "ucs", "clean" or "original" (see fill.FORMS)
    include_instructions: bool = False  # add the UCS form's instructions page (page 2)
    settings_version: int = 2         # bumped when a default changes for existing users
    output_dir: str = ""              # blank = next to first input file, else Documents
    filename_pattern: str = "Minute Agreement - {case} - {index} - {attorney}"
    batch_combine_dates: bool = False  # batch: all days of a case on one form instead of one form per day

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
        return {"immediate": self.days_immediate, "daily": self.days_daily,
                "expedited": self.days_expedited, "regular": self.days_regular}.get(speed_key(delivery))

    def delivery_name(self, delivery: str) -> str:
        """The active sheet's spelling of a speed ('Expedited' -> 'Expedite')."""
        sp = self.sheet().find(delivery)
        return sp.name if sp else delivery

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
            s.settings_version = 2
        return s
