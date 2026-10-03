"""Who wrote which pages of a transcript: the takes, for the run sheet.

When reporters share a trial, each one puts their initials on every page they write ("ds"), on a line of
its own at the foot of the page (or at the top). Pages in a row with the same initials are one take.
Each page also carries its printed page number and, in most transcripts, a running head naming the
witness ("J. Doe - Plaintiff - Direct") or "Proceedings". The word index printed after the
transcript (the concordance, "1:360/25") is left out: its pages are not counted or billed.

The title page lists the reporters by name ("DANA SMITH / Senior Court Reporters"), so the
initials can usually be turned into a name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOP, BOTTOM, HEAD = 0.12, 0.88, 0.075  # page margins, as parts of the page height
_INITIALS = re.compile(r"^[A-Za-z]{2,3}$")
_NUMBER = re.compile(r"^\d{1,5}$")
_EXAM = re.compile(r"(?i)\b(?:re-?)?(?:direct|cross)\b|\bvoir\s+dire\b|\bexamination\b")  # "Redirect" too
_INDEX_REF = re.compile(r"\b\d{1,2}:\d{1,5}/\d{1,2}\b")  # the word index: "1:360/25"
_PAGE_LINE = re.compile(r"\b\d{1,5}:\d{1,2}\b")          # and "394:18"
_SWORN = re.compile(r"(?i)\bcalled\s+(?:as\s+a\s+witness|virtually|on\s+behalf)|\bduly\s+(?:sworn|affirmed)|"
                    r"\ba\s+witness\s+(?:called|having)")
_EXCUSED = re.compile(r"(?i)\bwitness\s+(?:was\s+|is\s+)?(?:excused|steps?\s+down|stepped\s+down|"
                      r"left\s+the\s+(?:witness\s+)?(?:stand|box))")
# "N U R S E   J O H N   D O E, a witness" / "JOHN SMITH, called as a witness" / "JOHN DOE, JR., called"
_WITNESS_NAME = re.compile(r"([A-Z][A-Z .'\-]{2,80}?(?:\s*,?\s*(?:J\s?R|S\s?R|I\s?I\s?I|I\s?I|I\s?V)\b\s?\.?)?)"
                           r"\s*,\s*(?:(?:M\.\s?D|Ph\.\s?D|R\.\s?N|P\.\s?A)\.?,?\s*)?"
                           r"(?:a\s+witness|called|after|having|who\s+was)")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "phd", "rn", "pa"}
# lines of a title page that are not a reporter's name
_ROLE = re.compile(r"(?i)\b(?:attorneys?|for|esq|court|by|llp|llc|pc|p\.c|plaintiffs?|defendants?|justice|"
                   r"honorable|hon|judge|part|clerk|appearances?|index|county|supreme)\b|:")
_NAME = re.compile(r"[A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){1,3}")
_CREDENTIALS = re.compile(r"(?:(?:RPR|CSR|CRR|RMR|RDR|CCR|CRC|CRI|CM)(?:\s*,\s*|\s+))+")  # "RPR, CRR " (no dots)
_REPORTER_LINE = re.compile(r"(?i)^(?:senior|official|principal|certified)?\s*(?:court\s+)?reporters?\s*$|"
                            r"court\s+reporters?\s*$")
_LINE_NO = re.compile(r"^\s*\d{1,2}(?:\s+|$)")
TITLE_ONLY = "title page only"  # the run sheet's note for a take of title pages alone (half a take on Day's Take)


@dataclass
class PageMark:
    """What one transcript page says about itself."""
    number: int | None = None  # the printed page number
    initials: str = ""         # the reporter's initials, lower case ("" when not found)
    head: str = ""             # the running head ("J. Doe - Plaintiff - Direct", "Proceedings")
    sworn: str = ""            # a witness was sworn in on this page: their name ("?" when it isn't readable)
    excused: bool = False      # a witness stepped down on this page
    sworn_first: bool = False  # both on this page, the swearing-in first (the one sworn in stepped down)

    def witness(self) -> str:
        """The witness named in the running head ("J. Doe"), or ""."""
        parts = re.split(r"\s+-\s+", self.head)
        return parts[0].strip() if len(parts) >= 2 and _EXAM.search(self.head) else ""


@dataclass
class Take:
    """Pages in a row by one reporter."""
    initials: str
    start: int | None          # the printed number of its first page
    pages: int = 0
    witness_start: list[str] = field(default_factory=list)  # witnesses who took the stand in it
    witness_end: list[str] = field(default_factory=list)    # and who stepped down
    title_only: bool = False   # nothing but title pages


# ------------------------------------------------------------------ reading pages

def _lines(page) -> list[tuple[float, float, str, float]]:
    """The page's text lines as (top, bottom, text, left), top to bottom."""
    rows: dict[tuple, list] = {}
    for x0, y0, x1, y1, word, block, line, _ in page.get_text("words"):
        rows.setdefault((block, line), []).append((x0, y0, y1, word))
    out = []
    for words in rows.values():
        words.sort()
        out.append((min(w[1] for w in words), max(w[2] for w in words), " ".join(w[3] for w in words), words[0][0]))
    return sorted(out)


