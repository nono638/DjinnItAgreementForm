"""Which courthouse the program works for, and the tables that follow from its profile (MIGRATION.md).

Every module that needs to know which outputs exist asks here (or reads a table built here), instead of naming
them itself: settings.OUTPUTS and OUTPUT_FOLDERS, records.KINDS, the preview's names and colours (by_mark) and
batch's forms made once for a trial (keys("form")) all come from the courthouse's outputs. use() switches to
another courthouse and refreshes these tables in place, so `from .settings import OUTPUTS` keeps working in
every module (tests use it to try a made-up courthouse); it is not meant to run while work is going on.

For now there is one courthouse, Queens Supreme Court, Civil Term (queens_supreme_civil); choosing one in
Settings comes in a later release.
"""
from __future__ import annotations

from .base import ASKS, GROUPS, Courthouse, OutputSpec, Part, Party  # noqa: F401 (for the profiles and rules)
from .queens_supreme_civil import COURTHOUSE as QUEENS_SUPREME_CIVIL

BUILT_IN = (QUEENS_SUPREME_CIVIL,)  # the courthouses that come with the program (the build bundles their makers)
OUTPUTS: dict[str, str] = {}         # what Generate can make: key -> label, as listed (settings.OUTPUTS)
OUTPUT_FOLDERS: dict[str, str] = {}  # key -> its own folder under Documents (settings.OUTPUT_FOLDERS)
_current: Courthouse | None = None


def current() -> Courthouse:
    """The courthouse the program works for."""
    return _current


def outputs() -> tuple[OutputSpec, ...]:
    """The outputs the courthouse makes, as listed."""
    return _current.outputs


def output(key: str) -> OutputSpec | None:
    """The output of this key, or None when the courthouse has none such."""
    return next((o for o in _current.outputs if o.key == key), None)


def keys(group: str | None = None) -> list[str]:
    """The outputs' keys as listed; with `group` ("form", "billing"...), only those of that group."""
    return [o.key for o in _current.outputs if group is None or o.group == group]


def make_order(wanted) -> list[str]:
    """The outputs among `wanted` (keys; unknown ones are left out) in the order a job's files are made: by
    group (GROUPS: the run sheet first, the invoices last), then as listed. ["invoice", "agreement",
    "runsheet"] -> ["runsheet", "agreement", "invoice"]."""
    wanted = set(wanted)
    listed = [o for o in _current.outputs if o.key in wanted]
    return [o.key for o in sorted(listed, key=lambda o: GROUPS.index(o.group))]  # (sorted keeps the listed order)


def panel_order() -> list[OutputSpec]:
    """The outputs in the order of their panels in the window (OutputSpec.panel_pos; a tie: as listed)."""
    return sorted(_current.outputs, key=lambda o: o.panel_pos)


def asks(outputs, question: str) -> bool:
    """True when any of these outputs (keys) asks this question before it is made (OutputSpec.asks, of ASKS:
    "firms", "speed"...)."""
    return any(o.key in set(outputs) and question in o.asks for o in _current.outputs)


def split_rule():
    """How the courthouse divides a charge billed once for pages firms ordered together at different speeds
    (Courthouse.split_rule: split(parties, me) -> Part), or None when it bills no such pages."""
    return _current.split_rule_impl()


def speeds_together() -> int:
    """How many different speeds may share pages (Courthouse.speeds_together; 1 = they must all be one)."""
    return _current.speeds_together


def speed_upfront() -> bool:
    """Whether the firms of a split order must each commit to a speed before they are billed
    (Courthouse.speed_upfront), instead of choosing one from the invoice."""
    return _current.speed_upfront


def by_mark(mark: str) -> OutputSpec | None:
    """The output whose PDFs carry this mark (pdfout.mark: "MOFR"), or None ("math", a detailed copy, "")."""
    return next((o for o in _current.outputs if mark and o.mark == mark), None)


def use(courthouse: Courthouse) -> Courthouse | None:
    """Works for `courthouse` from now on and refreshes the tables built from it (in place: the modules that
    imported them see the new ones). Returns the courthouse before it."""
    global _current
    before, _current = _current, courthouse
    OUTPUTS.clear()
    OUTPUTS.update((o.key, o.label) for o in courthouse.outputs)
    OUTPUT_FOLDERS.clear()
    OUTPUT_FOLDERS.update((o.key, o.folder) for o in courthouse.outputs if o.folder)
    return before


use(QUEENS_SUPREME_CIVIL)
