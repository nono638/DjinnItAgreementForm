"""Writes a CaseInfo + Attorney + reporter profile into one of the minute agreement forms.

Forms (Settings.form_choice):
  "ucs"      - the court's fillable UCS form (default)
  "clean"    - the app's re-typeset form with named fields and extra lines
  "original" - the 1999 scan with fields added on top

Lines the UCS form or the original has no field for (forms/*_map.py OVERLAYS: the signature lines and a second
line for the dates, and on the original also the fax lines, the date of agreement and the case name's second and
third lines) get a text field added, so every value can still be changed in a PDF viewer. A signature picture
takes the place of the reporter's signature field there.

The dates are written with three or more days in a row as a range ("9/28/2026–10/2/2026", date_ranges); on the
UCS form and the original, dates that still don't fit their line go on to the second one, split between two dates
(_spill_dates).

Each attorney's agreement shows as its Estimated Number of Pages the pages that attorney ordered, whoever wrote
them (agreement_case, from batch.Job.ordered_pages); with no count (no transcript and no pages typed), the
field as it is. A firm that ordered no pages of the day gets no agreement (agreement_orderers).

Also helpers that the MOFR, invoices and run sheets share: the dates as ranges (date_ranges), writing form fields
(set_text) and adding new ones (add_text_field), file names (output_name, safe_filename, unique_path) and saving
(save_output, which labels every PDF the app makes so it is never read back as an input, and flattens it when
Settings say so).
lock_pdf saves a copy with the fields flattened (File → Lock finished PDFs).
"""
from __future__ import annotations

import copy
import dataclasses
import re
import threading
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import pymupdf

from . import signature
from .dates import us_date
from .forms import original_map, ucs_map
from .models import CaseInfo, Attorney, PROC_TYPES, SRC_DEFAULT
from .rates import speed_key
from .settings import Settings

FIELD_FONT = "helv"
MAX_FS, MIN_FS = 10.0, 5.5
DATES_FLOOR = 7.0  # the dates line of the UCS and original forms shrinks to this before it spills onto a second line
RANGE_DAYS = 3     # days in a row that become a range on the forms ("9/28/2026–9/30/2026"); two stay listed
FORM_SPEEDS = ("regular", "expedited", "daily")  # the speeds with a box of their own on the form


def forms_dir() -> Path:
    """The folder of blank forms. Works from source and from a PyInstaller bundle (the forms are
    shipped as data under minute_filler/forms)."""
    return Path(__file__).resolve().with_name("forms")


FORMS = {  # Settings.form_choice -> (file in forms/, name shown in Settings)
    "ucs": ("minute_agreement_ucs.pdf", "UCS form (fillable)"),
    "clean": ("minute_agreement_clean.pdf", "Re-typeset form"),
    "original": ("minute_agreement_original.pdf", "Original 1999 scan"),
}
DEFAULT_FORM = "ucs"


def form_path(choice: str) -> Path:
    """The blank PDF for a form choice; an unknown choice gets the UCS form."""
    return forms_dir() / FORMS.get(choice, FORMS[DEFAULT_FORM])[0]


_FONTS = threading.local()  # one Font per thread: the batch's thread measures too, and a Font has no lock


def text_width(s: str, fs: float) -> float:
    """Width of `s` in points, in the fields' font at size `fs`. Measured with the font itself:
    pymupdf.get_text_length measures a text wrong once it has a letter outside Latin-1 (the en dash of
    "10/12/2026–10/16/2026" makes it 14 points short at size 10), so too large a size was chosen and the last
    date was cut off."""
    font = getattr(_FONTS, "font", None)
    if font is None:
        font = _FONTS.font = pymupdf.Font(FIELD_FONT)
    return font.text_length(s, fontsize=fs)


def fit_size(s: str, rect: pymupdf.Rect, max_fs: float = MAX_FS) -> float:
    """The font size for `s` on one line of `rect`: as large as the line's height allows (at most max_fs),
    then smaller until it fits, but never below MIN_FS."""
    fs = min(max_fs, max(MIN_FS, rect.height * 0.78))
    while fs > MIN_FS and text_width(s, fs) > rect.width - 4:
        fs -= 0.5
    return fs


