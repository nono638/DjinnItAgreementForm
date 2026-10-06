"""Writes a CaseInfo + Attorney + reporter profile into one of the minute agreement forms.

Forms (Settings.form_choice):
  "ucs"      - the court's fillable UCS form (default)
  "clean"    - the app's re-typeset form with named fields and extra lines
  "original" - the 1999 scan with fields added on top

Lines the UCS form or the original has no field for (forms/*_map.py OVERLAYS: the signature lines, and on the
original also the fax lines, the date of agreement and the case name's second and third lines) get a text
field added, so every value can still be changed in a PDF viewer. A signature picture takes the place of the
reporter's signature field there.

Also helpers that the MOFR, invoices and run sheets share: writing form fields (set_text) and adding new
ones (add_text_field), file names (output_name, safe_filename, unique_path) and saving (save_output, which
labels every PDF the app makes so it is never read back as an input, and flattens it when Settings say so).
lock_pdf saves a copy with the fields flattened (File → Lock finished PDFs).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

import pymupdf

from . import signature
from .dates import us_date
from .forms import original_map, ucs_map
from .models import CaseInfo, Attorney, PROC_TYPES
from .rates import speed_key
from .settings import Settings

FIELD_FONT = "helv"
MAX_FS, MIN_FS = 10.0, 5.5
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


def text_width(s: str, fs: float) -> float:
    """Width of `s` in points, in the fields' font at size `fs`."""
    return pymupdf.get_text_length(s, fontname=FIELD_FONT, fontsize=fs)


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


def split_address(addr: str) -> list[str]:
    """An address as the form's two lines: the first line, then the rest joined with commas."""
    lines =[l.strip(" ,") for l in re.split(r"[\r\n]+", addr or "") if l.strip(" ,")]
    if len(lines) > 2:
        lines = [lines[0], ", ".join(lines[1:])]
    return lines + [""] * (2 - len(lines))


def build_values(case: CaseInfo, atty: Attorney | None, s: Settings) -> dict[str, str | bool]:
    """What goes on the form, by the keys of forms/*_map.py ('judge', 'proc_trial', 'delivery_daily',
    'atty_email'...). Boxes are True/False. With atty None the attorney's lines stay blank."""
    g = case.get
    v: dict[str, str | bool] = {
        "court": g("court"), "county": g("county"), "part": g("part"), "judge": g("judge"),
        "index_no": g("index_no"), "dates": g("dates"),
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
    fs: the font size (0 = automatic); font: "Helv", "TiRo" or "Cour" (fields can't be bold); right: right-aligned."""
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


def _fill_mapped(doc: pymupdf.Document, v: dict, fmap, case_fs: float) -> None:
    """Fills a form whose field names are mapped to our keys (forms/*_map.py). Its boxes are text
    fields, so a ticked one gets an "X"; lines it has no field for (fmap.OVERLAYS: signatures etc.) get a text
    field added, blank ones too, so a value can be typed in later.
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
    dated: add the date of the minutes, to tell apart the forms for several days of one case.
    pattern: another file name pattern (MOFR, invoice) than the agreement's; extra: more placeholders."""
    pattern = s.filename_pattern if pattern is None else pattern
    day = case.get("dates").split(",")[0].strip().replace("/", "-")
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
    (adds " (2)" etc.), closes it and returns where it went."""
    mark(doc, kind)
    if flatten:
        doc.bake()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = unique_path(path)
    doc.save(out, garbage=3, deflate=True)
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
    say so."""
    if s.agreement_today and not case.get("agreement_date"):
        case.set("agreement_date", us_date())
    v = build_values(case, atty, s)
    v["case_name_raw"] = " ".join(case.get("case_name").split())
    choice = s.form_choice if s.form_choice in FORMS else DEFAULT_FORM
    doc = pymupdf.open(form_path(choice))
    if choice == "original":
        _fill_mapped(doc, v, original_map, case_fs=9)
    elif choice == "ucs":
        _fill_mapped(doc, v, ucs_map, case_fs=10)
        if not s.include_instructions and doc.page_count > 1:
            doc.delete_pages(1, doc.page_count - 1)
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


def fill_all(case: CaseInfo, s: Settings, out_dir: Path, dated: bool = False) -> list[Path]:
    """One PDF per checked attorney (or a single form with a blank attorney block)."""
    return [fill(case, a, s, out_dir, dated) for a in case.orderers()]
