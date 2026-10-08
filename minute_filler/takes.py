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

import math
import re
from dataclasses import dataclass, field

TOP, BOTTOM, HEAD = 0.12, 0.88, 0.075  # page margins, as parts of the page height
_INITIALS = re.compile(r"^[A-Za-z]{2,3}$")
_NUMBER = re.compile(r"^\d{1,5}$")
_EXAM = re.compile(r"(?i)\b(?:re-?)?(?:direct|cross)\b|\bvoir\s+dire\b|\bexamination\b")  # "Redirect" too
_INDEX_REF = re.compile(r"\b\d{1,2}:\d{1,5}/\d{1,2}\b")  # the word index: "1:360/25"
_PAGE_LINE = re.compile(r"\b\d{1,5}:\d{1,2}\b")          # and "394:18"
# a time of day, not a reference: "9:10 a.m.", "at 9:10", "about 9:10"
_TIME_AFTER = re.compile(r"(?i)^\s*(?:[ap]\.?\s?m\b|o'clock)")
_TIME_BEFORE = re.compile(r"(?i)\b(?:at|about|around|until|till|by|from|to|between)\s+$")
# a transcript's line number at the left edge ("1", "1    THE COURT:"); an index entry is "1 (5) 312:4;..."
_LEFT_NUMBER = re.compile(r"(?m)^[ \t]{0,4}(\d{1,2})(?:[ \t]+(?!\(\d+\))\S|[ \t]*$)")
LINE_NUMBERS = 8  # this many in a row (1, 2, 3...) at the left edge: a page of the transcript, not of its index
_SWORN = re.compile(r"(?i)\bcalled\s+(?:as\s+a\s+witness|virtually|on\s+behalf)|\bduly\s+(?:sworn|affirmed)|"
                    r"\ba\s+witness\s+(?:called|having)")
_EXCUSED = re.compile(r"(?i)\bwitness\s+(?:was\s+|is\s+)?(?:excused|steps?\s+down|stepped\s+down|"
                      r"left\s+the\s+(?:witness\s+)?(?:stand|box))")
# The letters a name can carry after a comma ("SAM POE, DPM", "Pat Roe, Ph.D."), each as it is written:
# extract_regex.smart_title keeps them so ("Dpm" would be wrong), and a witness's name is read and compared
# without them (a podiatrist sworn in as "SAM POE, DPM, called as a witness" is Sam Poe, not "DPM")
CREDENTIALS = ("MD", "M.D.", "DO", "D.O.", "DPM", "D.P.M.", "DDS", "D.D.S.", "DMD", "D.M.D.", "DC", "D.C.",
               "OD", "O.D.", "DVM", "D.V.M.", "PhD", "Ph.D.", "PsyD", "Psy.D.", "EdD", "Ed.D.", "PharmD", "Pharm.D.",
               "RN", "R.N.", "LPN", "NP", "PA", "P.A.", "PA-C", "PT", "DPT", "OT", "LCSW", "LMSW", "CPA", "MPH",
               "MBA", "FACS", "F.A.C.S.", "FACOG", "BSN", "B.S.N.", "MSN", "M.S.N.", "APRN", "CRNA", "FNP", "FNP-C",
               "DNP", "LMHC", "LMFT")
_CREDENTIAL = "(?i:" + "|".join(sorted((re.escape(c).replace(r"\.", r"\.\s?") for c in CREDENTIALS),
                                       key=len, reverse=True)) + r")\.?(?![A-Za-z])"
# "N U R S E   J O H N   D O E, a witness" / "JOHN SMITH, called as a witness" / "JOHN DOE, JR., called" /
# "SAM POE, M.D., PH.D., called"
# ("Mc"/"Mac" may keep their small letters: "MARY McDONALD")
_WITNESS_NAME = re.compile(r"([A-Z](?:[A-Z .'\-]|(?<=\bM)c|(?<=\bM)a(?=c[A-Z])|(?<=\bMa)c){2,80}?"
                           r"(?:\s*,?\s*(?:J\s?R|S\s?R|I\s?I\s?I|I\s?I|I\s?V)\b\s?\.?)?)"
                           rf"\s*,\s*(?:{_CREDENTIAL},?\s*)*"
                           r"(?:a\s+witness|called|after|having|who\s+was)")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "phd", "rn", "pa"}