def wrap(s: str, width: float, lines: int, fs: float = MAX_FS) -> list[str]:
    """Greedy word wrap into at most `lines` lines; the last line keeps the overflow
    (and is later shrunk to fit)."""
    words, out, cur = s.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, fs) <= width - 4 or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    if len(out) > lines:
        out = out[: lines - 1] + [" ".join(out[lines - 1:])]
    return out + [""] * (lines - len(out))


def wrap_fit(s: str, width: float, lines: int, max_fs: float = MAX_FS) -> tuple[list[str], float]:
    """(lines, font size): the largest size at which `s` wraps into `lines` lines of `width`, the lines
    padded with "" to that many. At MIN_FS the last line may still overflow."""
    fs = max_fs
    while fs > MIN_FS:
        out = wrap(s, width, lines, fs)
        if all(text_width(l, fs) <= width - 4 for l in out):
            return out, fs
        fs -= 0.5
    return wrap(s, width, lines, MIN_FS), MIN_FS


def date_ranges(text: str) -> str:
    """The dates as the forms list them: days in a row (RANGE_DAYS or more) become a range with an en dash,
    "9/28/2026–10/2/2026", and a gap keeps the list, "9/28/2026–9/30/2026, 10/5/2026"; two days in a row stay
    "9/28/2026, 9/29/2026". The UCS form's field is 100 points wide: four dates written out no longer fit it, and
    the MOFR's line gives out at six (five from October on). Only a text that is nothing but dates (and commas,
    ";", "&" or "and") is rewritten, and only when it holds such a run: "6/3/2026 (a.m. session)" is kept as
    typed. The case's own Dates field, the invoice, the records and the file names keep every day written out (the
    records are searched by date)."""
    from .extract_regex import find_dates
    found = find_dates(text or "")
    if len(found) < RANGE_DAYS:
        return text
    rest = text
    for start, end, _ in reversed(found):
        rest = rest[:start] + rest[end:]
    if re.sub(r"(?i)[\s,;&]|\band\b", "", rest):
        return text  # more than a list of dates: left as typed
    days = sorted({date(int(y), int(m), int(d)) for _, _, s in found for m, d, y in [s.split("/")]})
    runs: list[list[date]] = []
    for day in days:
        if runs and day - runs[-1][-1] == timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    if not any(len(r) >= RANGE_DAYS for r in runs):
        return text
    return ", ".join(f"{us_date(r[0])}–{us_date(r[-1])}" if len(r) >= RANGE_DAYS else
                     ", ".join(us_date(d) for d in r) for r in runs)


def split_address(addr: str) -> list[str]:
    """An address as the form's two lines: the first line, then the rest joined with commas."""
    lines = [l.strip(" ,") for l in re.split(r"[\r\n]+", addr or "") if l.strip(" ,")]
    if len(lines) > 2:
        lines = [lines[0], ", ".join(lines[1:])]
    return lines + [""] * (2 - len(lines))


