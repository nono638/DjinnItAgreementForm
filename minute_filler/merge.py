"""Combines extractions from several inputs/extractors into one CaseInfo.

pool gathers every field's candidates (one value however it is written: same_value), merge picks the best of
each and joins the attorneys, and apply_defaults and the refresh_* functions fill in what follows from the
settings: court, county, the speed the agreement form names, its rate, the copies and the delivery date.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta

from .dates import next_weekday, us_date
from .extract_regex import dedupe_attorneys, find_dates, norm_index
from .models import (CaseInfo, Candidate, Extraction, FIELD_KEYS, FieldState, SRC_DEFAULT, SRC_DERIVED,
                     SRC_USER, SRC_AI)
from .rates import speed_key
from .settings import Settings

CHECK_THRESHOLD = 0.6  # a proceeding type is ticked from this confidence up


def _norm(v: str) -> str:
    """A value for comparing: 'Smith v Jones' and 'SMITH V. JONES' are the same."""
    return re.sub(r"[^a-z0-9]", "", v.lower())


def same_value(key: str, a: str, b: str) -> bool:
    """Two candidates for the field `key` are one value. Dates by the days they name ('1/12/2026' and
    '11/2/2026' are two, '9/14/2026' and 'Sept. 14, 2026' one); index numbers as norm_index writes them
    ('712345-24' and '712345/2024' are one); anything else by its letters and digits (_norm)."""
    if key == "dates":
        da, db = find_dates(a), find_dates(b)
        if da and db:
            return [d for _, _, d in da] == [d for _, _, d in db]
    if key == "index_no":
        na, nb = _index_key(a), _index_key(b)
        if na and nb:
            return na == nb
    return _norm(a) == _norm(b)


def _index_key(v: str) -> str | None:
    """An index number's value as norm_index writes it ('712345-24' -> '712345/2024'); None when it isn't
    a number and a year."""
    m = re.fullmatch(r"\s*(\d{3,7})\s*(?:[-/]|\s+of\s+)\s*(\d{4}|\d{2})\s*[A-Za-z]?\s*", v)
    return norm_index(m.group(1), m.group(2)) if m else None


def pool(extractions: list[Extraction]) -> dict[str, list[Candidate]]:
    """All candidates per field, best first; one value written two ways (same_value) is one candidate, as
    first written. When the rules and the AI agree on a value its confidence goes up, and it is credited to
    the rules."""
    out: dict[str, list[Candidate]] = {}
    for ex in extractions:
        for key, cands in ex.fields.items():
            lst = out.setdefault(key, [])
            for c in cands:
                twin = next((o for o in lst if same_value(key, o.value, c.value)), None)
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
    """One CaseInfo from the extractions of all of a job's documents: for each field the best candidate,
    with up to eight likely values offered as alternatives; the likely proceeding types ticked; the attorneys
    merged, one entry per firm (placeholders unticked, a lone real entry ticked); blanks filled from Settings
    (apply_defaults). Values the user typed into `previous` are kept."""
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

    # Attorneys: regex first (more exact), AI fills gaps / adds missing people; one entry per firm (an e-mail
    # from one of its attorneys, or the firm on another day's title page, joins its row). Copies, because merging
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
        if refresh_speed(case, s):  # a speed chosen before that is no longer offered (Speeds offered)
            refresh_rate(case, s)
    return case


def apply_defaults(case: CaseInfo, s: Settings, cands: dict[str, list[Candidate]] | None = None) -> None:
    """Fills blank fields from Settings: court, county, the rate from the rate sheet, the delivery date (when
    Settings.fill_delivery_date) and today's agreement date (when Settings.agreement_today). The speed and the
    copies are set even when not blank (refresh_speed, refresh_copies): the speed is the user's choice, else
    the Settings rule's, and the parties ticked decide the copies. With the pooled candidates `cands`, a blank
    page count can also come from an invoice's total at the chosen speed."""
    cands = cands or {}
    f = case.fields

    def default(key: str, value: str, source: str = SRC_DEFAULT, conf: float = 0.7) -> None:
        if value and not f[key].value:
            f[key] = FieldState(value, source, conf, [value])

    default("court", s.default_court)
    default("county", s.default_county)
    if refresh_speed(case, s):  # (a rate read for a speed no longer named would stay with the new one)
        refresh_rate(case, s)
    refresh_copies(case, s)
    default("rate", s.rate_for(f["delivery"].value))

    # Pages from an invoice's total at the chosen speed ("Regular Rate: $94.50" at $4.30 a page -> 22)
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
    """Estimated delivery = today + the turnaround days of the chosen speed, moved off a weekend (unless the
    user typed one; nothing when the speed's turnaround isn't known)."""
    f = case.fields["delivery_date"]
    if f.source == SRC_USER and f.value:
        return
    days = s.days_for(case.get("delivery"))
    if days is None:
        return
    d = us_date(next_weekday(date.today() + timedelta(days=days)))
    case.fields["delivery_date"] = FieldState(d, SRC_DERIVED, 0.8, [d])