# a name's letters after its comma (", DPM", ", M.D., PH.D."), dropped before two names are compared: only after a
# comma, as a witness can be named Do or Pa
_AFTER_COMMA = re.compile(rf"(?:\s*,\s*{_CREDENTIAL})+\s*,?\s*$")
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
    "394:18"). "Min-U-Script" alone doesn't say so: a condensed transcript prints it on every page. A page of
    testimony reading out the times of a chart ("9:10, 9:15, 9:20... 11:15") has the same shape, so a time with
    a.m., p.m. or o'clock after it, or "at", "about", "until"... before it, doesn't count, and a page with the
    transcript's line numbers down its left edge (1, 2, 3... in order, see has_line_numbers) is never the index,
    whatever else is on it (_scan also keeps a page signed with initials seen before it). Taken for the index,
    such a page would end the count, and every page after it go unbilled and off the run sheet."""
    if has_line_numbers(text):
        return False
    refs = len(_INDEX_REF.findall(text))
    for m in _PAGE_LINE.finditer(text):
        line = m.group(0).split(":")[1]
        # a time ("7:00", "9:38") is not a reference: a page has 25 lines, numbered without a 0 in front
        if line.startswith("0") or int(line) > 25:
            continue
        if _TIME_AFTER.match(text[m.end():m.end() + 12]) or _TIME_BEFORE.search(text[max(0, m.start() - 12):m.start()]):
            continue
        refs += 1
    return refs >= 8 or (refs >= 3 and "Min-U-Script" in text)


def has_line_numbers(text: str) -> bool:
    """A transcript page: at least LINE_NUMBERS line numbers in a row at the left edge, counted from 1 (1, 2, 3...),
    as a page's lines always are. The word index's entries for numbers can look the same ("10 [2] 45:3",
    "11 45:3, 67:12"; one with its count in parentheses, "10 (3) 312:4;...", is not taken for a line number), but
    the index sorts them as text ("1", "10", "100", "11" ... "19", "2"), so those in a row start at 10, 20...:
    "10" to "19" is no page of testimony."""
    run, best, prev = 0, 0, None
    for m in _LEFT_NUMBER.finditer(text):
        n = int(m.group(1))
        if n == 1:
            run = 1
        elif run and n == prev + 1:
            run += 1
        else:
            run = 0  # (a number that doesn't go on from 1, like the page number above the lines, counts for nothing)
        best, prev = max(best, run), n
    return best >= LINE_NUMBERS


@dataclass
class PageFacts:
    """What one PDF page says, read once for every reading of the page count (count_pages)."""
    mark: PageMark
    lined: bool = False  # a transcript's line numbers down its left edge (has_line_numbers)
    index: bool = False  # it looks like the word index (is_index_page)
    blank: bool = False  # next to no text and no picture: an empty page (a scanned page is not blank)
    top: str = ""        # an index-looking page's first lines: the heading that can name the case


def read_pages(doc) -> list[PageFacts]:
    """The facts of every page of a PDF, the word index's too."""
    facts = []
    for page in doc:
        text = page.get_text()
        lined = has_line_numbers(text)
        index = not lined and is_index_page(text)  # (a page with line numbers is never the index)
        top = "\n".join([l.strip() for l in text.splitlines() if l.strip()][:6]) if index else ""
        # a scanned page has no text either, but it is a page of the transcript: taken for blank, the scanned
        # pages after a typed cover page would all be left out of the count
        blank = len(text.strip()) < 20 and not page.get_images()
        facts.append(PageFacts(scan_page(page, text), lined, index, blank, top))
    return facts


def scan_pdf(doc) -> list[PageMark]:
    """One PageMark per transcript page, without the word index after it (see _scan)."""
    return _scan(read_pages(doc))


def _scan(facts: list[PageFacts]) -> list[PageMark]:
    """The pages up to the first one that looks like the word index: one PageMark each. [] when no page has
    initials or a page number (not a transcript, or a scan without text). A page that looks like the index but
    carries the initials seen on the pages before it is a page of the transcript (the index is unsigned)."""
    marks: list[PageMark] = []
    seen: set[str] = set()
    for i, f in enumerate(facts):
        if i and f.mark.initials not in seen and f.index:
            break
        if f.mark.initials:
            seen.add(f.mark.initials)
        marks.append(f.mark)
    # trailing pages with neither initials nor a number are dropped; a last page with only its number is
    # kept (it ends the last take)
    signed = [i for i, m in enumerate(marks) if m.initials or m.number is not None]
    return marks[:signed[-1] + 1] if signed else []


# ------------------------------------------------------------------ the page count, read several ways

INDEX_SHARE, INDEX_MIN = 0.25, 5  # the word index is at most a quarter of the PDF, or 5 pages for a short one
SEPARATORS = 2  # blank pages between the transcript and its word index that the walk from the end steps over
# the readings of the page count, in the order a tie between them is settled (see count_pages)
READINGS = {"scan": "reading the pages up to the word index", "lines": "the pages with line numbers",
            "back": "the word index found from the end", "numbers": "the printed page numbers"}