def build_values(case: CaseInfo, atty: Attorney | None, s: Settings) -> dict[str, str | bool]:
    """What goes on the form, by the keys of forms/*_map.py ('judge', 'proc_trial', 'delivery_daily',
    'atty_email'...). Boxes are True/False. With atty None the attorney's lines stay blank. The dates are
    listed as date_ranges says (days in a row as a range)."""
    g = case.get
    v: dict[str, str | bool] = {
        "court": g("court"), "county": g("county"), "part": g("part"), "judge": g("judge"),
        "index_no": g("index_no"), "dates": date_ranges(g("dates")),
        "proc_other": g("proc_other"), "proc_other_check": bool(g("proc_other").strip()),
        "rate": g("rate").lstrip("$"), "copies": g("copies"), "est_pages": g("est_pages"),
        "delivery_date": g("delivery_date"), "agreement_date": g("agreement_date"),
    }
    for p in PROC_TYPES:
        v[f"proc_{p.lower()}"] = p in case.proc_types
    delivery = g("delivery").strip()  # the speed chosen under "Agreement form"
    key = speed_key(delivery)
    for d in FORM_SPEEDS:
        v[f"delivery_{d}"] = key == d
    if delivery and key not in FORM_SPEEDS:  # e.g. "Immediate"
        v["delivery_other_check"] = True
        v["delivery_other"] = "" if key == "other" else delivery

    p = s.profile
    v.update({
        "rep_name": p.name, "rep_address_1": p.address1, "rep_address_2": p.address2,
        "rep_phone": p.phone, "rep_fax": p.fax, "rep_email": p.email,
        "sig_reporter": p.name if s.sign_reporter and not s.signature() else "",
    })
    if atty is not None:
        a1, a2 = split_address(atty.address)
        v.update({
            "atty_name": atty.name, "atty_firm": atty.firm, "atty_address_1": a1, "atty_address_2": a2,
            "atty_phone": atty.phone, "atty_fax": atty.fax, "atty_email": atty.email,
        })
    v["sig_attorney"] = "per email" if s.per_email else ""
    return v


_NO_ACCENT_FORM = str.maketrans("ŁłĐđØøıİ", "LlDdOoiI")


def form_text(s: str) -> str:
    """The fields of the court's form are set in Helvetica, which has the Western European
    letters only; others (Š, ł, ễ...) come out cut off, so they are written without their accent."""
    out = []
    for ch in s:
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            ch = (unicodedata.normalize("NFKD", ch.translate(_NO_ACCENT_FORM)).encode("ascii", "ignore").decode()
                  or ch)
        out.append(ch)
    return "".join(out)


def set_text(w: pymupdf.Widget, text: str, fs: float | None = None) -> None:
    """Writes `text` into a text field at size `fs`, else the largest that fits."""
    w.field_value = text
    w.text_font = "Helv"
    # size 0 on a blank field = automatic, for whoever types into it later in a PDF viewer
    w.text_fontsize = (fs or fit_size(text, w.rect)) if text else 0
    w.text_color = (0, 0, 0)
    w.update()


def add_text_field(page: pymupdf.Page, name: str, rect, text: str = "", fs: float = 0, font: str = "Helv",
                   color=(0, 0, 0), multiline: bool = False, right: bool = False) -> pymupdf.Widget:
    """Adds a text field holding `text` at `rect`, so the value can still be changed in a PDF viewer.
    fs: the font size (0 = automatic); font: "Helv", "TiRo" or "Cour" (fields can't be bold); right:
    right-aligned."""
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.field_value = text
    w.text_font = font
    w.text_fontsize = fs
    w.text_color = color
    w.border_width = 0
    if multiline:
        w.field_flags = pymupdf.PDF_TX_FIELD_IS_MULTILINE
    w = page.add_widget(w)
    if right:  # /Q 2: right-aligned (Widget has no property for it)
        page.parent.xref_set_key(w.xref, "Q", "2")
        w.update()
    return w


# PDFs made by 1.3.0 had a "Lock fields" button of this name. Most viewers (Firefox, Edge, Chrome) ignore the
# script behind it, and the attorneys saw it too, so it is no longer added; lock_pdf still takes it out.
LOCK_BUTTON = "DjinnIt lock"  # (the app was DjinnIt then)


def has_fields(path: Path) -> bool:
    """True when a PDF has fields to lock (the old Lock fields button doesn't count): False for one already
    flattened or locked."""
    with pymupdf.open(path) as doc:
        return any(w.field_name != LOCK_BUTTON for page in doc for w in page.widgets())


def lock_pdf(path: Path) -> Path:
    """Saves a copy of a PDF with its fields flattened into the page (they can no longer be changed) as
    '<name> (locked).pdf' next to it, never overwriting; returns the copy's path. The original is kept."""
    path = Path(path)
    with pymupdf.open(path) as doc:
        for page in doc:
            buttons = [w.xref for w in page.widgets() if w.field_name == LOCK_BUTTON]
            for xref in buttons:
                page.delete_widget(page.load_widget(xref))
        doc.bake()
        out = unique_path(path.with_name(f"{path.stem} (locked){path.suffix}"))
        doc.save(out, garbage=3, deflate=True)
    return out


