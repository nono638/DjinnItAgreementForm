"""Data model shared by extractors, the GUI and the PDF filler."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict

# Case-level fields in the order they appear on the form.
FIELD_KEYS = [
    "court", "county", "part", "judge", "case_name", "index_no", "dates",
    "proc_other", "rate", "delivery", "copies", "est_pages", "delivery_date",
    "agreement_date",
]

FIELD_LABELS = {
    "court": "Court",
    "county": "County",
    "part": "Part No.",
    "judge": "Judge / Justice",
    "case_name": "Name of Case",
    "index_no": "Index Number",
    "dates": "Date(s) of Minutes",
    "proc_other": "Other proceeding (specify)",
    "rate": "Rate per page ($)",
    "delivery": "Delivery",
    "copies": "No. of copies",
    "est_pages": "Est. number of pages",
    "delivery_date": "Est. delivery date",
    "agreement_date": "Date of agreement",
}

# Fields the user is asked about when they are blank at fill time.
REQUIRED_KEYS = ["case_name", "index_no", "dates", "judge"]

# The form's proceeding checkboxes ("Other" is the proc_other field).
PROC_TYPES = ["Arraignment", "Application", "Hearing", "Plea", "Trial", "Sentence"]
# The delivery speeds by name, and "Other" (the ones offered come from the rate sheet: Settings.offered_speeds)
DELIVERY_TYPES = ["Regular", "Expedited", "Daily", "Immediate", "Other"]

# Where a value came from, most to least trustworthy for display purposes.
SRC_USER, SRC_REGEX, SRC_AI, SRC_DERIVED, SRC_DEFAULT = "you", "regex", "AI", "derived", "default"
SRC_PDF = "PDF"  # counted from the transcript PDF itself (its pages), not read from its words
SRC_RECORDS = "records"  # the user's records: an earlier job with the same index number (a suggestion)


@dataclass
class Candidate:
    """One proposed value for a field: where it came from (SRC_*), how sure the extractor is (0 to 1),
    and an optional note shown with it ("from file name")."""
    value: str
    source: str
    confidence: float = 0.5
    note: str = ""


# Words written after a name that belong to it ("Sam Poe, Jr."): split_names keeps them with the name before.
_NAME_SUFFIX = re.compile(r"(?i)^(?:jr|sr|ii|iii|iv|m\.?\s?d|ph\.?\s?d|d\.?d\.?s|cpa)\.?$")
# Entity words at the end of a firm's name that firm_key leaves out ("Counsel & Counsel, LLP" = "Counsel & Counsel")
_FIRM_SUFFIXES = {"llp", "pllc", "llc", "pc", "lp", "pa", "esqs", "esq", "inc", "ltd", "plc"}


def split_names(text: str) -> list[str]:
    """The attorneys named in an Attorney's name: 'Alex B. Counsel, Dana Smith and Sam Poe, Jr.' ->
    ['Alex B. Counsel', 'Dana Smith', 'Sam Poe, Jr.']. Split at commas, semicolons, 'and' and '&'; a suffix
    (Jr., III, M.D.) stays with the name before it, and 'Esq.' is left out."""
    out: list[str] = []
    for part in re.split(r"\s*(?:;|,|\s&\s|\band\b)\s*", text or ""):
        part = re.sub(r"(?i)(?:^|\s+)esq\.?$", "", part.strip(" ,:")).strip(" ,:")
        if not part:
            continue
        if out and _NAME_SUFFIX.match(part):
            out[-1] += ", " + part
        else:
            out.append(part)
    return out


def join_names(names: list[str]) -> str:
    """The names of a firm's attorneys as one Attorney.name: 'Alex B. Counsel, Dana Smith'."""
    return ", ".join(n for n in names if n)


def legacy_key(text: str) -> str:
    """How Attorney.key() read up to version 2.0 (a name or firm in lowercase without '.' and ','), which
    the Excerpts... rows of records made by older versions still use (see Attorney.legacy_keys)."""
    return (text or "").lower().replace(".", "").replace(",", "").strip()