@dataclass
class PageCount:
    """A transcript's own pages (without the word index), and how sure the reading of them is.

    Four readings of the PDF are compared (READINGS): the pages up to the first one that looks like the index
    (_scan), the last page with a transcript's line numbers, the index found by walking back from the last page,
    and the last page whose printed number follows the first one's (378 on the first page, so 460 on the 83rd).
    A reading that can't tell (a scan has no text, a transcript without printed numbers) has no say. Blank pages
    at the end of the PDF are neither the transcript nor its index."""
    pages: int
    index_pages: int = 0              # the pages after the transcript (the word index): not written nor billed
    first: int | None = None          # the printed numbers of its first and last page ("378", "460")
    last: int | None = None
    how: str = "scan"                 # the reading the count comes from (READINGS), "pdf" when none could tell
    readings: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.95          # 0.8 with a note (another reading than the scan's), 0.55 with a warning
    note: str = ""                    # which reading the count comes from, when the scan's was put right
    warning: str = ""                 # "⚠ ...: check the count", when the readings don't settle it (count_pages)
    others: list[int] = field(default_factory=list)  # the counts other readings gave


def index_limit(page_count: int) -> int:
    """The most pages the word index can take: a quarter of the PDF, or INDEX_MIN for a short one."""
    return max(math.ceil(INDEX_SHARE * page_count), INDEX_MIN)


def _empty(f: PageFacts) -> bool:
    """A blank page between the transcript and its index. A short page signed with initials is the transcript's
    last page ("397 / 1 * * * / ds"), however little is on it."""
    return f.blank and not f.mark.initials


def _index_run(facts: list[PageFacts]) -> int:
    """The most pages in a row among these that look like the word index."""
    run = most = 0
    for f in facts:
        run = run + 1 if f.index else 0
        most = max(most, run)
    return most


def count_pages(facts: list[PageFacts], page_count: int | None = None) -> PageCount:
    """Reads the transcript's own pages four ways (see PageCount) and reconciles them.

    The count that most readings give wins, among those that leave the index no more than index_limit() of the
    pages (blank pages at the end left out); a tie goes to the scan's count, then to the reading listed first in
    READINGS. The printed numbers can't see where the index begins when its pages are numbered on, so they never
    count past the index found from the end. The count kept has a warning ("⚠ 30 of the PDF's 96 pages were
    taken for the word index...") when no count passes the limit, when another count has two readings behind it,
    and when pages between the scan's count and a larger one look like the index (two or more in a row: an index
    with a page of line numbers after it). A count from another reading than the scan's otherwise says which
    (note). The count is never 0 for a PDF with pages: when no reading can tell, or the readings leave the index
    too much and the pages they leave don't look like one (the scanned pages after a typed cover), it is every
    page, with a warning in the second case."""
    total = len(facts) if page_count is None else page_count
    room = len(facts)  # the pages with something on them: the blank ones at the end left out
    while room > 1 and facts[room - 1].blank:
        room -= 1
    room = min(room, total)
    readings: dict[str, int] = {}
    scanned = len(_scan(facts))
    if scanned:
        readings["scan"] = scanned
    lined = [i for i, f in enumerate(facts) if f.lined]
    if lined:
        readings["lines"] = lined[-1] + 1
    k = room
    while k > 1 and facts[k - 1].index:
        k -= 1
    if k < room:  # (without an index at the end, walking back says nothing)
        j = k
        while j > 1 and _empty(facts[j - 1]):
            j -= 1
        # a blank page or two between the transcript and its index, not the pages of a scanned transcript
        if k - j <= SEPARATORS and not _empty(facts[j - 1]):
            k = j
        readings["back"] = k
    numbered = [(i, f.mark.number) for i, f in enumerate(facts) if f.mark.number is not None]
    offset = None
    if numbered:
        offset = numbered[0][1] - numbered[0][0]  # the printed number of PDF page i is i + offset
        follow = [i for i, n in numbered if n - i == offset]
        if len(follow) >= 3:  # (one or two numbers in a row prove nothing)
            readings["numbers"] = min(follow[-1] + 1, readings.get("back", room))
        else:
            offset = None
    if not readings:
        return PageCount(total, how="pdf")
    votes: dict[int, list[str]] = {}
    for name in READINGS:
        if name in readings:
            votes.setdefault(readings[name], []).append(name)
    limit = index_limit(room)
    passing = {n: names for n, names in votes.items() if room - n <= limit}
    order = list(READINGS)

    def rank(n: int) -> tuple:
        names = votes[n]
        return len(names), "scan" in names, -min(order.index(x) for x in names)
    out = PageCount(0, readings=dict(readings))
    best = max(passing, key=rank) if passing else readings.get("scan", 0)
    if best and best not in passing and sum(f.index for f in facts[best:room]) * 2 < room - best:
        best = 0  # the pages it leaves to the index mostly don't look like one: a scan after a typed cover page
    if not best:  # nothing but readings that leave the index too much: every page, checked
        out.pages, out.how, out.index_pages = room, "pdf", 0
        out.others = sorted(votes)
        out.warning = ("⚠ the readings of the PDF leave more than a quarter of it to the word index: every page "
                       "is counted, check the count")
        out.confidence = 0.55
        return out
    out.pages, out.how = best, votes[best][0]
    out.index_pages = max(0, room - best)
    out.others = sorted(n for n in votes if n != best)
    # the span printed on the pages, when the first page carries its own number (not "0") and the numbers run on
    # to the count's last page: not across a cover page without one, nor two transcripts numbered apart
    if offset is not None and offset >= 1 and numbered[0][0] == 0 and readings.get("numbers") == best:
        out.first, out.last = offset, offset + best - 1
    rival = [n for n in out.others if len(votes[n]) >= 2 or n in passing]
    if best not in passing:
        out.warning = (f"⚠ {out.index_pages} of the PDF's {room} pages were taken for the word index, more than "
                       f"a quarter: check the count" + (f" (another reading gives {rival[0]})" if rival else ""))
    elif any(len(votes[n]) >= 2 for n in out.others):
        other = next(n for n in out.others if len(votes[n]) >= 2)
        too_much = "" if other in passing else "; that leaves more than a quarter of the PDF to the index"
        out.warning = (f"⚠ the PDF reads as {best} pages ({READINGS[out.how]}) or {other} "
                       f"({READINGS[votes[other][0]]}{too_much}): check the count")
    elif "scan" in readings and out.how != "scan":
        scanned = readings["scan"]
        if scanned < best and _index_run(facts[scanned:best]) >= 2:  # (one exhibit list is no word index)
            out.warning = (f"⚠ the PDF reads as {best} pages ({READINGS[out.how]}) or {scanned} "
                           f"({READINGS['scan']}): pages in between look like the word index, check the count")
        else:
            why = ("the word index looked too long" if room - scanned > limit
                   else f"the first reading gave {scanned}")
            out.note = f"counted from {READINGS[out.how]}: {why}"
    out.confidence = 0.55 if out.warning else 0.8 if out.note else 0.95
    return out


