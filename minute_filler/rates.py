"""Rate sheets: small CSV files (editable in Excel) listing delivery speeds and per-page rates.

Format (same layout as the reporter's own invoice sheet):

    Rate,Original,Copy,Email,Index,Days
    Immediate,$7.60,$1.45,$1.45,$1.45,0
    Daily,$6.50,$1.25,$1.25,$1.25,1
    Expedite,$5.40,$1.10,$1.10,$1.10,7
    Regular,$4.30,$1.00,$1.00,$1.00,21
    Rates Last Updated:,5/2/2024

- First column: the speed name. "Original" is the per-page rate written on the form.
- Columns are found by how their heading starts, so a misspelt "Origianal" still counts; with no
  such heading the second column is the rate.
- "Copy" and any other columns (Email, Index) are kept for reference and for invoices. Email and Index are
  taken by name in any capitals ("EMAIL", "E-mail"); with no such heading, one close to it ("Index Price",
  "E mail") is read as it, when only one is (_close_headings). Every speed on the sheet needs all four prices
  (the window warns otherwise: missing_prices); a speed not offered is left off.
- "Days" (optional) is the turnaround used for the estimated delivery date; without it the
  turnaround days from Settings are used.
- Files whose name contains "template" are ignored, so the blank template can sit alongside.

Sheets live in a user-editable folder (default %APPDATA%\\YinItAgreementForm\\Rate Sheets),
seeded from the sheets bundled with the app (the courthouse's: bundled_dir).
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass, field
from pathlib import Path

from . import courthouses


def bundled_dir() -> Path | None:
    """The folder of the rate sheets that come with the program: the courthouse's (Courthouse.rate_sheets)."""
    return courthouses.current().rate_sheets


BUNDLED_DIR = bundled_dir()  # (as the program starts; seed reads bundled_dir() each time)


@dataclass
class Speed:
    """One row of a rate sheet: a delivery speed and its prices."""
    name: str
    original: str                      # per-page rate, e.g. "4.30"
    copy: str = ""                     # per-page rate for a copy
    days: int | None = None            # turnaround; None = from Settings
    # other columns: heading -> value ("Email": "$1.00"); a heading read as Email or Index is named so
    # ("Index Price" -> "Index": RateSheet.read_as)
    extras: dict[str, str] = field(default_factory=dict)

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
    """One rate sheet file: its name (the file name without .csv), its speeds, cheapest Original first (the
    later of two at one price counts as the faster), the 'Rates Last Updated' date, skipped: the speeds named on
    a row with prices but no Original (not read: see missing_prices), read_as: the headings read as another
    price ({"Index Price": "Index"}: the speeds' extras name the price so), and digest: a hash of the file as it
    was read (sheet_fingerprint). The sheet used without one (fallback()) has no file (path None)."""
    name: str
    path: Path | None
    speeds: list[Speed]
    updated: str = ""
    skipped: list[str] = field(default_factory=list)
    read_as: dict[str, str] = field(default_factory=dict)
    digest: str = ""

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


_AMOUNT = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")


def parse_amount(text) -> Decimal | None:
    """An amount typed by the user, all of it a money value: '63', '63.5', '$1,250.00' -> Decimal('1250.00').
    None for anything else: blank, negative ('-5'), a letter in it ('6O.00'), a decimal comma ('12,50') or
    words ('63 or 70'), which parse_money would read a number out of."""
    m = _AMOUNT.fullmatch(str(text if text is not None else "").strip())
    if not m:
        return None
    return Decimal(m.group(1).replace(",", "") + "." + (m.group(2) or "0")).quantize(Decimal("0.01"))


def _money(v: str) -> str:
    """parse_money as text: '$4.3' -> '4.30'; '' when there is no number."""
    d = parse_money(v)
    return "" if d is None else str(d)


