"""The APPEARANCES of a transcript's title page(s): who appeared, one entry for each firm or office.

A title page lists counsel like this (the order of the lines varies):

    COUNSEL & COUNSEL, LLP                     <- the firm, a city agency ("Corporation Counsel of the City
    Attorneys for the Plaintiff                   of New York", "Legal Aid Society"...), maybe over two lines
    500 Sample Road, Suite 31                  <- the address: one to three lines, ending in a ZIP code or
    Lake Town, New York 10002                     just the state ("New York, New York")
    BY: ALEX B. COUNSEL, ESQ. and              <- its attorneys: one line, or more ("Esq." on each, or
        DANA SMITH, ESQ.                          names alone after "BY:")

    ADVOCATE & PARTNERS LLP                    <- blank lines or a page break, then the next firm
    ...

The role ("Attorneys for ...", "For the People", "Court Evaluator") can stand before the firm, after it or
after the attorneys. However many attorneys a firm names, it is one entry (one Attorney, its names joined):
one invoice, one minute agreement, one party.

parse_appearances reads the lines of the title page(s) in order, after the APPEARANCES heading (or, without
one, after the caption and the judge). Each line is classified (_kind: firm, address, attorneys, role,
contact, ...); lines of the page itself (page and line numbers, the reporter's initials, "COPY", "Title
continues on next page", the court's heading and the caption repeated on the next page, the reporter's name)
are left out. Entries are then cut where a new firm, role or attorney line starts after an entry that already
has its address or attorneys, and where a line follows a blank gap wider than the page's own line spacing
(double-spaced pages have a blank line between all lines). A page break alone doesn't end an entry: a firm on
one page can have its attorneys on the next.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .extract_regex import (ADDRESS_RE, EMAIL_RE, FIRM_RE, PHONE_RE, PLACEHOLDER_RE, ROLE_RE, SPECIAL_ROLE_RE,
                            SURE_ADDRESS_RE, X_LINE_RE, fmt_phone, is_mostly_upper, mentions_reporter, same_person)
from .models import Attorney, SRC_REGEX, firm_key, join_names
from .settings import Profile

# ------------------------------------------------------------------ patterns

APPEARANCES_RE = re.compile(r"(?i)^\s*A\s*P\s*P\s*E\s*A\s*R\s*A\s*N\s*C\s*E\s*S\b")
PRESENT_RE = re.compile(r"(?i)^\s*P\s*R\s*E\s*S\s*E\s*N\s*T\s*:?\s*$")
ALSO_PRESENT_RE = re.compile(r"(?i)^\s*also\s+(?:present|appearing)\b")
STATES = r"(?:N\.?\s?Y\.?|New\s+York|N\.?\s?J\.?|New\s+Jersey|CT|Conn\.?|Connecticut|PA|Pennsylvania)"
# The city line that ends an address: "Lake Town, New York 10002", "Brooklyn, NY", "Mineola NY 11501",
# "New York, New York" (a ZIP code, or just the state)
CITY_LINE_RE = re.compile(
    rf"(?i)(?:,\s*{STATES}\.?,?(?:\s+\d{{5}}(?:-\d{{4}})?)?\s*$|\b\d{{5}}(?:-\d{{4}})?\s*$|"
    rf"^(?!of\b|the\b)[A-Za-z.'-]+(?:\s+[A-Za-z.'-]+){{0,2}}\s+(?:N\.?Y\.?|N\.?J\.?|CT)\.?\s*$)")
# Address words besides those of ADDRESS_RE: a room, a building number written out ("One Police Plaza")
MORE_ADDRESS_RE = re.compile(
    r"(?i)\b(?:room|rm\.|highway|hwy\.?|turnpike|tpke\.?|square|terrace|expressway|quadrangle|building|bldg\.?|"
    r"tower|"
    r"(?:north|south|east|west)\s+(?:street|avenue))\b|"
    r"^(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+[A-Z][a-z]*\s+"
    r"(?:\w+\s+)?(?:plaza|street|avenue|place|center|centre|square|road|park)\b")
# A city agency or public office ("Corporation Counsel of the City of New York", "Office of the Kings County
# District Attorney", "Legal Aid Society", "Department of Social Services", "Mental Hygiene Legal Service")
AGENCY_RE = re.compile(
    r"(?i)\b(?:corporation\s+counsel|district\s+attorney|attorney\s+general|(?:county|town|village|city)\s+"
    r"attorney|law\s+department|department\b|office\s+of|bureau\b|legal\s+aid|legal\s+services?|"
    r"defenders?\b|public\s+advocate|mental\s+hygiene|housing\s+authority|transit\s+authority|port\s+authority|"
    r"society\b|counsel'?s\s+office|(?:city|state|county|town|village)\s+of\s+[a-z]|"
    r"N\.?Y\.?C\.?\s+law|NYCHA|commission\b)")
# "BY: ...", "Name, Esq.", "Of Counsel: ...", "MR. COUNSEL" (an attorney named on a line of their own)
ATTY_RE = re.compile(r"(?i)\besq\b(?!s)|^by\b\s*:?|^(?:of\s+counsel|appearing)\s*:|^(?:mr|ms|mrs|miss)\.?\s+[A-Z]")
# A title after an attorney's name, on a line of its own: "Assistant Corporation Counsel", "Of Counsel"
TITLE_RE = re.compile(
    r"(?i)^,?\s*(?:of\s+counsel|(?:(?:senior|special|chief|deputy|executive|first)\s+)*assistant\b.*|"
    r"(?:senior|special|trial|associate|general)\s+counsel|partner|associate|paralegal|law\s+clerk|"
    r"A\.?D\.?A\.?|A\.?A\.?G\.?|A\.?C\.?C\.?)\.?,?$")
# "Attorneys for ...", "Counsel to ...", "Appearing for ...", "On behalf of ...", "For the People"
MORE_ROLE_RE = re.compile(
    r"(?i)^(?:att(?:orne)?ys?\.?|counsel|appearing|on\s+behalf)\s+(?:for|to|of|on\s+behalf\s+of)\s+(?:the\s+)?(.+?)"
    r"[.:,]?$|^for\s+(?:the\s+)?(people|plaintiffs?|defendants?|petitioners?|respondents?|claimants?|movants?|"
    r"appellants?|appellees?|intervenors?|(?:third|non)[- ]party\b.*?|(?:the\s+)?(?:state|city|county)\b.*?|"
    r"[A-Z].{0,60}?)\s*:?$")
PRO_SE_RE = re.compile(r"(?i)\bpro\s*-?\s*se\b|\bself[- ]represented\b|\bappearing\s+in\s+person\b|\bin\s+person\b")
PARTY_WORDS_RE = re.compile(r"(?i)\b(?:the\s+)?(?:plaintiffs?|defendants?|petitioners?|respondents?|claimants?|"
                            r"movants?|appellants?|appellees?|tenants?|landlords?)\b")
# Lines of the page, not of the appearances
NOISE_RE = re.compile(
    r"(?i)^(?:\d{1,4}|[a-z]{1,3}|[A-Z]{1,3}|-\s*\d+\s*-|page\s+\d+(?:\s+of\s+\d+)?|copy|proceedings|"
    r"\(?[^()]{0,60}\bcontinue[sd]?\b[^()]{0,60}\)?|"
    r"(?:the\s+)?supreme\s+court\b.*|(?:civil|criminal|family|surrogate'?s|district|county|housing)\s+court\b.*|"
    r"county\s+of\s+[a-z. ]+(?:\s*:.*)?|.*\b(?:civil|criminal|trial)\s+term\b.*|(?:ias\s+|trial\s+)?part\s+[\w-]+|"
    r"index\s*(?:no\.?|number|#).*|cal(?:endar)?\.?\s*no\.?.*|\d{3,7}\s*[-/]\s*\d{2,4}|-?\s*against\s*-?|"
    r"(?:plaintiffs?|defendants?|petitioners?|respondents?)[.,;]?|attorneys\s+at\s+law|counsel(?:l)?ors\s+at\s+law|"
    r"h\s*e\s*l\s*d\s*:?|b\s*e\s*f\s*o\s*r\s*e\s*:?|j\s*u\s*s\s*t\s*i\s*c\s*e\b.*|.*\bj\.\s?s\.\s?c\.?|"
    r".*\b(?:courthouse|court\s+house|microsoft\s+teams|via\s+(?:zoom|teams|skype)|virtual(?:ly)?)\b.*|"
    r"(?:jury|bench|non-?jury)\s+trial|[*\s]{3,})$")
# The caption repeated at the top of a later page: "JANE ROE v. SAM POE, et al." (a small "v": "DANA V. SMITH" is
# a name with a middle initial)
CAPTION_RE = re.compile(r"\S\s+(?:v|vs)\.?\s+\S|\bet\s+al\b|-\s*against\s*-")
REPORTER_RE = re.compile(r"(?i)\b(?:court\s+reporters?|stenographers?|reported\s+by)\b")
JUDGE_RE = re.compile(r"(?i)^(?:the\s+)?(?:honorable|hon\.)\s")
# Words that end a line which goes on to the next ("Attorneys for Poe, Hill, and" / "Example Health")
CONNECTOR_END_RE = re.compile(r"(?i)(?:,|&|-|\b(?:and|of|the|for|to|as|on|in|d/b/a))\s*$")
# ... and words that start one which goes on from the line before ("& LANE, LLP", "of the City of New York"); a
# small letter too, but not a capital ("MERIDIAN HOLLOW" under a role is a firm of its own)
CONNECTOR_START_RE = re.compile(r"^(?:&|(?i:and|of|for|to|d/b/a)\b|[a-z])")
# "COUNSEL & COUNSEL, LLP, Attorneys for the Plaintiff": the firm and its role on one line
FIRM_ROLE_RE = re.compile(r"(?i)^(?P<firm>.+?),?\s+(?P<role>(?:attorneys?|counsel)\s+(?:for|to)\s+.+)$")
# "Appearing for the Plaintiff:  DANA SMITH, ESQ.", "For the Defendant: Unrepresented": a role, then who appeared
ROLE_COLON_RE = re.compile(r"^(?P<role>[^:]+:)\s*(?P<rest>\S.*)$")

FIRM, AGENCY, ADDRESS, ATTY, TITLE, ROLE, CONTACT, PLACEHOLDER, PRO_SE, HEAD, OTHER = (
    "firm", "agency", "address", "atty", "title", "role", "contact", "placeholder", "pro_se", "head", "other")


def is_address(line: str) -> bool:
    """A line of a street address (see _address_strength)."""
    return _address_strength(line) > 0


def _address_strength(line: str) -> int:
    """2: surely part of an address (a street number, suite, floor, P.O. Box, a city line with the state or
    ZIP); 1: only a street word ('Plaza', 'Lane', 'Broadway'), which a firm's name can have too; 0: no."""
    if SURE_ADDRESS_RE.search(line) or CITY_LINE_RE.search(line) or re.search(r"(?i)\broom\s+\d", line):
        return 2
    if MORE_ADDRESS_RE.search(line):
        return 2 if re.match(r"(?i)(?:one|two|three|four|five|six|seven|eight|nine|ten)\b", line) else 1
    return 1 if ADDRESS_RE.search(line) else 0


