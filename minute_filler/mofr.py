"""Fills the UCS Minute Order Form/Receipt (MOFR) - the reporter's parts only (see forms/mofr_map.py).

One MOFR per job, or one for the days of a case together (batch.case_forms): unlike the minute agreement it is
not addressed to an attorney. Its page count is the pages anyone ordered (deliver.generate).
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .fill import date_ranges, form_text, forms_dir, output_name, save_output, set_check, set_text
from .forms import mofr_map
from .models import CaseInfo, PROC_TYPES
from .rates import speed_key
from .settings import Settings

MOFR_FILE = "mofr.pdf"
# Proceedings with a box of their own on the MOFR; the others are listed under "Other Proceeding"
MOFR_PROCS = ("Sentence", "Plea")


def mofr_path() -> Path:
    """The blank MOFR that comes with the app (in the forms folder)."""
    return forms_dir() / MOFR_FILE


def build_values(case: CaseInfo, s: Settings, pages: str = "") -> dict[str, str | bool]:
    """The MOFR's values: field key (see mofr_map) -> text or checkmark. pages: the page count to write, when
    known (the pages anyone ordered, see fill_mofr); "" = the case's Est. number of pages. The dates are listed
    as fill.date_ranges says (days in a row as a range: the line gives out at six dates written out, five from
    October on, and has no second line to go on to)."""
    g = case.get
    civil = s.mofr_division != "criminal"
    title = " ".join(g("case_name").split())
    if not civil:  # "PEOPLE V" is printed on the form already
        title = re.sub(r"(?i)^(the\s+)?people(\s+of\s+the\s+state\s+of\s+new\s+york)?(,?\s+etc\.?,?)?\s+v[s.]*\s+",
                       "", title)
    others = [p for p in PROC_TYPES if p in case.proc_types and p not in MOFR_PROCS]
    if g("proc_other").strip():
        others.append(g("proc_other").strip())
    key = speed_key(g("delivery"))
    v: dict[str, str | bool] = {
        "county": g("county"), "title": title, "index_no": g("index_no"), "part": g("part"),
        "judge": g("judge"), "dates": date_ranges(g("dates")), "copies": g("copies"),
        "pages": pages or g("est_pages"),
        "rep_name": s.profile.name, "rep_location": s.profile.address1,
        "proc_other": ", ".join(others), "proc_other_check": bool(others),
        "proc_sentence": "Sentence" in case.proc_types, "proc_plea": "Plea" in case.proc_types,
        "civil": civil, "criminal": not civil,
        "daily": key == "daily", "expedited": key == "expedited", "regular": key == "regular",
    }
    return v


def fill_mofr(case: CaseInfo, s: Settings, out_dir: Path, dated: bool = False, pages: str = "") -> Path:
    """Writes one filled MOFR into out_dir and returns its path. pages: the pages anyone ordered, whoever
    wrote them (deliver.generate; for several days, batch.case_forms), when known; "" = the case's Est. number
    of pages. dated: add the date of the minutes to the file name (first and last day for several days, see
    fill.output_name)."""
    v = build_values(case, s, pages)
    doc = pymupdf.open(mofr_path())
    page = doc[0]
    civil = bool(v["civil"])
    if civil:  # cover the printed "PEOPLE V": a civil caption is written out in full
        page.draw_rect(pymupdf.Rect(mofr_map.PEOPLE_V), color=None, fill=(1, 1, 1), overlay=True)
    for w in page.widgets():
        name = w.field_name
        if name in mofr_map.TEXT:
            if civil and mofr_map.TEXT[name] == "title":
                w.rect = pymupdf.Rect(mofr_map.PEOPLE_V[0], w.rect.y0, w.rect.x1, w.rect.y1)
            set_text(w, form_text(str(v.get(mofr_map.TEXT[name], ""))))
        elif name in mofr_map.CHECKS:
            set_check(w, bool(v.get(mofr_map.CHECKS[name])))
    name = output_name(case, None, s, dated, pattern=s.mofr_filename_pattern, fallback="MOFR")
    return save_output(doc, "MOFR", out_dir / name, s.flatten)
