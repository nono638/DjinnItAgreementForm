"""The shapes a courthouse profile is made of (MIGRATION.md): a Courthouse and the outputs it makes (OutputSpec).
This module imports nothing else of the package, so a profile, and every module that reads one, can import it
without a cycle.

A profile says how things are done at one courthouse: the program itself (reading documents, the questions,
saving, the records) stays the same everywhere. More of what differs (the words documents are read by, the
forms, the billing) moves into it in later releases.

Why a registry: until Release A the four outputs were named in six places that had to agree (settings,
records, the preview, deliver's if-chain, batch and the window), so a fifth output, or a courthouse without a
MOFR, meant editing all six, and one missed was a bug. Now each output is described once, in its courthouse's
profile, and those modules ask courthouses/ (tests/test_courthouses.py adds a made-up fifth output without
touching them).

Why the code is named ("minute_filler.fill:Agreements") and not imported here: the outputs' code (fill, mofr,
invoice...) imports settings, which imports this registry, so importing it back would be a cycle; and a
profile that comes from a folder outside the program later names its own code the same way. The cost: the
build can't see such imports, so it lists them (Courthouse.modules).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

# How outputs are grouped, in the order a job's files are made: the run sheet first (one open in Excel holds
# the case, so nothing at all is made and trying again bills nothing twice), then the forms (made once for all
# the days of a trial in Generate all), other documents, and the invoices last.
GROUPS = ("sheet", "form", "doc", "billing")
PER = ("attorney", "case")  # how many files of an output a job gets (OutputSpec.per)
# What the window's Generate (and Generate all) may ask before an output is made (OutputSpec.asks):
#   firms     rows of the attorneys that may be one firm (one firm is one agreement, one invoice)
#   parties   a Parties number set by hand that isn't the firms that ordered
#   speed     the speed a document asks for, when it isn't the Settings rule's (the forms name one)
#   fields    the fields the forms need, when blank or in doubt (else only the case name is asked about)
#   attorney  who ordered, when nobody is ticked (the file is addressed to an attorney)
#   speeds    the speed each firm of a split order committed to (firms that ordered the same pages), when one
#             isn't set (Courthouse.speed_upfront; the forms name it, the invoices bill it)
ASKS = ("firms", "parties", "speed", "fields", "attorney", "speeds")


@dataclass(frozen=True)
class OutputSpec:
    """One thing Generate can make.

    key: its name in settings.json ("outputs", "output_dirs"), in the records' "kind" column and on the command
    line (--outputs): it never changes once released. label: its name in the window, Settings and the Records
    ("Minute agreement"). group: one of GROUPS. mark: the kind its PDFs are labelled with (pdfout.mark: "YinIt
    MOFR"), "" for a file that isn't a PDF; it never changes either, as the preview tells old files apart by it.
    color: its tab in the preview ("" for one never previewed). folder: a folder of its own under Documents
    when Settings -> Options -> Folders gives none ("" = the job's Save to folder).

    maker: where the code that makes it is, "module:name" ("minute_filler.fill:Agreements"), imported only when
    a file is made (maker_impl()). It has make(m), which writes the job's files and records each one through
    m.record (m: a deliver.Making, everything generate knows of the job); it may have check(m), which raises
    ValueError before anything at all is made when the job can't have it ("an invoice needs pages to bill"); and
    an output of group "form" has make_form(form, s, folder) -> Path, which makes one form covering several days
    of a case (a deliver.CaseForm, see batch.case_forms). It is a class only to keep these under the one name
    the profile gives (static methods: it holds nothing), as fill.Agreements and mofr.Mofrs are.

    per: how many files of it a job gets: "attorney" (one for each attorney that ordered pages, as minute
    agreements are) or "case" (one, as the MOFR); a form made once for several days of a case is one per
    attorney or one in all the same way (batch.case_forms). The invoices and the run sheet are counted by the
    batch itself, as the days of a case share them. policy: what it asks of a job, "module:name" of a class
    like batch.OutputPolicy (why a job can't have it, the problems that stop it); "" = that class itself:
    every job can have it.

    In the window: tip, the tooltip of its box in the Outputs box ("" = none, or one the window sets itself);
    words, how the line under its heading counts its files (("minute agreement form", "minute agreement forms");
    () = its label); asks, the questions Generate asks before making it (of ASKS); panel, "module:name" of the
    function that adds its options to its panel (panel(window, form): see gui/panels.py; "" = no options);
    panel_pos, its place among the panels (the window's order is not the listed one)."""
    key: str
    label: str
    group: str
    mark: str = ""
    color: str = ""
    folder: str = ""
    maker: str = ""
    per: str = "case"
    policy: str = ""
    tip: str = ""
    words: tuple[str, str] | tuple = ()
    asks: tuple[str, ...] = ()
    panel: str = ""
    panel_pos: int = 99

    def __post_init__(self):
        if self.group not in GROUPS:
            raise ValueError(f"output {self.key!r}: unknown group {self.group!r} (one of {', '.join(GROUPS)})")
        if self.per not in PER:
            raise ValueError(f"output {self.key!r}: per must be one of {', '.join(PER)}, not {self.per!r}")
        if not self.maker:
            raise ValueError(f"output {self.key!r}: no maker")
        unknown = [a for a in self.asks if a not in ASKS]
        if unknown:
            raise ValueError(f"output {self.key!r}: unknown question(s) {', '.join(unknown)} (of {', '.join(ASKS)})")
        if self.words and len(self.words) != 2:
            raise ValueError(f"output {self.key!r}: words are (one, several), not {self.words!r}")

    def count_words(self) -> tuple[str, str]:
        """(one, several) as the window counts its files: `words`, else its label ("Note" -> ("note", "notes"))."""
        if self.words:
            return self.words
        one = self.label if self.label.isupper() else self.label[:1].lower() + self.label[1:]
        return one, one + "s"

    def maker_impl(self):
        """What `maker` names, imported now."""
        return resolve(self.maker)

    def policy_impl(self):
        """What `policy` names, imported now; None for the default (batch.OutputPolicy)."""
        return resolve(self.policy) if self.policy else None


def resolve(ref: str):
    """The object a reference names: "minute_filler.fill:Agreements" -> that class ("module" alone: the
    module). ImportError or AttributeError when there is no such thing."""
    module, _, name = ref.partition(":")
    obj = importlib.import_module(module)
    for part in name.split(".") if name else []:
        obj = getattr(obj, part)
    return obj


@dataclass(frozen=True)
class Party:
    """One firm on pages several firms ordered, as a split rule (Courthouse.split_rule) sees it: its name (for the
    math's words: "Smith Law"; "This firm" for the one whose part is worked out), the speed it ordered (the rate
    sheet's name, "Daily"), the price of a page of the charge being split at that speed (the original, or the
    judge's index), and how fast that speed is (rank: higher is faster; its place among the rate sheet's speeds,
    which are read cheapest Original first)."""
    name: str
    speed: str
    rate: Fraction
    rank: int


@dataclass(frozen=True)
class Part:
    """One firm's part of a charge billed once for pages several firms ordered at different speeds: what it pays a
    page of it (exact, maybe part of a cent) and how that is reached, as steps for the math (words, amount): ("Smith
    Law ordered Daily and pays $6.50 ÷ 2 = $3.25 a page of it", 3.25), ("This firm pays the rest: ...", 4.35)."""
    per_page: Fraction
    steps: tuple[tuple[str, Fraction], ...] = ()


@dataclass(frozen=True)
class Courthouse:
    """A courthouse (or court-reporting agency): key, the name shown, and its outputs in the order they are
    listed (in Settings, the Records and the Folders rows). rate_sheets: the folder of the rate sheets that come
    with the program, copied into the user's (rates.seed; None: none); fallback_rates: the prices used when no
    rate sheet can be read, so a form still gets a rate: (speed, original, copy) each.

    How it bills firms that order the same pages (a split order) at different speeds, the first of its billing
    rules (MIGRATION.md, BillingRules): split_rule, "module:name" of a function split(parties: list[Party], me:
    int) -> Part, how a charge billed once for those pages (the original, the judge's index) is divided between
    them ("" = it isn't: pages ordered together must all be at one speed); speeds_together, how many different
    speeds may share pages (1 = one); speed_upfront, whether the firms of a split order must each commit to a
    speed before they are billed (True: their invoices bill that speed alone, and Generate asks when it isn't
    set), rather than choosing one from the invoice as a firm billed alone does."""
    key: str
    name: str
    outputs: tuple[OutputSpec, ...]
    rate_sheets: Path | None = None
    fallback_rates: tuple[tuple[str, str, str], ...] = ()
    split_rule: str = ""
    speeds_together: int = 1
    speed_upfront: bool = False

    def modules(self) -> list[str]:
        """Every module its outputs and rules name (maker, policy, panel, split_rule), each once: imported only
        when needed, by name, so the build lists them (YinItAgreementForm.spec), as it can't see such imports."""
        refs = [r for o in self.outputs for r in (o.maker, o.policy, o.panel) if r]
        refs += [self.split_rule] if self.split_rule else []
        return list(dict.fromkeys(r.partition(":")[0] for r in refs))

    def split_rule_impl(self):
        """What split_rule names, imported now; None when the courthouse has none (mixed speeds can't be billed)."""
        return resolve(self.split_rule) if self.split_rule else None

    def __post_init__(self):
        keys = [o.key for o in self.outputs]
        if len(set(keys)) != len(keys):
            raise ValueError(f"courthouse {self.key!r}: an output is listed twice ({', '.join(keys)})")
        marks = [o.mark for o in self.outputs if o.mark]
        if len(set(marks)) != len(marks):
            raise ValueError(f"courthouse {self.key!r}: two outputs share a mark ({', '.join(marks)})")
        if self.speeds_together < 1:
            raise ValueError(f"courthouse {self.key!r}: speeds_together must be 1 or more")
        if self.speeds_together > 1 and not self.split_rule:
            raise ValueError(f"courthouse {self.key!r}: several speeds may share pages, but no split_rule says "
                             "how they are billed")