def is_city_line(line: str) -> bool:
    """The last line of an address: a city with the state and/or ZIP code."""
    return bool(CITY_LINE_RE.search(line))


def _kind(line: str) -> str:
    """What a line of the appearances is (one of the kinds above)."""
    if PLACEHOLDER_RE.match(line):
        return PLACEHOLDER
    if PRO_SE_RE.search(line):
        return PRO_SE
    if ROLE_RE.match(line) or SPECIAL_ROLE_RE.match(line) or MORE_ROLE_RE.match(line):
        if not re.match(r"(?i)^of\s+counsel$", line):
            return ROLE
    if TITLE_RE.match(line):
        return TITLE
    if EMAIL_RE.search(line):
        return CONTACT
    ph = PHONE_RE.search(line)
    if ph and len(re.sub(r"[\d\s().+:/-]|tel|phone|fax|ph|t|f|o|c|m|cell|office|direct|main", "", line,
                         flags=re.I)) < 4:
        return CONTACT
    firm_mark = bool(FIRM_RE.search(re.sub(r"(?i)\besqs?\b\.?", "", line))) or bool(re.search(r"(?i)\besqs\b", line))
    if firm_mark and not re.match(r"(?i)^by\b", line) and not SURE_ADDRESS_RE.search(line):
        return FIRM
    if ATTY_RE.search(line):
        return ATTY
    if JUDGE_RE.match(line):
        return HEAD
    strength = _address_strength(line)
    if strength == 2 or (strength == 1 and not AGENCY_RE.search(line)):
        return ADDRESS
    if AGENCY_RE.search(line):
        return AGENCY
    if re.search(r"\s&\s*$", line):  # "CARTER BROOKS WILLOW &" (the firm goes on to the next line)
        return FIRM
    return OTHER


