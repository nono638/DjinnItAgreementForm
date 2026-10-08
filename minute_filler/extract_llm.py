"""Optional second opinion from a local Ollama model, chosen in Settings -> AI (default gemma4:e2b; see MODELS).

The model gets the text (e-mail body, OCR of a photo, a transcript's title page(s))
and answers in JSON shaped like TEMPLATE (or, if that reply can't be read, constrained
by SCHEMA). Its answers are checked against the text and given a lower confidence than
labelled regex hits, so they mostly fill gaps; a date the text neither names nor implies is
dropped (check_dates). Whether it may tick who ordered follows Settings.ai_ticks.
If there is no text at all (OCR unavailable), the image itself is sent.
"""
from __future__ import annotations

import json
import re
from datetime import date

from .extract_regex import (COURTS, DELIVERY_WORDS, MONTH_RE, MONTHS, PHONE_RE, PLACEHOLDER_RE, RegexExtractor,
                            dedupe_attorneys, find_dates, fmt_date, fmt_phone, looks_like_transcript,
                            mentions_reporter, norm_index, normalize_caption, smart_title, tidy_name, title_pages)
from .ingest import Ingested
from .models import Attorney, Extraction, PROC_TYPES, SRC_AI, join_names, split_names
from .settings import Settings

MAX_CHARS = 6000  # the input text is cut here, to keep the question short for a small local model
AI_CONF = 0.5     # below a labelled rules hit (0.8-0.95), so the AI's answer mostly fills gaps
AI_UNSURE = 0.35  # AI dates the text implies without writing them out (see check_dates)

# What joins the first and last day of a range: "9/14 through 9/18/2026"
_RANGE_JOIN = re.compile(r"\s*(?:-|through|thru|to|until)\s*", re.I)
# One month, two days, one year: "September 14-16, 2026"
_MONTH_SPAN = re.compile(MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|through|thru|to)\s*(\d{1,2})"
                         r"(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
# A day named without its date ("yesterday's hearing", "last Tuesday"): the prompt asks the model to work it out
_RELATIVE_DAY = re.compile(r"\b(?:yesterday|today|tomorrow|tonight|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b",
                           re.I)
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december"]


def _day(v: str) -> date:
    """'9/14/2026' -> date(2026, 9, 14); ValueError for anything else."""
    m, d, y = (int(p) for p in v.split("/"))
    return date(y, m, d)


def _in_words(day: date, source: str) -> bool:
    """The text writes the day in words find_dates doesn't read ('14 September 2026', 'the 28th day of
    September, 2026'): the day's number and its month's name side by side, and its year. ('Part 14 ... September
    2026' is no 9/14/2026.)"""
    name = _MONTH_NAMES[day.month - 1]
    month = "(?:" + "|".join({name, name[:3]} | ({"sept"} if day.month == 9 else set())) + r")\b\.?"
    num = rf"(?<!\d){day.day}(?:st|nd|rd|th)?(?!\d)"
    side_by_side = rf"(?i){num}\s+(?:day\s+of\s+)?{month}|\b{month}\s+(?:the\s+)?{num}"
    return bool(re.search(side_by_side, source) and re.search(rf"\b{day.year}\b", source))


def check_dates(dates: list[str], source: str) -> tuple[list[str], bool]:
    """The AI's proceeding dates ('M/D/YYYY') that the text supports, and whether it writes all of them out.

    Written out: a date find_dates reads ('10/1/2026', 'Oct. 1'), the first and last day of 'September 14-16,
    2026', or one in words find_dates doesn't read (see _in_words). Implied, kept less surely: a day inside a
    range the text names ('9/14 through 9/18/2026', 'September 14-16, 2026', at most a month long), or a day
    within a month of today when the text names one as 'yesterday' or 'Tuesday'. Any other date is taken as
    made up and dropped.
    """
    found = find_dates(source, allow_yearless=True)
    written = {v for _, _, v in found}
    spans = [(_day(a), _day(b)) for (_, end, a), (start, _, b) in zip(found, found[1:])
             if _RANGE_JOIN.fullmatch(source[end:start])]
    for m in _MONTH_SPAN.finditer(source):
        month, year = MONTHS[m.group(1)[:3].lower()], int(m.group(4))
        a, b = fmt_date(year, month, int(m.group(2))), fmt_date(year, month, int(m.group(3)))
        if a and b:
            written |= {a, b}
            spans.append((_day(a), _day(b)))
    relative = bool(_RELATIVE_DAY.search(source))
    kept, sure = [], True
    for v in dates:
        try:
            day = _day(v)
        except ValueError:
            continue
        if v in written or _in_words(day, source):
            kept.append(v)
        elif any(a < day < b and (b - a).days <= 31 for a, b in spans) or \
                (relative and abs((day - date.today()).days) <= 31):
            kept.append(v)
            sure = False
    return kept, sure

