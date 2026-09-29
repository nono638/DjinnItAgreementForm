"""Rule-based extraction of minute-agreement fields from plain text.

Every finding is a Candidate with a confidence in [0, 1]; merge.py picks the
winner and keeps the rest as alternatives for the user to choose from.
"""
from __future__ import annotations

import difflib
import re
from datetime import date

from .ingest import Ingested
from .models import Attorney, Extraction, SRC_REGEX
from .settings import Profile

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

KEEP_UPPER = {"LLP", "PLLC", "LLC", "PC", "P.C.", "L.L.P.", "P.L.L.C.", "L.L.C.", "P.A.", "N.A.", "NY", "N.Y.",
              "NYC", "USA", "II", "III", "IV", "DDS", "MD", "CPA", "LP", "NYCHA", "MTA", "MSK", "NYU", "CUNY"}
SMALL_WORDS = {"and", "of", "the", "for", "in", "on", "at", "to", "a", "an", "v.", "vs.", "v", "vs", "de", "del"}

FIRM_RE = re.compile(
    r"(\bL\.?L\.?P\.?|\bP\.?L\.?L\.?C\.?|\bL\.?L\.?C\.?\b|\bP\.\s?C\.?|,\s*PC\b|\bP\.A\.|\bEsqs\.|\bAssociates\b|"
    r"\bLaw\s+(?:Office|Firm|Group)|\bLaw\s+Offices?\b|Attorneys?\s+at\s+Law|\bCorporation\s+Counsel\b|"
    r"\bLegal\s+Aid\b|\bLegal\s+Services?\b|\s&\s)", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]*(\d{3})[\s.-](\d{4})(?!\d)")
EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
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
X_LINE_RE = re.compile(r"^[-\s]*-{5,}[-\s]*X?\s*$|^X\s*[-\s]{5,}$")
HEADING_RE = re.compile(
    r"^(A\s*P\s*P\s*E\s*A\s*R\s*A\s*N\s*C\s*E\s*S|B\s*E\s*F\s*O\s*R\s*E|H\s*E\s*L\s*D|P\s*R\s*E\s*S\s*E\s*N\s*T)"
    r"\s*:?\s*$|^(THE\s+)?HONORABLE\b|^J\s+U\s+S\s+T\s+I\s+C\s+E|^\d{1,3}$|^COPY$|^Proceedings$|"
    r"(senior|official|principal)?\s*court\s+reporter\s*$", re.I)


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
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.85


def fmt_date(y: int, m: int, d: int) -> str | None:
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
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}"