def looks_like_name(line: str) -> bool:
    """A person's name on a line of its own ('DANA SMITH', 'Sam R. Advocate, Jr.'): one to six words that
    start with a capital, no digits, and nothing of a firm, an address or a role."""
    words = line.replace(",", " ").split()
    return bool(words) and len(words) <= 6 and not re.search(r"\d", line) and \
        all(re.match(r"[A-Z(]", w) or w.lower() in ("de", "van", "von", "da", "del", "la", "le", "and") for w in words) \
        and not (FIRM_RE.search(line) or AGENCY_RE.search(line) or is_address(line))


# ------------------------------------------------------------------ names

_TITLES = re.compile(r"(?i)^(?:mr|ms|mrs|miss|dr|hon|honorable)\.?\s+")


def parse_names(line: str, tc: Callable[[str], str]) -> list[str]:
    """The attorneys named on a line: 'BY: ALEX B. COUNSEL, ESQ. and DANA SMITH, ESQ.' -> ['Alex B. Counsel',
    'Dana Smith']; also 'By: Mr. Counsel', 'Of Counsel: Dana Smith', names split by commas, 'and', '&' or ';'
    ('Sam Poe, Jr.' stays one). Titles go when a full name is left ('Ms. Dana Smith' -> 'Dana Smith', but
    'Mr. Counsel' stays), and so do 'Esq.', 'of Counsel' and titles after a name ('Assistant Corporation
    Counsel')."""
    s = re.sub(r"(?i)^\s*(?:by|of\s+counsel|appearing)\b\s*:?\s*", "", line)
    s = re.sub(r"(?i),?\s*\bof\s+counsel\b.*$", "", s)
    s = re.sub(r"(?i),\s*(?:(?:senior|special|chief|deputy|executive|first)\s+)*assistant\b.*$", "", s)
    s = re.sub(r"(?i),?\s*\besq\b\.?", " ; ", s)
    out: list[str] = []
    for part in re.split(r"(?i)\s*(?:;|\s&\s|\band\b|,)\s*", s):
        part = re.sub(r"(?i)^(?:&|and)\s+", "", part.strip(" .,:;")).strip(" .,:;")
        if not part:
            continue
        if out and re.fullmatch(r"(?i)jr|sr|ii|iii|iv|m\.?\s?d|ph\.?\s?d", part):  # "SAM POE, JR."
            key = re.sub(r"[^a-z]", "", part.lower())
            out[-1] += ", " + {"jr": "Jr.", "sr": "Sr.", "md": "M.D.", "phd": "Ph.D."}.get(key, part.upper())
            continue
        if len(_TITLES.sub("", part).split()) > 1:  # "Ms. Dana Smith" -> "Dana Smith"; "Mr. Counsel" stays
            part = _TITLES.sub("", part)
        if not re.search(r"[A-Za-z]{2}", part) or re.search(r"\d", part) or len(part.split()) > 6 or \
                FIRM_RE.search(part) or re.search(r"(?i)\b(?:attorneys?|for|the)\b", part):
            continue
        name = tc(part)
        if re.match(r"(?i)^(?:mr|ms|mrs|miss|dr)\b\.?\s", name) and not re.match(r"^\w+\.", name):
            name = re.sub(r"^(\w+)\s", r"\1. ", name, count=1)  # "MR JONES" -> "Mr. Jones"
        out.append(name)
    return out