# JSON schema for the slower fallback call (Ollama's `format`)
SCHEMA = {
    "type": "object",
    "properties": {
        "court": {"type": "string", "description": "Court type, e.g. Supreme, Civil, Family"},
        "county": {"type": "string"},
        "part": {"type": "string", "description": "Courtroom part: a number or a letter code, e.g. 25, TR-3, MDP"},
        "judge": {"type": "string", "description": "Judge or justice name without titles"},
        "case_name": {"type": "string", "description": "Caption, e.g. 'Smith v. Jones'"},
        "index_number": {"type": "string", "description": "Index/docket number like 123456/2024"},
        "proceeding_dates": {"type": "array", "items": {"type": "string"},
                             "description": "Dates of the proceedings whose minutes are wanted, M/D/YYYY"},
        "proceeding_types": {"type": "array", "items": {"type": "string", "enum": PROC_TYPES + ["Other"]}},
        "other_proceeding": {"type": "string"},
        "delivery": {"type": "string", "enum": ["", "Regular", "Expedited", "Daily", "Immediate"]},
        "copies": {"type": "string"},
        "attorneys": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}, "firm": {"type": "string"}, "address": {"type": "string"},
                    "phone": {"type": "string"}, "email": {"type": "string"},
                    "party": {"type": "string", "description": "Who they represent"},
                    "is_requester": {"type": "boolean", "description": "True if this person is asking for the minutes"},
                },
                "required": ["name", "firm", "address", "phone", "email", "party", "is_requester"],
            },
        },
    },
    "required": ["court", "county", "part", "judge", "case_name", "index_number", "proceeding_dates",
                 "proceeding_types", "other_proceeding", "delivery", "copies", "attorneys"],
}

PROMPT = """You extract details for a New York court reporter's "Minute Agreement Form" (an order for a transcript).
The input is {kind}. Read it and fill the JSON fields.
Rules:
- Only use facts stated in the input. Use "" or [] when something is not given. Never invent values.
- The court reporter {reporter} is NOT an attorney; do not list them.
- proceeding_dates are the dates of the court proceedings (not the date an e-mail was sent), formatted M/D/YYYY.
  Today is {today}; resolve relative dates like "last Tuesday" against it.
- Attorneys: law firms, government offices or people representing parties: ONE entry per firm or office, with
  all of its attorneys in "name", separated by commas ("Jane Doe, John Roe"), and its street address in
  "address". Mark is_requester true for whoever is ordering the minutes.
- Skip entries like "Unrepresented" or "No one appeared".

Answer with ONLY a JSON object shaped exactly like this template:
{template}

INPUT:
\"\"\"
{text}
\"\"\"
"""

# The models Settings -> AI offers whether or not they are installed yet: name -> what to know about it.
# Any other model Ollama has can be chosen too (the box lists the installed ones, and takes a typed name).
MODELS = {
    "gemma4:e2b": "smaller and faster: the usual choice",
    "gemma4:e4b": "larger and more accurate, but much slower on a laptop without a graphics card (GPU)",
}

# The reply's shape, shown to the model in the prompt (for the fast call: gemma4 ignores SCHEMA when not thinking)
TEMPLATE = json.dumps({
    "court": "", "county": "", "part": "", "judge": "", "case_name": "", "index_number": "",
    "proceeding_dates": ["M/D/YYYY"], "proceeding_types": ["one of " + "/".join(PROC_TYPES) + "/Other"],
    "other_proceeding": "", "delivery": "Regular/Expedited/Daily/Immediate or empty", "copies": "",
    "attorneys": [{"name": "", "firm": "", "address": "", "phone": "", "email": "", "party": "",
                   "is_requester": False}],
})