def _page_number(lines: list, h: float, w: float) -> int | None:
    """The printed page number: a number alone on its line at the top of the page, else at the foot. A number
    up to 25 at the left edge is a line number ("1" of the first line, "25" of the last), not the page's."""
    top = [(y0, t) for y0, y1, t, x0 in lines if y1 < TOP * h and _NUMBER.match(t)]
    foot = [(y0, t) for y0, y1, t, x0 in lines if y0 > BOTTOM * h and _NUMBER.match(t)]
    line_no = {(y0, t) for y0, y1, t, x0 in lines if x0 < 0.2 * w and _NUMBER.match(t) and int(t) <= 25}
    # the topmost one at the top, the lowest one at the foot
    for found, pick in (([c for c in top if c not in line_no], min), ([c for c in foot if c not in line_no], max),
                        (top, min), (foot, max)):
        if found:
            return int(pick(found)[1])
    return None


def scan_page(page, text: str | None = None) -> PageMark:
    """Reads the page number, the initials, the running head and any swearing-in from one PDF page.
    text: the page's plain text, if already read."""
    h = page.rect.height or 792
    lines = _lines(page)
    if text is None:
        text = page.get_text()
    mark = PageMark()
    mark.number = _page_number(lines, h, page.rect.width or 612)
    # the initials: alone on their line, at the foot of the page (or at the top)
    for y0, y1, t, _x in sorted(lines, key=lambda l: -l[0]):
        if (y0 > BOTTOM * h or y1 < TOP * h) and _INITIALS.match(t):
            mark.initials = t.lower()
            break
    mark.head = " ".join(t for y0, y1, t, _x in lines if y0 < HEAD * h and not _NUMBER.match(t)
                         and not _INITIALS.match(t)).strip()
    # the plain text keeps the spacing of a letter-spaced name ("J O H N   D O E")
    body = "\n".join(_LINE_NO.sub("", l, count=1).rstrip() for l in text.splitlines() if l.strip())
    m = _SWORN.search(body)
    if m:
        before = " ".join(body[max(0, m.start() - 160):m.end()].split("\n"))
        names = list(_WITNESS_NAME.finditer(before))
        mark.sworn = unspace(names[-1].group(1)) if names else "?"
    ex = _EXCUSED.search(body)
    mark.excused = bool(ex)
    mark.sworn_first = bool(m and ex and m.start() < ex.start())
    return mark


def unspace(name: str) -> str:
    """'N U R S E   J O H N   D O E' -> 'NURSE JOHN DOE' (letter-spaced names, as
    transcripts print a witness's name when they are sworn in); other names only lose extra spaces."""
    name = name.strip()
    words = re.split(r"\s{2,}", name)
    if len(words) > 1 and re.search(r"\b[A-Z]\.? [A-Z]\.? [A-Z]\b", name):
        return " ".join(w.replace(" ", "") for w in words)
    return " ".join(name.split())