# ------------------------------------------------------------------ entries

@dataclass
class _Entry:
    """One firm's lines while they are read."""
    heading: str = ""                                  # "For Defendants:" above it (sign-in sheets)
    firm: list[str] = field(default_factory=list)      # the firm or agency, line by line
    header: list[str] = field(default_factory=list)    # unmarked lines before the address: a firm without a
    #                                                    mark ("SMITH JONES"), or the client of a sign-in sheet
    head: list[str] = field(default_factory=list)      # "HON. ...", the head of an office
    address: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    role: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    phone: str = ""
    fax: str = ""
    email: str = ""
    placeholder: str = ""
    pro_se: bool = False
    names_before_address: bool = False  # a lone attorney's entry: the name first, then the address
    city_done: bool = False       # the address has its city line

    def empty(self) -> bool:
        """Nothing read into it yet (a heading alone doesn't count)."""
        return not (self.firm or self.header or self.head or self.address or self.names or self.role or
                    self.clients or self.phone or self.email or self.placeholder or self.pro_se)

    def only_header(self) -> bool:
        """Only lines that don't make an entry yet (unmarked lines, a head of office, a role, clients): no
        firm, address, attorney, contact or placeholder."""
        return not (self.firm or self.address or self.names or self.placeholder or self.pro_se or
                    self.phone or self.email)


def _pages_of(text: str) -> list[list[str]]:
    """The lines of each page (form feeds separate the pages)."""
    return [p.split("\n") for p in text.split("\f")]


def _strip_margin_numbers(lines: list[str]) -> list[str]:
    """Line numbers left in front of the text ("4 COUNSEL & COUNSEL, LLP"), as text recognition (OCR) reads a
    numbered page: removed when at least five lines start with a number of 1 to 28 and those numbers mostly go
    up, by 1 to 4 at a time (so that a street number, "12 Court Street", is not taken for one)."""
    hits = [(i, int(m.group(1))) for i, l in enumerate(lines) if (m := re.match(r"^\s*(\d{1,2})\s+(?=\S)", l))
            and 1 <= int(m.group(1)) <= 28]
    if len(hits) < 5:
        return lines
    ups = sum(1 for (_, a), (_, b) in zip(hits, hits[1:]) if 0 < b - a <= 4)
    if ups < 0.75 * (len(hits) - 1):
        return lines
    out = list(lines)
    for i, _ in hits:
        out[i] = re.sub(r"^\s*\d{1,2}\s+", "", out[i])
    return out