def refresh_speed(case: CaseInfo, s: Settings) -> bool:
    """The agreement form names one speed: the one the user chose for the job (in the Order card, or when
    asked about a speed a document mentions: batch.Job.speed_question), while it is still among the speeds
    offered (or "Other"); else always the one Settings picks (Settings.agreement_speed: by default "Expedited,
    else the slowest offered"). A speed found in a document doesn't change it: an e-mail's "at your regular
    rate" is no choice of the user's (the window asks instead). True when it changed."""
    f = case.fields["delivery"]
    if f.source == SRC_USER and f.value and (f.value == "Other" or s.speed_offered(f.value)):
        return False
    pick = s.agreement_speed()
    if f.source == SRC_DEFAULT and f.value == pick:
        return False
    case.fields["delivery"] = FieldState(pick, SRC_DEFAULT, 0.7, [pick]) if pick else FieldState()
    return f.value != pick


def apply_speed_rule(case: CaseInfo, s: Settings) -> bool:
    """The agreement form's speed as refresh_speed says, and when it changed, its rate per page (unless typed)
    and the delivery date (unless typed; when Settings.fill_delivery_date) follow it. True when it changed.
    Called wherever what decides it changes: the speeds ticked, the rate sheet, the settings, a job shown."""
    if not refresh_speed(case, s):
        return False
    refresh_rate(case, s)
    if s.fill_delivery_date:
        refresh_delivery_date(case, s)
    return True


def refresh_copies(case: CaseInfo, s: Settings, parties: int = 0) -> None:
    """No. of copies = the number of ordering parties, as the invoice counts them: `parties` when the Parties
    number of the invoice was set (batch.Job.parties; batch.Job.refresh_copies passes it), else the attorneys
    ticked who get an invoice (CaseInfo.ordering_parties: a firm is one party, however many attorneys its row
    names). A number the user typed wins; with nobody ticked, a number read from a document stays, else the
    default from Settings. A number read from a document gives way to the parties once someone is ticked."""
    f = case.fields["copies"]
    if f.source == SRC_USER and f.value:
        return
    n = parties or case.ordering_parties()
    if n:
        case.fields["copies"] = FieldState(str(n), SRC_DERIVED, 0.9, [str(n)])
    elif f.value and f.source not in (SRC_DEFAULT, SRC_DERIVED):
        return  # read from a document: kept until someone is ticked
    elif s.default_copies:
        case.fields["copies"] = FieldState(s.default_copies, SRC_DEFAULT, 0.7, [s.default_copies])
    else:
        case.fields["copies"] = FieldState()


def refresh_rate(case: CaseInfo, s: Settings) -> None:
    """Rate = the rate sheet's rate for the chosen delivery type (unless the user typed one)."""
    f = case.fields["rate"]
    if f.source == SRC_USER and f.value:
        return
    r = s.rate_for(case.get("delivery"))
    if r:
        case.fields["rate"] = FieldState(r, SRC_DEFAULT, 0.7, [r])
