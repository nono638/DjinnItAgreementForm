"""Rule-based extraction of minute-agreement fields from plain text.

Every finding is a Candidate with a confidence in [0, 1]; merge.py picks the
winner and keeps the rest as alternatives for the user to choose from.
The transcript layout helpers here (strip_line_numbers, title_page_count) are
also used by runsheet.py.
"""
from __future__ import annotations

import difflib
import re
from datetime import date, timedelta

from .ingest import Ingested
from .models import Attorney, Extraction, SRC_PDF, SRC_REGEX
from .settings import Profile
from .takes import body_pages

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


def smart_title(s: str) -> str:
    """Title-cases ALL-CAPS names while keeping initials, suffixes and small words sane."""
    parts = re.split(r"(\s+|-|/)", s.strip())
    out, first = [], True
    for w in parts:
        if not w or re.fullmatch(r"\s+|-|/", w):
            out.append(w)
            continue
        core = w.strip(",;:()")
        pre, post = w[: w.find(core)] if core else "", w[w.find(core) + len(core):] if core else ""
        up = core.upper()
        if up in KEEP_UPPER:
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


def find_dates(text: str, allow_yearless: bool = False) -> list[tuple[int, int, str]]:
    """Returns (start, end, M/D/YYYY) for every date in text, in order ('Sept. 14, 2026', '9/14/26').

    allow_yearless (for e-mails): also '9/14' and 'March 3' without a year. Such a day takes the year of
    a dated day right after it in a list ('9/14 and 9/15/2025'), else the year from guess_year.
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
                for m in re.finditer(r"(?<![\d/.$-])(\d{1,2})/(\d{1,2})(?![\d/%-])", text)]
        bare += [(m.start(), m.end(), MONTHS[m.group(1)[:3].lower()], int(m.group(2))) for m in re.finditer(
            MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+\d{4})", text, re.I)]
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
    GROUP"), so that the rules see the text alone. Returns (text, True) when the lines were numbered that
    way; text whose numbers stand on lines of their own, or that has none, comes back unchanged."""
    lines = text.split("\n")
    hits = [(i, m) for i, l in enumerate(lines) if (m := _LINE_NO.match(l))]
    nums = [int(m.group(1)) for _, m in hits]
    counted = any(nums[k:k + 5] == [1, 2, 3, 4, 5] for k in range(len(nums)))
    if not counted or not any(m.end() < len(lines[i]) for i, m in hits):
        return text, False
    for i, m in hits:
        lines[i] = lines[i][m.end():]
    return "\n".join(lines), True


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


def norm_index(num, yr) -> str | None:
    """An index number as 'num/year' ('712345', '24' -> '712345/2024'); None unless the year is 1950-2100."""
    yr = int(yr)
    yr = yr + 2000 if yr < 100 else yr
    return f"{int(num)}/{yr}" if 1950 <= yr <= 2100 else None


