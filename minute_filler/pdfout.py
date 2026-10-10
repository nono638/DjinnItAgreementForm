"""The toolkit every PDF the app makes is written with, whatever the output: measuring and fitting text in a
field's font (text_width, fit_size, wrap, wrap_fit), letters Helvetica can't show (form_text), writing fields
(set_text, set_check) and adding new ones (add_text_field), file names (output_name, short_caption,
safe_filename, unique_path) and the names of the cases' own folders (case_folder_name, free_folder), and
saving (save_output, which labels each PDF with `mark` so that is_generated never takes it back as an input,
and flattens it when Settings say so). lock_pdf saves a copy with the fields flattened (File → Lock finished
PDFs).

The agreement forms (fill.py), the MOFR, the invoices, the run sheet and the window use it. It was part of fill.py
until the outputs were separated (MIGRATION.md, Release A); fill.py still re-exports these names, so older imports
keep working.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf

if TYPE_CHECKING:  # (for the hints only: this module imports nothing of the package's at load time)
    from .models import Attorney, CaseInfo
    from .settings import Settings


FIELD_FONT = "helv"
MAX_FS, MIN_FS = 10.0, 5.5

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


# The characters Windows allows in no file or folder name (control characters such as Tab neither): the folder
# name pattern can't have them (Settings.case_folder_pattern), and what documents say has them replaced.
FOLDER_FORBIDDEN = '<>:"/\\|?*'
FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# names Windows keeps for devices: no folder can be called "CON" or "nul.txt"
_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$")
# characters in a case folder's name, at most: a long one, under a long Save to, with long file names (up to 150,
# safe_filename), could pass the 260 of a path that Windows allows when long paths aren't turned on. The cap makes
# that less likely; it can't rule it out (the default "{index}" is 11)
CASE_FOLDER_MAX = 100
# the placeholders a case folder's name can have (case_folder_name; Settings lists them under the pattern)
CASE_FOLDER_PLACES = ("index", "case", "date", "dates", "court", "county", "part", "judge")


def case_folder_name(case: CaseInfo, pattern: str) -> str:
    """The name of a case's own folder (Settings.case_folders) from `pattern` (Settings.case_folder_pattern):
    {index} '712345-2021' (also for '712345/21'), {case} the short caption, {date} the first day '6-3-2026',
    {dates} its days '6-3-2026 to 6-4-2026', {court}, {county}, {part}, {judge}. What the documents say has the
    characters Windows forbids replaced ('Roe v. Poe: 9/14' -> 'Roe v. Poe- 9-14'); a placeholder left blank goes
    with the ' - ', '()' or '[]' around it; one not known stays as typed. Nothing left: the short caption, else
    'No index number'.
    A name Windows keeps for devices ('CON') gets '_' after it; at most CASE_FOLDER_MAX characters, and never
    ending in a dot or a space (Windows drops them)."""
    from .extract_regex import find_dates

    def clean(v: str) -> str:
        """A value fit for a folder name: forbidden characters '-', spaces squeezed, and no dot, dash or space at
        either end (Windows drops a trailing dot or space)."""
        return re.sub(r"\s+", " ", FORBIDDEN_RE.sub("-", v or "")).strip(" .-")

    found = sorted({d for _, _, d in find_dates(case.get("dates"))}, key=_date_key)
    first, last = (found[0].replace("/", "-"), found[-1].replace("/", "-")) if found else ("", "")
    index = (case.get("index_no") or "").strip()
    short = re.fullmatch(r"(\d+)\s*[/-]\s*(\d\d)", index)
    if short and int(short[2]) < 50:  # ("712222/24" is "712222/2024": one case, one folder; anything else as typed)
        index = f"{short[1]}-20{short[2]}"
    values = {"index": index, "case": short_caption(case.get("case_name")), "date": first,
              "dates": first if first == last else f"{first} to {last}",
              **{k: case.get(k) for k in ("court", "county", "part", "judge")}}
    values = {k: clean(v) for k, v in values.items()}
    name = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), pattern)
    name = re.sub(r"\(\s*\)|\[\s*\]", "", name)  # ("{index} ({dates})" with no dates)
    name = re.sub(r"(\s-\s*)+$", "", re.sub(r"^(\s*-\s)+", "", re.sub(r"\s-(\s+-)+\s", " - ", name)))
    name = clean(name) or values["case"] or "No index number"
    device = _RESERVED.match(name)
    if device:  # (right after the device's word: Windows reads "nul.txt_" as "nul" too)
        name = name[:device.end(1)] + "_" + name[device.end(1):]
    return name[:CASE_FOLDER_MAX].rstrip(" .")


def free_folder(folder: Path) -> Path:
    """`folder`, or 'name (2)', 'name (3)'... when it is already there: a new case folder beside the one
    there (not unique_path, which takes 'Roe v. Poe' for 'Roe v' plus a suffix)."""
    if not folder.exists():
        return folder
    for i in range(2, 1000):
        q = folder.with_name(f"{folder.name} ({i})")
        if not q.exists():
            return q
    return folder


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