def set_check(w: pymupdf.Widget, on: bool) -> None:
    """Ticks or clears a checkbox field."""
    w.field_value = w.on_state() if on else "Off"
    w.update()


def _spread(v: dict, text: str, keys: list[str], width: float, fixed: dict[str, float],
            max_fs: float = MAX_FS) -> None:
    """Wraps `text` over several single-line fields at one shared font size."""
    lines, fs = wrap_fit(text, width, len(keys), max_fs)
    for k, line in zip(keys, lines):
        v[k] = line
        fixed[k] = fs


def _fill_clean(doc: pymupdf.Document, v: dict) -> None:
    """Fills the re-typeset form, whose fields are named by our keys and have real checkboxes."""
    page = doc[0]
    widgets = {w.field_name: w for w in page.widgets()}
    fixed: dict[str, float] = {}
    _spread(v, v.get("case_name_raw", ""), ["case_name_1", "case_name_2", "case_name_3"],
            widgets["case_name_1"].rect.width, fixed)
    _spread(v, str(v.get("dates", "")), ["dates", "dates_2"], widgets["dates"].rect.width, fixed)
    for name, w in widgets.items():
        val = v.get(name, "")
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            set_check(w, bool(val))
        else:
            set_text(w, "" if isinstance(val, bool) else str(val), fixed.get(name))


def _spill_dates(v: dict, first: pymupdf.Rect, second: pymupdf.Rect, fixed: dict[str, float]) -> None:
    """The dates on a mapped form: left on their own line when they fit it at DATES_FLOOR or larger (set_text then
    picks the size); else split between dates over that line and the one added under it (dates_2), at the largest
    size both fit at. "9/28/2026, 9/30/2026, 10/2/2026, 10/5/2026" is 107 points at the smallest size and the
    UCS line 100 wide: on one line it was cut off. The text is split only between two dates (_date_breaks), and
    each line keeps it as typed."""
    text = str(v.get("dates", ""))
    if not text or fit_size(text, first) >= DATES_FLOOR:
        return
    # (first line, second line) for each place the text may be split, the most on the first line first
    splits = [(re.sub(r"(?i)(?:[\s,;&]|\band\b)+$", "", text[:i]), text[i:].strip())
              for i in reversed(_date_breaks(text))]
    splits = [(a, b) for a, b in splits if a and b]
    if not splits:
        return
    fs = min(MAX_FS, max(MIN_FS, first.height * 0.78))
    fits = lambda text, rect, size: text_width(text, size) <= rect.width - 4
    while fs > MIN_FS:
        for a, b in splits:
            if fits(a, first, fs) and fits(b, second, fs):
                v["dates"], v["dates_2"] = a, b
                fixed["dates"] = fixed["dates_2"] = fs
                return
        fs -= 0.5
    # not even over two lines at the smallest size: as many as fit on the first, the rest on the second (which
    # keeps the overflow, as wrap does)
    v["dates"], v["dates_2"] = next(((a, b) for a, b in splits if fits(a, first, MIN_FS)), splits[-1])
    fixed["dates"] = fixed["dates_2"] = MIN_FS


def _date_breaks(text: str) -> list[int]:
    """Where a dates text may go on to a second line: after the last comma, ";", "&" or "and" between two dates,
    never inside a date ("September 28, 2026") or a range ("9/28/2026–10/2/2026"). A text with fewer than two dates
    is split after a comma outside its date ("6/3/2026 (a.m. session), the p.m. session")."""
    from .extract_regex import find_dates
    found = find_dates(text)
    if len(found) < 2:
        return [m.end() for m in re.finditer(",", text) if not any(s <= m.start() < e for s, e, _ in found)]
    out = []
    for (_, end, _), (start, _, _) in zip(found, found[1:]):
        seps = list(re.finditer(r"(?i)[,;&]|\band\b", text[end:start]))  # (a dash between them: a range)
        if seps:
            out.append(end + seps[-1].end())
    return out