def _section(pages: list[list[str]], profile: Profile | None = None) -> list[tuple[str, int, bool]]:
    """The lines of the appearances as (text, blank lines before it, first line of a page). From the
    APPEARANCES (or PRESENT) heading on; without one, from the end of the caption and the judge's lines. On a
    later page with a heading of its own ("APPEARANCES (continued)"), what stands above it (the court and the
    caption, repeated) is left out. Ends at ALSO PRESENT."""
    flat: list[tuple[str, int, int]] = []  # (text, page, line)
    for p, lines in enumerate(pages):
        for i, l in enumerate(_strip_margin_numbers([x.rstrip() for x in lines])):
            flat.append((l.strip(), p, i))
    heads = [k for k, (l, _, _) in enumerate(flat) if APPEARANCES_RE.match(l)]
    if not heads:
        heads = [k for k, (l, _, _) in enumerate(flat) if PRESENT_RE.match(l)]
    if heads:
        start = heads[0] + 1
        later = {flat[k][1]: k for k in heads[1:]}  # the page's own heading: drop what stands above it
        keep = [k for k in range(start, len(flat)) if not (flat[k][1] in later and k < later[flat[k][1]])
                and k not in heads]
    else:
        first_atty = next((k for k, (l, _, _) in enumerate(flat) if (ATTY_RE.search(l) or FIRM_RE.search(l)) and
                           not X_LINE_RE.match(l) and _after_caption(flat, k)), None)
        if first_atty is None:
            return []
        marks = [k for k, (l, p, _) in enumerate(flat[:first_atty]) if X_LINE_RE.match(l) or
                 re.fullmatch(r"(?i)-?\s*against\s*-?|b\s*e\s*f\s*o\s*r\s*e\s*:?|h\s*e\s*l\s*d\s*:?|"
                              r"(?:the\s+)?honorable\b.*|j\s*u\s*s\s*t\s*i\s*c\s*e\b.*|.*\bj\.\s?s\.\s?c\.?", l)]
        start = marks[-1] + 1 if marks else 0
        keep = list(range(start, len(flat)))
    out: list[tuple[str, int, bool]] = []
    gap, page = 0, None
    for k in keep:
        l, p, _ = flat[k]
        if ALSO_PRESENT_RE.match(l):
            break
        entity = re.fullmatch(r"(?i)(?:l\.?l\.?p|p\.?l\.?l\.?c|l\.?l\.?c|p\.?\s?c)\.?,?", l)  # "LLP" on its own line
        if not l or (NOISE_RE.match(l) or CAPTION_RE.search(l) and _kind(l) != ROLE) and not ATTY_RE.search(l) \
                and not entity or mentions_reporter(profile, l):
            gap += 1
            continue
        out.append((l, gap, page is not None and p != page))
        gap, page = 0, p
    return out


def _split_lines(items: list[tuple[str, int, bool]]) -> list[tuple[str, int, bool]]:
    """Lines that hold two things, as two lines: a firm and its role ('COUNSEL & COUNSEL, LLP, Attorneys for the
    Plaintiff'), and a role with who appeared after a colon ('Appearing for the Plaintiff:  DANA SMITH, ESQ.';
    'For the People: Casey Stone' -> 'For the People:' and 'BY: Casey Stone')."""
    out: list[tuple[str, int, bool]] = []
    for line, gap, pb in items:
        m = FIRM_ROLE_RE.match(line)
        if m and _kind(m["firm"]) in (FIRM, AGENCY) and _kind(m["role"]) == ROLE:
            out += [(m["firm"].rstrip(" ,"), gap, pb), (m["role"], 0, False)]
            continue
        m = ROLE_COLON_RE.match(line)
        if m and _kind(line) == ROLE and _kind(m["role"]) == ROLE:
            rest = m["rest"]
            kind = _kind(rest)
            if kind == OTHER and looks_like_name(rest):
                rest, kind = "BY: " + rest, ATTY
            if kind in (ATTY, PLACEHOLDER, PRO_SE, FIRM, AGENCY):
                out += [(m["role"], gap, pb), (rest, 0, False)]
                continue
        out.append((line, gap, pb))
    return out


def _after_caption(flat, k) -> bool:
    """Line k stands after the caption's closing rule (or there is none on its page)."""
    page = flat[k][1]
    rules = [j for j, (l, p, _) in enumerate(flat) if p == page and X_LINE_RE.match(l)]
    return not rules or k > rules[-1]


