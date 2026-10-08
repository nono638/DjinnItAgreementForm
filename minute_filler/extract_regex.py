"""Rule-based extraction of minute-agreement fields from plain text.

Every finding is a Candidate with a confidence in [0, 1]; merge.py picks the
winner and keeps the rest as alternatives for the user to choose from.
The transcript layout helpers here (strip_line_numbers, title_page_count) are
also used by runsheet.py and batch.py. A title page's APPEARANCES are read by
appearances.py; the rules for telling two entries are one firm or person
(dedupe_attorneys, same_entry, merge_entry) are also used by merge.py and batch.py.
find_dates is shared as well (merge.same_value, fill.date_ranges, batch.py, runsheet.py), and so
are DELIVERY_WORDS and looks_like_transcript (extract_llm.py), so that the rules and
the AI's answer are read alike.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date, timedelta

from .ingest import Ingested
from .models import Attorney, Candidate, Extraction, SRC_PDF, SRC_REGEX, firm_key, join_names, to_int
from .settings import Profile
from .takes import CREDENTIALS, body_pages

# ------------------------------------------------------------------ helpers

NY_COUNTIES = [
    "Albany", "Allegany", "Bronx", "Broome", "Cattaraugus", "Cayuga", "Chautauqua", "Chemung", "Chenango",
    "Clinton", "Columbia", "Cortland", "Delaware", "Dutchess", "Erie", "Essex", "Franklin", "Fulton", "Genesee",
    "Greene", "Hamilton", "Herkimer", "Jefferson", "Kings", "Lewis", "Livingston", "Madison", "Monroe",
    "Montgomery", "Nassau", "New York", "Niagara", "Oneida", "Onondaga", "Ontario", "Orange", "Orleans", "Oswego",
    "Otsego", "Putnam", "Queens", "Rensselaer", "Richmond", "Rockland", "St. Lawrence", "Saratoga", "Schenectady",
    "Schoharie", "Schuyler", "Seneca", "Steuben", "Suffolk", "Sullivan", "Tioga", "Tompkins", "Ulster", "Warren",
    "Washington", "Wayne", "Westchester", "Wyoming", "Yates",
]
BOROUGHS = {"brooklyn": "Kings", "manhattan": "New York", "staten island": "Richmond", "the bronx": "Bronx"}

# (heading, the court's name on the form). Tried in this order; the first that appears wins.
# Housing Court is a part of the Civil Court.
COURTS = [
    (r"SUPREME\s+COURT", "Supreme"),
    (r"CIVIL\s+COURT", "Civil"),
    (r"HOUSING\s+(?:COURT|PART)", "Civil"),
    (r"FAMILY\s+COURT", "Family"),
    (r"SURROGATE'?S\s+COURT", "Surrogate's"),
    (r"CRIMINAL\s+COURT", "Criminal"),
    (r"COUNTY\s+COURT", "County"),
    (r"DISTRICT\s+COURT", "District"),
    (r"COURT\s+OF\s+CLAIMS", "Court of Claims"),
]

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTH_RE = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

# smart_title keeps these as they are: KEEP_UPPER in capitals, SMALL_WORDS in lowercase (after the first word)
KEEP_UPPER = {"LLP", "PLLC", "LLC", "PC", "P.C.", "L.L.P.", "P.L.L.C.", "L.L.C.", "P.A.", "N.A.", "NY", "N.Y.",
              "NYC", "USA", "II", "III", "IV", "DDS", "MD", "CPA", "LP", "NYCHA", "MTA", "MSK", "NYU", "CUNY"}
SMALL_WORDS = {"and", "of", "the", "for", "in", "on", "at", "to", "a", "an", "v.", "vs.", "v", "vs", "de", "del"}
_CREDENTIAL_FORMS = {c.upper(): c for c in CREDENTIALS}  # 'PH.D.' -> 'Ph.D.' (see smart_title)

FIRM_RE = re.compile(
    r"(\bL\.?L\.?P\.?|\bP\.?L\.?L\.?C\.?|\bL\.?L\.?C\.?\b|\bP\.\s?C\.?|,\s*PC\b|\bP\.A\.|\bEsqs\.|\bAssociates\b|"
    r"\bLaw\s+(?:Office|Firm|Group)|\bLaw\s+Offices?\b|Attorneys?\s+at\s+Law|\bCorporation\s+Counsel\b|"
    r"\bLegal\s+Aid\b|\bLegal\s+Services?\b|\s&\s)", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]*(\d{3})[\s.-](\d{4})(?!\d)")
# The lookbehind keeps EMAIL_RE fast (linear) on long unbroken runs such as encoded attachments.
EMAIL_RE = re.compile(r"(?<![\w.+'-])[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
# "Dana Smith, Esq." / "BY: DANA SMITH, ESQ." -> the name
ESQ_RE = re.compile(r"(?:\bBY\s*:?\s*)?([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,4}),?\s+Esq\b\.?", re.I)
ROLE_RE = re.compile(r"^(?:attorneys?|counsel)\s+(?:for|to)\s+(?:the\s+)?(.+?)[.:,]?$", re.I)
SPECIAL_ROLE_RE = re.compile(
    r"^(court evaluator|guardian ad litem|law guardian|attorney for the child|mental hygiene legal service.*|"
    r"court[- ]appointed counsel|of counsel)$", re.I)
PARTY_HEADING_RE = re.compile(r"^for\s+(?:the\s+)?(plaintiffs?|defendants?|petitioners?|respondents?|"
                              r"claimants?|movants?|third[- ]party.*?)\s*:?$", re.I)
PLACEHOLDER_RE = re.compile(r"^(unrepresented|no one appeared|pro se|self[- ]represented|no appearance)\.?$", re.I)
GREETING_RE = re.compile(r"^(thanks?|thank you|regards|best|sincerely|cheers|very truly yours|respectfully)[,!.]?$", re.I)
ADDRESS_RE = re.compile(r"(^\d+[\w-]*\s+\w|\b(street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd|plaza|place|"
                        r"suite|floor|fl\.|broadway|drive|lane|parkway|court\s+st|p\.?o\.?\s+box)\b|"
                        r",\s*(NY|N\.Y\.|New York|NJ|New Jersey|CT)\b|\b\d{5}(?:-\d{4})?\s*$)", re.I)
# What makes a line an address even though it carries a firm's "&", "LLP" or "Law Office": a street
# number in front, a suite or floor, a state or a ZIP code. A street word alone ("Lane", "Plaza",
# "Broadway") does not: "Hill & Lane" and "Broadway Law Group, PLLC" are firms.
SURE_ADDRESS_RE = re.compile(r"(^\d+[\w-]*\s+\w|&\s*\d|\b(suite|floor|fl\.|p\.?o\.?\s+box)\b|"
                             r",\s*(NY|N\.Y\.|New York|NJ|New Jersey|CT)\b|\b\d{5}(?:-\d{4})?\s*$)", re.I)
# The "-----------X" rules that frame a court caption
X_LINE_RE = re.compile(r"^[-\s]*-{5,}[-\s]*X?\s*$|^X\s*[-\s]{5,}$")
# Lines that end a block of counsel: APPEARANCES / BEFORE / HELD / PRESENT (often letter-spaced),
# the judge's HONORABLE line, a bare page or line number, "COPY", "Proceedings" and the court reporter's line
HEADING_RE = re.compile(
    r"^(A\s*P\s*P\s*E\s*A\s*R\s*A\s*N\s*C\s*E\s*S|B\s*E\s*F\s*O\s*R\s*E|H\s*E\s*L\s*D|P\s*R\s*E\s*S\s*E\s*N\s*T)"
    r"\s*:?\s*$|^(THE\s+)?HONORABLE\b|^J\s+U\s+S\s+T\s+I\s+C\s+E|^\d{1,3}$|^COPY$|^Proceedings$|"
    r"(senior|official|principal)?\s*court\s+reporter\s*$", re.I)


def is_firm_line(line: str) -> bool:
    """A law firm's name: it has a firm's mark (&, LLP, PLLC, P.C., Law Office of ...) and is not plainly
    an address (see SURE_ADDRESS_RE)."""
    return bool(FIRM_RE.search(line)) and not SURE_ADDRESS_RE.search(line)


def _credential(core: str) -> str | None:
    """A doctor's or nurse's letters as they are written ('DPM', 'PH.D.' -> 'Ph.D.', 'PH.D' -> 'Ph.D'), or None."""
    up = core.upper()
    if up in _CREDENTIAL_FORMS:
        return _CREDENTIAL_FORMS[up]
    dotted = _CREDENTIAL_FORMS.get(up + ".")
    return dotted[:-1] if dotted else None


def smart_title(s: str) -> str:
    """Title-cases ALL-CAPS names while keeping initials, suffixes and small words sane. The letters after a
    name's comma keep their own case ('SAM POE, DPM' -> 'Sam Poe, DPM', 'PAT ROE, PH.D.' -> 'Pat Roe, Ph.D.'), but
    only where they end the name (the last word, or another comma or "and" after them): 'DO' anywhere else is a
    word, and 'ROE v. NGUYEN, DO THI MAI' a name."""
    parts = re.split(r"(\s+|-(?!C\b)|/)", s.strip())  # ("PA-C", "FNP-C" are one word)
    words = [i for i, w in enumerate(parts) if w and not re.fullmatch(r"\s+|-|/", w)]
    nxt = {i: parts[j] for i, j in zip(words, words[1:])}
    out, first, after_comma = [], True, False
    for i, w in enumerate(parts):
        if not w or re.fullmatch(r"\s+|-|/", w):
            out.append(w)
            continue
        core = w.strip(",;:()")
        pre, post = w[: w.find(core)] if core else "", w[w.find(core) + len(core):] if core else ""
        up = core.upper()
        ends = i not in nxt or w.endswith(",") or nxt[i].lower() in ("and", "&", "et")
        cred = _credential(core) if after_comma and not first and ends else None
        after_comma = w.endswith(",")
        if cred:
            new = cred
        elif up in KEEP_UPPER:
            new = up
        elif re.fullmatch(r"(?:[A-Z]\.){1,4}[A-Z]?\.?", core):  # initials like V.M. or N.
            new = core
        elif core.lower() in SMALL_WORDS and not first:
            new = core.lower()
        elif core.lower() == "esq.":
            new = "Esq."
        elif re.match(r"(?i)mc[a-z]{2,}", core):
            new = "Mc" + core[2:].capitalize()
        elif re.match(r"(?i)o'[a-z]+", core):
            new = "O'" + core[2:].capitalize()
        else:
            new = core.capitalize()
        out.append(pre + new + post)
        first = False
    return "".join(out)


def is_mostly_upper(s: str) -> bool:
    """True when over 85% of the letters are capitals, as in an ALL-CAPS name on a transcript cover page."""
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85


def fmt_date(y: int, m: int, d: int) -> str | None:
    """M/D/YYYY for a real calendar date in 1990-2100 (a two-digit year means 20xx); None otherwise."""
    if y < 100:
        y += 2000
    try:
        date(y, m, d)
    except ValueError:
        return None
    if not 1990 <= y <= 2100:
        return None
    return f"{m}/{d}/{y}"


def fmt_phone(m: re.Match) -> str:
    """Writes the three groups matched by PHONE_RE as (555) 555-0100."""
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}"


def guess_year(month: int, day: int, today: date | None = None) -> int | None:
    """Year of a date written without one: the most recent such day, since minutes are ordered
    after the proceeding - unless the day is coming up within the next two months."""
    today = today or date.today()
    for y in (today.year, today.year - 1):
        try:
            if date(y, month, day) <= today + timedelta(days=60):
                return y
        except ValueError:  # 2/29
            continue
    return None


DATE_JOIN_WORDS = r"(,|and|&|-|through|thru|to)"  # between the days of a list: "9/14, 9/15 and 9/16"
# What follows a small fraction, so that "1/2 day" and "3/4 page" are read as no date (see find_dates)
_FRACTION_UNIT = re.compile(r"\s*(?:day|hour|page|half|inch|of)s?\b", re.I)


def find_dates(text: str, allow_yearless: bool = False) -> list[tuple[int, int, str]]:
    """Returns (start, end, M/D/YYYY) for every date in text, in order ('Sept. 14, 2026', '9/14/26').

    allow_yearless (for e-mails): also '9/14' and 'March 3' without a year. Such a day takes the year of
    a dated day right after it in a list ('9/14 and 9/15/2025'), else the year from guess_year. A small
    fraction (1/2 to 7/8) before a unit is no date ('a 1/2 day hearing', '2/3 of the transcript'), while
    'the 9/28 hearing' is one. Nor is a lowercase 'may' or 'march' that is a verb ('the judge may 3 days later'):
    without a year, those need their capital ('May 3') or 'on', 'for', 'from', 'of' or 'dated' before
    them ('on may 3'). Other months may be lowercase ('sept 14 and sept 15').
    """
    found = []
    for m in re.finditer(MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text, re.I):
        v = fmt_date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        if v:
            found.append((m.start(), m.end(), v))
    for m in re.finditer(r"(?<![\d/.-])(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/-])", text):
        v = fmt_date(int(m.group(4)), int(m.group(1)), int(m.group(3)))
        if v:
            found.append((m.start(), m.end(), v))
    if allow_yearless:
        bare = [(m.start(), m.end(), int(m.group(1)), int(m.group(2)))
                for m in re.finditer(r"(?<![\d/.$-])(\d{1,2})/(\d{1,2})(?![\d/%-])", text)
                if not (0 < int(m.group(1)) < int(m.group(2)) <= 8 and _FRACTION_UNIT.match(text, m.end()))]
        bare += [(m.start(), m.end(), MONTHS[m.group(1)[:3].lower()], int(m.group(2))) for m in re.finditer(
            MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+\d{4})", text, re.I)
            if m.group(1)[0].isupper() or m.group(1).lower() not in ("may", "mar", "march")
            or re.search(r"(?i)\b(?:on|for|from|of|dated)\s+$", text[max(0, m.start() - 8):m.start()])]
        # From the last to the first, so that in "9/14, 9/15 and 9/16/2025" every day gets the year 2025.
        for start, end, mo, d in sorted(bare, reverse=True):
            after = min((f for f in found if f[0] >= end), default=None)
            if after and re.fullmatch(rf"\s*{DATE_JOIN_WORDS}\s*(and\s*)?", text[end:after[0]], re.I):
                y = int(after[2].rsplit("/", 1)[1])
            else:
                y = guess_year(mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None
            v = fmt_date(y, mo, d) if y else None
            if v:
                found.append((start, end, v))
    found.sort()
    return found


# ------------------------------------------------------- transcript layout

# A transcript's line number at the left margin: " 1    SUPREME COURT ..." or a number on a line of its own.
_LINE_NO = re.compile(r"^[ \t]{0,4}(\d{1,2})(?:[ \t]{2,}(?=\S)|[ \t]*$)")
# Signs read by title_page_count: the title says it goes on ("Appearances continued", "continued on the
# next page"); someone speaks (THE COURT:, MR. POE:, a Q or A line), so the proceedings have begun; a page
# lists counsel (Esq., "Attorneys for", "BY:").
_CONTINUES = re.compile(r"(?i)\b(?:title|appearances?|caption)\b[^\n]{0,40}\bcontinue[sd]?\b|"
                        r"\bcontinue[sd]?\s+on\s+(?:the\s+)?(?:next|following)\s+page")
_SPEAKER = re.compile(r"(?m)^\s*(?:\d{1,2}\s+)?(?:(?:THE\s+(?:COURT|WITNESS|CLERK|DEFENDANT|PLAINTIFF)|"
                      r"(?:MR|MS|MRS|DR)\.\s+[A-Z][A-Za-z'\- ]*|COURT\s+OFFICER)\s*:|[QA][.:]?[ \t]{2,}\S)")
_COUNSEL = re.compile(r"(?im)\besq\b|\battorneys?\s+for\b|^\s*(?:\d{1,2}\s+)?by\s*:")


def strip_line_numbers(text: str) -> tuple[str, bool]:
    """Removes the line numbers some transcripts print in front of the text of each line ("17    SMITH LAW
    GROUP"; with a single space, as OCR reads a page: _strip_ocr_line_numbers), so that the rules see the
    text alone. Returns (text, True) when the lines were numbered that way; text whose numbers stand on lines
    of their own, or that has none, comes back unchanged with False."""
    lines = text.split("\n")
    hits = [(i, m) for i, l in enumerate(lines) if (m := _LINE_NO.match(l))]
    nums = [int(m.group(1)) for _, m in hits]
    counted = any(nums[k:k + 5] == [1, 2, 3, 4, 5] for k in range(len(nums)))
    if not counted or not any(m.end() < len(lines[i]) for i, m in hits):
        return _strip_ocr_line_numbers(lines)
    for i, m in hits:
        lines[i] = lines[i][m.end():]
    return "\n".join(lines), True


# A transcript's line numbers on lines of their own, as PDF text often comes out: "1\n2\n3\n"
_BARE_LINE_NOS = re.compile(r"(?m)^\s*1\s*\n\s*2\s*\n\s*3\s*\n")


def looks_like_transcript(text: str) -> bool:
    """The text has a transcript's numbered lines, in front of the text or on lines of their own. Used where
    the document's kind isn't known, such as a .txt transcript the AI reads (extract_llm)."""
    return strip_line_numbers(text)[1] or bool(_BARE_LINE_NOS.search(text))


# A line number followed by one space, as text recognition (OCR) reads a numbered page: "9 COUNSEL & COUNSEL"
_OCR_LINE_NO = re.compile(r"^[ \t]{0,4}(\d{1,2})(?:[ \t]+(?=\S)|[ \t]*$)")


def _strip_ocr_line_numbers(lines: list[str]) -> tuple[str, bool]:
    """strip_line_numbers for numbers followed by a single space ("9 COUNSEL & COUNSEL, LLP"). A street number
    looks the same ("12 Court Street"), so a number is only taken for the line's when the page counts 1, 2, 3,
    4, 5 that way and the number goes on from the last one taken (by 1 to 4, from 1 on each page, up to 28)."""
    hits = [(i, int(m.group(1)), m) for i, l in enumerate(lines) if (m := _OCR_LINE_NO.match(l))]
    nums = [n for _, n, _ in hits]
    if not any(nums[k:k + 5] == [1, 2, 3, 4, 5] for k in range(len(nums))):
        return "\n".join(lines), False
    out, last, taken = list(lines), 0, 0
    page_starts = {i for i, l in enumerate(lines) if "\f" in l}
    hit_at = {i: (n, m) for i, n, m in hits}
    for i, l in enumerate(lines):
        if i in page_starts:
            last = 0
        if i in hit_at:
            n, m = hit_at[i]
            if 0 < n - last <= 4 and n <= 28:
                out[i] = l[m.end():]
                last, taken = n, taken + (m.end() < len(l))
    if taken < 5:  # (numbers on lines of their own, or no numbering: nothing to remove)
        return "\n".join(lines), False
    return "\n".join(out), True


def title_page_count(text: str) -> int:
    """How many pages the title of a transcript takes: its first page, plus the pages after it while the
    title goes on (more appearances than fit on one page). A page belongs to the title when the page before
    says so ("Title continues on next page") or when it lists counsel and nobody speaks on it yet.
    `text` has its pages separated by form feeds ("\\f"), as ingest gives them."""
    pages = text.split("\f")
    count = 1
    for prev, page in zip(pages, pages[1:]):
        if _SPEAKER.search(page) or not (_CONTINUES.search(prev) or _COUNSEL.search(page)):
            break
        count += 1
    return count


def title_pages(text: str) -> str:
    """The title of a transcript (see title_page_count), its pages joined."""
    return "\n\n".join(text.split("\f")[:title_page_count(text)])


# --------------------------------------------------------------- extractor

def tidy_name(s: str, title_case: bool = True) -> str:
    """Collapses spaces and, with title_case, turns ALL CAPS into Title Case (see smart_title)."""
    s = " ".join(s.split())
    return smart_title(s) if title_case and is_mostly_upper(s) else s


def normalize_caption(s: str, tidy=lambda part: part) -> str:
    """Writes 'X vs Y' and 'X -against- Y' as 'X v. Y', passing each side through tidy."""
    s = " ".join(s.split())
    s = re.sub(r"\s+(?:v|vs)\.?\s+", " v. ", s, flags=re.I)
    s = re.sub(r"\s+-?\s*against\s*-?\s+", " v. ", s, flags=re.I)
    return " v. ".join(tidy(p.strip(" ,")) for p in s.split(" v. "))


def mentions_reporter(profile: Profile | None, text: str) -> bool:
    """The reporter's own name appears in text (their signature block, not an attorney's)."""
    return bool(profile and profile.name) and profile.name.lower() in (text or "").lower()


def is_reporter(profile: Profile | None, name: str = "", email: str = "") -> bool:
    """name or email is exactly the reporter's own."""
    if not profile:
        return False
    return bool(profile.name and name and name.lower() == profile.name.lower()) or \
        bool(profile.email and email and email.lower() == profile.email.lower())


