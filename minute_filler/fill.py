"""Writes a CaseInfo + Attorney + reporter profile into one of the minute agreement forms.

Forms (Settings.form_choice):
  "ucs"      - the court's fillable UCS form (default)
  "clean"    - the app's re-typeset form with named fields and extra lines
  "original" - the 1999 scan with fields added on top

Lines the UCS form or the original has no field for (forms/*_map.py OVERLAYS: the signature lines, a second
line for the dates and the line saying which pages are at which speed, and on the original also the fax lines,
the date of agreement and the case name's second and third lines) get a text field added, so every value can
still be changed in a PDF viewer. A signature picture takes the place of the reporter's signature field there.

The dates are written with three or more days in a row as a range ("9/28/2026–10/2/2026", date_ranges); on the
UCS form and the original, dates that still don't fit their line go on to the second one, split between two dates
(_spill_dates).

Each attorney's agreement shows as its Estimated Number of Pages the pages that attorney ordered, whoever wrote
them (agreement_case, from batch.Job.ordered_pages); with no count (no transcript and no pages typed), the
field as it is. A firm that ordered no pages of the day gets no agreement (agreement_orderers). A firm whose
speed is set gets that speed, its rate and its delivery date; one that ordered at two (Daily on one day, Regular
on another) gets one agreement with both ticked, "see below" as the rate and a line saying which pages are at
which ('Daily $6.50 a page: 6/3/2026; Regular $4.30 a page: 6/4/2026': speeds_case, speeds_note; the
re-typeset form has no such line, so that goes in its rate field).

The MOFR lists its dates as date_ranges does too, and its speeds as speeds_case gives them (case_speeds). The
toolkit every output writes its PDF with (fitting text, fields, file names, saving) is in pdfout.py; its names
are still importable from here. Agreements is the output deliver.generate and make_forms make the agreements
through (courthouses.OutputSpec.maker).
"""
from __future__ import annotations

import copy
import dataclasses
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf

from . import signature
from .dates import next_weekday, us_date
from .forms import original_map, ucs_map
from .models import CaseInfo, Attorney, PROC_TYPES, SRC_DEFAULT, to_int
from .pdfout import (MAX_FS, MIN_FS, add_text_field, fit_size, form_text, output_name, save_output, set_check,
                     set_text, text_width, wrap_fit)
# (re-exported: these lived here before pdfout.py, and older code and tests import them from fill)
from .pdfout import (FIELD_FONT, LOCK_BUTTON, MARK, OLD_MARKS, has_fields, is_generated, lock_pdf,  # noqa: F401
                     mark, safe_filename, short_caption, unique_path, wrap)
from .rates import speed_key
from .settings import Settings

if TYPE_CHECKING:
    from .deliver import CaseForm, Making

DATES_FLOOR = 7.0  # the dates line of the UCS and original forms shrinks to this before it spills onto a second line
RANGE_DAYS = 3     # days in a row that become a range on the forms ("9/28/2026–9/30/2026"); two stay listed
FORM_SPEEDS = ("regular", "expedited", "daily")  # the speeds with a box of their own on the form
# The rate line of an agreement covering pages ordered at two speeds: a line further down says which is which
# (speeds_note, beside No. of Copies Ordered, under the speed boxes, on the UCS form and the original: OVERLAYS
# "speeds_note")
SEE_BELOW = "see below"


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
    # the speed chosen under "Agreement form", or the speeds of an agreement covering several (form_speeds)
    speeds = list(dict.fromkeys(sp for sp, _, _ in case.form_speeds)) or [g("delivery").strip()]
    keys = [speed_key(sp) for sp in speeds]
    for d in FORM_SPEEDS:
        v[f"delivery_{d}"] = d in keys
    other = [sp for sp, k in zip(speeds, keys) if sp and k not in FORM_SPEEDS]  # e.g. "Immediate"
    if other:
        v["delivery_other_check"] = True
        v["delivery_other"] = ", ".join(sp for sp in other if speed_key(sp) != "other")
    if case.form_speeds:  # which pages are at which speed, beside No. of Copies; the rate line says "see below"
        v["rate"] = SEE_BELOW
        v["speeds_note"] = speeds_note(case.form_speeds)

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
    if v.get("speeds_note"):  # (no line for it under the rate: the speeds go in the rate's own field)
        v["rate"] = v["speeds_note"]
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
    if v.get("rate") and v["rate"] != SEE_BELOW:
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


def speeds_note(form_speeds: list) -> str:
    """Which pages of a form are at which speed (CaseInfo.form_speeds): 'Daily $6.50 a page: 6/3/2026; Regular
    $4.30 a page: 6/4/2026, 6/5/2026' (where the pages of a day differ: '6/3/2026 pp. 1–60')."""
    by: dict[str, tuple[str, list[str]]] = {}
    for sp, rate, where in form_speeds:
        r, places = by.setdefault(sp, (rate, []))
        if where and where not in places:
            places.append(where)
    return "; ".join(f"{sp}" + (f" ${r} a page" if r else "") + (f": {', '.join(places)}" if places else "")
                     for sp, (r, places) in by.items())


