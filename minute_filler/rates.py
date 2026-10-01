"""Rate sheets: small CSV files (editable in Excel) listing delivery speeds and per-page rates.

Format (same layout as the reporter's own invoice sheet):

    Rate,Original,Copy,Email,Index,Days
    Immediate,$7.60,$1.45,$1.45,$1.45,0
    Daily,$6.50,$1.30,$1.30,$1.30,1
    Expedite,$5.40,$1.10,$1.10,$1.10,7
    Regular,$4.30,$1.00,$1.00,$1.00,21
    Rates Last Updated:,5/2/2024

- First column: the speed name. "Original" is the per-page rate written on the form.
- Columns are found by how their heading starts, so a misspelt "Origianal" still counts; with no
  such heading the second column is the rate.
- "Copy" and any other columns (Email, Index) are kept for reference and for invoices.
- "Days" (optional) is the turnaround used for the estimated delivery date; without it the
  turnaround days from Settings are used.
- Files whose name contains "template" are ignored, so the blank template can sit alongside.

Sheets live in a user-editable folder (default %APPDATA%\\DjinnItAgreementForm\\Rate Sheets),
seeded from the sheets bundled with the app.
"""
from __future__ import annotations

import csv
import re
import shutil
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().with_name("rate_sheets")


@dataclass
class Speed:
    """One row of a rate sheet: a delivery speed and its prices."""
    name: str
    original: str                      # per-page rate, e.g. "4.30"
    copy: str = ""                     # per-page rate for a copy
    days: int | None = None            # turnaround; None = from Settings
    extras: dict[str, str] = field(default_factory=dict)  # other columns: heading -> value ("Email": "$1.00")

    @property
    def key(self) -> str:
        """The speed as speed_key names it ('Expedite' -> 'expedited')."""
        return speed_key(self.name)

    def label(self) -> str:
        """'Regular  ·  $4.30/pg  ·  copy $1.00', for lists of speeds."""
        bits = [self.name, f"${self.original}/pg"]
        if self.copy:
            bits.append(f"copy ${self.copy}")
        return "  ·  ".join(bits)


@dataclass
class RateSheet:
    """One rate sheet file: its name (the file name without .csv), its speeds, cheapest first, and the
    'Rates Last Updated' date. FALLBACK has no file (path None)."""
    name: str
    path: Path | None
    speeds: list[Speed]
    updated: str = ""

    def find(self, delivery: str) -> Speed | None:
        """Matches 'Expedited' to 'Expedite', 'same day' to 'Immediate', etc.; None when the sheet lacks it."""
        k = speed_key(delivery)
        return next((s for s in self.speeds if s.key == k), None) or \
            next((s for s in self.speeds if s.name.lower() == (delivery or "").strip().lower()), None)

    def rate(self, delivery: str) -> str:
        """The per-page rate for a speed ('Regular' -> '4.30'); '' when the sheet lacks it."""
        s = self.find(delivery)
        return s.original if s else ""


def speed_key(name: str) -> str:
    """A speed's name as one of 'regular', 'expedited', 'daily' or 'immediate', whatever the spelling
    ('Expedite', 'rush' -> 'expedited'); any other name comes back trimmed and in lowercase."""
    n = (name or "").strip().lower()
    if n.startswith("expedit") or n in ("rush", "expedite"):
        return "expedited"
    if n.startswith("daily") or n in ("overnight", "next day", "next-day"):
        return "daily"
    if n.startswith("immediate") or n in ("same day", "same-day", "hourly"):
        return "immediate"
    if n.startswith("regular") or n in ("standard", "normal"):
        return "regular"
    return n


def parse_money(v) -> Decimal | None:
    """'$1,234.5' / 4.3 / 'about 4.30 a page' -> Decimal('1234.50') etc.; None when there is no number."""
    m = re.search(r"\d+(?:\.\d+)?", str(v if v is not None else "").replace(",", ""))
    return Decimal(m.group()).quantize(Decimal("0.01"), ROUND_HALF_UP) if m else None


def _money(v: str) -> str:
    """parse_money as text: '$4.3' -> '4.30'; '' when there is no number."""
    d = parse_money(v)
    return "" if d is None else str(d)


def load_sheet(path: Path) -> RateSheet:
    """Reads one rate sheet CSV (layout above). Rows without a rate are skipped; raises ValueError when
    the file is empty or has no rates at all."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [[c.strip() for c in r] for r in csv.reader(f)]
    rows = [r for r in rows if any(r)]
    if not rows:
        raise ValueError("empty file")
    header = [h.lower() for h in rows[0]]

    def col(*prefixes):
        return next((i for i, h in enumerate(header) if any(h.startswith(p) for p in prefixes)), None)

    i_orig = col("orig", "rate per page", "per page", "price")
    if i_orig is None or i_orig == 0:
        i_orig = 1
    i_copy = col("copy", "copies")
    i_days = col("days", "turnaround")
    speeds, updated = [], ""
    for r in rows[1:]:
        first = r[0]
        if re.match(r"(?i)rates?\s+(last\s+)?updated", first):
            updated = next((c for c in r[1:] if c), "")
            continue
        if not first or len(r) <= i_orig:
            continue
        orig = _money(r[i_orig])
        if not orig:
            continue
        days = None
        if i_days is not None and i_days < len(r) and r[i_days].strip().isdigit():
            days = int(r[i_days])
        extras = {rows[0][i]: r[i] for i in range(1, min(len(r), len(rows[0])))
                  if i not in (i_orig, i_copy, i_days) and r[i]}
        speeds.append(Speed(first, orig, _money(r[i_copy]) if i_copy is not None and i_copy < len(r) else "",
                            days, extras))
    if not speeds:
        raise ValueError("no rates found")
    speeds.sort(key=lambda s: float(s.original))  # cheapest first, whatever order the file uses
    return RateSheet(path.stem, path, speeds, updated)


def default_dir() -> Path:
    """The Rate Sheets folder in the settings folder."""
    from .settings import settings_dir
    return settings_dir() / "Rate Sheets"


def sheets_dir(custom: str = "") -> Path:
    """The rate sheet folder (`custom`, else default_dir()), created and seeded when needed."""
    d = Path(custom) if custom else default_dir()
    d.mkdir(parents=True, exist_ok=True)
    seed(d)
    return d


def seed(d: Path) -> None:
    """Copies bundled sheets (and the template) into the user folder without overwriting edits."""
    if not BUNDLED_DIR.exists():
        return
    for src in BUNDLED_DIR.glob("*.csv"):
        dst = d / src.name
        if not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def list_sheets(custom_dir: str = "") -> tuple[list[RateSheet], list[str]]:
    """(valid sheets, problems) - templates are skipped."""
    sheets, problems = [], []
    for p in sorted(sheets_dir(custom_dir).glob("*.csv"), key=lambda p: p.name.lower()):
        if "template" in p.stem.lower():
            continue
        try:
            sheets.append(load_sheet(p))
        except Exception as e:
            problems.append(f"{p.name}: {e}")
    return sheets, problems


# Used when no rate sheet can be read, so the form still gets a rate.
FALLBACK = RateSheet("Built-in (form minimums)", None, [
    Speed("Regular", "3.30", "1.00"), Speed("Expedited", "4.40", "1.10"), Speed("Daily", "5.50", "1.25")])


def pick(sheets: list[RateSheet], name: str) -> RateSheet:
    """The sheet called `name`, else the sample sheet, else the first one; FALLBACK when there are none."""
    if not sheets:
        return FALLBACK
    return next((s for s in sheets if s.name == name), None) or \
        next((s for s in sheets if "sample" in s.name.lower()), sheets[0])