# A number with the letters OCR mistakes for digits: an index number or a date ('712345/2O21', '7l2345-2021',
# '5/22/2O26'), or the year after a month and day ('June 3, 2O26'). Only numbers: a word such as 'OIL/LIO' has
# too few digits (see _fix_ocr_digits).
_OCR_NUMBER = re.compile(r"(?<![\w/-])[\dOIl]{1,7}[/-][\dOIl]{2,4}(?:[/-][\dOIl]{2,4})?(?![\w/-])|"
                         r"(?<=\d, )[\dOIl]{4}(?!\w)|(?<=\d )[\dOIl]{4}(?!\w)")


def _fix_ocr_digits(m: re.Match) -> str:
    """O -> 0 and I/l -> 1 in a number _OCR_NUMBER matched, when it has three digits at least and not more
    than two such letters; anything else comes back as it was."""
    s = m.group(0)
    letters = sum(c in "OIl" for c in s)
    if sum(c.isdigit() for c in s) < 3 or letters == 0 or letters > 2:
        return s
    return s.replace("O", "0").replace("I", "1").replace("l", "1")


def norm_index(num, yr) -> str | None:
    """An index number as 'num/year' ('712345', '24' -> '712345/2024'); None unless the year is 1950-2100."""
    yr = int(yr)
    yr = yr + 2000 if yr < 100 else yr
    return f"{int(num)}/{yr}" if 1950 <= yr <= 2100 else None