def is_index_page(text: str) -> bool:
    """The word index (concordance) printed after a transcript: many 'page:line' references ("1:360/25",
    "394:18"). "Min-U-Script" alone doesn't say so: a condensed transcript prints it on every page."""
    lines = [ref.split(":")[1] for ref in _PAGE_LINE.findall(text)]
    # a time ("7:00", "9:38") is not a reference: a page has 25 lines, numbered without a 0 in front
    refs = len(_INDEX_REF.findall(text)) + sum(1 for n in lines if not n.startswith("0") and int(n) <= 25)
    return refs >= 8 or (refs >= 3 and "Min-U-Script" in text)


def scan_pdf(doc) -> list[PageMark]:
    """One PageMark per transcript page, without the word index after it. [] when no page has initials or
    a page number (not a transcript, or a scan without text)."""
    marks: list[PageMark] = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if i and is_index_page(text):
            break
        marks.append(scan_page(page, text))
    # trailing pages with neither initials nor a number are dropped; a last page with only its number is
    # kept (it ends the last take)
    signed = [i for i, m in enumerate(marks) if m.initials or m.number is not None]
    return marks[:signed[-1] + 1] if signed else []


# ------------------------------------------------------------------ pages -> takes

def body_pages(marks: list[PageMark], page_count: int) -> int:
    """The transcript's own pages (without the word index): what is written and billed. page_count (the
    whole PDF) when no page could be read."""
    return len(marks) or page_count


def find_takes(marks: list[PageMark], title_count: int = 1) -> list[Take]:
    """Groups the pages into takes. A page without initials belongs to the take it is in (or to the next
    one, at the start); a page without a number is numbered on from the one before. Each take also lists
    the witnesses who took the stand and stepped down in it, from the swearing-in and "witness excused"
    lines and the running heads. title_count: how many title pages the transcript begins with."""
    known = [m.initials for m in marks if m.initials]
    if not marks:
        return []
    fill = known[0] if known else ""
    takes: list[Take] = []
    witness = ""           # the witness on the stand, as far as the running heads tell ("": nobody)
    stepped_down = ""      # the last witness to step down: a running head still naming them doesn't start them again
    sworn_waiting = False  # sworn in, name still to come from the next running head
    first_body = True
    prev_no = None

    def step_down(take: Take) -> None:
        """The witness on the stand steps down in take, under the name they were sworn in by if it's them."""
        nonlocal witness, stepped_down
        if witness:
            last = take.witness_start[-1] if take.witness_start else ""
            take.witness_end.append(last if _same_person(witness, last) else witness)
            stepped_down, witness = witness, ""

    for i, m in enumerate(marks):
        initials = m.initials or fill
        fill = initials
        number = m.number if m.number is not None else (prev_no + 1 if prev_no is not None else None)
        prev_no = number
        if not takes or takes[-1].initials != initials:
            takes.append(Take(initials, number, title_only=i < title_count))
        take = takes[-1]
        take.pages += 1
        take.title_only = take.title_only and i < title_count
        if i < title_count:
            continue
        head = m.witness()
        if m.excused and m.sworn and not m.sworn_first:
            step_down(take)  # the witness on the stand before the one sworn in on this page
        if m.sworn:
            name = m.sworn
            if name == "?":  # not readable: the running head's, unless that is still the one who stepped down
                name = head if head and not _same_person(head, stepped_down) else ""
            sworn_waiting = not name
            if name:
                take.witness_start.append(name)
                witness = name
        elif head and not _same_person(head, witness) and not _same_person(head, stepped_down) and not first_body:
            if sworn_waiting:  # the name of the witness sworn in on an earlier page
                take.witness_start.append(head)
            elif not _same_person(head, take.witness_start[-1] if take.witness_start else ""):
                take.witness_start.append(head)
            sworn_waiting = False
            witness = head
        elif head and first_body:
            witness = head  # still on the stand from the day before
        if m.excused and (m.sworn_first or not m.sworn):
            step_down(take)
        first_body = False
    for t in takes:
        t.witness_start = list(dict.fromkeys(t.witness_start))
        t.witness_end = list(dict.fromkeys(t.witness_end))
    return takes