def find_dates(text: str, allow_yearless: bool = False) -> list[tuple[int, int, str]]:
    """Returns (start, end, M/D/YYYY) for every date in text."""
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
        today = date.today()
        for m in re.finditer(r"(?<![\d/.$-])(\d{1,2})/(\d{1,2})(?![\d/%-])", text):
            mo, d = int(m.group(1)), int(m.group(2))
            y = today.year
            try:
                if date(y, mo, d) > today.replace(month=12, day=31):
                    y -= 1
            except ValueError:
                continue
            v = fmt_date(y, mo, d)
            if v:
                found.append((m.start(), m.end(), v))
        for m in re.finditer(MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+\d{4})", text, re.I):
            v = fmt_date(date.today().year, MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
            if v:
                found.append((m.start(), m.end(), v))
    found.sort()
    return found


# --------------------------------------------------------------- extractor

class RegexExtractor:
    def __init__(self, profile: Profile | None = None, title_case: bool = True):
        self.profile = profile or Profile()
        self.title_case = title_case
        self.own_emails = {e.lower() for e in re.findall(EMAIL_RE, self.profile.email or "")}
        self.own_phones = {re.sub(r"\D", "", self.profile.phone or "")[-10:]} - {""}

    def tc(self, s: str) -> str:
        s = " ".join(s.split())
        return smart_title(s) if self.title_case and is_mostly_upper(s) else s

    # ----- entry point
    def extract(self, ing: Ingested) -> Extraction:
        ex = Extraction()
        text = self._clean(ing.text)
        self.kind = ing.kind
        self.is_invoice = bool(re.search(r"(?im)^\s*invoice\b", text))
        self.is_transcript = bool(re.search(r"(?m)^\s*1\s*\n\s*2\s*\n\s*3\s*\n", text)) and not self.is_invoice
        self.is_caption_doc = bool(re.search(r"(?i)\bappearances\b|a p p e a r|b e f o r e|\bBEFORE:|-against-|"
                                             r"\bindex\s+n", text))
        head = text.split("\f")[0][:2500]

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

        # The file name often carries case, index and date ("5-22-2026 Smith v Jones - 712345-2024").
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
        text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
        text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
        text = text.replace("�", "")
        # split("\n"), not splitlines(): keep the \f page separators
        return "\n".join(l.rstrip(" \t") for l in text.replace("\r", "").split("\n"))

    # ----- index number
    def _index(self, text: str, ex: Extraction, unlabeled_conf: float = 0.45) -> None:
        def norm(num, yr):
            yr = int(yr)
            yr = yr + 2000 if yr < 100 else yr
            return f"{int(num)}/{yr}" if 1950 <= yr <= 2100 else None

        labeled = re.compile(
            r"\b(?:index|ind\.?|docket|file|calendar\s+index)\s*(?:no\.?|number|num\.?|#)?\s*[:.#]?\s*"
            r"(\d{3,7})\s*(?:[-/]|\s+of\s+)\s*(\d{4}|\d{2})\b", re.I)
        for m in labeled.finditer(text):
            v = norm(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.95)
        for m in re.finditer(r"\bNo\.?\s*:?\s*(\d{4,7})\s*[-/]\s*((?:19|20)\d{2})\b", text):
            v = norm(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, 0.8)
        for m in re.finditer(r"(?<![\d$.,-])(\d{5,7})\s*[-/]\s*((?:19|20)\d{2})(?![\d-])", text):
            before = text[max(0, m.start() - 6): m.start()]
            if re.search(r"\b[A-Z]{2}\s*$", before):  # ZIP+4 after a state
                continue
            v = norm(m.group(1), m.group(2))
            if v:
                ex.add("index_no", v, SRC_REGEX, unlabeled_conf)

    # ----- court and county
    def _court_county(self, head: str, text: str, ex: Extraction) -> None:
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
        pat = re.compile(r"\b(?:IAS\s+|TRIAL\s+|TAP\s+)?PART\s*(?:No\.?)?\s*[:#]?\s*((?:[A-Z]{1,3}[- ]?)?\d{1,3}[A-Z]?)\b", re.I)
        for scope, conf in ((head, 0.9), (text, 0.7)):
            for m in pat.finditer(scope):
                val = m.group(1).upper().replace(" ", "")
                ex.add("part", val, SRC_REGEX, conf)

    # ----- judge
    def _judge(self, head: str, text: str, ex: Extraction) -> None:
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
        s = " ".join(s.split())
        s = re.sub(r"\s+(?:v|vs)\.?\s+", " v. ", s, flags=re.I)
        s = re.sub(r"\s+-?against-?\s+", " v. ", s, flags=re.I)
        parts = s.split(" v. ")
        return " v. ".join(self.tc(p.strip(" ,")) for p in parts)

    def _inline_case(self, text: str, ex: Extraction, conf: float) -> None:
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
                l = lines[j].strip()
                if l and (stopper.search(l) or X_LINE_RE.match(l)):
                    break
                if l and not role.match(l) and not noise.match(l):
                    left.insert(0, l)
                j -= 1
            right, j = [], i + 1
            while j < len(lines) and len(right) < 8:
                l = lines[j].strip()
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
    def _split_parties(s: str) -> list[str]:
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
        lp, rp = self._split_parties(left), self._split_parties(right)
        l = lp[0] + (", et al." if len(lp) > 1 else "") if lp else left
        r = rp[0] + (", et al." if len(rp) > 1 else "") if rp else right
        return f"{l} v. {r}"

    # ----- dates
    def _dates(self, text: str, head: str, ex: Extraction) -> None:
        is_email = self.kind in ("email", "text")
        body = text
        if is_email:  # skip header lines like "Sent: ..." / "Date: ..."
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
            joined = k and re.fullmatch(r"\s*(,|and|&|-|through|thru|to)?\s*(and\s*)?",
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
    PROC_PATTERNS = {
        "Trial": r"\b(?:jury|bench|non-?jury)\s+trial\b|\btrial\b",
        "Hearing": r"\bhearing\b|appoint(?:ment of)?\s+a\s+guardian|\bguardianship\b|article\s+81|\btraverse\b|"
                   r"\b(?:frye|mapp|huntley|dunaway|sandoval|wade|fact-?finding)\b",
        "Application": r"order\s+to\s+show\s+cause|\bmotion\b|\bapplication\b|oral\s+argument",
        "Sentence": r"\bsentenc(?:e|ing)\b",
        "Plea": r"\bplea\b|\bpleads?\s+guilty\b",
        "Arraignment": r"\barraign(?:ment|ed)?\b",
    }
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
        if ing.kind == "pdf" and self.is_transcript and ing.page_count:
            note = ""
            if ing.first_page_no and ing.first_page_no > 1:
                note = f"transcript pages {ing.first_page_no}-{ing.first_page_no + ing.page_count - 1}"
            ex.add("est_pages", str(ing.page_count), SRC_REGEX, 0.95, note or "page count of the transcript")
        for m in re.finditer(r"(?i)\b(?:about|approx\.?|approximately|~|est\.?|estimated)?\s*(\d{1,4})\s+pages?\b", text):
            if self.kind in ("email", "text"):
                ex.add("est_pages", m.group(1), SRC_REGEX, 0.7)

    # ----- attorneys
    def _attorneys(self, text: str) -> list[Attorney]:
        found: list[Attorney] = []
        if self.is_transcript:  # appearances are on the cover page; the body is dialogue
            text = text.split("")[0]
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
            if self.profile.name and any(self.profile.name.lower() in b.lower() for b in block) and \
                    not any(re.search(r"(?i)\besq\b", b) for b in block):
                continue
            upper = is_mostly_upper(" ".join(block))
            party, client, firm, firm_idx = "", [], "", -1
            names, addr, phone, fax, email_ = [], [], "", "", ""
            placeholder = ""
            caps_names = False  # transcript style: everything in capitals
            for i, l in enumerate(block):
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
                if m and not ADDRESS_RE.search(l):
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
                if not firm and FIRM_RE.search(l) and not ADDRESS_RE.search(l):
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
    out: list[Attorney] = []
    own_name = (profile.name.lower() if profile and profile.name else "\0")
    own_email = (profile.email.lower() if profile and profile.email else "\0")
    for a in atts:
        if a.name.lower() == own_name or (a.email and a.email.lower() == own_email):
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
    words = [w for w in re.sub(r"[^a-z ]", "", n.lower()).split() if len(w) > 1]
    return f"{words[0]} {words[-1]}" if len(words) >= 2 else " ".join(words)