# A US state on an address line, before a ZIP code: its name or two-letter abbreviation (capitals)
_STATE_RE = re.compile(
    r"\b(?:New\s+York|New\s+Jersey|Connecticut|Pennsylvania|Massachusetts|Florida|California|N\.?Y\.?|N\.?J\.?|"
    r"AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    r"OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b\.?")


def _zip_plus_4(text: str, m: re.Match) -> bool:
    """The number-year pair m matched is a ZIP+4 code: five digits, a dash and four ('10007-2015'), with a state
    right before it ('New York, New York 10007-2015', 'Kew Gardens, N.Y. 11415-2010', ', NY 10001-1234'). A state
    further back on the line proves nothing ('Supreme Court of New York, Queens County, 712345/2021'), nor does
    a capital word that happens to be a state's code before an index number ('THE MINUTES IN 712345/2021')."""
    if len(m.group(1)) != 5 or "/" in m.group(0):
        return False
    line_start = text.rfind("\n", 0, m.start()) + 1
    return bool(re.search(_STATE_RE.pattern + r"\s*,?\s*$", text[line_start:m.start()]))


# What asks for a delivery speed, as the rules read an e-mail (_order_terms) and as the AI's answer is checked
# (extract_llm): one table, so that both agree. The phrases are strict: "please reply immediately" and "my daily
# routine" ask for no speed; "immediate copy", "daily copy", "expedited" and "at your regular rate" do.
DELIVERY_WORDS = {
    "Expedited": r"(?i)\b(expedit\w*|rush)\b",
    "Daily": r"(?i)\b(daily\s+(copy|delivery|transcript)|overnight|next[- ]day)\b",
    "Immediate": r"(?i)\b(immediate(ly)?\s+(copy|delivery|transcript)|same[- ]day|hourly)\b",
    "Regular": r"(?i)\b(regular|standard|normal)\s+(delivery|turnaround|rate|copy)\b",
}
# How sure the rules are of a speed read with DELIVERY_WORDS
_SPEED_CONF = {"Expedited": 0.8, "Daily": 0.75, "Immediate": 0.75, "Regular": 0.7}


class RegexExtractor:
    """Proposes values for the form fields from the text of one document, using patterns only.

    Every finding is added with a confidence, so that a labelled index number outranks a stray one
    and merge.py can offer the runners-up as alternatives. The reporter's own e-mail, phone and name
    (the Profile) are never taken for the attorney's. extract() also keeps per-document facts on self
    (kind, is_invoice, is_transcript, is_caption_doc) for the field methods, so call it once per
    document, not from two threads at once.
    """
    def __init__(self, profile: Profile | None = None, title_case: bool = True):
        """profile: the reporter's own details, which are skipped when reading attorneys.
        title_case: turn ALL-CAPS names into Title Case.
        """
        self.profile = profile or Profile()
        self.title_case = title_case
        self.own_emails = {e.lower() for e in re.findall(EMAIL_RE, self.profile.email or "")}
        self.own_phones = {re.sub(r"\D", "", self.profile.phone or "")[-10:]} - {""}
        # per-document facts, set by extract() (a field method called on its own sees no document)
        self.kind = ""
        self.is_invoice = self.is_transcript = self.is_caption_doc = False

    def tc(self, s: str) -> str:
        """tidy_name with this extractor's title_case setting."""
        return tidy_name(s, self.title_case)

    # ----- entry point
    def extract(self, ing: Ingested) -> Extraction:
        """Runs every rule over one document and returns the candidates as an Extraction.

        First decides what kind of document it is (invoice, transcript, a court caption or none), then
        fills each field. The file name is searched too (index number, case, dates), at 90% of the
        confidence, because names like '5-22-2026 Roe v Poe - 712345-2024' are common.
        """
        ex = Extraction()
        text, numbered = strip_line_numbers(self._clean(ing.text))
        self.kind = ing.kind
        # an invoice says so on its first page: a line that starts "Invoice", and its number, "Bill To"/"To:" or
        # an amount. On an e-mail's page (a From:, Subject: or Sent: line) only its number or "Bill To" count:
        # the e-mail's own To: header and the prices it talks about don't make "Invoice to follow" an invoice
        page1 = text.split("\f")[0]
        evidence = r"\binvoice\s*(?:no\.?|number|#)\s*:?\s*\S*\d|^\s*bill\s+to\b"
        if not re.search(r"(?im)^(?:from|subject|sent)\s*:", page1):
            evidence += r"|^\s*to\s*:|\$\s*\d|\b(?:total|amount)\b"
        invoice = bool(re.search(r"(?im)^\s*invoice\b", page1)) and bool(re.search("(?im)" + evidence, page1))
        # a transcript numbers its lines. Numbers in front of the text win over the invoice's heading
        # (testimony wrapping onto a line that starts "invoice that you sent"); numbers on lines of their own
        # don't, since an invoice's item column can read 1, 2, 3 too
        self.is_transcript = numbered or (not invoice and bool(_BARE_LINE_NOS.search(text)))
        self.is_invoice = invoice and not self.is_transcript
        self.is_caption_doc = bool(re.search(r"(?i)\bappearances\b|a p p e a r|b e f o r e|\bBEFORE:|-against-|"
                                             r"\bindex\s+n", text))
        head = page1[:2500]
        ex.doc_kind = ("invoice" if self.is_invoice else "transcript" if self.is_transcript
                       else "email" if ing.kind == "email" else "text")

        self._index(text, ex)
        self._court_county(head, text, ex)
        self._part(head, text, ex)
        self._judge(head, text, ex)
        count = getattr(ing, "count", None)
        # a short transcript's word index is among the first pages: its "v." lines are no caption
        body = "\f".join(text.split("\f")[:count.pages]) if count is not None and count.index_pages else text
        self._case_name(body, ex, getattr(ing, "index_head", ""))
        self._dates(text, head, ex)
        self._proc_types(head, text, ex)
        self._order_terms(text, ex)
        self._pages(ing, text, ex)
        ex.attorneys = self._attorneys(text)

        if ing.kind in ("pdf", "image", "email", "text") and "." in ing.name:
            stem = re.sub(r"[_]+", " ", ing.name.rsplit(".", 1)[0])
            fx = Extraction()
            self._index(stem, fx, unlabeled_conf=0.6)
            self._inline_case(stem, fx, 0.55)
            for _, _, v in find_dates(stem):
                fx.add("dates", v, SRC_REGEX, 0.35, "from file name")
            for k, cands in fx.fields.items():
                for c in cands:
                    ex.add(k, c.value, c.source, c.confidence * 0.9, c.note or "from file name")
        return ex

    @staticmethod
    def _clean(text: str) -> str:
        """Straightens curly quotes and every kind of dash, undoes ligatures ('Oﬃces' -> 'Offices', NFKC),
        drops zero-width and replacement characters and trailing blanks, and keeps the page breaks (form
        feeds). In a number that looks like an index number or a date, the letters OCR reads for digits are
        put back ('712345/2O21' -> '712345/2021', '7l2345/2021' -> '712345/2021'); words are left alone."""
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
        text = text.replace("“", '"').replace("”", '"')
        text = re.sub("[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", text)   # hyphens, dashes, minus
        text = re.sub("[\u200b\u200c\u200d\u2060\ufeff\ufffd]", "", text)   # zero-width, replacement
        text = _OCR_NUMBER.sub(_fix_ocr_digits, text)
        # split("\n"), not splitlines(): keep the \f page separators
        return "\n".join(l.rstrip(" \t") for l in text.replace("\r", "").split("\n"))

    # ----- index number
    def _index(self, text: str, ex: Extraction, unlabeled_conf: float = 0.45) -> None:
        """Index numbers ('712345-2024', 'Index No. 712345/24', '712345 of 2024'), written as '712345/2024'.

        Confidence: 0.95 when labelled ('Index No. 712345-2024', 'Docket ...'), 0.8 after a bare 'No.',
        and unlabeled_conf for a lone number-year pair. A ZIP+4 code is not one: a pair right after a
        state ('New York, New York 10007-2015', 'N.Y. 11415-2010') is skipped.
        A letter the court writes after the year ('712345/2021E') is left out, as norm_index and the
        records write index numbers.
        """
        labeled = re.compile(
            r"\b(?:index|ind\.?|docket|file|calendar\s+index)\s*(?:no\.?|number|num\.?|#)?\s*[:.#]?\s*"
            r"(\d{3,7})\s*(?:[-/]|\s+of\s+)\s*(\d{4}|\d{2})[A-Z]?\b", re.I)
        for m in labeled.finditer(text):
            v = norm_index(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.95)
        for m in re.finditer(r"\bNo\.?\s*:?\s*(\d{4,7})\s*[-/]\s*((?:19|20)\d{2})[A-Z]?\b", text):
            v = norm_index(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.8)
        for m in re.finditer(r"(?<![\d$.,-])(\d{5,7})\s*[-/]\s*((?:19|20)\d{2})(?![\d-])", text):
            if _zip_plus_4(text, m):
                continue
            v = norm_index(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, unlabeled_conf)

    # ----- court and county
    def _court_county(self, head: str, text: str, ex: Extraction) -> None:
        """Court (Supreme, Civil, Family, ...) from COURTS, and county from 'County of X', 'X County',
        'X Supreme Court' or a borough name (Brooklyn means Kings). The top of the first page counts
        for more than the rest of the text.
        """
        for scope, conf in ((head, 0.9), (text, 0.6)):
            for pat, name in COURTS:
                if re.search(pat, scope, re.I):
                    ex.add("court", name, SRC_REGEX, conf)
                    break
        for scope, conf in ((head, 0.9), (text, 0.6)):
            for m in re.finditer(r"\bCOUNTY[ \t]+OF[ \t]+([A-Za-z.]+(?:[ \t]+[A-Z][a-z]+)?)|"
                                 r"\b([A-Za-z.]+(?:[ \t]+[A-Za-z]+)?)[ \t]+COUNTY\b", scope, re.I):
                raw = (m.group(1) or m.group(2)).strip()
                county = self._match_county(raw)
                if county:
                    ex.add("county", county, SRC_REGEX, conf)
            for m in re.finditer(r"\b(" + "|".join(NY_COUNTIES) + r")\s+(?:Supreme|Civil|Family|Criminal|County)\s+Court",
                                 scope, re.I):
                ex.add("county", self._match_county(m.group(1)) or "", SRC_REGEX, conf - 0.1)
            for b, county in BOROUGHS.items():
                if re.search(rf"\b{b}\b", scope, re.I):
                    ex.add("county", county, SRC_REGEX, conf - 0.3)

    @staticmethod
    def _match_county(raw: str) -> str | None:
        """The official spelling of a county for raw text such as 'QUEENS' or 'St Lawrence'.

        Allows a small typo for words of four or more letters; returns None if it is not a New York county.
        """
        words = raw.split()
        for cand in (" ".join(words[-2:]), words[-1] if words else ""):
            if not cand:
                continue
            for c in NY_COUNTIES:
                if c.lower() == cand.lower():
                    return c
        last = words[-1] if words else ""
        lower = {c.lower(): c for c in NY_COUNTIES}
        close = difflib.get_close_matches(last.lower(), list(lower), n=1, cutoff=0.8)
        return lower[close[0]] if close and len(last) >= 4 else None

    # ----- part
    def _part(self, head: str, text: str, ex: Extraction) -> None:
        """Part, either numbered ('PART 25', 'Part TR-3') or a letter code ('Part MDP').

        Letter codes must be written in capitals so that 'part of the record' is not read as a part, and so
        must the letters in front of a number ('Part TR-3': 'part of 25 pages' is no 'Part OF25'). A lowercase
        'part 12' counts only when labelled ('part: 12', 'part no. 12') or, in an e-mail, when 'of' doesn't
        follow the number ('judge lopez, part 7.' yes; 'send part 2 of the transcript' no). An e-mail's
        'Part 12' is trusted a little less than a court document's.
        """
        # numbered parts ("PART 25", "Part: 53", "Part TR-3"): the word, its label and the number
        numbered = re.compile(r"\b(?:IAS\s+|TRIAL\s+|TAP\s+)?(PART)\s*(No\.?|#|:)?\s*[:#]?\s*"
                              r"((?:(?-i:[A-Z]{1,4})[- ]?)?\d{1,3}[A-Z]?)\b", re.I)
        # letter parts ("PART MDP", "Part: TAP-A") - the code itself in capitals
        lettered = re.compile(r"\b(?:PART|Part)(?:\s+No\.?)?[ \t]*[:#]?[ \t]*([A-Z]{1,6}(?:-[A-Z0-9]{1,3})?)\b(?![a-z])")
        # capital words that follow PART in ALL-CAPS text without being one ("PART OF THE RECORD")
        not_a_part = {"OF", "THE", "AND", "IN", "TO", "A", "AN", "IS", "IT", "ON", "FOR", "AS", "OR", "BY", "AT", "NO"}
        is_email = self.kind in ("email", "text")
        for scope, conf in ((head, 0.9), (text, 0.7)):
            for m in numbered.finditer(scope):
                word, label, val = m.group(1), m.group(2), m.group(3).upper().replace(" ", "")
                letters = re.match(r"[A-Z]+", val)
                if letters and letters.group(0) in not_a_part:
                    continue
                if label:
                    part_conf = conf
                elif word[0] == "P":
                    part_conf = conf - 0.15 if is_email else conf
                elif is_email and not re.match(r"\s+of\b", scope[m.end():m.end() + 4]):
                    part_conf = 0.5  # "part 7." in a lowercase e-mail
                else:
                    continue  # "part 2 of the transcript", "part 12 of the record" in testimony
                ex.add("part", val, SRC_REGEX, part_conf)
            for m in lettered.finditer(scope):
                if m.group(1) not in not_a_part:
                    ex.add("part", m.group(1), SRC_REGEX, conf - 0.05)

    # ----- judge
    def _judge(self, head: str, text: str, ex: Extraction) -> None:
        """Judge from 'HONORABLE X', 'Judge: X', 'X, J.S.C.' or 'before Justice X'.

        Informal lowercase mentions ('justice smith', common in e-mails) are accepted at low confidence.
        Filler words at either end are trimmed ('Justice Lane Part 12' -> 'Lane'), a possessive goes ('before
        Judge Lane's part' -> 'Lane'), and the top of the first page counts for more.
        """
        stop = r"(?=\s*(?:,?\s*J\.?S\.?C\.?|,?\s*J\.?C\.?C\.?|\bis\s+presiding|\bpresiding|\n|$|,|;|\())"
        name = r"([A-Z][A-Za-z'\-]*\.?(?:[ \t]+(?:[A-Z]\.|[A-Z][A-Za-z'\-]+)){0,4})"
        pats = [
            (rf"\b(?:THE\s+)?(?:HONORABLE|HON\.?)[ \t]+(?:JUSTICE[ \t]+|JUDGE[ \t]+)?{name}{stop}", 0.9, re.I),
            (rf"\bJudge\s*:\s*{name}", 0.9, 0),
            (rf"\bJustice\s*:\s*{name}", 0.9, 0),
            (rf"{name},?\s+J\.\s?S\.\s?C\.", 0.85, 0),
            (rf"\b(?:before|by|from|with)\s+(?:the\s+)?(?:Justice|Judge)[ \t]+{name}", 0.8, 0),
            (rf"\b(?:Justice|Judge)[ \t]+{name}", 0.65, 0),
            (r"\b(?:justice|judge|hon\.?)[ \t]+([a-z][a-z'\-]+)\b", 0.45, re.I),  # informal e-mails
        ]
        bad = re.compile(r"^(of|the|is|and|for|in|on|at|presiding|justice|judge|supreme|court|part|ias|tap|trial|"
                         r"term|index|room|courtroom|no|j|s|c)$", re.I)
        for scope_i, scope in enumerate((head, text)):
            for pat, conf, flags in pats:
                for m in re.finditer(pat, scope, flags):
                    raw = re.sub(r"'s\b", "", m.group(1), flags=re.I).strip(" .,")  # "Lane's part" -> "Lane"
                    words = raw.split()
                    while words and bad.match(words[-1].strip(".")):
                        words.pop()
                    if not words or bad.match(words[0].strip(".")):
                        continue
                    val = self.tc(" ".join(words))
                    if val.islower():
                        val = smart_title(val.upper())
                    ex.add("judge", val, SRC_REGEX, conf if scope_i == 0 else conf - 0.1)

    # ----- case name
    def _case_name(self, text: str, ex: Extraction, index_head: str = "") -> None:
        """Case name, read several ways: a 'Title:', 'Caption:' or 'Re:' line, 'Matter of ...', the court
        caption, the heading of the word index printed after a transcript (index_head, see takes.index_heading),
        and 'X v. Y' anywhere in the text. Lowercase 'smith v jones' is only trusted, weakly, in e-mails.
        What a subject line adds after the caption is cut off, as _inline_case cuts it: 'Re: Jane Roe v. Sam
        Poe, Index No. 712345/2021, Part 12' -> 'Jane Roe v. Sam Poe'.
        The readings are then reconciled: a name that two of the first four find (the label line, Matter of,
        the caption, the index heading: each reads another part of the document) is surer, +0.1. A name found
        only in the running text doesn't count for that, as it may be the same line read twice.
        """
        readings: list[tuple[str, Extraction]] = []

        def reading(name: str) -> Extraction:
            r = Extraction()
            readings.append((name, r))
            return r
        label = reading("label")
        labelled: list[tuple[int, int]] = []  # the label lines: a 'Matter of' on one is the label's reading
        for m in re.finditer(r"(?im)^\s*(?:title|case(?:\s+name)?|caption|re)\s*:\s*(.+)$", text):
            labelled.append(m.span())
            val = re.split(r"(?i)\s*(?:,|\s-)\s*(?=(?:index|part|judge|justice|minutes|transcripts?|dated|"
                           r"before|on\s+\d)\b|ind\.)", m.group(1).strip())[0].strip(" ,-")
            if re.search(r"\sv\.?s?\.?\s|\bmatter of\b|\bagainst\b", val, re.I):
                label.add("case_name", self._norm_case(val), SRC_REGEX, 0.9)
            else:
                self._inline_case(val, label, 0.75)
        matter = reading("matter")
        # (the party starts with a capital: testimony's "as a matter of law" is no case)
        for m in re.finditer(r"(?i:\b(?:IN\s+THE\s+)?MATTER\s+OF\s*(?:THE\s+)?:?)\s*"
                             r"(?!(?i:law|fact|course|record|right|time|principle)\b)([A-Z][^\n,]*)", text):
            if any(start <= m.start() < end for start, end in labelled):
                continue
            party = m.group(1).strip(" ,.:")
            if party and len(party) < 90:
                matter.add("case_name", "Matter of " + self.tc(party), SRC_REGEX, 0.85)
        self._caption(text, reading("caption"))
        self._index_heading(index_head, reading("index heading"))
        running = reading("text")
        self._inline_case(text, running, 0.6)
        if self.kind in ("email", "text"):  # lowercase "smith v jones"
            for m in re.finditer(r"\b([a-z][\w.'&-]+)\s+(?:v|vs)\.?\s+([a-z][\w.'&-]+)\b", text, re.I):
                left, right = m.group(1), m.group(2)
                if left.lower() not in ("re", "the", "for", "of", "in", "minutes", "transcript"):
                    running.add("case_name", f"{smart_title(left.upper())} v. {smart_title(right.upper())}",
                                SRC_REGEX, 0.4)
        found: dict[str, list[tuple[str, Candidate]]] = {}
        for name, r in readings:
            for c in r.fields.get("case_name", []):
                found.setdefault(re.sub(r"[^a-z0-9]", "", c.value.lower()), []).append((name, c))
        for hits in found.values():
            best = max((c for _, c in hits), key=lambda c: (c.confidence, len(c.value)))
            sure = len({name for name, _ in hits if name != "text"}) >= 2
            ex.add("case_name", best.value, SRC_REGEX, min(0.99, best.confidence + 0.1) if sure else best.confidence,
                   best.note)

    def _index_heading(self, head: str, ex: Extraction) -> None:
        """The case as the word index's heading names it, at 0.75: 'JANE ROE v.' over 'SAM POE, DPM' (the 'v.'
        ending the first line, or alone between them, or '-against-'), or 'JANE ROE v. SAM POE' on one line (what
        stands beside it after a '|' or a wide gap left out, on either side: 'Roe v. Poe | Word Index', 'WORD
        INDEX      ROE v. POE'). A date is no party."""
        vs = r"(?:v\.?|vs\.?|-?\s*against\s*-?)"
        beside = r"\s+[|•·]\s+|\s{2,}"

        def case_part(line: str) -> str:
            """The part of a line, between '|'s or wide gaps, that has the 'v.' ('WORD INDEX    JANE ROE v.'),
            else its first part."""
            parts = [p for p in re.split(beside, line.strip()) if p.strip()]
            return next((p for p in parts if re.search(rf"(?:^|[\s,]){vs}(?:\s|$)", p, re.I)), parts[0])
        lines = [case_part(l) for l in self._clean(head).splitlines() if l.strip()]
        for i, line in enumerate(lines):
            left = right = ""
            if m := re.fullmatch(rf"(.+?)[\s,]+{vs}", line, re.I):
                left, right = m.group(1), lines[i + 1] if i + 1 < len(lines) else ""
            elif re.fullmatch(vs, line, re.I) and 0 < i < len(lines) - 1:
                left, right = lines[i - 1], lines[i + 1]
            elif m := re.fullmatch(rf"(.+?)\s+{vs}\s+(.+)", line, re.I):
                left, right = m.group(1), m.group(2)
            left = re.split(beside, left)[-1].strip(" ,")
            right = re.split(beside, right)[0].strip(" ,")
            if left and right and all(len(s.split()) <= 12 and s[:1].isalpha() and not find_dates(s)
                                      for s in (left, right)):
                ex.add("case_name", f"{self._tidy_side(left)} v. {self._tidy_side(right)}", SRC_REGEX, 0.75,
                       "the word index's heading")
                return

    def _tidy_side(self, s: str) -> str:
        """tc, and a side in capitals but for 'et al.' and the like too: 'SAM POE, DPM, et al.' -> 'Sam Poe, DPM, et
        al.' (smart_title of the whole side, so the letters after the comma and the small words keep their case).
        A side written in mixed case otherwise stays as written ('ABC Holding Corp.', 'Sam Poe, DO')."""
        if not self.title_case or is_mostly_upper(s):
            return self.tc(s)
        words = s.split()
        if not all(w.isupper() or w.strip(",.").lower() in ("et", "al", "ano", "and", "&") for w in words):
            return " ".join(words)
        titled = smart_title(" ".join(words)).split()
        return " ".join(t if len(w) > 1 and w.isupper() else w for w, t in zip(words, titled))

    def _norm_case(self, s: str) -> str:
        """normalize_caption, tidying each side with tc."""
        return normalize_caption(s, self.tc)

    def _inline_case(self, text: str, ex: Extraction, conf: float) -> None:
        """Finds 'Party v. Party' in running text or a file name and adds each hit at confidence conf.

        Lead-in words ('Re:', 'Minutes for') and trailing ones ('Index 123', 'on 9/14') are cut off the
        two parties.
        """
        word = r"(?:[A-Z][A-Za-z.'&\-]*|&|and|of|the)"
        pat = rf"((?:[A-Z][A-Za-z.'&\-]*)(?:[ \t]+{word}){{0,6}})[ \t]+(?:v|vs|V|VS)\.?[ \t]+((?:[A-Z][A-Za-z.'&\-]*)(?:[ \t]+{word}){{0,6}})"
        lead = re.compile(r"^(?:(?:re|fw|fwd|subject|minutes|transcripts?|order(?:ing)?|for|in|the|of|from|case|"
                          r"request|requesting|copy|copies|of|matter|hi|hello|dear)\b[:\s]*)+", re.I)
        for m in re.finditer(pat, text):
            left = lead.sub("", m.group(1)).strip()
            right = re.sub(r"\s+(?:and|of|the|&)$", "", m.group(2)).strip()
            right = re.split(r"\s+(?:Index|Ind|Part|Judge|Justice|on|before|in|for|dated)\b", right)[0]
            if left and right:
                ex.add("case_name", self._norm_case(f"{left} v. {right}"), SRC_REGEX, conf)

    def _caption(self, text: str, ex: Extraction) -> None:
        """Reads the court caption: the lines above and below a line that says 'against' or 'v.'.

        That line may carry the caption's right-hand column ('-against-        Index No. 712345/2021'), as
        every other line of it may. Takes up to eight lines each way. Going up it stops at the court heading
        or an X rule line and skips party roles ('Plaintiffs,'); going down it stops at those and at the first
        party role. Index numbers, a line number left on a line of its own and the like are skipped. Adds the
        full caption (0.85) and a short form with 'et al.' (0.55).
        """
        lines = text.splitlines()
        role = re.compile(r"^(?:[-\s]*)(plaintiffs?|defendants?|petitioners?|respondents?|claimants?|appellants?|"
                          r"appellees?|third[- ]party\s+\w+)[,.;:\s-]*(?:and\s*)?$", re.I)
        noise = re.compile(r"(?i)^(index\s*(no\.?|number|#)?\s*:?|\d{3,7}\s*[-/]\s*\d{2,4}|cal\.?\s*no\..*|"
                           r"attorneys?\.?|jury\s+trial|bench\s+trial|trial|hearing|motion|x|-+|\d{1,3})$")
        stopper = re.compile(r"(?i)(\bcourt\b|county\s+of|\bpart\s+\d|^-{5,}|x\s*$|cal\.\s*no|supreme|state of new york)")
        # (with one space only, a column to the right is known by its label: '-against- Index No. 712345/2021')
        against = re.compile(r"\s*-?\s*(?:against|vs?\.?)\s*-?\s*"
                             r"(?:\s(?:index\b|ind\.|no\.|case\b|docket\b|cal\b|calendar\b|#).*)?", re.I)
        for i, line in enumerate(lines):
            if not against.fullmatch(self._caption_side(line)):
                continue
            left, j = [], i - 1
            while j >= 0 and len(left) < 8:
                l = self._caption_side(lines[j])
                if l and (stopper.search(l) or X_LINE_RE.match(l)):
                    break
                if l and not role.match(l) and not noise.match(l):
                    left.insert(0, l)
                j -= 1
            right, j = [], i + 1
            while j < len(lines) and len(right) < 8:
                l = self._caption_side(lines[j])
                if l and (role.match(l) or X_LINE_RE.match(l) or stopper.search(l)):
                    break
                if l and not noise.match(l):
                    right.append(l)
                j += 1
            if left and right:
                lt = re.sub(r"^for\s+", "", " ".join(left), flags=re.I).strip(" ,")
                rt = " ".join(right).strip(" ,")
                full = f"{self.tc(lt)} v. {self.tc(rt)}"
                ex.add("case_name", full, SRC_REGEX, 0.85)
                short = self._short_caption(self.tc(lt), self.tc(rt))
                if short != full:
                    ex.add("case_name", short, SRC_REGEX, 0.55, "short form")

    @staticmethod
    def _caption_side(line: str) -> str:
        """A caption line without what stands in the column to its right ('Plaintiffs,       INDEX NO.')."""
        return re.split(r"\s{4,}", line.strip())[0]

    @staticmethod
    def _split_parties(s: str) -> list[str]:
        """Splits a list of parties on commas, 'and' and semicolons.

        Company suffixes and titles (Inc., LLC, Jr., 'as trustee') stay with the party they belong to.
        """
        pieces = re.split(r",\s+(?=[A-Z])|\s+and\s+(?=[A-Z])|\s*;\s*", s)
        parties = []
        suffix = re.compile(r"^(Inc|LLC|L\.L\.C|Corp|Co|P\.C|LLP|Ltd|N\.A|Jr|Sr|II|III|as\b.*|et al)\.?$", re.I)
        for p in pieces:
            if parties and (suffix.match(p.strip()) or len(p.split()) == 1 and p[:1].isupper() and len(p) < 5):
                parties[-1] += ", " + p
            else:
                parties.append(p)
        return [p.strip(" ,") for p in parties if p.strip(" ,")]

    def _short_caption(self, left: str, right: str) -> str:
        """The first party on each side with 'et al.' added when there are more, e.g. 'Roe, et al. v. Poe'."""
        lp, rp = self._split_parties(left), self._split_parties(right)
        l = lp[0] + (", et al." if len(lp) > 1 else "") if lp else left
        r = rp[0] + (", et al." if len(rp) > 1 else "") if rp else right
        return f"{l} v. {r}"

    # ----- dates
    def _dates(self, text: str, head: str, ex: Extraction) -> None:
        """Dates of the minutes, each with a confidence that depends on its surroundings.

        Highest: after a label such as 'Date of proceedings:' or 'held on'. Next: a date alone on one of
        the first lines of a court document. Then a date after 'on', 'from' or 'for', then any other date.
        E-mails and other plain text skip 'Sent:', 'Date:', 'Received:' and 'On ... wrote:' lines and allow
        dates without a year. Runs of dates joined by 'and', commas or 'through' also become one multi-date
        candidate, and an e-mail naming two to six different dates gets an 'all dates in the text' candidate.
        """
        is_email = self.kind in ("email", "text")
        body = text
        if is_email:
            body = "\n".join(l for l in text.splitlines()
                             # "Sent: ..." / "Date: ..." headers, not "Date of proceedings: ..."
                             if not re.match(r"(?i)^\s*((sent|date|received)\s*:|on .* wrote\s*:?)", l))
        labeled = re.compile(r"(?i)(date\(?s?\)?\s+of\s+(?:the\s+)?(?:proceedings?|minutes|hearing|trial)|"
                             r"proceedings?\s+(?:held\s+)?on|dates?\s*:|held\s+on|minutes\s+(?:from|of|for)|"
                             r"transcripts?\s+(?:from|of|for)|appear(?:ed|ance)\s+on|cal\.?\s*no\.?)\s*:?\s*$")
        all_dates = find_dates(body, allow_yearless=is_email)
        for start, end, v in all_dates:
            before = body[max(0, start - 60): start]
            line_start = body.rfind("\n", 0, start) + 1
            line_end = body.find("\n", end)
            line = body[line_start: line_end if line_end != -1 else len(body)].strip()
            if labeled.search(before.split("\n")[-1] + " ") or labeled.search(before):
                conf = 0.95 if re.search(r"(?i)proceeding|cal\.?\s*no", before) else 0.85
            elif re.fullmatch(r"[A-Za-z]*,?\s*" + re.escape(body[start:end]) + r"\.?", line) and start < len(head) + 20:
                conf = 0.9 if self.is_caption_doc and not self.is_invoice else 0.45
            elif re.search(r"(?i)\b(on|dated|and|&|of|from|for)\s*$", before):
                conf = 0.55 if is_email else 0.3
            else:
                conf = 0.4 if is_email else 0.25
            ex.add("dates", v, SRC_REGEX, conf)
        # "on 9/14 and 9/15/2026", "March 3, 4 & 5": runs of dates joined by and / , / & / through
        run: list[str] = []
        for k, (start, end, v) in enumerate(all_dates + [(len(body) + 99, 0, "")]):
            joined = k and re.fullmatch(rf"\s*{DATE_JOIN_WORDS}?\s*(and\s*)?",
                                        body[all_dates[k - 1][1]:start], re.I)
            if run and joined:
                run.append(v)
                continue
            if len(set(run)) >= 2:
                ex.add("dates", ", ".join(dict.fromkeys(run)), SRC_REGEX, 0.75, "consecutive dates")
            run = [v]
        if is_email:  # a request for several days of minutes
            uniq = list(dict.fromkeys(v for _, _, v in all_dates))
            if 2 <= len(uniq) <= 6:
                ex.add("dates", ", ".join(uniq), SRC_REGEX, 0.5, "all dates in the text")

    # ----- proceeding types
    # Words that tick each proceeding type box on the form
    PROC_PATTERNS = {
        "Trial": r"\b(?:jury|bench|non-?jury)\s+trial\b|\btrial\b",
        "Hearing": r"\bhearing\b|appoint(?:ment of)?\s+a\s+guardian|\bguardianship\b|article\s+81|\btraverse\b|"
                   r"\b(?:frye|mapp|huntley|dunaway|sandoval|wade|fact-?finding)\b",
        "Application": r"order\s+to\s+show\s+cause|\bmotion\b|\bapplication\b|oral\s+argument",
        "Sentence": r"\bsentenc(?:e|ing)\b",
        "Plea": r"\bplea\b|\bpleads?\s+guilty\b",
        "Arraignment": r"\barraign(?:ment|ed)?\b",
    }
    # Labels offered for "Other (specify)", from e-mails only
    OTHER_PATTERNS = {
        "Jury selection": r"jury\s+selection|voir\s+dire",
        "Summations": r"\bsummations?\b|closing\s+arguments?",
        "Jury charge": r"jury\s+charge|\bthe\s+charge\b",
        "Verdict": r"\bverdict\b",
        "Openings": r"opening\s+statements?|\bopenings\b",
        "Inquest": r"\binquest\b",
        "Conference": r"\bconference\b",
        "Decision": r"\bdecision\b",
    }

    def _proc_types(self, head: str, text: str, ex: Extraction) -> None:
        """Ticks the types of proceeding (Trial, Hearing, Application, Sentence, Plea, Arraignment).

        The top of a court document counts most; an e-mail's whole text is trusted. Invoices are skipped.
        E-mails may also suggest a label for 'Other' (Jury selection, Verdict, ...).
        """
        is_email = self.kind in ("email", "text")
        if self.is_invoice:
            return
        scopes = [(text, 0.8)] if is_email else [(head[:1600], 0.9), (text, 0.4)]
        for scope, conf in scopes:
            for ptype, pat in self.PROC_PATTERNS.items():
                if re.search(pat, scope, re.I):
                    ex.add_proc(ptype, conf)
        if is_email:
            for label, pat in self.OTHER_PATTERNS.items():
                if re.search(pat, text, re.I):
                    ex.add("proc_other", label, SRC_REGEX, 0.6)

    # ----- delivery, copies, rate
    def _order_terms(self, text: str, ex: Extraction) -> None:
        """What was ordered, from the wording of an e-mail: delivery speed (the phrases of DELIVERY_WORDS:
        'expedited', 'daily copy', 'immediate copy', 'regular rate'), number of copies ('original and 2
        copies') and a rate per page. Each speed found keeps the words that asked for it as its note
        ('daily copy').

        On an invoice it reads the totals it lists per speed ('Regular Rate: $94.50', 'Expedited Rate: ...');
        merge.apply_defaults works the page count out from them.
        """
        if self.kind in ("email", "text") and not self.is_invoice:
            # (the words found go with the speed: the window quotes them when it asks which speed the job is)
            for speed, pattern in DELIVERY_WORDS.items():
                m = re.search(pattern, text)
                if m:
                    ex.add("delivery", speed, SRC_REGEX, _SPEED_CONF[speed], note=" ".join(m.group(0).split()))
            nums = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "a": 1, "an": 1}
            m = re.search(r"(?i)\boriginal\s+(?:and|&|\+)\s+(\d+|one|two|three|four|five|a)\s+cop", text)
            if m:
                ex.add("copies", str(nums.get(m.group(1).lower(), m.group(1))), SRC_REGEX, 0.8)
            m = re.search(r"(?i)\b(\d+|one|two|three|four|five)\s+(?:certified\s+|additional\s+)?cop(?:y|ies)\b", text)
            if m:
                ex.add("copies", str(nums.get(m.group(1).lower(), m.group(1))), SRC_REGEX, 0.7)
            m = re.search(r"\$\s*(\d+\.\d{2})\s*(?:/|per)\s*page", text, re.I)
            if m:
                ex.add("rate", m.group(1), SRC_REGEX, 0.8)
        if self.is_invoice:
            for label, key in (("Regular", "invoice_regular"), ("Expedited", "invoice_expedited")):
                m = re.search(label + r"\s+Rate\s*:?\s*\$?\s*([\d,]+\.\d{2})", text, re.I)
                if m:
                    ex.add(key, m.group(1).replace(",", ""), SRC_REGEX, 0.9)

    # ----- pages
    def _pages(self, ing: Ingested, text: str, ex: Extraction) -> None:
        """Estimated pages: the page count of a transcript PDF (noting its page numbers, such as
        'transcript pages 358-380', when it does not start at 1), or 'N pages' written in an e-mail
        ('about 1,250 pages' -> 1250). A range of pages in an e-mail ('pages 10-25', 'pp. 10 to 25') counts
        them, 16, at a lower confidence; a single page ('p. 10') is not a count. 'expect 30-40 pages' is an
        estimate, the larger number: 40.
        A transcript's count leaves out the word index printed after it, and is read four ways and reconciled
        (takes.count_pages): when the readings disagree or the index looks too long, its confidence is lower and
        the field is marked for review (batch._all_pages offers the other counts). It is counted, not read from
        the words, so its source is SRC_PDF (the "PDF" badge), not SRC_REGEX.
        """
        if ing.kind == "pdf" and self.is_transcript and ing.page_count:
            count = getattr(ing, "count", None)
            pages = body_pages(ing.marks, ing.page_count, count)  # without the word index printed after it
            note = ""
            if ing.first_page_no and ing.first_page_no > 1:
                note = f"transcript pages {ing.first_page_no}-{ing.first_page_no + pages - 1}"
            index = count.index_pages if count is not None else ing.page_count - pages
            if index > 0:
                note = (note or f"{pages} transcript pages") + \
                    f", not counting {index} page(s) after the transcript (word index)"
            if count is not None and (count.warning or count.note):
                note = count.warning or count.note
            ex.add("est_pages", str(pages), SRC_PDF, count.confidence if count else 0.95,
                   note or "page count of the transcript")
        if self.kind not in ("email", "text"):
            return
        num, dash = r"(\d{1,3}(?:,\d{3})+|\d{1,5})", r"\s*(?:-|to|through|thru)\s*"
        ranges = []
        # (not a signature's phone number: 'p. 212-555-0100')
        for m in re.finditer(rf"(?i)\b(?:pp\.?|pages?|p\.)\s*{num}{dash}{num}\b(?!\s*[-.]\s*\d)", text):
            first, last = to_int(m.group(1)), to_int(m.group(2))
            if last >= first > 0:
                ex.add("est_pages", str(last - first + 1), SRC_REGEX, 0.5, f"pages {first}-{last}")
            ranges.append((m.start(), m.end()))
        for m in re.finditer(rf"(?i)\b{num}{dash}{num}\s+pages?\b", text):  # an estimate: 'about 30-40 pages'
            if not any(s <= m.start() < e for s, e in ranges):
                low, high = to_int(m.group(1)), to_int(m.group(2))
                if high >= low > 0:
                    ex.add("est_pages", str(high), SRC_REGEX, 0.7, f"{low}-{high} pages")
                ranges.append((m.start(), m.end()))
        for m in re.finditer(rf"(?i)\b(?:about|approx\.?|approximately|~|est\.?|estimated)?\s*{num}\s+pages?\b", text):
            if not any(s <= m.start() < e for s, e in ranges):
                ex.add("est_pages", str(to_int(m.group(1))), SRC_REGEX, 0.7)

    # ----- attorneys
    def _attorneys(self, text: str) -> list[Attorney]:
        """Attorneys and firms found in the text, as Attorney entries: one per firm (its attorneys named
        together), one per attorney without a firm.

        A transcript's title page(s), or any text with an APPEARANCES heading, is read first by
        appearances.parse_appearances. For a transcript that is all, when it finds anyone. An e-mail with a
        title page pasted in keeps those entries, and the rules below read only what stands above the
        heading, so that its sender is still found.

        The rules below are for e-mails, invoices and other papers, and for a title page parse_appearances
        can't read (then only the title page(s) are read, from APPEARANCES down). Lines of dialogue ('MR.
        POE: ...') are dropped. Then two passes. First, an invoice's 'To: Firm, attn: e-mail' line and an
        e-mail's 'From:' line become ticked entries (the orderer, the sender). Then the text is cut into
        blocks at blank lines and headings such as APPEARANCES; in each block names come from 'Name, Esq.'
        or 'BY:', the firm from FIRM_RE, and address, phone, fax, e-mail and role ('Attorney for the
        Plaintiff') from the other lines. Placeholders such as 'Unrepresented' become unticked entries.
        Block entries are not ticked, because a transcript lists everyone who appeared, not who ordered.
        The reporter's own block, e-mail and phone are skipped, and entries of one firm or person are
        merged by dedupe_attorneys.
        """
        found: list[Attorney] = []
        listed: list[Attorney] = []
        heading = re.search(r"(?im)^\s*A\s*P\s*P\s*E\s*A\s*R\s*A\s*N\s*C\s*E\s*S\b", text)
        if self.is_transcript or heading:
            # a title page: its APPEARANCES, one entry per firm (appearances.py); the rules below are for
            # e-mails, invoices and other papers, and for a title page that parse_appearances can't read
            from .appearances import parse_appearances
            title = "\f".join(text.split("\f")[:title_page_count(text)])
            listed = parse_appearances(title, self.tc, self.profile)
            if listed and self.is_transcript:
                return dedupe_attorneys(listed, self.profile)
            if listed:  # an e-mail with a title page pasted in: its sender (who orders) is read from what
                text = text[:heading.start()]  # stands above it, as in any e-mail
        if self.is_transcript:  # appearances are on the title page(s); the body is dialogue
            text = title_pages(text)
            m = re.search(r"(?im)^\s*A\s*P\s*P\s*E\s*A\s*R\s*A\s*N\s*C\s*E\s*S\b", text)
            if m:  # what stands above the heading is the caption: its parties are not counsel
                text = text[m.start():]
        lines = [l.strip() for l in text.splitlines()]
        lines = [l for l in lines if not re.match(r"^(MR|MS|MRS|DR)\.\s+[A-Z'-]+:|^THE\s+[A-Z ]+:", l)]

        # Invoice "To: Firm, attn: email" and email "From: Name <addr>"
        for l in lines:
            m = re.match(r"(?i)^(to|from)\s*:\s*(.+)$", l)
            if not m:
                continue
            who, rest = m.group(1).lower(), m.group(2)
            if who == "to" and not self.is_invoice:
                continue
            if who == "from" and self.kind != "email":
                continue
            emails = [e for e in EMAIL_RE.findall(rest) if e.lower() not in self.own_emails]
            name_part = re.sub(r"<[^>]*>|\[[^\]]*\]|" + EMAIL_RE.pattern, "", rest)
            name_part = re.split(r"(?i),?\s*attn:?", name_part)[0].strip(' ,;"\'')
            if not emails and not name_part:
                continue
            if any(e.lower() in self.own_emails for e in EMAIL_RE.findall(rest)) and not emails:
                continue
            a = Attorney(email=emails[0] if emails else "", source=SRC_REGEX, checked=True,
                         party="orderer (invoice)" if who == "to" else "sender")
            if FIRM_RE.search(name_part) or who == "to":
                a.firm = name_part
            else:
                a.name = self.tc(name_part)
            found.append(a)

        # Blocks separated by blank lines / headings.
        blocks, cur = [], []
        for l in lines + [""]:
            if not l or HEADING_RE.search(l) or X_LINE_RE.match(l):
                if cur:
                    blocks.append(cur)
                cur = []
                if l and PARTY_HEADING_RE.match(l):
                    cur = [l]
                continue
            cur.append(l)

        heading = ""
        for block in blocks:
            if any(mentions_reporter(self.profile, b) for b in block) and \
                    not any(re.search(r"(?i)\besq\b", b) for b in block):
                continue
            upper = is_mostly_upper(" ".join(block))
            party, client, firm, firm_idx = "", [], "", -1
            names, addr, phone, fax, email_ = [], [], "", "", ""
            placeholder = ""
            caps_names = False  # transcript style: everything in capitals
            role_open = False   # the role went on to the next line ("Attorneys for A, B, and" / "C")
            for i, l in enumerate(block):
                if role_open and not (ESQ_RE.search(l) or (ADDRESS_RE.search(l) and not is_firm_line(l))
                                      or EMAIL_RE.search(l)
                                      or PHONE_RE.search(l) or re.match(r"(?i)^by\s*:", l)):
                    party = f"{party} {l.lower() if upper else l}".strip()
                    role_open = bool(re.search(r"(?i)(,|&|\band)$", l))
                    continue
                role_open = False
                if PARTY_HEADING_RE.match(l):
                    heading = PARTY_HEADING_RE.match(l).group(1).capitalize()
                    continue
                if PLACEHOLDER_RE.match(l):
                    placeholder = l.rstrip(".")
                    continue
                if GREETING_RE.match(l):
                    continue
                rm = ROLE_RE.match(l) or SPECIAL_ROLE_RE.match(l)
                if rm:
                    party = (rm.group(1) if rm.re is ROLE_RE else l).strip()
                    party = party[0].upper() + party[1:].lower() if upper else party
                    if re.search(r"\b(?:[A-Za-z]\.)+[A-Za-z]$", party):  # "Sam Poe, M.D": the full stop was its own
                        party += "."
                    role_open = bool(re.search(r"(?i)(,|&|\band)$", l))
                    continue
                esq = list(ESQ_RE.finditer(l))
                if esq:
                    for m in esq:
                        caps_names = caps_names or is_mostly_upper(m.group(1))
                        n = re.sub(r"(?i)^(by|of counsel)\s*:?\s*", "", m.group(1)).strip()
                        n = re.sub(r"(?i)^and\s+", "", n)
                        if n:
                            names.append(self.tc(n))
                    continue
                m = re.match(r"(?i)^by\s*:?\s+([A-Z][A-Za-z.'\- ]{3,40})$", l)
                if m and not SURE_ADDRESS_RE.search(l):  # "BY: John Lane" is a name
                    caps_names = caps_names or is_mostly_upper(m.group(1))
                    names.append(self.tc(m.group(1)))
                    continue
                emails = EMAIL_RE.findall(l)
                if emails:
                    e = next((x for x in emails if x.lower() not in self.own_emails), "")
                    email_ = email_ or e
                    continue
                ph = PHONE_RE.search(l)
                if ph and len(re.sub(r"[\d\s().+:-]|tel|phone|fax|ph|t|f|o|c|m|cell|office|direct", "", l, flags=re.I)) < 4:
                    digits = "".join(ph.groups())
                    if digits in self.own_phones:
                        continue
                    if re.search(r"(?i)\bfax\b|^f\b", l):
                        fax = fax or fmt_phone(ph)
                    else:
                        phone = phone or fmt_phone(ph)
                    continue
                if not firm and is_firm_line(l):
                    firm, firm_idx = l, i
                    if l.startswith("&") and client and client[-1] == block[i - 1].rstrip(":").strip():
                        firm = f"{client.pop()} {l}"
                    if i + 1 < len(block) and block[i + 1].startswith("&"):
                        firm = f"{l} {block[i + 1]}"
                    continue
                if firm and l.startswith("&") and i == firm_idx + 1:
                    continue
                if firm and ADDRESS_RE.search(l):
                    addr.append(l.rstrip(" ,"))
                    continue
                if not firm and ADDRESS_RE.search(l) and (names or phone):
                    addr.append(l.rstrip(" ,"))
                    continue
                if not firm:
                    client.append(l.rstrip(":").strip())

            if placeholder and not names and not firm:
                found.append(Attorney(name=placeholder, party=heading or party, source=SRC_REGEX, checked=False))
                continue
            if not names and firm:
                # e-mail signatures: a person-like line just before the firm
                prev = [c for c in client if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+){1,2}", c)]
                if prev:
                    names.append(prev[-1])
                    client.remove(prev[-1])
            if not names and not firm:
                continue
            if not firm and not (phone or email_ or addr):
                continue
            if not names and not (addr or phone or email_ or party):
                continue  # e.g. a caption party like "Metal & Glass Corp."
            firm_s = (self.tc(firm) if upper or caps_names else firm).strip(" ,")
            client_s = " ".join(client).strip(" ,:")
            party_s = party or heading
            if client_s and len(client_s) < 80 and not ADDRESS_RE.search(client_s):
                party_s = f"{party_s} ({client_s})" if party_s else client_s
            address = "\n".join(self.tc(a) if upper or caps_names else a for a in addr[:3])
            # a firm's attorneys share one entry (one party); attorneys without a firm are one each
            for n in [join_names(list(dict.fromkeys(names)))] if firm_s else names or [""]:
                found.append(Attorney(name=n, firm=firm_s, address=address, phone=phone, fax=fax, email=email_,
                                      party=party_s, source=SRC_REGEX, checked=False))
        return dedupe_attorneys(found + listed, self.profile)