def load_sheet(path: Path) -> RateSheet:
    """Reads one rate sheet CSV (layout above). Rows without a rate are skipped (one with other prices is
    named in RateSheet.skipped); raises ValueError when the file is empty or has no rates at all."""
    import hashlib
    data = path.read_bytes()  # (read once: what is hashed is what was read, see sheet_fingerprint)
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""))]
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
    named = _close_headings(rows[0], {0, i_orig, i_copy, i_days})  # ("Index Price" -> "Index", for invoices)
    read_as = {rows[0][i]: label for i, label in named.items()}
    speeds, updated, skipped = [], "", []
    for r in rows[1:]:
        first = r[0]
        if re.match(r"(?i)rates?\s+(last\s+)?updated", first):
            updated = next((c for c in r[1:] if c), "")
            continue
        orig = _money(r[i_orig]) if first and len(r) > i_orig else ""
        if not orig:
            # a speed with prices but no Original ("Realtime,,$2.00,..."): not offered, and the user should know.
            # A row with nothing but its name or days (the template's unfilled speeds) is a speed not offered
            if first and any(_money(c) for i, c in enumerate(r) if i not in (0, i_orig, i_days)):
                skipped.append(first)
            continue
        days = None
        if i_days is not None and i_days < len(r) and r[i_days].strip().isdigit():
            days = int(r[i_days])
        extras = {named.get(i, rows[0][i]): r[i] for i in range(1, min(len(r), len(rows[0])))
                  if i not in (i_orig, i_copy, i_days) and r[i]}
        speeds.append(Speed(first, orig, _money(r[i_copy]) if i_copy is not None and i_copy < len(r) else "",
                            days, extras))
    if not speeds:
        raise ValueError("no rates found")
    speeds.sort(key=lambda s: float(s.original))  # cheapest first, whatever order the file uses
    return RateSheet(path.stem, path, speeds, updated, skipped, read_as, hashlib.sha256(data).hexdigest()[:16])


# The prices an invoice reads from a speed's row besides Original and Copy (which have columns of their own): the
# column as the template names it, the headings invoice_calc.extra_rate takes for it (in any capitals), and the
# pattern of a heading close to it ("Index Price", "E mail", "Emailed copy"), read as it when no heading is the name
EXTRA_PRICES = (("Email", ("email", "e-mail"), re.compile(r"(?i)\be[\s._-]*mail")),
                ("Index", ("index",), re.compile(r"(?i)\bind[ei]x|\bindices")))


def _close_headings(header: list[str], taken: set) -> dict[int, str]:
    """The columns of a rate sheet read as its Email or Index prices though not headed so: {4: "Index"} for
    "Index Price" (`taken`: the speed's name, Original, Copy and Days). A heading that is the name itself
    ("Index", "E-mail", "EMAIL": what invoice_calc.extra_rate takes) comes first; only without one is a heading
    close to it read ("Index Price", "E mail", "Emailed copy", a slip like "Idnex"), when it is the only one. Two
    that could both be it ("Index Price", "Index Fee"), one close to both ("Email/Index") and a judge's index are
    not guessed at: the price is then missing, and the window says so (missing_prices)."""
    from difflib import SequenceMatcher

    def near(heading: str, label: str, pattern) -> bool:
        """'Index Price', 'Idnex' (a word one slip from the name) -> near 'Index'."""
        return bool(pattern.search(heading)) or any(SequenceMatcher(None, w, label.lower()).ratio() >= 0.8
                                                    for w in re.findall(r"[a-z]+", heading.lower()))

    free = [i for i in range(1, len(header)) if i not in taken]
    exact = {label: next((i for i in free if header[i].strip().lower() in names), None)
             for label, names, _ in EXTRA_PRICES}
    found: dict[int, str] = {}
    for label, _, pattern in EXTRA_PRICES:
        if exact[label] is not None:
            continue
        close = [i for i in free if i not in exact.values() and near(header[i], label, pattern)
                 and "judge" not in header[i].lower()
                 and not any(near(header[i], other, p) for other, _, p in EXTRA_PRICES if other != label)]
        if len(close) == 1:
            found[close[0]] = label
    return found