def firm_key(firm: str) -> str:
    """A firm's name for comparing: lowercase, '&' read as 'and', without punctuation, a leading 'The' or the
    entity at the end, so 'Counsel & Counsel, LLP', 'COUNSEL AND COUNSEL' and 'Counsel & Counsel L.L.P.'
    are the same firm ('counsel and counsel'). Accents go ('Ñandú & Pingüino' -> 'nandu and pinguino'); letters of
    other scripts stay."""
    s = unicodedata.normalize("NFKD", (firm or "").lower().replace("&", " and ").replace(".", ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    words = re.sub(r"[\W_]+", " ", s).split()
    if words[:1] == ["the"]:
        words = words[1:]
    while len(words) > 1 and words[-1] in _FIRM_SUFFIXES:
        words.pop()
    if len(words) > 2 and words[-2:] == ["p", "c"]:  # "P. C." written apart
        words = words[:-2]
    return " ".join(words)


@dataclass
class Attorney:
    """A firm or office (or a lone attorney) found in the inputs: one entry for billing. Its attorneys are all
    named in `name` ('Alex B. Counsel, Dana Smith', see names()): two attorneys of one firm are one entry,
    one party. Every ticked (checked) one orders the minutes and gets its own agreement and invoice (one
    of each per key(); placeholders get no invoice, see is_placeholder; no agreement for a day it ordered
    0 pages of, see fill.agreement_orderers)."""
    name: str = ""     # the attorney, or the firm's attorneys joined by ", " (join_names)
    firm: str = ""
    address: str = ""  # multi-line
    phone: str = ""
    fax: str = ""
    email: str = ""
    party: str = ""    # who they represent ("Plaintiff (Jane Roe)")
    source: str = ""   # SRC_REGEX or SRC_AI
    checked: bool = True

    def key(self) -> str:
        """The entry for comparing and billing: its firm (firm_key: 'Counsel & Counsel, LLP' -> 'counsel and
        counsel'), else its name ('Dana Smith, Esq.' -> 'dana smith esq'). Who ordered which pages of a day
        (batch.Job.portions) and who was invoiced already (batch.Job.invoiced_keys) name entries by it; keyed by
        the firm, it stays the same when an attorney of the firm is added to the row."""
        return firm_key(self.firm) or legacy_key(self.name or self.firm)

    def names(self) -> list[str]:
        """The attorneys named in this entry (split_names of name)."""
        return split_names(self.name)

    def label(self) -> str:
        """The entry in a few words, for a column or a line of the window: its attorney ('Dana Smith'), or its
        firm when it names several attorneys ('Counsel & Counsel, LLP') or none."""
        return self.firm if self.firm and len(self.names()) != 1 else (self.name or self.firm)

    def legacy_keys(self) -> set[str]:
        """The keys older versions gave this entry or any one of its attorneys (one entry per attorney, keyed by
        the name, else the firm): Excerpts... rows kept in older records name them (see batch.one_row_per_firm)."""
        return {k for k in [legacy_key(self.name or self.firm), legacy_key(self.firm),
                            *(legacy_key(n) for n in self.names())] if k}

    def is_placeholder(self) -> bool:
        """'Unrepresented', 'No one appeared' and similar are not orderers."""
        text = f"{self.name} {self.firm}".lower()
        return (not self.name.strip() and not self.firm.strip()) or any(
            p in text for p in ("unrepresented", "no one appeared", "pro se", "self-represented", "no appearance")
        )

    def to_dict(self) -> dict:
        """Every field by name, ready for JSON."""
        return asdict(self)


def to_int(v, default: int = 0) -> int:
    """'30' -> 30, '1,200' -> 1200; blank or unreadable -> default."""
    try:
        return int(str(v).replace(",", "").strip())
    except ValueError:
        return default


@dataclass
class Extraction:
    """Raw output of one extractor over one input."""
    fields: dict[str, list[Candidate]] = field(default_factory=dict)
    proc_types: dict[str, float] = field(default_factory=dict)  # type -> confidence
    attorneys: list[Attorney] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    doc_kind: str = ""  # "transcript", "invoice", "email" or "text" (set by the rules extractor)

    def add(self, key: str, value: str, source: str, confidence: float, note: str = "") -> None:
        """Adds a candidate for field `key`. Blank values are skipped; a value already there (in any
        case) only has its confidence raised."""
        value = (value or "").strip()
        if not value:
            return
        lst = self.fields.setdefault(key, [])
        for c in lst:
            if c.value.lower() == value.lower():
                c.confidence = max(c.confidence, confidence)
                return
        lst.append(Candidate(value, source, confidence, note))

    def add_proc(self, ptype: str, confidence: float) -> None:
        """Proposes a proceeding type (one of PROC_TYPES), keeping the highest confidence seen."""
        self.proc_types[ptype] = max(self.proc_types.get(ptype, 0.0), confidence)


@dataclass
class FieldState:
    """The value chosen for one field, where it came from, and the other values offered in the window."""
    value: str = ""
    source: str = ""
    confidence: float = 0.0
    alternatives: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """A value worth a second look: the extractors weren't sure of it, or found rivals, or it is only a
        suggestion from the user's records."""
        return bool(self.value) and (self.confidence < 0.6 or len(self.alternatives) > 1 or self.source == SRC_RECORDS)


@dataclass
class CaseInfo:
    """Merged, user-editable state for one job: a FieldState per FIELD_KEYS field, the proceeding types ticked,
    the attorneys (ticked or not) and the extractors' notes."""
    fields: dict[str, FieldState] = field(default_factory=lambda: {k: FieldState() for k in FIELD_KEYS})
    proc_types: set[str] = field(default_factory=set)
    attorneys: list[Attorney] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def get(self, key: str) -> str:
        """The value of a field in FIELD_KEYS."""
        return self.fields[key].value

    def set(self, key: str, value: str, source: str = SRC_USER) -> None:
        """Sets a field as certain (confidence 1.0); by default as typed by the user."""
        fs = self.fields[key]
        fs.value, fs.source, fs.confidence = value, source, 1.0

    def orderers(self) -> list[Attorney | None]:
        """The ticked entries, or [None] (one form with a blank attorney block) when nobody is ticked.
        fill.agreement_orderers picks the agreements from them (one per firm key, none for a firm that ordered
        0 pages); invoices go to invoice_orderers()."""
        return [a for a in self.attorneys if a.checked] or [None]

    def invoice_orderers(self) -> list[Attorney | None]:
        """Who gets an invoice: the ticked attorneys, one per Attorney.key() (the same firm, or the same attorney,
        entered twice is billed once) and without placeholders ("Unrepresented", a blank row being typed in), or
        [None] (one invoice with a blank Bill To)."""
        out, seen = [], set()
        for a in self.attorneys:
            if a.checked and not a.is_placeholder() and a.key() and a.key() not in seen:
                seen.add(a.key())
                out.append(a)
        return out or [None]

    def ordering_parties(self) -> int:
        """How many parties ordered: the ticked attorneys who get an invoice (invoice_orderers), as the invoice
        counts its parties (a firm is one party, however many of its attorneys it names); 0 when none is ticked."""
        return sum(1 for a in self.invoice_orderers() if a is not None)

    def missing_required(self) -> list[str]:
        """The REQUIRED_KEYS that are still blank."""
        return [k for k in REQUIRED_KEYS if not self.get(k).strip()]