def dedupe_attorneys(atts: list[Attorney], profile: Profile | None = None) -> list[Attorney]:
    """Merges entries that are the same firm or person, so that every firm is one entry (one row, one party),
    and drops the reporter's own entry.

    Entries match (same_entry) as the user answered about them (firm_answer), else on the same e-mail; two
    entries with firms on the same firm (same_firm: also 'Counsel & Counsel, LLP' and 'COUNSEL & COUNSEL'),
    whoever they name; an entry without a firm joins one with a firm when it names one of its attorneys,
    writes from its e-mail domain, or from a domain that spells the firm's name (a public service such as
    gmail.com says nothing about a firm); two entries without a firm on a shared name, or the same e-mail
    domain when one has no name. The first of two matching entries is kept: the attorneys of the other are
    added to its names (a fuller spelling of a name replaces 'Mr. Counsel'), its blank fields are filled in,
    and it stays ticked if either one was. Matching goes on until no two entries left match (A matching C only
    once B joined it).
    """
    out: list[Attorney] = []
    for a in atts:
        if is_reporter(profile, a.name, a.email):
            continue
        match = next((b for b in out if same_entry(a, b)), None)
        if match is None:
            out.append(a)
        else:
            merge_entry(match, a)
    merged = True
    while merged:  # an entry filled in by a merge may now match another one
        merged = False
        for i, j in ((i, j) for i in range(len(out)) for j in range(i + 1, len(out))):
            if same_entry(out[i], out[j]):
                merge_entry(out[i], out.pop(j))
                merged = True
                break
    return out