def missing_prices(sheet: RateSheet) -> list[tuple[str, list[str]]]:
    """The prices a rate sheet leaves out, speed by speed, as its columns name them: [("Regular", ["Email",
    "Index"]), ("Realtime", ["Original"])] (a row with no Original isn't read at all: RateSheet.skipped). Every
    speed on the sheet needs all of them, or invoices come out wrong (no index charged, copies at $0.00); a speed
    left off the sheet is one not offered, which is fine (the user, 2026-10-09: not every agency does Immediate,
    some courts offer Realtime, "but every peripheral should be spelled out"). Any amount counts, $0.00 too; a
    blank or words ("n/a") don't. [] for a complete sheet, and for fallback()'s (no file to fill in)."""
    if sheet.path is None:
        return []
    out = []
    for sp in sheet.speeds:
        gaps = [] if sp.copy else ["Copy"]
        for label, names, _ in EXTRA_PRICES:
            if parse_money(next((v for k, v in sp.extras.items() if k.strip().lower() in names), "")) is None:
                gaps.append(label)
        if gaps:
            out.append((sp.name, gaps))
    return out + [(name, ["Original"]) for name in sheet.skipped]


def sheet_fingerprint(sheet: RateSheet) -> str:
    """The sheet's name and a hash of its file as it was read ("Court Rates:3f2a9c..."), to tell whether it
    changed since the user said to use it as it was (Settings.rate_sheets_ok). Of the file as read, not as it is
    now: edited since without being read again (⟳), "Use it anyway" would pass prices the user never saw. ""
    without a file (fallback()'s)."""
    if sheet.path is None or not sheet.digest:
        return ""
    return f"{sheet.name}:{sheet.digest}"


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


# Bundled sheets as earlier versions shipped them (SHA-256 of the file, CRLF and LF): a copy in the user folder
# that is still exactly one of these was never edited, so seed() replaces it with the sheet as it ships now.
# Sample Rates before 2.5.0 had Daily copies, e-mailed copies and indexes at $1.30 a page; they are $1.25.
OLD_BUNDLED = {
    "Sample Rates.csv": {"e5cf39e5e2f794569669abf41c2fdaad81d90b162c90b2641295807cbf0efbdb",
                         "4bfb2589aca110fcd28f4f73bcca47fc4f0eab03982e288eda11db9f10446234"},
}


def is_old_bundled(name: str, data: bytes) -> bool:
    """Whether `data` is the bundled sheet `name` ("Sample Rates.csv") exactly as an earlier version shipped it
    (OLD_BUNDLED): a copy nobody edited, to be read as the sheet as it ships now."""
    import hashlib
    return hashlib.sha256(data).hexdigest() in OLD_BUNDLED.get(name, ())


def seed(d: Path) -> None:
    """Copies bundled sheets (and the template) into the user folder without overwriting edits: a sheet that is
    missing is copied, and one still exactly as an earlier version shipped it (OLD_BUNDLED) is brought up to
    date."""
    bundled = bundled_dir()
    if bundled is None or not bundled.exists():
        return
    for src in bundled.glob("*.csv"):
        dst = d / src.name
        try:
            if not dst.exists():
                shutil.copy2(src, dst)
                continue
            # (a sheet no version changed is not read: the folder can be on OneDrive or a network drive, and
            # this runs each time the sheets are listed)
            if src.name not in OLD_BUNDLED or not is_old_bundled(src.name, dst.read_bytes()):
                continue  # (the user's own, or already the sheet as it ships)
            # copied next to it, then swapped in at once: a copy that stopped part way (the disk full) would match
            # no hash, and the half-written sheet would be kept as the user's own
            new = dst.with_name(dst.name + ".new")
            try:
                shutil.copy2(src, new)
                os.replace(new, dst)
            finally:
                new.unlink(missing_ok=True)  # (still there only when the swap failed: the sheet open in Excel)
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


def fallback() -> RateSheet:
    """The sheet used when no rate sheet can be read, so the form still gets a rate: the courthouse's prices
    (Courthouse.fallback_rates: in New York, the minimums on the form)."""
    return RateSheet("Built-in (form minimums)", None, [Speed(*r) for r in courthouses.current().fallback_rates])


FALLBACK = fallback()  # (as the program starts; pick gives fallback() each time)


def pick(sheets: list[RateSheet], name: str) -> RateSheet:
    """The sheet called `name`, else the sample sheet, else the first one; fallback() when there are none."""
    if not sheets:
        return fallback()
    return next((s for s in sheets if s.name == name), None) or \
        next((s for s in sheets if "sample" in s.name.lower()), sheets[0])