def counted_marks(facts: list[PageFacts], count: PageCount) -> list[PageMark]:
    """The PageMarks of the transcript's pages as count_pages counted them: the scan's (as before), or, when
    another reading won, one per page up to its count. [] when none of those pages has initials or a printed
    number (a scan), or when every page is counted because no reading could tell."""
    marks = _scan(facts)
    if marks and len(marks) == count.pages:
        return marks
    marks = [f.mark for f in facts[:count.pages]]
    return marks if count.how != "pdf" and any(m.initials or m.number is not None for m in marks) else []


def index_heading(facts: list[PageFacts], count: PageCount) -> str:
    """The first lines of the word index after the transcript (its heading often names the case), or ""."""
    return next((f.top for f in facts[count.pages:] if f.index and f.top), "")


# ------------------------------------------------------------------ pages -> takes

def body_pages(marks: list[PageMark], page_count: int, count: PageCount | None = None) -> int:
    """The transcript's own pages (without the word index): what is written and billed. count_pages's count
    when given; else the pages scanned, or page_count (the whole PDF) when no page could be read."""
    if count is not None:
        return count.pages
    return len(marks) or page_count


def find_takes(marks: list[PageMark], title_count: int = 1, front: str = "") -> list[Take]:
    """Groups the pages into takes. A page without initials belongs to the take it is in; a page without a
    number is numbered on from the one before. Each take also lists the witnesses who took the stand and
    stepped down in it, from the swearing-in and "witness excused" lines and the running heads. title_count:
    how many title pages the transcript begins with. front: whose the pages before the first initials are, as
    Whose pages... answered (batch.Job.front_owner: initials, or "none" for nobody's, a take of their own with
    no reporter); without an answer they are the first reporter found's, as on a transcript one reporter wrote,
    and as the invoice counts them on it."""
    known = [m.initials for m in marks if m.initials]
    if not marks:
        return []
    fill = ("" if front == "none" else front) if front else known[0] if known else ""
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
    """'J. Doe' and 'Nurse John Doe, Jr.' are the same witness: the same last word (less Jr., M.D. and such, and
    the letters after a comma: 'Sam Poe, DPM' is Sam Poe, but 'Linh Do' is Do)."""
    def last(s: str) -> str:
        words = [re.sub(r"[^a-z]", "", w) for w in _AFTER_COMMA.sub("", s).lower().split()]
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