def merge_entry(match: Attorney, a: Attorney) -> None:
    """Merges entry `a` into `match` (see dedupe_attorneys): a's attorneys are added to match's names, match's
    blank fields are filled from a, and match is ticked when either was."""
    names = match.names()
    for n in a.names():
        twin = next((i for i, m in enumerate(names) if same_person(n, m)), None)
        if twin is None:
            names.append(n)
        elif len(_name_words(n)) > len(_name_words(names[twin])):  # "John Jones" for "Mr. Jones"
            names[twin] = n
    if names != match.names():
        match.name = join_names(names)
    for f in ("name", "firm", "address", "phone", "fax", "email", "party"):
        if not getattr(match, f) and getattr(a, f):
            setattr(match, f, getattr(a, f))
    match.checked = match.checked or a.checked


# E-mail services anyone can use: their domain says nothing about a firm
_PUBLIC_MAIL = re.compile(r"(?i)@(?:gmail|googlemail|yahoo|ymail|aol|hotmail|outlook|live|msn|icloud|me|mac|"
                          r"verizon|optonline|optimum|att|comcast|earthlink|protonmail|proton|mail|gmx)\.")
_HONORIFICS = {"mr", "ms", "mrs", "miss", "dr", "hon", "esq", "jr", "sr", "ii", "iii", "iv", "md"}