def _fill_mapped(doc: pymupdf.Document, v: dict, fmap, case_fs: float) -> None:
    """Fills a form whose field names are mapped to our keys (forms/*_map.py). Its boxes are text
    fields, so a ticked one gets an "X"; lines it has no field for (fmap.OVERLAYS: signatures etc.) get a text
    field added, blank ones too, so a value can be typed in later. Dates that don't fit their line go on over
    the line added under it (_spill_dates).
    case_fs: the largest font size for the case name."""
    page = doc[0]
    # the original's fields are named "Text-<id>"; fields not in the map end up under None and are skipped
    widgets = {fmap.WIDGETS.get(w.field_name.removeprefix("Text-")): w for w in page.widgets()}
    fixed: dict[str, float] = {}
    for k, val in v.items():
        if isinstance(val, str):
            v[k] = form_text(val)
    _spread(v, v.get("case_name_raw", ""), ["case_name_1", "case_name_2", "case_name_3"],
            widgets["case_name_1"].rect.width, fixed, max_fs=case_fs)
    if "dates_2" in fmap.OVERLAYS:
        _spill_dates(v, widgets["dates"].rect, pymupdf.Rect(fmap.OVERLAYS["dates_2"]), fixed)
    for src, dst in fmap.MERGE_INTO.items():
        if v.get(src):
            v[dst] = ", ".join(x for x in (v.get(dst, ""), v[src]) if x)
    if v.get("rate"):
        v["rate"] = f"${v['rate']}"
    for key, w in widgets.items():
        if key is None:
            continue
        val = v.get(key, "")
        if isinstance(val, bool):
            val = "X" if val else ""
        if key == "proc_other" and not val and v.get("proc_other_check"):
            val = "X"
        if key == "delivery_other" and v.get("delivery_other_check"):
            val = v.get("delivery_other") or "X"
        set_text(w, str(val), fixed.get(key))
    for key, rect in fmap.OVERLAYS.items():
        text = str(v.get(key) or "")
        r = pymupdf.Rect(rect)
        add_text_field(page, key, r, text, (fixed.get(key) or fit_size(text, r, max_fs=10)) if text else 0)


def safe_filename(s: str) -> str:
    """A name Windows accepts for a file: 'Roe v. Poe: 9/14' -> 'Roe v. Poe- 9-14'. At most 150 characters."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:150] or "Minute Agreement"


_NOT_A_SPLIT = r"(?!(?:Inc|LLC|L\.L\.C|Corp|P\.C|LLP|Ltd|Jr|Sr)\b)"  # ", Inc." does not start another party


def short_caption(name: str, limit: int = 60) -> str:
    """'A, B v. C Inc., D' -> 'A v. C Inc.' (used in file names)."""
    sides = re.split(r"\s+v\.?\s+", " ".join(name.split()), maxsplit=1)
    firsts = [re.split(r",\s+" + _NOT_A_SPLIT + r"|\s+and\s+", side)[0].strip(" ,") for side in sides]
    short = " v. ".join(firsts)
    return short if len(short) <= limit else short[:limit].rsplit(" ", 1)[0]


def output_name(case: CaseInfo, atty: Attorney | None, s: Settings, dated: bool = False,
                pattern: str | None = None, fallback: str = "Minute Agreement", **extra: str) -> str:
    """The file name (with .pdf) for a PDF made for this case, from a pattern with {case} (short caption),
    {index}, {attorney}, {date} (the first date of the minutes) and {today}. A pattern that can't be
    filled in gives '<fallback> - <index>'.
    dated: add the date of the minutes, to tell apart the forms for several days of one case; a form of several
    days (one for the whole case, see batch.form_groups) gets its first and last day ('9-28-2026 to 10-2-2026'),
    for {date} too.
    pattern: another file name pattern (MOFR, invoice) than the agreement's; extra: more placeholders."""
    from .extract_regex import find_dates
    pattern = s.filename_pattern if pattern is None else pattern
    found = list(dict.fromkeys(d for _, _, d in find_dates(case.get("dates"))))
    # the first day read as a date: a typed 'September 28, 2026' is '9-28-2026', not 'September 28'
    day = found[0].replace("/", "-") if found else case.get("dates").split(",")[0].strip().replace("/", "-")
    if dated and len(found) > 1:
        first, last = min(found, key=_date_key), max(found, key=_date_key)
        day = f"{first.replace('/', '-')} to {last.replace('/', '-')}"
    today = date.today()
    caption = short_caption(case.get("case_name")) or "Case"
    if dated and day and "{case}" in pattern and "{date}" not in pattern:
        caption, dated = f"{caption} ({day})", False
    try:
        name = pattern.format(
            case=caption, today=f"{today.month}-{today.day}-{today.year}",
            index=case.get("index_no").replace("/", "-") or "no index",
            attorney=(atty.name or atty.firm) if atty else "",
            date=day, **extra,
        )
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):  # a bad custom pattern
        name = f"{fallback} - {case.get('index_no').replace('/', '-')}"
    if dated and day and "{date}" not in pattern:
        name += f" - {day}"
    name = re.sub(r"(\s-\s*)+$", "", re.sub(r"\s-\s+-\s", " - ", name)).strip()
    return (safe_filename(name) if name.strip(" .-") else fallback) + ".pdf"