# Words that show a delivery speed was asked for. The rules read the stricter phrases in _order_terms; the AI's
# answer is only kept when one of these is in the text.
DELIVERY_WORDS = {
    "Regular": r"regular|standard|normal",
    "Expedited": r"expedit|rush|asap|urgent",
    "Daily": r"daily|overnight|next[- ]day",
    "Immediate": r"immediate|same[- ]day|hourly",
}


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
        self.is_invoice = bool(re.search(r"(?im)^\s*invoice\b", text))
        # a transcript numbers its lines: 1, 2, 3 on lines of their own, or in front of the text
        self.is_transcript = (numbered or bool(re.search(r"(?m)^\s*1\s*\n\s*2\s*\n\s*3\s*\n", text))) \
            and not self.is_invoice
        self.is_caption_doc = bool(re.search(r"(?i)\bappearances\b|a p p e a r|b e f o r e|\bBEFORE:|-against-|"
                                             r"\bindex\s+n", text))
        head = text.split("\f")[0][:2500]
        ex.doc_kind = ("invoice" if self.is_invoice else "transcript" if self.is_transcript
                       else "email" if ing.kind == "email" else "text")

        self._index(text, ex)
        self._court_county(head, text, ex)
        self._part(head, text, ex)
        self._judge(head, text, ex)
        self._case_name(text, ex)
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
        """Straightens curly quotes and dashes, drops replacement characters and trailing blanks, and keeps
        the page breaks (form feeds)."""
        text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
        text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
        text = text.replace("�", "")
        # split("\n"), not splitlines(): keep the \f page separators
        return "\n".join(l.rstrip(" \t") for l in text.replace("\r", "").split("\n"))

    # ----- index number
    def _index(self, text: str, ex: Extraction, unlabeled_conf: float = 0.45) -> None:
        """Index numbers ('712345-2024', 'Index No. 712345/24', '712345 of 2024'), written as '712345/2024'.

        Confidence: 0.95 when labelled ('Index No. 712345-2024', 'Docket ...'), 0.8 after a bare 'No.',
        and unlabeled_conf for a lone number-year pair (ZIP+4 codes after a state are skipped).
        """
        labeled = re.compile(
            r"\b(?:index|ind\.?|docket|file|calendar\s+index)\s*(?:no\.?|number|num\.?|#)?\s*[:.#]?\s*"
            r"(\d{3,7})\s*(?:[-/]|\s+of\s+)\s*(\d{4}|\d{2})\b", re.I)
        for m in labeled.finditer(text):
            v = norm_index(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.95)
        for m in re.finditer(r"\bNo\.?\s*:?\s*(\d{4,7})\s*[-/]\s*((?:19|20)\d{2})\b", text):
            v = norm_index(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.8)
        for m in re.finditer(r"(?<![\d$.,-])(\d{5,7})\s*[-/]\s*((?:19|20)\d{2})(?![\d-])", text):
            before = text[max(0, m.start() - 6): m.start()]
            if re.search(r"\b[A-Z]{2}\s*$", before):  # ZIP+4 after a state
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

        Letter codes must be written in capitals so that 'part of the record' is not read as a part.
        """
        # numbered parts ("PART 25", "Part: 53", "Part TR-3") - any capitalisation
        numbered = re.compile(r"\b(?:IAS\s+|TRIAL\s+|TAP\s+)?PART\s*(?:No\.?)?\s*[:#]?\s*"
                              r"((?:[A-Z]{1,4}[- ]?)?\d{1,3}[A-Z]?)\b", re.I)
        # letter parts ("PART MDP", "Part: TAP-A") - the code itself in capitals
        lettered = re.compile(r"\b(?:PART|Part)(?:\s+No\.?)?[ \t]*[:#]?[ \t]*([A-Z]{1,6}(?:-[A-Z0-9]{1,3})?)\b(?![a-z])")
        # capital words that follow PART in ALL-CAPS text without being one ("PART OF THE RECORD")
        not_a_part = {"OF", "THE", "AND", "IN", "TO", "A", "AN", "IS", "IT", "ON", "FOR", "AS", "OR", "BY", "AT", "NO"}
        for scope, conf in ((head, 0.9), (text, 0.7)):
            for m in numbered.finditer(scope):
                ex.add("part", m.group(1).upper().replace(" ", ""), SRC_REGEX, conf)
            for m in lettered.finditer(scope):
                if m.group(1) not in not_a_part:
                    ex.add("part", m.group(1), SRC_REGEX, conf - 0.05)

    # ----- judge
    def _judge(self, head: str, text: str, ex: Extraction) -> None:
        """Judge from 'HONORABLE X', 'Judge: X', 'X, J.S.C.' or 'before Justice X'.

        Informal lowercase mentions ('justice smith', common in e-mails) are accepted at low confidence.
        Filler words at either end are trimmed, and the top of the first page counts for more.
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
        bad = re.compile(r"^(of|the|is|and|for|in|presiding|justice|judge|supreme|court|j|s|c)$", re.I)
        for scope_i, scope in enumerate((head, text)):
            for pat, conf, flags in pats:
                for m in re.finditer(pat, scope, flags):
                    raw = m.group(1).strip(" .,")
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
    def _case_name(self, text: str, ex: Extraction) -> None:
        """Case name from a 'Title:', 'Caption:' or 'Re:' line, 'Matter of ...', a court caption, or
        'X v. Y' anywhere in the text. Lowercase 'smith v jones' is only trusted, weakly, in e-mails.
        """
        for m in re.finditer(r"(?im)^\s*(?:title|case(?:\s+name)?|caption|re)\s*:\s*(.+)$", text):
            val = m.group(1).strip()
            if re.search(r"\sv\.?s?\.?\s|\bmatter of\b|\bagainst\b", val, re.I):
                ex.add("case_name", self._norm_case(val), SRC_REGEX, 0.9)
            else:
                self._inline_case(val, ex, 0.75)
        for m in re.finditer(r"(?i)\b(?:IN\s+THE\s+)?MATTER\s+OF\s*(?:THE\s+)?:?\s*([^\n,]+)", text):
            party = m.group(1).strip(" ,.:")
            if party and len(party) < 90:
                ex.add("case_name", "Matter of " + self.tc(party), SRC_REGEX, 0.85)
        self._caption(text, ex)
        self._inline_case(text, ex, 0.6)
        if self.kind in ("email", "text"):  # lowercase "smith v jones"
            for m in re.finditer(r"\b([a-z][\w.'&-]+)\s+(?:v|vs)\.?\s+([a-z][\w.'&-]+)\b", text, re.I):
                left, right = m.group(1), m.group(2)
                if left.lower() not in ("re", "the", "for", "of", "in", "minutes", "transcript"):
                    ex.add("case_name", f"{smart_title(left.upper())} v. {smart_title(right.upper())}", SRC_REGEX, 0.4)

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

        Takes up to eight lines each way. Going up it stops at the court heading or an X rule line and
        skips party roles ('Plaintiffs,'); going down it stops at those and at the first party role.
        Index numbers and the like are skipped. Adds the full caption (0.85) and a short form with
        'et al.' (0.55).
        """
        lines = text.splitlines()
        role = re.compile(r"^(?:[-\s]*)(plaintiffs?|defendants?|petitioners?|respondents?|claimants?|appellants?|"
                          r"appellees?|third[- ]party\s+\w+)[,.;:\s-]*(?:and\s*)?$", re.I)
        noise = re.compile(r"(?i)^(index\s*(no\.?|number|#)?\s*:?|\d{3,7}\s*[-/]\s*\d{2,4}|cal\.?\s*no\..*|"
                           r"attorneys?\.?|jury\s+trial|bench\s+trial|trial|hearing|motion|x|-+)$")
        stopper = re.compile(r"(?i)(\bcourt\b|county\s+of|\bpart\s+\d|^-{5,}|x\s*$|cal\.\s*no|supreme|state of new york)")
        for i, line in enumerate(lines):
            if not re.fullmatch(r"\s*-?\s*(against|vs?\.?)\s*-?\s*", line, re.I):
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
        """What was ordered, from the wording of an e-mail: delivery speed (expedited, daily, immediate,
        regular), number of copies ('original and 2 copies') and a rate per page.

        On an invoice it reads the totals it lists per speed ('Regular Rate: $94.50', 'Expedited Rate: ...');
        merge.apply_defaults works the page count out from them.
        """
        if self.kind in ("email", "text") and not self.is_invoice:
            if re.search(r"(?i)\b(expedit\w*|rush)\b", text):
                ex.add("delivery", "Expedited", SRC_REGEX, 0.8)
            if re.search(r"(?i)\b(daily\s+(copy|delivery|transcript)|overnight|next[- ]day)\b", text):
                ex.add("delivery", "Daily", SRC_REGEX, 0.75)
            if re.search(r"(?i)\b(immediate(ly)?\s+(copy|delivery|transcript)|same[- ]day|hourly)\b", text):
                ex.add("delivery", "Immediate", SRC_REGEX, 0.75)
            if re.search(r"(?i)\b(regular|standard|normal)\s+(delivery|turnaround|rate|copy)\b", text):
                ex.add("delivery", "Regular", SRC_REGEX, 0.7)
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
        'transcript pages 358-380', when it does not start at 1), or 'N pages' written in an e-mail.
        A transcript's count leaves out the word index printed after it (see takes.scan_pdf). It is
        counted, not read from the words, so its source is SRC_PDF (the "PDF" badge), not SRC_REGEX.
        """
        if ing.kind == "pdf" and self.is_transcript and ing.page_count:
            pages = body_pages(ing.marks, ing.page_count)  # without the word index printed after it
            note = ""
            if ing.first_page_no and ing.first_page_no > 1:
                note = f"transcript pages {ing.first_page_no}-{ing.first_page_no + pages - 1}"
            if pages < ing.page_count:
                note = (note or f"{pages} transcript pages") + \
                    f", not counting {ing.page_count - pages} page(s) after the transcript (word index)"
            ex.add("est_pages", str(pages), SRC_PDF, 0.95, note or "page count of the transcript")
        for m in re.finditer(r"(?i)\b(?:about|approx\.?|approximately|~|est\.?|estimated)?\s*(\d{1,4})\s+pages?\b", text):
            if self.kind in ("email", "text"):
                ex.add("est_pages", m.group(1), SRC_REGEX, 0.7)

    # ----- attorneys
    def _attorneys(self, text: str) -> list[Attorney]:
        """Attorneys and firms found in the text, as Attorney entries.

        In a transcript only the title page(s) are read (from APPEARANCES down, if it has that heading).
        Lines of dialogue ('MR. POE: ...') are dropped everywhere. Then two passes. First, an invoice's
        'To: Firm, attn: e-mail' line and an e-mail's 'From:' line become ticked entries (the orderer, the
        sender). Then the text is cut into blocks at blank lines and headings such as APPEARANCES; in each
        block names come from 'Name, Esq.' or
        'BY:', the firm from FIRM_RE, and address, phone, fax, e-mail and role ('Attorney for the
        Plaintiff') from the other lines. Placeholders such as 'Unrepresented' become unticked entries.
        The reporter's own block, e-mail and phone are skipped, and duplicates are merged by
        dedupe_attorneys. Block entries are not ticked, because a transcript lists everyone who
        appeared, not who ordered.
        """
        found: list[Attorney] = []
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
            for n in names or [""]:
                found.append(Attorney(name=n, firm=firm_s, address=address, phone=phone, fax=fax, email=email_,
                                      party=party_s, source=SRC_REGEX, checked=False))
        return dedupe_attorneys(found, self.profile)