def _name_words(n: str) -> list[str]:
    """A name's words in lowercase, without punctuation, one-letter initials or titles (Mr., Esq., Jr.)."""
    return [w for w in re.sub(r"[^a-z ]", " ", (n or "").lower()).split() if len(w) > 1 and w not in _HONORIFICS]


def same_person(a: str, b: str, loose: bool = True) -> bool:
    """Two spellings of one attorney's name: the same first and last name ('John Q. Smith', 'JOHN SMITH'), or
    (loose) the same last name when one gives only that ('Mr. Smith': in one firm's row, the same attorney;
    between two entries only maybe, see maybe_same_entry)."""
    wa, wb = _name_words(a), _name_words(b)
    if not wa or not wb:
        return False
    if len(wa) >= 2 and len(wb) >= 2:
        return (wa[0], wa[-1]) == (wb[0], wb[-1])
    return loose and wa[-1] == wb[-1]


def same_firm(a: str, b: str) -> bool:
    """Two spellings of one firm (firm_key): 'Example Law Group, P.C.', 'EXAMPLE LAW GROUP' and 'Example Law
    Group P.C.'. A name that only starts with the other ('Smith Law' and 'Smith Law Group') may be another
    firm: near_firm, and the user is asked (maybe_same_entry)."""
    ka, kb = firm_key(a), firm_key(b)
    return bool(ka) and ka == kb


