"""Writes a CaseInfo + Attorney + reporter profile into one of the PDF forms.

Forms (Settings.form_choice):
  "ucs"      - the court's fillable UCS form (default)
  "clean"    - the app's re-typeset form with named fields and extra lines
  "original" - the 1999 scan with fields added on top
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pymupdf

from .forms import original_map, ucs_map
from .models import CaseInfo, Attorney, PROC_TYPES
from .rates import speed_key
from .settings import Settings

FIELD_FONT = "helv"
MAX_FS, MIN_FS = 10.0, 5.5


def forms_dir() -> Path:
    # Works from source and from a PyInstaller bundle (forms are shipped as
    # data under minute_filler/forms).
    return Path(__file__).resolve().with_name("forms")


FORMS = {
    "ucs": ("minute_agreement_ucs.pdf", "UCS form (fillable)"),
    "clean": ("minute_agreement_clean.pdf", "Re-typeset form"),
    "original": ("minute_agreement_original.pdf", "Original 1999 scan"),
}
DEFAULT_FORM = "ucs"


def form_path(choice: str) -> Path:
    return forms_dir() / FORMS.get(choice, FORMS[DEFAULT_FORM])[0]


def text_width(s: str, fs: float) -> float:
    return pymupdf.get_text_length(s, fontname=FIELD_FONT, fontsize=fs)


def fit_size(s: str, rect: pymupdf.Rect, max_fs: float = MAX_FS) -> float:
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
    """Largest font size at which `s` wraps into `lines` lines of `width`."""
    fs = max_fs
    while fs > MIN_FS:
        out = wrap(s, width, lines, fs)
        if all(text_width(l, fs) <= width - 4 for l in out):
            return out, fs
        fs -= 0.5
    return wrap(s, width, lines, MIN_FS), MIN_FS


def split_address(addr: str) -> list[str]:
    lines = [l.strip(" ,") for l in re.split(r"[\r\n]+", addr or "") if l.strip(" ,")]
    if len(lines) > 2:
        lines = [lines[0], ", ".join(lines[1:])]
    return lines + [""] * (2 - len(lines))


def build_values(case: CaseInfo, atty: Attorney | None, s: Settings) -> dict[str, str | bool]:
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
    delivery = g("delivery").strip()
    key = speed_key(delivery)
    for d in ("regular", "expedited", "daily"):
        v[f"delivery_{d}"] = key == d
    if delivery and key not in ("regular", "expedited", "daily"):  # e.g. "Immediate"
        v["delivery_other_check"] = True
        v["delivery_other"] = "" if key == "other" else delivery

    p = s.profile
    v.update({
        "rep_name": p.name, "rep_address_1": p.address1, "rep_address_2": p.address2,
        "rep_phone": p.phone, "rep_fax": p.fax, "rep_email": p.email,
        "sig_reporter": p.name if s.sign_reporter else "",
    })
    if atty is not None:
        a1, a2 = split_address(atty.address)
        v.update({
            "atty_name": atty.name, "atty_firm": atty.firm, "atty_address_1": a1, "atty_address_2": a2,
            "atty_phone": atty.phone, "atty_fax": atty.fax, "atty_email": atty.email,
        })
    v["sig_attorney"] = "per email" if s.per_email else ""
    return v


def _set_text(w: pymupdf.Widget, text: str, fs: float | None = None) -> None:
    w.field_value = text
    w.text_font = "Helv"
    w.text_fontsize = (fs or fit_size(text, w.rect)) if text else 0
    w.text_color = (0, 0, 0)
    w.update()


def _set_check(w: pymupdf.Widget, on: bool) -> None:
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
    page = doc[0]
    widgets = {w.field_name: w for w in page.widgets()}
    fixed: dict[str, float] = {}
    _spread(v, v.get("case_name_raw", ""), ["case_name_1", "case_name_2", "case_name_3"],
            widgets["case_name_1"].rect.width, fixed)
    _spread(v, str(v.get("dates", "")), ["dates", "dates_2"], widgets["dates"].rect.width, fixed)
    for name, w in widgets.items():
        val = v.get(name, "")
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
            _set_check(w, bool(val))
        else:
            _set_text(w, "" if isinstance(val, bool) else str(val), fixed.get(name))


def _fill_mapped(doc: pymupdf.Document, v: dict, fmap, case_fs: float) -> None:
    """Fills a form whose field names are mapped to our keys (forms/*_map.py): blank text
    lines get "X" for checkmarks, and missing lines (signatures etc.) are printed as overlays."""
    page = doc[0]
    widgets = {fmap.WIDGETS.get(w.field_name.removeprefix("Text-")): w for w in page.widgets()}
    fixed: dict[str, float] = {}
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
        _set_text(w, str(val), fixed.get(key))
    for key, rect in fmap.OVERLAYS.items():
        text = str(v.get(key) or "")
        if not text:
            continue
        r = pymupdf.Rect(rect)
        fs = fixed.get(key) or fit_size(text, r, max_fs=10)
        page.insert_text((r.x0 + 2, r.y1 - 2.5), text, fontname=FIELD_FONT, fontsize=fs, color=(0, 0, 0))


def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:150] or "Minute Agreement"


_NOT_A_SPLIT = r"(?!(?:Inc|LLC|L\.L\.C|Corp|P\.C|LLP|Ltd|Jr|Sr)\b)"


def short_caption(name: str, limit: int = 60) -> str:
    """'A, B v. C Inc., D' -> 'A v. C Inc.' (used in file names)."""
    sides = re.split(r"\s+v\.?\s+", " ".join(name.split()), maxsplit=1)
    firsts = [re.split(r",\s+" + _NOT_A_SPLIT + r"|\s+and\s+", side)[0].strip(" ,") for side in sides]
    short = " v. ".join(firsts)
    return short if len(short) <= limit else short[:limit].rsplit(" ", 1)[0]


def output_name(case: CaseInfo, atty: Attorney | None, s: Settings) -> str:
    try:
        name = s.filename_pattern.format(
            case=short_caption(case.get("case_name")) or "Case",
            index=case.get("index_no").replace("/", "-") or "no index",
            attorney=(atty.name or atty.firm) if atty else "",
            date=case.get("dates").split(",")[0].replace("/", "-"),
        )
    except (KeyError, IndexError, ValueError):  # a bad custom pattern
        name = f"Minute Agreement - {case.get('index_no').replace('/', '-')}"
    name = re.sub(r"(\s-\s*)+$", "", re.sub(r"\s-\s+-\s", " - ", name)).strip()
    return safe_filename(name) + ".pdf"


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p


def fill(case: CaseInfo, atty: Attorney | None, s: Settings, out_dir: Path) -> Path:
    if s.agreement_today and not case.get("agreement_date"):
        case.set("agreement_date", date.today().strftime("%-m/%-d/%Y") if sys.platform != "win32"
                 else date.today().strftime("%#m/%#d/%Y"))
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
    if s.flatten:
        doc.bake()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = unique_path(out_dir / output_name(case, atty, s))
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


def fill_all(case: CaseInfo, s: Settings, out_dir: Path) -> list[Path]:
    """One PDF per checked attorney (or a single form with a blank attorney block)."""
    chosen = [a for a in case.attorneys if a.checked]
    return [fill(case, a, s, out_dir) for a in chosen] if chosen else [fill(case, None, s, out_dir)]