def dedupe_attorneys(atts: list[Attorney], profile: Profile | None = None) -> list[Attorney]:
    """Merges entries that are the same person or firm and drops the reporter's own entry.

    Entries match on the same e-mail, the same name, the same firm when neither has a name, or the
    same e-mail domain when a name or firm is missing on one of them. Blank fields of the first are
    filled from the duplicate, and the merged entry stays ticked if either one was.
    """
    out: list[Attorney] = []
    for a in atts:
        if is_reporter(profile, a.name, a.email):
            continue
        match = None
        for b in out:
            same_email = a.email and b.email and a.email.lower() == b.email.lower()
            same_name = a.name and b.name and _name_key(a.name) == _name_key(b.name)
            same_firm_only = not a.name and not b.name and a.firm and a.firm.lower() == b.firm.lower()
            email_domain_firm = (not a.name or not b.name) and a.email and b.email and \
                a.email.split("@")[-1].lower() == b.email.split("@")[-1].lower() and (not a.firm or not b.firm)
            if same_email or same_name or same_firm_only or email_domain_firm:
                match = b
                break
        if match is None:
            out.append(a)
            continue
        for f in ("name", "firm", "address", "phone", "fax", "email", "party"):
            if not getattr(match, f) and getattr(a, f):
                setattr(match, f, getattr(a, f))
        match.checked = match.checked or a.checked
    return out


def _name_key(n: str) -> str:
    """'first last' in lowercase, without punctuation or one-letter initials, so 'John Q. Smith' matches 'John Smith'."""
    words = [w for w in re.sub(r"[^a-z ]", "", n.lower()).split() if len(w) > 1]
    return f"{words[0]} {words[-1]}" if len(words) >= 2 else " ".join(words)