def near_firm(a: str, b: str) -> bool:
    """One firm's name starts with the other's, two words at least ('Smith Law' and 'Smith Law Group, PLLC'):
    maybe the same firm, maybe not (see maybe_same_entry)."""
    ka, kb = firm_key(a).split(), firm_key(b).split()
    if not ka or not kb or ka == kb:
        return False
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 2 and long_[:len(short)] == short


# The user's answers about two entries that may be one firm or attorney (maybe_same_entry: asked when unsure):
# {frozenset of their two Attorney.key()s: True (the same) or False (not)}. Set by use_firm_answers from
# Settings.firm_answers and the window's answers for now only, when the settings are loaded or changed and when
# the user answers.
_ANSWERS: dict[frozenset, bool] = {}


def use_firm_answers(rows) -> None:
    """Takes the user's answers ([[key, key, same, name, name], ...]: Settings.firm_answers, and the window's
    answers for now only) for same_entry and maybe_same_entry. The two names are only for showing the answer
    (rows saved by 2.2.0 have none); anything else in the list is left out. A new dict is put in place in one
    step: Generate all reads the answers on another thread, which must never see them half filled."""
    global _ANSWERS
    answers: dict[frozenset, bool] = {}
    for r in rows or []:
        if isinstance(r, (list, tuple)) and len(r) >= 3 and all(isinstance(k, str) and k for k in r[:2]) \
                and isinstance(r[2], bool) and r[0] != r[1]:
            answers[frozenset(r[:2])] = r[2]
    _ANSWERS = answers