def _same_person(a: str, b: str) -> bool:
    """'J. Doe' and 'Nurse John Doe, Jr.' are the same witness: the same last word (less Jr., M.D. and such)."""
    def last(s: str) -> str:
        words = [re.sub(r"[^a-z]", "", w) for w in s.lower().split()]
        words = [w for w in words if w and w not in _SUFFIXES]
        return words[-1] if words else ""
    return bool(a and b) and last(a) != "" and last(a) == last(b)


# ------------------------------------------------------------------ initials -> names

def initials_of(name: str) -> set[str]:
    """'DANA SMITH' -> {'ds'}; 'Pat Q. Reporter' -> {'pr', 'pqr'}."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", name) if w.lower() not in {"jr", "sr", "ii", "iii"}]
    if len(words) < 2:
        return set()
    return {(words[0][0] + words[-1][0]).lower(), "".join(w[0] for w in words).lower()}


def title_reporters(title: str) -> list[str]:
    """The reporters named on a title page, above "Senior Court Reporter(s)" ("DANA SMITH", "DANA SMITH, RPR")
    or on that line ("DANA SMITH, Senior Court Reporter")."""
    lines = [_LINE_NO.sub("", l).strip() for l in title.replace("\f", "\n").splitlines()]
    names: list[str] = []
    for i, line in enumerate(lines):
        if not line or not _REPORTER_LINE.search(line):
            continue
        found = []
        for prev in reversed(lines[max(0, i - 6):i]):
            if not prev:
                if found:
                    break  # the reporters' block ends at a blank line
                continue
            name = _reporter_name(prev)
            if not name:
                break
            found.append(name)
        names += reversed(found)
        if "," in line:
            names += [n for n in [_reporter_name(line, on_role_line=True)] if n]
    return list(dict.fromkeys(names))


def _reporter_name(line: str, on_role_line: bool = False) -> str:
    """The name on a line of the reporters' block, or "": "DANA SMITH", "DANA SMITH, RPR" or, on the
    "Senior Court Reporter" line itself, "DANA SMITH, Senior Court Reporter". Attorneys' lines are not names."""
    name, comma, rest = line.partition(",")
    if comma and not on_role_line and not _CREDENTIALS.fullmatch(rest.replace(".", "").strip().upper() + " "):
        return ""
    name = " ".join(name.split())
    return name if _NAME.fullmatch(name) and not _ROLE.search(name) else ""


def first_name(name: str, title_case: bool = True) -> str:
    """'DANA SMITH' -> 'Dana' (run sheets name the reporters by first name)."""
    word = (name.split() or [""])[0]
    return word.capitalize() if title_case and (word.isupper() or word.islower()) else word


def my_initials(own_name: str, own_initials: str = "") -> set[str]:
    """The initials the user puts on their pages: those under My info ('P.R.' -> {'pr'}), else the ones of
    their name ('Pat Q. Reporter' -> {'pr', 'pqr'}); empty when neither is known."""
    return {own_initials.lower().replace(".", "").replace(" ", "")} - {""} or initials_of(own_name)


def page_owners(marks: list[PageMark]) -> list[str]:
    """Whose each page is: the initials on it, else those of the page before (a page without them belongs to
    the take it is in, as find_takes says). Pages before the first initials are "": nobody's initials are on
    them, so on a transcript of several reporters whose they are isn't known. [] without marks."""
    out, cur = [], ""
    for m in marks:
        cur = m.initials or cur
        out.append(cur)
    return out


def reporter_label(initials: str, names: list[str], known: dict[str, str], own_name: str = "",
                   own_initials: str = "", title_case: bool = True) -> str:
    """What the run sheet's Reporter column says for these initials: the name given in Settings, the user's
    own first name, a name from the title page whose initials match, else the initials in capitals."""
    if not initials:
        return ""
    key = initials.lower()
    if key in known:
        return known[key]
    own = my_initials(own_name, own_initials)
    if own_name and key in own:
        return first_name(own_name, title_case)
    for n in names:
        if key in initials_of(n):
            return first_name(n, title_case)
    return initials.upper()