def parse_json(text: str) -> dict:
    """The JSON object in a model's reply. Lenient: strips ``` fences and prose around the first {...}
    object, and blanks filler answers such as "N/A" (see _blank_na). Raises ValueError when there is none."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    data = json.loads(text[start:end + 1])
    return _blank_na(data)


def _blank_na(v):
    """Small models write "N/A", "unknown" or the template's own "M/D/YYYY" for a missing value: these
    become "", and blank list items are dropped, all the way down."""
    if isinstance(v, dict):
        return {k: _blank_na(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_blank_na(x) for x in v if _blank_na(x) not in ("", None)]
    if isinstance(v, str) and v.strip().lower() in ("n/a", "na", "none", "unknown", "null", "not given", "m/d/yyyy"):
        return ""
    return v


def no_answer(e: Exception) -> bool:
    """Ollama gave no answer at all (it timed out, or nothing listens at the host), as against a reply that
    couldn't be read: asking again would only wait the whole timeout a second time."""
    try:
        import httpx
        transport = (httpx.TransportError,)
    except ImportError:  # pragma: no cover
        transport = ()
    return isinstance(e, (TimeoutError, ConnectionError, *transport))


def client_options(host: str) -> dict:
    """Options for ollama.Client (passed on to httpx): Ollama on this computer is reached directly, not through
    a proxy set up in Windows (which may not pass "localhost" on)."""
    name = re.sub(r"^\w+://", "", (host or "").strip()).split("/")[0].lower()
    name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name  # without the port
    local = name in ("", "localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0") or name.startswith("[::1]")
    return {"trust_env": False} if local else {}