def _date_key(d: str) -> tuple:
    """'6/2/2026' -> (2026, 6, 2), to sort M/D/YYYY dates."""
    m, day, y = (int(x) for x in d.split("/"))
    return y, m, day


MARK = "YinIt"  # PDF "creator" of every file this app makes: such files are skipped as inputs
OLD_MARKS = ("DjinnIt",)  # the mark before the app was renamed (2.0)


def mark(doc: pymupdf.Document, kind: str) -> None:
    """Labels a PDF as made by this app: its creator becomes MARK and the kind ("YinIt agreement",
    "YinIt invoice", "YinIt math")."""
    meta = dict(doc.metadata or {})
    meta["creator"] = f"{MARK} {kind}"
    doc.set_metadata(meta)


def save_output(doc: pymupdf.Document, kind: str, path: Path, flatten: bool = False) -> Path:
    """Saves a PDF this app made: labels it (see mark), optionally flattens the fields, never overwrites
    (adds " (2)" etc.), closes it and returns where it went. When the save fails, the error is raised and the file
    half written is deleted; when even that fails, the error says where it was left (`left_behind`)."""
    mark(doc, kind)
    if flatten:
        doc.bake()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = unique_path(path)
    try:
        doc.save(out, garbage=3, deflate=True)
    except Exception as e:
        # the disk full, or the file taken meanwhile: a file half written is no PDF, and an invoice's number
        # would stay with it (invoice.make_invoice gives the number back only when nothing is left on disk)
        try:
            out.unlink(missing_ok=True)
        except OSError:
            e.left_behind = out  # (held by an antivirus scan, say: make_invoice tries once more, then keeps the number)
        raise
    doc.close()
    return out


def is_generated(path: Path) -> bool:
    """True for a PDF that this app made (see mark), also under its old name (OLD_MARKS)."""
    if path.suffix.lower() != ".pdf":
        return False
    try:
        with pymupdf.open(path) as doc:
            return (doc.metadata or {}).get("creator", "").startswith((MARK, *OLD_MARKS))
    except Exception:
        return False



def unique_path(p: Path) -> Path:
    """`p`, or 'name (2).pdf', 'name (3).pdf'... when it already exists, so nothing is overwritten."""
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p