def speeds_case(case: CaseInfo, speeds: list[tuple[str, str]], s: Settings) -> CaseInfo:
    """The case as a form of pages ordered at these speeds shows it ((speed, where) each: the days or pages at
    it, see batch.Job.ordered_speeds). One speed that isn't the case's own: that speed, its rate on the sheet and
    its delivery date (unless one was typed). Several: CaseInfo.form_speeds (each ticked, "see below" on the
    rate line, speeds_note saying which pages are at which, see build_values) and the latest delivery date of
    them (unless one was typed). The case itself when there is nothing to change. A shallow copy, as
    agreement_case's."""
    from .merge import refresh_delivery_date, refresh_rate
    from .models import SRC_DERIVED, SRC_USER, FieldState
    names = list(dict.fromkeys(sp for sp, _ in speeds if sp))
    kinds = list(dict.fromkeys(speed_key(sp) for sp in names))
    if not names or kinds == [speed_key(case.get("delivery"))]:
        return case
    out = copy.copy(case)
    out.fields = dict(case.fields)
    if len(kinds) == 1:
        out.fields["delivery"] = FieldState(names[0], SRC_USER, 1.0, [names[0]])
        out.fields["rate"] = FieldState()  # (a rate typed was the case's speed's)
        refresh_rate(out, s)
        refresh_delivery_date(out, s)
        return out
    out.form_speeds = [(sp, s.rate_for(sp), where) for sp, where in speeds if sp]
    days = [s.days_for(sp) for sp in names]
    typed = out.fields["delivery_date"]
    if all(d is not None for d in days) and not (typed.source == SRC_USER and typed.value):
        d = us_date(next_weekday(date.today() + timedelta(days=max(days))))
        out.fields["delivery_date"] = FieldState(d, SRC_DERIVED, 0.8, [d])
    return out


def case_speeds(case: CaseInfo, ordered: dict[str, int] | None, speeds: dict[str, list] | None) -> list:
    """Every speed the firms that ordered pages ordered them at ((speed, where) each, see batch.Job.ordered_speeds),
    with the case's own speed for a firm whose speed isn't set, for a form of the whole case (the MOFR, see
    speeds_case); [] when no firm's is set (the case's speed alone)."""
    speeds = speeds or {}
    firms = [k for k, n in (ordered or {}).items() if k and n > 0] or list(speeds)
    if not any(speeds.get(k) for k in firms):
        return []
    out = [x for k in firms for x in speeds.get(k, [])]
    if any(not speeds.get(k) for k in firms):
        out.append((case.get("delivery"), ""))
    return out


def agreement_case(case: CaseInfo, atty: Attorney | None, ordered: dict[str, int] | None,
                   speeds: dict[str, list] | None = None, s: Settings | None = None) -> CaseInfo:
    """The case as one attorney's minute agreement shows it: its Est. number of pages that attorney's
    (agreement_pages), and the speed(s) it ordered at when they are set (speeds: by Attorney.key(), see
    batch.Job.ordered_speeds; speeds_case, with the settings `s`); the case itself when there is no such
    count nor speed. A shallow copy: the other fields are the case's own FieldState objects, so an agreement
    date fill sets is set on the case too."""
    n = agreement_pages(ordered, atty)
    out = case
    if n is not None:
        out = copy.copy(case)
        out.fields = dict(case.fields)
        out.fields["est_pages"] = dataclasses.replace(case.fields["est_pages"], value=str(n))
    k = atty.key() if atty is not None else ""
    if speeds and s is not None and speeds.get(k):
        out = speeds_case(out, speeds[k], s)
    return out


def fill_all(case: CaseInfo, s: Settings, out_dir: Path, dated: bool = False,
             ordered: dict[str, int] | None = None) -> list[Path]:
    """One PDF per checked attorney (or a single form with a blank attorney block), each with the pages that
    attorney ordered when `ordered` says (agreement_case). Used by --selftest (main.selftest); unlike
    deliver.generate it doesn't pick the attorneys with agreement_orderers, so every ticked row gets one, and
    each names the case's own speed."""
    return [fill(agreement_case(case, a, ordered), a, s, out_dir, dated) for a in case.orderers()]


class Agreements:
    """The minute agreement as an output (courthouses.OutputSpec.maker): deliver.generate makes the job's
    (make), deliver.make_forms one for several days of a case (make_form)."""

    @staticmethod
    def make(m: Making) -> None:
        """One agreement per attorney that ordered pages of the day (agreement_orderers), each showing the pages
        it ordered and the speed(s) it ordered them at, when set (agreement_case), recorded with them."""
        for atty in agreement_orderers(m.case, m.ordered):
            # (its Est. number of pages: the pages it ordered; its speed: the one it committed to)
            on = agreement_case(m.case, atty, m.ordered, m.speeds, m.s)
            m.record("agreement", fill(on, atty, m.s, m.folder("agreement"), m.dated), atty,
                     count=to_int(on.get("est_pages")) if on is not m.case else m.pages)

    @staticmethod
    def make_form(form: CaseForm, s: Settings, folder: Path) -> Path:
        """The agreement of several days (batch.case_forms) for form.atty (None: the blank attorney block)."""
        return fill(form.case, form.atty, s, folder, True)