def firm_answer(a: Attorney, b: Attorney) -> bool | None:
    """What the user answered about a and b being one firm or attorney; None when not asked."""
    ka, kb = a.key(), b.key()
    return _ANSWERS.get(frozenset((ka, kb))) if ka and kb and ka != kb else None


def maybe_same_entry(a: Attorney, b: Attorney) -> bool:
    """a and b may be one firm or attorney, but it isn't sure, so the user is asked (batch.firm_questions): two
    firms one of whose names starts with the other's ('Smith Law' and 'Smith Law Group'), or an entry naming an
    attorney by the last name alone ('Mr. Smith') and one with an attorney of that last name ('Dana Smith' of
    Smith Law). False when they are surely one (same_entry), surely not (two firms named otherwise), or the
    user has answered."""
    if a.key() == b.key() or firm_answer(a, b) is not None or same_entry(a, b):
        return False
    if a.firm and b.firm:
        return near_firm(a.firm, b.firm)
    return any(same_person(x, y) for x in a.names() for y in b.names())


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""


def _domain_spells_firm(email: str, firm: str) -> bool:
    """The e-mail's domain spells the firm's name: 'pc@counselcounsel.example' for 'Counsel & Counsel, LLP'
    ('counselandcounsel' or 'counselcounsel'), 'x@smithlaw.example' for 'Smith Law Group'."""
    if not email or _PUBLIC_MAIL.search(email):
        return False
    label = _domain(email).split(".")[0]
    words = firm_key(firm).split()
    if len(label) < 4 or not words:
        return False
    for compact in ("".join(words), "".join(w for w in words if w != "and")):
        if compact.startswith(label) or (len(compact) >= 6 and label.startswith(compact)):
            return True
    return len(words[0]) >= 4 and len(words) >= 2 and label.startswith(words[0] + words[1][:3])


def same_entry(a: Attorney, b: Attorney) -> bool:
    """a and b are surely the same firm or person (see dedupe_attorneys), or the user said so (firm_answer).
    Only maybe the same ('Smith Law' and 'Smith Law Group', 'Mr. Smith' and 'Dana Smith'): see
    maybe_same_entry."""
    answer = firm_answer(a, b)
    if answer is not None:
        return answer
    if a.email and b.email and a.email.lower() == b.email.lower():
        return True
    if a.firm and b.firm:
        return same_firm(a.firm, b.firm)
    shared_name = any(same_person(x, y, loose=False) for x in a.names() for y in b.names())
    if a.firm or b.firm:  # one has a firm: the other may be one of its attorneys
        f, other = (a, b) if a.firm else (b, a)
        return shared_name or bool(other.email and not _PUBLIC_MAIL.search(other.email) and (
            _domain(other.email) == _domain(f.email) or _domain_spells_firm(other.email, f.firm)))
    return shared_name or bool((not a.name or not b.name) and a.email and b.email and
                               not _PUBLIC_MAIL.search(a.email) and _domain(a.email) == _domain(b.email))