def fill(case: CaseInfo, atty: Attorney | None, s: Settings, out_dir: Path, dated: bool = False) -> Path:
    """Fills the agreement form chosen in Settings for one attorney (None = blank attorney lines), saves
    it in out_dir and returns its path. A blank agreement date on `case` is set to today when Settings
    say so: as a default, not as typed by the user (agreement_case shares the job's fields, and a value marked
    as the user's would be kept through every re-merge of the job)."""
    if s.agreement_today and not case.get("agreement_date"):
        case.set("agreement_date", us_date(), SRC_DEFAULT)
    v = build_values(case, atty, s)
    v["case_name_raw"] = " ".join(case.get("case_name").split())
    choice = s.form_choice if s.form_choice in FORMS else DEFAULT_FORM
    doc = pymupdf.open(form_path(choice))
    if choice == "original":
        _fill_mapped(doc, v, original_map, case_fs=9)
    elif choice == "ucs":
        _fill_mapped(doc, v, ucs_map, case_fs=10)
        if not s.include_instructions and doc.page_count > ucs_map.INSTRUCTION_PAGES:
            doc.delete_pages(doc.page_count - ucs_map.INSTRUCTION_PAGES, doc.page_count - 1)
    else:
        _fill_clean(doc, v)
    if s.signature():
        if choice == "clean":
            line = next(w.rect for w in doc[0].widgets() if w.field_name == "sig_reporter")
        else:
            line = pymupdf.Rect((original_map if choice == "original" else ucs_map).OVERLAYS["sig_reporter"])
            page = doc[0]
            for xref in [w.xref for w in page.widgets() if w.field_name == "sig_reporter"]:
                page.delete_widget(page.load_widget(xref))  # no blank field over the signature picture
        signature.place(doc[0], line, s.signature())
    return save_output(doc, "agreement", out_dir / output_name(case, atty, s, dated), s.flatten)


def agreement_pages(ordered: dict[str, int] | None, atty: Attorney | None) -> int | None:
    """The Est. number of pages of one attorney's minute agreement: the pages it ordered, whoever wrote them
    (ordered: by Attorney.key(), see batch.Job.ordered_pages), or those anyone ordered ("") for the blank
    attorney block or an attorney not among them (a placeholder ticked). None without `ordered` (no transcript
    and no pages typed, or the Pages field typed as 0: the form shows the Est. number of pages field as it
    is)."""
    if not ordered:
        return None
    k = atty.key() if atty is not None else ""
    return ordered[k] if k in ordered else ordered.get("")


def agreement_orderers(case: CaseInfo, ordered: dict[str, int] | None) -> list[Attorney | None]:
    """Who gets a minute agreement for a day (CaseInfo.orderers: [None], a blank attorney block, when nobody is
    ticked): each ticked entry once (two rows of one firm: one agreement), but not one that ordered no pages
    of it (a firm ticked again after Excerpts... gave it no run: agreement_pages 0), as batch.case_forms does
    for the days of a case together."""
    out: list[Attorney | None] = []
    seen: set[str] = set()
    for atty in case.orderers():
        if atty is not None:
            k = atty.key()
            if k in seen or agreement_pages(ordered, atty) == 0:
                continue
            seen.add(k)
        out.append(atty)
    return out


def agreement_case(case: CaseInfo, atty: Attorney | None, ordered: dict[str, int] | None) -> CaseInfo:
    """The case as one attorney's minute agreement shows it: its Est. number of pages that attorney's
    (agreement_pages); the case itself when there is no such count. A shallow copy: the other fields are the
    case's own FieldState objects, so an agreement date fill sets is set on the case too."""
    n = agreement_pages(ordered, atty)
    if n is None:
        return case
    out = copy.copy(case)
    out.fields = dict(case.fields)
    out.fields["est_pages"] = dataclasses.replace(case.fields["est_pages"], value=str(n))
    return out


def fill_all(case: CaseInfo, s: Settings, out_dir: Path, dated: bool = False,
             ordered: dict[str, int] | None = None) -> list[Path]:
    """One PDF per checked attorney (or a single form with a blank attorney block), each with the pages that
    attorney ordered when `ordered` says (agreement_case). Used by the command line (main.py); unlike
    deliver.generate it doesn't pick the attorneys with agreement_orderers, so every ticked row gets one."""
    return [fill(agreement_case(case, a, ordered), a, s, out_dir, dated) for a in case.orderers()]
