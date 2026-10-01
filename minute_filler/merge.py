"""Combines extractions from several inputs/extractors into one CaseInfo."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta

from .dates import next_weekday, us_date
from .extract_regex import dedupe_attorneys
from .models import (CaseInfo, Candidate, Extraction, FIELD_KEYS, FieldState, SRC_DEFAULT, SRC_DERIVED,
                     SRC_USER, SRC_AI)
from .rates import speed_key
from .settings import Settings

CHECK_THRESHOLD = 0.6


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", v.lower())


def pool(extractions: list[Extraction]) -> dict[str, list[Candidate]]:
    """All candidates per field; agreement between sources boosts confidence."""
    out: dict[str, list[Candidate]] = {}
    for ex in extractions:
        for key, cands in ex.fields.items():
            lst = out.setdefault(key, [])
            for c in cands:
                twin = next((o for o in lst if _norm(o.value) == _norm(c.value)), None)
                if twin is None:
                    lst.append(Candidate(c.value, c.source, c.confidence, c.note))
                elif twin.source != c.source:
                    twin.confidence = min(0.99, max(twin.confidence, c.confidence) + 0.1)
                    if twin.source == SRC_AI:
                        twin.source = c.source
                else:
                    twin.confidence = max(twin.confidence, c.confidence)
    for lst in out.values():
        # highest confidence first; ties go to the longer (more complete) value
        lst.sort(key=lambda c: (-c.confidence, -len(c.value)))
    return out


def merge(extractions: list[Extraction], s: Settings, previous: CaseInfo | None = None) -> CaseInfo:
    case = CaseInfo()
    cands = pool(extractions)

    for key in FIELD_KEYS:
        lst = cands.get(key, [])
        if not lst:
            continue
        best = lst[0]
        alts = [c.value for c in lst if c.confidence >= 0.3][:8]
        if best.value not in alts:
            alts.insert(0, best.value)
        case.fields[key] = FieldState(best.value, best.source, best.confidence, alts)

    # Proceeding types
    ptypes: dict[str, float] = {}
    for ex in extractions:
        for t, c in ex.proc_types.items():
            ptypes[t] = min(0.99, max(ptypes.get(t, 0), c) + (0.1 if t in ptypes else 0))
    case.proc_types = {t for t, c in ptypes.items() if c >= CHECK_THRESHOLD}

    # Attorneys: regex first (more exact), AI fills gaps / adds missing people. Copies, because merging
    # fills in and ticks entries: a document's own findings must stay as they were read, or a tick or an
    # e-mail address taken from another document would stay behind after that document is removed.
    atts = [replace(a) for ex in extractions for a in ex.attorneys]
    atts.sort(key=lambda a: a.source == SRC_AI)
    case.attorneys = dedupe_attorneys(atts, s.profile)
    real = [a for a in case.attorneys if not a.is_placeholder()]
    for a in case.attorneys:
        if a.is_placeholder():
            a.checked = False
    if len(real) == 1:
        real[0].checked = True

    for ex in extractions:
        case.notes += ex.notes

    apply_defaults(case, s, cands)

    # Keep anything the user typed in an earlier pass (e.g. AI results arriving later).
    if previous is not None:
        for key in FIELD_KEYS:
            old = previous.fields[key]
            if old.source == SRC_USER:
                case.fields[key] = old
    return case


def apply_defaults(case: CaseInfo, s: Settings, cands: dict[str, list[Candidate]] | None = None) -> None:
    cands = cands or {}
    f = case.fields

    def default(key: str, value: str, source: str = SRC_DEFAULT, conf: float = 0.7) -> None:
        if value and not f[key].value:
            f[key] = FieldState(value, source, conf, [value])

    default("court", s.default_court)
    default("county", s.default_county)
    default("delivery", s.delivery_name(s.default_delivery))
    # use the rate sheet's own spelling ("Expedited" -> "Expedite")
    if f["delivery"].value and f["delivery"].source != SRC_USER:
        f["delivery"].value = s.delivery_name(f["delivery"].value)
    default("copies", s.default_copies)
    default("rate", s.rate_for(f["delivery"].value))

    # Pages from an invoice total: total / rate
    if not f["est_pages"].value:
        for key, dl in (("invoice_regular", "Regular"), ("invoice_expedited", "Expedited")):
            if key in cands and speed_key(dl) == speed_key(f["delivery"].value):
                try:
                    pages = round(float(cands[key][0].value) / float(s.rate_for(dl)))
                except (ValueError, ZeroDivisionError):
                    continue
                if pages > 0:
                    f["est_pages"] = FieldState(str(pages), SRC_DERIVED, 0.4,
                                                [str(pages)])
                break

    if s.fill_delivery_date:
        refresh_delivery_date(case, s)
    if s.agreement_today:
        default("agreement_date", us_date(), SRC_DEFAULT, 0.9)


def refresh_delivery_date(case: CaseInfo, s: Settings) -> None:
    """Estimated delivery = today + turnaround for the chosen delivery type (unless the user typed one)."""
    f = case.fields["delivery_date"]
    if f.source == SRC_USER and f.value:
        return
    days = s.days_for(case.get("delivery"))
    if days is None:
        return
    d = us_date(next_weekday(date.today() + timedelta(days=days)))
    case.fields["delivery_date"] = FieldState(d, SRC_DERIVED, 0.8, [d])


def refresh_rate(case: CaseInfo, s: Settings) -> None:
    f = case.fields["rate"]
    if f.source == SRC_USER and f.value:
        return
    r = s.rate_for(case.get("delivery"))
    if r:
        case.fields["rate"] = FieldState(r, SRC_DEFAULT, 0.7, [r])