def parse_appearances(text: str, tc: Callable[[str], str] = lambda s: s,
                      profile: Profile | None = None) -> list[Attorney]:
    """The firms, agencies and lone attorneys listed on a title page, one Attorney each (all of a firm's
    attorneys named in it, see the module's docstring), none of them ticked. text: the title page(s), pages
    separated by form feeds. tc: tidies an ALL-CAPS name (RegexExtractor.tc). profile: the reporter, whose own
    name is left out. "Unrepresented" and "No one appeared" become placeholder entries; a party appearing
    without an attorney (pro se) is an entry with their name."""
    items = _split_lines(_section(_pages_of(text), profile))
    if not items:
        return []
    gaps = sorted(g for _, g, pb in items[1:] if not pb)
    base = gaps[len(gaps) // 3] if gaps else 0   # the page's own line spacing (0: single spaced)
    base = min(base, 1)
    kinds = [_kind(l) for l, _, _ in items]
    first = {k: next((i for i, x in enumerate(kinds) if x == k), len(kinds)) for k in (ROLE, FIRM, AGENCY, ATTY,
                                                                                     OTHER, ADDRESS)}
    # roles stand before their firms ("Attorneys for the Plaintiff" / "COUNSEL & COUNSEL" / ...)
    role_first = first[ROLE] < min(first[FIRM], first[AGENCY], first[OTHER], first[ATTY], first[ADDRESS])

    entries: list[_Entry] = []
    cur = _Entry()
    heading = ""
    prev = ""

    def new() -> None:
        nonlocal cur
        if not cur.empty():
            entries.append(cur)
        cur = _Entry(heading=heading)

    loose = 0  # names just read from lines of their own (no "Esq.", no "BY:"), which may be the reporters'
    for (line, gap, _page_break), kind in zip(items, kinds):
        brk = gap > base  # a page break alone doesn't end an entry: its attorneys can be on the next page
        if REPORTER_RE.search(line):
            if cur.only_header():   # the reporters' names above their title
                cur = _Entry(heading=heading)
            elif loose and prev == ATTY and not gap:  # (after a gap, or the reporter's own name left out, the
                del cur.names[-loose:]                #  names above are the last firm's attorneys)
            prev, loose = "", 0
            continue
        if kind != OTHER:
            loose = 0
        # A role that goes on to the next line ("Attorneys for Poe, Hill, and" / "Example Health System")
        if prev == ROLE and kind in (OTHER, AGENCY) and not brk and (
                cur.role and CONNECTOR_END_RE.search(cur.role[-1]) or CONNECTOR_START_RE.match(line) or
                ((cur.firm or cur.header) and not cur.address and not cur.names)):
            cur.role.append(line)
            continue
        if kind == OTHER and re.fullmatch(r"(?i)[A-Za-z .'-]+:", line) and re.match(r"(?i)for\b", line):
            kind = ROLE
        if kind == ROLE and line.endswith(":") and re.match(r"(?i)for\b", line) and not cur.names:
            # "For Defendants:" heading a group of firms (sign-in sheets)
            new()
            heading = _role_text(line, tc)
            cur.heading = heading
            prev = ROLE
            cur.role = []
            continue

        if kind in (FIRM, AGENCY):
            continues = prev in (FIRM, AGENCY) and not brk and not cur.address and not cur.names and (
                CONNECTOR_END_RE.search(cur.firm[-1]) or CONNECTOR_START_RE.match(line) or kind == AGENCY
                or prev == AGENCY or re.match(r"(?i)^(?:L\.?L\.?P|P\.?L\.?L\.?C|P\.?C)\.?$", line))
            if continues:
                cur.firm.append(line)
            elif cur.firm or cur.address or (cur.names and (brk or cur.names_before_address)) or cur.placeholder \
                    or cur.pro_se:
                new()
                cur.firm.append(line)
            elif cur.header and prev == OTHER and not brk and (CONNECTOR_START_RE.match(line) or not cur.heading):
                # "CARTER BROOKS WILLOW" / "& LANE, LLP": the firm's name goes on. (Under a heading such as
                # "For Defendants:" the lines above a firm are its client's name, see _finish.)
                cur.firm += [cur.header.pop(), line]
            else:
                cur.firm.append(line)
        elif kind == HEAD:  # "HON. ...", the head of the office whose lines follow
            if not cur.only_header():
                new()
            cur.head.append(line)
        elif kind == ROLE:
            if cur.empty() or cur.only_header() and not cur.role:
                pass
            elif cur.role and (cur.names or cur.address or cur.firm) or (role_first and (cur.names or cur.address)) \
                    or (brk and cur.names and cur.role):
                new()
            cur.role.append(line)
        elif kind == ADDRESS:
            weak = _address_strength(line) == 1
            if weak and not cur.address and not cur.firm and not cur.names and not cur.header and not cur.pro_se:
                cur.header.append(line)  # "JOHN LANE" style: a firm's name with a street word, before its address
                kind = OTHER
            elif weak and brk and cur.city_done:
                new()  # "BROADWAY LEGAL GROUP" after a finished entry: the next firm, not more of the address
                cur.header.append(line)
                kind = OTHER
            elif cur.city_done and cur.names and brk:
                new()
                cur.address.append(line)
            elif cur.city_done and len(cur.address) >= 2 and (cur.firm or cur.header) and brk and not cur.names:
                new()  # a firm without attorneys, then another firm's address? keep it with a new entry
                cur.address.append(line)
            else:
                cur.address.append(line)
            if kind == ADDRESS:
                cur.names_before_address = cur.names_before_address or bool(cur.names)
                cur.city_done = cur.city_done or is_city_line(line)
        elif kind == ATTY:
            if cur.names and (cur.names_before_address and cur.address or brk and prev not in (ATTY, TITLE)) \
                    or cur.placeholder or cur.pro_se \
                    or (brk and cur.city_done and not cur.names and not re.match(r"(?i)^by\b", line)):
                new()  # (the last: "ROBIN EXAMPLE, ESQ." heading an entry of its own, after a firm without a BY)
            cur.names += parse_names(line, tc)
        elif kind == TITLE:
            pass
        elif kind == CONTACT:
            _contact(cur, line, profile)
        elif kind == PLACEHOLDER:
            if PRO_SE_RE.search(line) and cur.header and not (cur.firm or cur.names or cur.address):
                cur.pro_se = True  # "SAM POE" / "Pro Se"
            else:
                if not cur.empty() and not (cur.only_header() and not cur.header):
                    new()
                cur.placeholder = line.rstrip(".")
        elif kind == PRO_SE:
            if not cur.empty() and not (cur.only_header() and not cur.head):
                new()  # (a header is kept: "SAM POE" / "Defendant, Pro Se" is the party's own name)
            cur.pro_se = True
            rest = PRO_SE_RE.sub(" ", line)
            role = PARTY_WORDS_RE.search(rest)
            if role:
                cur.role.append(role.group(0).strip())
            rest = PARTY_WORDS_RE.sub(" ", re.sub(r"(?i)^\s*(?:by|appearing)\b\s*:?", "", rest))
            cur.names += [n for n in parse_names(rest, tc)]
        else:  # OTHER
            if prev in (ATTY, TITLE) and not brk and looks_like_name(line):
                more = parse_names(line, tc)  # "BY:" / "DANA SMITH", or the second name of "BY: A and" / "B"
                cur.names += more
                loose += len(more)
                kind = ATTY
            elif cur.address and not cur.city_done and prev == ADDRESS and not brk and len(line.split()) <= 4:
                cur.address.append(line)  # "Garden City" under "300 Garden City Plaza"
                cur.city_done = True
                kind = ADDRESS
            elif cur.names or cur.city_done or cur.placeholder or cur.pro_se or (cur.address and brk):
                new()
                cur.header.append(line)
            elif cur.firm and prev in (FIRM, AGENCY) and not brk and not cur.address and not cur.role and (
                    CONNECTOR_END_RE.search(cur.firm[-1]) or CONNECTOR_START_RE.match(line) or prev == AGENCY):
                cur.firm.append(line)  # "LAW OFFICES OF" / "DANA SMITH"; "District Attorney" / "Queens County"
                kind = FIRM
            elif cur.firm and not cur.address and not cur.role:
                cur.clients.append(line)  # a line after the firm before anything else: whom it represents
            else:
                cur.header.append(line)
        prev = kind
    new()

    out: list[Attorney] = []
    for e in entries:
        a = _finish(e, tc)
        if a is None:
            continue
        twin = next((b for b in out if b.firm and a.firm and firm_key(b.firm) == firm_key(a.firm)), None)
        if twin is None:
            out.append(a)
            continue
        # the same firm listed twice (for two clients): one entry
        names = twin.names() + [n for n in a.names() if not any(same_person(n, m) for m in twin.names())]
        twin.name = join_names(names)
        if a.party and a.party not in twin.party:
            twin.party = f"{twin.party}; {a.party}" if twin.party else a.party
        for f in ("address", "phone", "fax", "email"):
            setattr(twin, f, getattr(twin, f) or getattr(a, f))
    return out


def _contact(e: _Entry, line: str, profile: Profile | None) -> None:
    """A phone, fax or e-mail line of an entry: the first e-mail, phone and fax found are kept, and a number
    is a fax when the words just before it say so ('Fax:', 'F'). The reporter's own are skipped."""
    own_mail = (profile.email or "").lower() if profile else ""
    own_phone = re.sub(r"\D", "", profile.phone or "")[-10:] if profile else ""
    for m in EMAIL_RE.findall(line):
        if m.lower() != own_mail and not e.email:
            e.email = m
    if EMAIL_RE.search(line):
        return
    last = 0
    for ph in PHONE_RE.finditer(line):  # "Phone: (555) 555-0101  Fax: (555) 555-0102": each by the word before it
        label, last = line[last:ph.start()], ph.end()
        if "".join(ph.groups()) == own_phone:
            continue
        if re.search(r"(?i)\bfax\b|(?:^|\s)f\b", label):
            e.fax = e.fax or fmt_phone(ph)
        else:
            e.phone = e.phone or fmt_phone(ph)


def _role_text(line: str, tc: Callable[[str], str]) -> str:
    """'Attorneys for the Plaintiff' -> 'Plaintiff'; 'For the People' -> 'People'; 'Court Evaluator' as it is."""
    m = ROLE_RE.match(line) or MORE_ROLE_RE.match(line)
    s = next((g for g in (m.groups() if m else ()) if g), line) if m else line
    s = tc(s.strip(" .:,"))
    s = s[:1].upper() + s[1:]
    if re.search(r"(?i)\b(?:corp|inc|co|ltd|jr|sr|bros|assn|dept)$", s):  # "Example Metal & Glass Corp."
        s += "."
    if re.search(r"\b(?:[A-Za-z]\.)+[A-Za-z]$", s):  # "Sam Poe, M.D": the full stop was its own
        s += "."
    return s


def _finish(e: _Entry, tc: Callable[[str], str]) -> Attorney | None:
    """The Attorney of an entry, or None when it names no firm, office or person."""
    def tidy(lines: list[str]) -> str:
        """The lines as one name; in capitals all through, made Title Case as one ('... HAYES' / 'AND GREEN,
        LLP' -> '... Hayes and Green, LLP'), else line by line ('NEW YORK CITY POLICE DEPARTMENT' / 'Legal Bureau')."""
        s = " ".join(" ".join(lines).split()).strip(" ,;:")
        return tc(s) if is_mostly_upper(s) else " ".join(tc(" ".join(l.split())) for l in lines).strip(" ,;:")

    header = [h.rstrip(":").strip() for h in e.header]
    clients = list(e.clients)
    firm_lines = list(e.firm)
    if e.pro_se:
        names = e.names or [tc(h) for h in header if looks_like_name(h)]
        if not names:
            return Attorney(name=e.placeholder or "Pro se", party=_party(e, tc), source=SRC_REGEX, checked=False)
        party = _party(e, tc)
        party = f"{party}, pro se" if party else "Pro se"
        return Attorney(name=join_names(names), address=_address(e.address, tc), phone=e.phone, fax=e.fax,
                        email=e.email, party=party, source=SRC_REGEX, checked=False)
    if e.placeholder and not e.names and not firm_lines:
        return Attorney(name=e.placeholder, party=_party(e, tc) or e.heading, source=SRC_REGEX, checked=False)
    if firm_lines:
        clients = [h for h in header] + clients  # unmarked lines with a firm: its client (sign-in sheets)
    else:
        firm_lines = [h for h in header if not h.endswith(":")]
        clients = [h for h in header if h.endswith(":")] + clients
        if firm_lines and not (e.address or e.names or e.role):
            return None   # a line of the page that isn't counsel
    firm = tidy(firm_lines)
    if not firm and not e.names:
        return None
    party = _party(e, tc)
    client = " ".join(c.rstrip(":") for c in clients).strip(" ,:")
    if client and len(client) < 80 and not is_address(client):
        client = tidy([client])
        party = f"{party} ({client})" if party else client
    names = []
    for n in e.names:
        if not any(same_person(n, m) for m in names):
            names.append(n)
    return Attorney(name=join_names(names), firm=firm, address=_address(e.address, tc), phone=e.phone, fax=e.fax,
                    email=e.email, party=party, source=SRC_REGEX, checked=False)


def _party(e: _Entry, tc: Callable[[str], str]) -> str:
    """Whom the entry represents: its role lines ('Attorneys for Poe, Hill, and' + 'Example Health System'),
    else the heading above it ('For Defendants:')."""
    if not e.role:
        return e.heading
    first = _role_text(e.role[0], tc)
    rest = tc(" ".join(" ".join(e.role[1:]).split()))
    return " ".join(f"{first} {rest}".split()).strip(" ,;:")


def _address(lines: list[str], tc: Callable[[str], str]) -> str:
    """The address as lines (up to four: P.O. Box, street, suite, city), ALL CAPS made Title Case (tc)."""
    return "\n".join(tc(" ".join(l.split()).rstrip(" ,")) for l in lines[:4])