class OllamaExtractor:
    """Asks the Ollama model in Settings about one document at a time and returns what it found as an
    Extraction, checked against the document's text."""
    def __init__(self, settings: Settings):
        """settings: the Ollama host, model and timeout, and the reporter's name (never taken for an attorney)."""
        self.s = settings
        self._client = None

    @property
    def client(self):
        """The ollama.Client for Settings' host, made on first use (with the AI timeout)."""
        if self._client is None:
            import ollama
            self._client = ollama.Client(host=self.s.ollama_host, timeout=self.s.ai_timeout,
                                         **client_options(self.s.ollama_host))
        return self._client

    def installed_models(self, timeout: float = 3) -> list[str]:
        """The models Ollama has installed; raises when Ollama doesn't answer within `timeout` seconds."""
        import ollama
        c = ollama.Client(host=self.s.ollama_host, timeout=timeout, **client_options(self.s.ollama_host))
        try:
            return [m.model for m in c.list().models]
        finally:
            c.close()

    def status(self) -> tuple[bool, str]:
        """(ok, message) - checks that Ollama runs and the model is installed."""
        try:
            models = self.installed_models()
        except Exception:
            return False, "Ollama is not running - using rules only."
        want = self.s.ollama_model
        if not any(m == want or m.split(":")[0] == want or m == f"{want}:latest" for m in models):
            return False, f"Ollama model '{want}' is not installed (ollama pull {want})."
        return True, f"AI ready ({want})"

    def extract(self, ing: Ingested) -> Extraction:
        """The model's reading of one document. Raises when Ollama fails or the reply has no JSON. A reply
        that isn't JSON is asked for again with the schema; an Ollama that doesn't answer (a timeout, a
        connection error) is not asked twice, which would take twice the AI timeout."""
        text = ing.text.strip()
        if ing.kind == "pdf":  # a transcript's title page(s) carry everything
            text = title_pages(text)
        kind = {"email": "an e-mail", "image": "OCR text of a photographed court document",
                "pdf": "text of a court document"}.get(ing.kind, "free text")
        images = None
        if not text and ing.images:
            kind, images, text = "an image of a court document", ing.images[:1], "(see image)"
        prompt = PROMPT.format(kind=kind, reporter=self.s.profile.name or "(unknown)", template=TEMPLATE,
                               today=date.today().strftime("%A %m/%d/%Y"), text=text[:MAX_CHARS])
        msg = {"role": "user", "content": prompt}
        if images:
            msg["images"] = images
        base = dict(model=self.s.ollama_model, messages=[msg], options={"temperature": 0}, keep_alive="15m")
        # gemma4 ignores `format` when thinking is off, so ask for JSON in the prompt (fast) and
        # fall back to a schema-constrained call with thinking (slow, but always valid JSON).
        try:
            data = parse_json(self.client.chat(think=False, **base).message.content)
        except Exception as e:
            if no_answer(e):
                raise
            data = parse_json(self.client.chat(format=SCHEMA, **base).message.content)
        # a model that read the picture itself can't be checked against any text
        return self._to_extraction(data, None if images else text, ing.kind)

    # ------------------------------------------------------------ validate
    def _to_extraction(self, d: dict, source_text: str | None, kind: str = "text") -> Extraction:
        """Validates the model's reply. Small models don't always keep to the template, so any
        value may have the wrong type. With source_text=None nothing can be checked against it.

        kind is the input's Ingested.kind. Whether the model may tick an attorney as the orderer (its
        is_requester) follows Settings.ai_ticks: "text" (the default) only for an e-mail or pasted text
        (kind "email" or "text", and not a transcript saved as text; a transcript or a photo lists who
        appeared, not who ordered), "never" not at all, "any" from every input.

        The source is cleaned as the rules clean it (RegexExtractor._clean: dashes, ligatures, OCR digits),
        so that '712345–2021' in the text confirms the model's '712345/2021'.
        """
        ex = Extraction()
        if not isinstance(d, dict):
            return ex
        check = source_text is not None
        if check:
            source_text = RegexExtractor._clean(source_text)
        src_lower = (source_text or "").lower()
        src_digits = re.sub(r"\D", "", src_lower)
        may_tick = self.s.ai_ticks == "any" or (self.s.ai_ticks == "text" and kind in ("email", "text")
                                                and not looks_like_transcript(source_text or ""))

        def seen(v: str) -> bool:
            """Loose check that a value really appears in the source (guards against inventions)."""
            toks = [t for t in re.findall(r"[a-z0-9]{3,}", v.lower())]
            return not check or not toks or sum(t in src_lower for t in toks) / len(toks) >= 0.6

        def near_copies(n: str) -> bool:
            """The number n stands within two words of 'copy'/'copies' in the source ('2 copies',
            'copies: 2', 'original and two copies'); not just anywhere in a text that mentions copies."""
            words = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five"}
            num = rf"(?:{n}|{words[n]})" if n in words else re.escape(n)
            return not check or bool(re.search(rf"\b{num}\s+(?:\w+\s+){{0,2}}cop(?:y|ies)\b|"
                                               rf"\bcop(?:y|ies)\W+(?:\w+\s+){{0,2}}{num}\b", src_lower))

        def items(v) -> list:
            """A list answer as it is; a single value as a list of one."""
            return v if isinstance(v, list) else [v] if v else []

        def tidy(v) -> str:
            """An answer as one clean string: lists joined, anything but text or a number blank, and
            'dana smith' or 'DANA SMITH' in Title Case."""
            if isinstance(v, list):
                v = ", ".join(x for x in v if isinstance(x, str) and x)
            if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                return ""
            v = " ".join(str(v or "").split()).strip(" ,;")
            if v.islower() and len(v) > 2:
                v = smart_title(v.upper())
            return tidy_name(v)

        courts = {name.lower(): name for _, name in COURTS}
        for key, field in (("court", "court"), ("county", "county"), ("judge", "judge"), ("case_name", "case_name"),
                           ("other_proceeding", "proc_other")):
            v = tidy(d.get(key))
            if key == "court":
                v = re.sub(r"(?i)\s*court\b.*$", "", v).strip()
                v = courts.get(v.lower(), "")
            if key == "county":
                v = RegexExtractor._match_county(re.sub(r"(?i)\s*county\b|\bcounty of\s*", "", v).strip()) or ""
            if key == "judge":
                v = re.sub(r"(?i)^(hon\.?|honorable|justice|judge)\s+|,?\s*j\.?s\.?c\.?$", "", v).strip()
            if key == "case_name":
                v = normalize_caption(v)
            if v and seen(v):  # small models invent counties/courts - only keep what the text says
                ex.add(field, v, SRC_AI, AI_CONF)
        part = re.sub(r"(?i)^part\s*", "", tidy(d.get("part"))).strip().upper()
        # numbers ("25", "TR-3") or letters ("MDP"), and only a part that the text names
        if re.fullmatch(r"[A-Z]{0,4}-?\d{1,3}[A-Z]?|[A-Z]{1,6}(?:-[A-Z0-9]{1,3})?", part) and \
                (not check or re.search(rf"(?<![a-z0-9]){re.escape(part.lower())}(?![a-z0-9])", src_lower)):
            ex.add("part", part, SRC_AI, AI_CONF)
        m = re.search(r"(\d{3,7})\s*[-/]\s*(\d{4}|\d{2})", tidy(d.get("index_number")))
        v = norm_index(m.group(1), m.group(2)) if m else None
        # the number and its year as one token of the source ('712345/2021', '712345-21', '712345 of 2021'):
        # the number alone could be a phone extension
        in_source = {norm_index(a, b) for a, b in re.findall(r"(\d{3,7})\s*(?:[-/]|\s+of\s+)\s*(\d{4}|\d{2})[a-z]?\b",
                                                             src_lower)}
        if v and (not check or v in in_source):
            ex.add("index_no", v, SRC_AI, AI_CONF)
        dates = []
        for raw in items(d.get("proceeding_dates")):
            dates += [v for _, _, v in find_dates(str(raw))]
        # only days the text names or implies (check_dates): a small model makes dates up
        dates, sure = check_dates(list(dict.fromkeys(dates)), source_text) if check else (list(dict.fromkeys(dates)), True)
        if dates:
            ex.add("dates", ", ".join(dates), SRC_AI, AI_CONF if sure else AI_UNSURE)
        for t in items(d.get("proceeding_types")):
            if isinstance(t, str) and t in PROC_TYPES:
                ex.add_proc(t, 0.55)
        # Only trust delivery/copies when the text actually asks for them, in the words the rules read
        # (DELIVERY_WORDS: "please reply immediately" asks for no speed).
        dv = d.get("delivery")
        said = re.search(DELIVERY_WORDS[dv], src_lower) if isinstance(dv, str) and dv in DELIVERY_WORDS else None
        if said:  # (the words go with it: the window quotes them when it asks which speed the job is)
            ex.add("delivery", dv, SRC_AI, 0.45, note=said.group(0))
        if tidy(d.get("copies")).isdigit() and near_copies(tidy(d["copies"])):
            ex.add("copies", tidy(d["copies"]), SRC_AI, 0.45)

        for a in items(d.get("attorneys")):
            if isinstance(a, str):
                a = {"name": a}
            if not isinstance(a, dict):
                continue
            name, firm = tidy(a.get("name")), tidy(a.get("firm"))
            if not (name or firm) or PLACEHOLDER_RE.match(name or firm) or mentions_reporter(self.s.profile, name):
                continue
            # each on its own: an invented name next to a real firm goes, and the firm stays (and the other way round)
            name, firm = (name if seen(name) else ""), (firm if seen(firm) else "")
            if not (name or firm):
                continue
            phone = ""
            pm = PHONE_RE.search(tidy(a.get("phone")))
            if pm and (not check or "".join(pm.groups()) in src_digits):
                phone = fmt_phone(pm)
            mail = a.get("email").strip() if isinstance(a.get("email"), str) else ""
            if "@" not in mail or (check and mail.lower() not in src_lower):
                mail = ""
            addr = tidy(a.get("address"))
            if PHONE_RE.search(addr) and len(re.sub(r"[\d\W]", "", addr)) < 3 or "@" in addr or not seen(addr):
                addr = ""  # a phone/e-mail in the wrong slot, or an invented address
            m = re.match(r"^(.*),\s*([^,]+,\s*(?:[A-Z]{2}|New York|New Jersey)\.?\s+\d{5}.*)$", addr)
            if m:  # "123 Main St, Suite 4, New York, NY 10001" -> street / city line
                addr = f"{m.group(1)}\n{m.group(2)}"
            # ticked on true (or the word "true") only, as bool("false") would tick; and only where Settings.ai_ticks
            # lets the AI tick (may_tick)
            asked = a.get("is_requester")
            ticked = asked is True or (isinstance(asked, str) and asked.strip().lower() == "true")
            # the names written as the rules write them: 'Dana Smith, Esq. and Sam Poe' -> 'Dana Smith, Sam Poe'
            ex.attorneys.append(Attorney(
                name=join_names(split_names(name)), firm=firm, address=addr, phone=phone,
                email=mail, party=tidy(a.get("party")),
                source=SRC_AI, checked=ticked and may_tick))
        # one entry per firm, as the rules give them (a model may list a firm's attorneys one by one)
        ex.attorneys = dedupe_attorneys(ex.attorneys, self.s.profile)
        return ex
