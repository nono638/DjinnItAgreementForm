"""Data model shared by extractors, the GUI and the PDF filler."""
from __future__ import annotations

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

PROC_TYPES = ["Arraignment", "Application", "Hearing", "Plea", "Trial", "Sentence"]
DELIVERY_TYPES = ["Regular", "Expedited", "Daily", "Other"]

# Where a value came from, most to least trustworthy for display purposes.
SRC_USER, SRC_REGEX, SRC_AI, SRC_DERIVED, SRC_DEFAULT = "you", "regex", "AI", "derived", "default"


@dataclass
class Candidate:
    """One proposed value for a field: where it came from (SRC_*), how sure the extractor is (0 to 1),
    and an optional note shown with it ("from file name")."""
    value: str
    source: str
    confidence: float = 0.5
    note: str = ""


@dataclass
class Attorney:
    """An attorney or firm found in the inputs. Every ticked (checked) one orders the minutes and gets
    their own agreement and invoice."""
    name: str = ""
    firm: str = ""
    address: str = ""  # multi-line
    phone: str = ""
    fax: str = ""
    email: str = ""
    party: str = ""    # who they represent ("Plaintiff (Jane Roe)")
    source: str = ""   # SRC_REGEX or SRC_AI
    checked: bool = True

    def key(self) -> str:
        """Name (or firm) for comparing entries: 'Dana Smith, Esq.' -> 'dana smith esq'."""
        return (self.name or self.firm).lower().replace(".", "").replace(",", "").strip()

    def is_placeholder(self) -> bool:
        """'Unrepresented', 'No one appeared' and similar are not orderers."""
        text = f"{self.name} {self.firm}".lower()
        return (not self.name.strip() and not self.firm.strip()) or any(
            p in text for p in ("unrepresented", "no one appeared", "pro se", "self-represented", "no appearance")
        )

    def to_dict(self) -> dict:
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
        """A value worth a second look: the extractors weren't sure of it, or found rivals."""
        return bool(self.value) and (self.confidence < 0.6 or len(self.alternatives) > 1)


@dataclass
class CaseInfo:
    """Merged, user-editable state for one job."""
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
        """The ticked attorneys - one agreement and one invoice each - or [None] (one with a blank attorney)."""
        return [a for a in self.attorneys if a.checked] or [None]

    def missing_required(self) -> list[str]:
        """The REQUIRED_KEYS that are still blank."""
        return [k for k in REQUIRED_KEYS if not self.get(k).strip()]
