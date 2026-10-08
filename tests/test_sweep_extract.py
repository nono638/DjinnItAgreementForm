"""Regressions from the extraction bug sweep and its review: documents mistaken for invoices (or transcripts for
invoices), ZIP+4 codes read as index numbers (and index numbers as ZIP+4 codes), a phone number after 'p.' read as
pages, the AI's answers checked too loosely (names, index numbers, dates,
copies, speeds; which inputs it may tick from, Settings.ai_ticks), the AI asked twice when Ollama doesn't answer,
captions, parts and judges read out of running text, dates and page counts misread, and the text cleaned before
it is read (ligatures, dashes, OCR letters in numbers). Every document and name here is made up."""
import re
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest

from minute_filler.extract_llm import AI_CONF, AI_UNSURE, SCHEMA, OllamaExtractor
from minute_filler.extract_regex import DELIVERY_WORDS, RegexExtractor, find_dates
from minute_filler.ingest import Ingested, ingest_text
from minute_filler.merge import pool
from minute_filler.models import DELIVERY_TYPES, Extraction
from minute_filler.settings import Settings

from helpers import PAT, best

x = RegexExtractor(PAT)

PAGE1 = ("   101\n 1   SUPREME COURT OF THE STATE OF NEW YORK\n 2   COUNTY OF QUEENS: PART 12\n 3   JANE ROE\n"
         " 4   -against-\n 5   SAM POE\n 6   INDEX NO. 712345/2021\n 7   June 3, 2026\n 8   HON. DANA LANE\n"
         " 9   A P P E A R A N C E S\n10   SMITH LAW, BY: DANA SMITH, ESQ.\n11   Attorneys for Plaintiff\n12\n13\n")
PAGE2 = "   102\n 1   Q.  Did you receive the\n 2   invoice that you sent?\n 3   A.  Yes.\n 4\n 5\n"
# What an e-mail asking for minutes says, with the attorney's signature
MAIL = ("From: Dana Smith <dana@smithlaw.example.com>\nSubject: minutes\n\nPlease send the minutes of Jane Roe v. Sam "
        "Poe, Index No. 712345/2021, held 10/1/2026, before judge Lane. Reply immediately. My ext. is 712345. "
        "Original and two copies please.\n\nDana Smith\nLaw Offices of Dana Smith\n100 Main Street\n"
        "New York, New York 10007-2015\n")


def ai(reply: dict, source=MAIL, kind="email", **settings) -> Extraction:
    """The AI's reply as the extractor checks it against the source text of an input of that kind."""
    s = Settings()
    s.profile = PAT
    for k, v in settings.items():
        setattr(s, k, v)
    return OllamaExtractor(s)._to_extraction(reply, source, kind)


def part(text: str, kind: str = "email") -> list[tuple[str, float]]:
    """The parts the rules read in text, as (value, confidence)."""
    ex = Extraction()
    x.kind = kind
    x._part(text, text, ex)
    return [(c.value, c.confidence) for c in ex.fields.get("part", [])]


# ----------------------------------------------------------------- A1, A2, A13: what kind of document, index numbers

def test_testimony_wrapping_onto_a_line_starting_invoice_is_still_a_transcript():
    """A numbered page is a transcript whatever a line of it starts with; its page count is still read."""
    ex = x.extract(Ingested("Roe transcript.pdf", "pdf", text=PAGE1 + "\f" + PAGE2, page_count=40))
    assert ex.doc_kind == "transcript"
    assert best(ex, "est_pages") == "40"


def test_an_invoice_heading_needs_an_invoice_behind_it():
    """A line 'Invoice' on page 1 with a number, To: or an amount is an invoice; the word alone is not, nor an
    invoice that starts on page 2."""
    assert x.extract(ingest_text("Invoice No. 2026-12\nTo: Smith Law\nTotal: $94.50\n")).doc_kind == "invoice"
    assert x.extract(ingest_text("Invoice\nTo: Smith Law, attn: billing@example.com\nTitle: Roe v Poe\n")).doc_kind == "invoice"
    assert x.extract(ingest_text("invoice\nplease send the minutes of Roe v. Poe\n")).doc_kind != "invoice"
    assert x.extract(ingest_text("Roe v. Poe\n\f\nInvoice No. 12\nTotal $5.00\n")).doc_kind != "invoice"


def test_a_zip_plus_4_code_after_a_state_is_no_index_number():
    """'New York, New York 10007-2015' and 'N.Y. 11415-2010' are addresses; the labelled index number still wins."""
    ex = x.extract(ingest_text(MAIL))
    assert [c.value for c in ex.fields["index_no"]] == ["712345/2021"]
    for line in ("Kew Gardens, N.Y. 11415-2010\n", "Jamaica, NY 11415-2010\n", "100 Main St., Newark, New Jersey 07102-1234\n",
                 "New York, New York 10007-2015\n"):
        assert "index_no" not in x.extract(ingest_text(line)).fields, line
    assert best(x.extract(ingest_text("the number 712345-2021 please\n")), "index_no") == "712345/2021"


def test_an_index_number_with_a_letter_after_the_year_is_labelled():
    """'Index No. 712345/2021E' is the labelled number 712345/2021 (the letter is left out, as the records write it)."""
    ex = x.extract(ingest_text("Index No. 712345/2021E\n"))
    assert [(c.value, c.confidence) for c in ex.fields["index_no"]] == [("712345/2021", 0.95)]


# ----------------------------------------------------------------- A4: the AI's answers are checked one by one

def test_ai_invented_name_next_to_a_real_firm_is_dropped():
    """The name and the firm must each be in the text: 'Invented Person' of the real 'Law Offices of Dana Smith' goes,
    and so does the invented firm of the real Dana Smith, who stays, ticked as the AI said. With both invented, the
    entry goes."""
    ex = ai({"attorneys": [{"name": "Invented Person", "firm": "Law Offices of Dana Smith"},
                           {"name": "Dana Smith", "firm": "Law Offices of Dana Smith"}]})
    assert [(a.name, a.firm) for a in ex.attorneys] == [("Dana Smith", "Law Offices of Dana Smith")]
    ex = ai({"attorneys": [{"name": "Dana Smith", "firm": "Nowhere & Partners LLP", "is_requester": True}]})
    assert [(a.name, a.firm, a.checked) for a in ex.attorneys] == [("Dana Smith", "", True)]
    assert ai({"attorneys": [{"name": "Invented Person", "firm": "Nowhere & Partners LLP"}]}).attorneys == []


def test_ai_is_requester_written_false_does_not_tick():
    """Only a plain true (or the word 'true') ticks the orderer; the string "false" does not, nor "yes" or 1."""
    for asked, want in ((True, True), ("true", True), ("TRUE", True), ("false", False), (False, False), ("yes", False), (1, False)):
        ex = ai({"attorneys": [{"name": "Dana Smith", "firm": "Law Offices of Dana Smith", "is_requester": asked}]})
        assert ex.attorneys[0].checked is want, asked


def test_ai_ticks_setting_says_which_inputs_may_tick():
    """Settings.ai_ticks: "text" ticks from an e-mail or pasted text only, "any" from a transcript or photo too,
    "never" from nothing."""
    reply = {"attorneys": [{"name": "Dana Smith", "firm": "Law Offices of Dana Smith", "is_requester": True}]}
    title = "APPEARANCES: LAW OFFICES OF DANA SMITH, BY: DANA SMITH, ESQ."
    assert ai(reply, source=title, kind="pdf").attorneys[0].checked is False
    assert ai(reply, source=title, kind="image").attorneys[0].checked is False
    assert ai(reply, source=title, kind="pdf", ai_ticks="any").attorneys[0].checked is True
    assert ai(reply, source=title, kind="image", ai_ticks="any").attorneys[0].checked is True
    assert ai(reply, kind="email").attorneys[0].checked is True
    assert ai(reply, kind="text").attorneys[0].checked is True
    assert ai(reply, kind="email", ai_ticks="never").attorneys[0].checked is False


def test_ai_index_number_needs_its_year_in_the_text():
    """The AI's '712345/2024' is not confirmed by a phone extension 712345: the number and year are one token."""
    assert "index_no" not in ai({"index_number": "712345/2024"}).fields
    assert best(ai({"index_number": "712345/2021"}), "index_no") == "712345/2021"
    assert best(ai({"index_number": "712345-2021"}, source="index 712345/21 please"), "index_no") == "712345/2021"
    assert best(ai({"index_number": "712345/2021"}, source="Index No. 712345 of 2021"), "index_no") == "712345/2021"


def test_ai_invented_date_is_dropped():
    """A date the text never names goes; the one it names stays (with or without its year)."""
    assert "dates" not in ai({"proceeding_dates": ["5/5/2019"]}).fields
    assert best(ai({"proceeding_dates": ["10/1/2026", "5/5/2019"]}), "dates") == "10/1/2026"
    y = find_dates("Oct. 1", allow_yearless=True)[0][2]
    assert best(ai({"proceeding_dates": [y]}, source="the hearing on Oct. 1 in Roe v. Poe"), "dates") == y


def test_ai_copies_need_the_number_near_the_word_copies():
    """'Original and two copies' confirms 2, not 4, which only stands somewhere in a text that mentions copies."""
    assert "copies" not in ai({"copies": "4"}).fields
    assert best(ai({"copies": "2"}), "copies") == "2"
    assert best(ai({"copies": "3"}, source="copies: 3"), "copies") == "3"
    assert best(ai({"copies": "3"}, source="3 certified copies"), "copies") == "3"


def test_immediately_alone_asks_for_no_speed():
    """'Reply immediately' is no Immediate order, for the rules and for the AI's answer alike; 'immediate copy' is."""
    assert "delivery" not in ai({"delivery": "Immediate"}).fields
    assert best(ai({"delivery": "Immediate"}, source="an immediate copy please"), "delivery") == "Immediate"
    assert not re.search(DELIVERY_WORDS["Daily"], "my daily routine")
    assert not re.search(DELIVERY_WORDS["Regular"], "the regular judge")
    assert re.search(DELIVERY_WORDS["Daily"], "a daily copy") and re.search(DELIVERY_WORDS["Regular"], "at your regular rate")
    assert "delivery" not in x.extract(ingest_text("please reply immediately")).fields
    assert best(x.extract(ingest_text("From: a@example.com\n\nan immediate copy please")), "delivery") == "Immediate"


# ----------------------------------------------------------------- A14: the AI call and the model's choices

class FakeClient:
    """An ollama.Client whose chat answers from a list (an exception is raised), counting the calls."""
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def chat(self, **kw):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(message=SimpleNamespace(content=a))


def test_ai_is_not_asked_twice_when_ollama_does_not_answer():
    """A timeout or a connection error is raised at once, not retried with the slow schema call (2x the timeout);
    a reply that isn't JSON is asked for again."""
    s = Settings()
    s.profile = PAT
    ing = Ingested("Pasted text 1", "text", text="please send the minutes of Roe v. Poe, judge Lane")
    for err in (httpx.ConnectTimeout("timed out"), httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")):
        ex = OllamaExtractor(s)
        ex._client = FakeClient([err, '{"judge": "Lane"}'])
        with pytest.raises(httpx.TransportError):
            ex.extract(ing)
        assert ex._client.calls == 1
    ex = OllamaExtractor(s)
    ex._client = FakeClient(["I cannot help with that", '{"judge": "Lane"}'])
    assert best(ex.extract(ing), "judge") == "Lane"
    assert ex._client.calls == 2


def test_the_speeds_offered_include_immediate_and_the_schema_allows_letter_parts():
    """Immediate is among the delivery speeds by name, and the AI's schema no longer asks for a part in digits
    only: its 'MDP' is kept when the text names it."""
    assert "Immediate" in DELIVERY_TYPES
    assert "digits only" not in SCHEMA["properties"]["part"]["description"]
    assert best(ai({"part": "MDP"}, source="PART MDP"), "part") == "MDP"


# ----------------------------------------------------------------- A5, A6, A7: caption, part and judge in running text

def test_a_subject_line_caption_stops_before_the_index_number():
    """'Re: Jane Roe v. Sam Poe, Index No. 712345/2021, Part 12' names the case 'Jane Roe v. Sam Poe', and
    'Title: Roe v. Poe - minutes of 10/1/2026' the case 'Roe v. Poe' (with no index number or part)."""
    ex = x.extract(ingest_text("Re: Jane Roe v. Sam Poe, Index No. 712345/2021, Part 12\n\nplease send the minutes"))
    assert best(ex, "case_name") == "Jane Roe v. Sam Poe"
    ex = x.extract(ingest_text("Title: Roe v. Poe - minutes of 10/1/2026\n"))
    assert best(ex, "case_name") == "Roe v. Poe"
    assert best(ex, "index_no") == "" and best(ex, "part") == ""


def test_part_of_the_transcript_is_no_part():
    """'send part 2 of the transcript' and 'part of 25 pages' name no part; a court's 'IAS PART 12', 'Trial Part 5',
    'Part: TAP-A', 'PART MDP', an e-mail's 'in Part 19' and a lowercase 'part 7.' still do."""
    assert part("Please send part 2 of the transcript") == []
    assert part("it is part of 25 pages") == []
    assert part("PART OF 25 PAGES", kind="pdf") == []
    assert part("the first part of the record, part 12 of it") == []
    for text, want in (("SUPREME COURT, IAS PART 12\n", "12"), ("Trial Part 5", "5"), ("Part: TAP-A", "TAP-A"),
                       ("PART MDP", "MDP"), ("Part TR-3", "TR-3")):
        assert part(text, kind="pdf")[0] == (want, pytest.approx(0.9, abs=0.051)), text
    assert part("before Justice Rodriguez in Part 19 on 9/14")[0] == ("19", 0.75)
    assert part("judge lopez, part 7. thanks")[0] == ("7", 0.5)
    assert part("part no. 7, judge lopez")[0] == ("7", 0.9)


def test_judge_name_stops_before_part_and_loses_its_possessive():
    """'before Judge Lane's part' and 'Justice Lane Part 12' both give 'Lane'."""
    for text in ("before Judge Lane's part", "Justice Lane Part 12", "Justice Lane IAS Part 12", "Justice Dana Lane, Part 12",
                 "Judge Lane Room 405"):
        ex = Extraction()
        x._judge(text, text, ex)
        assert best(ex, "judge") in ("Lane", "Dana Lane"), text
        assert not any(re.search(r"Part|IAS|Room|'s", c.value) for c in ex.fields["judge"]), text


# ----------------------------------------------------------------- A8, A9, A11: dates and page counts

def test_two_dates_with_the_same_digits_are_both_offered():
    """1/12/2026 and 11/2/2026 are two days (they used to collapse to one); '712345-24' and '712345/2024' are one number."""
    a, b = Extraction(), Extraction()
    a.add("dates", "1/12/2026", "regex", 0.5)
    b.add("dates", "11/2/2026", "regex", 0.5)
    b.add("dates", "Jan. 12, 2026", "AI", 0.5)
    assert [c.value for c in pool([a, b])["dates"]] == ["1/12/2026", "11/2/2026"]
    a, b = Extraction(), Extraction()
    a.add("index_no", "712345-24", "regex", 0.5)
    b.add("index_no", "712345/2024", "AI", 0.5)
    assert [c.value for c in pool([a, b])["index_no"]] == ["712345-24"]


def test_page_counts_with_thousands_separators_and_ranges():
    """'about 1,250 pages' is 1250, not 250; 'pages 10-25' counts 16 (less surely); 'expect 30-40 pages' is an
    estimate, 40; 'p. 10' is no count."""
    def pages(text):
        return [(c.value, c.confidence) for c in x.extract(ingest_text(text)).fields.get("est_pages", [])]
    assert pages("about 1,250 pages") == [("1250", 0.7)]
    assert pages("roughly 30 pages") == [("30", 0.7)]
    assert pages("10 to 25 pages") == [("25", 0.7)]
    assert pages("expect 30-40 pages") == [("40", 0.7)]
    assert pages("about 200 to 300 pages") == [("300", 0.7)]
    assert pages("pp. 10-25") == [("16", 0.5)]
    assert pages("pages 10 to 25") == [("16", 0.5)]
    assert pages("p. 10") == []
    assert pages("transcript pages 358-380, 23 pages") == [("23", 0.7)]


def test_fractions_and_verbs_are_no_dates():
    """'a 1/2 day hearing', '1/2 of the transcript' and 'the judge may 3 days later' name no day; 'Oct. 7',
    'October 7th', 'on may 3' and a subject line's '10/7' still do."""
    for text in ("a 1/2 day hearing", "send 1/2 of the transcript", "the judge may 3 days later", "1/2 hour", "3/4 page"):
        assert find_dates(text, allow_yearless=True) == [], text
    for text in ("Oct. 7", "October 7th", "on may 3", "Subject: minutes 10/7", "minutes of march 3", "dated sept 14"):
        assert len(find_dates(text, allow_yearless=True)) == 1, text


# ----------------------------------------------------------------- A10: cleaning the text

def test_clean_undoes_ligatures_dashes_and_zero_width_characters():
    """'O\ufb03ces' reads 'Offices', every dash is '-', and zero-width characters are gone."""
    clean = RegexExtractor._clean
    assert clean("Law O\ufb03ces of Dana Smith") == "Law Offices of Dana Smith"
    assert clean("712345\u20102021 \u2011 \u2012 \u2013 \u2014 \u2212") == "712345-2021 - - - - -"
    assert clean("Index\u200b No.\ufeff 712345/2021\u200d\u2060\ufffd") == "Index No. 712345/2021"
    assert clean("Roe\xa0v. Poe \u201cquote\u201d \u2018a\u2019") == "Roe v. Poe \"quote\" 'a'"


def test_clean_puts_digits_back_in_numbers_ocr_misread():
    """O and l/I in an index number or date become 0 and 1 ('712345/2O21', '7l2345/2021', '5/22/2O26', 'June 3, 2O26');
    words keep their letters."""
    clean = RegexExtractor._clean
    assert clean("Index No. 712345/2O21 and 7l2345-2021") == "Index No. 712345/2021 and 712345-2021"
    assert clean("on 5/22/2O26 and June 3, 2O26") == "on 5/22/2026 and June 3, 2026"
    assert clean("OIL/LIO and I/O and Ill-l0 and (212) 555-O1OO") == "OIL/LIO and I/O and Ill-l0 and (212) 555-O1OO"
    assert best(x.extract(ingest_text("Index No. 7l2345/2O21")), "index_no") == "712345/2021"


# ----------------------------------------------------------------- the review of the sweep's own fixes

def test_a_day_before_hearing_or_the_is_a_date_and_only_small_fractions_are_not():
    """'the 9/28 hearing' and 'On 9/28 the court' name a day (the fraction rule had taken them); '2/3 of' doesn't."""
    for text in ("Please send the minutes of the 9/28 hearing", "On 9/28 the court held a hearing"):
        assert len(find_dates(text, allow_yearless=True)) == 1, text
    assert find_dates("2/3 of the pages", allow_yearless=True) == []


def test_lowercase_months_but_may_and_march_need_no_word_before_them():
    """Lowercase months but 'may' and 'march' need no word before them: 'sept 14 and sept 15' are two days and 'on
    oct 1, oct 2 and oct 3' three; 'may' alone is still a verb."""
    assert len(find_dates("minutes from sept 14 and sept 15", allow_yearless=True)) == 2
    assert len(find_dates("on oct 1, oct 2 and oct 3", allow_yearless=True)) == 3
    assert find_dates("the judge may 3 days later", allow_yearless=True) == []


def test_a_state_earlier_on_the_line_does_not_hide_the_index_number():
    """Only a state right before the number makes a ZIP+4 code; 'New York, Queens County, 712345/2021' and 'OK, the
    index is 712345/2021' (OK, a state code) are index numbers."""
    for text in ("Roe v. Poe, Supreme Court of New York, Queens County, 712345/2021\n",
                 "Subject: Roe v. Poe - NY Sup. Ct. 712345/2021\n", "OK, the index is 712345/2021\n"):
        assert best(x.extract(ingest_text(text)), "index_no") == "712345/2021", text


def test_an_invoice_with_a_numbered_item_column_is_still_an_invoice():
    """An item column reading 1, 2, 3 under an INVOICE heading is no transcript's line numbers."""
    text = "INVOICE\nInvoice No. 1234\nBill To: Smith Law\nItem\n1\n2\n3\nRegular Rate: $540.00\nTotal\n"
    assert x.extract(Ingested("Invoice 1234.pdf", "pdf", text=text, page_count=1)).doc_kind == "invoice"


def test_an_email_saying_invoice_to_follow_is_an_email():
    """The e-mail's own To: header is no invoice's addressee, so 'Invoice' on a line of its own doesn't make one."""
    mail = ("From: Dana Smith <dana@smithlaw.example.com>\nTo: Pat Reporter <pat@example.com>\nSubject: Roe v. Poe\n\n"
            "Invoice\nto follow from our office. Please send the minutes expedited, original and 2 copies.\n")
    ex = x.extract(ingest_text(mail))
    assert ex.doc_kind == "email"
    assert best(ex, "delivery") == "Expedited"


def test_a_re_line_cut_before_ind_no_and_a_capital_possessive_judge():
    """'Re: Roe v. Poe, Ind. No. 712345/2021' is the case 'Roe v. Poe'; 'THE HONORABLE DANA LANE'S PART' is Dana Lane."""
    assert best(x.extract(ingest_text("Re: Roe v. Poe, Ind. No. 712345/2021\n")), "case_name") == "Roe v. Poe"
    assert best(x.extract(ingest_text("THE HONORABLE DANA LANE'S PART\n")), "judge") == "Dana Lane"


def test_ai_does_not_tick_from_a_transcript_saved_as_text():
    """A .txt transcript lists who appeared, not who ordered: under "text" the AI's is_requester doesn't tick it."""
    reply = {"attorneys": [{"name": "Dana Smith", "firm": "Smith Law", "is_requester": True}]}
    transcript = ("1    SUPREME COURT OF THE STATE OF NEW YORK\n2    COUNTY OF QUEENS\n3    JANE ROE v. SAM POE\n"
                  "4    APPEARANCES:\n5    SMITH LAW, BY: DANA SMITH, ESQ.\n6\n")
    assert ai(reply, source=transcript, kind="text").attorneys[0].checked is False
    assert ai(reply, source=transcript, kind="text", ai_ticks="any").attorneys[0].checked is True


def test_ai_answers_are_checked_against_the_text_cleaned_as_the_rules_clean_it():
    """An en dash, a letter after the year and an OCR 'O' for a zero still confirm the AI's '712345/2021'."""
    for source in ("Index No. 712345–2021", "Index No. 712345/2021E", "Index No. 712345/2O21"):
        assert best(ai({"index_number": "712345/2021"}, source=source), "index_no") == "712345/2021", source


def test_ai_dates_inside_a_range_or_from_a_relative_day_are_kept_less_surely():
    """The days between '9/14 through 9/18/2026' (or 'September 14-16, 2026') and the day 'yesterday' names are kept at
    AI_UNSURE; a day written in words is sure; a day outside the range, or far from today, is still made up."""
    week = ["9/14/2026", "9/15/2026", "9/16/2026", "9/17/2026", "9/18/2026"]
    ex = ai({"proceeding_dates": week}, source="trial minutes, 9/14 through 9/18/2026 please")
    assert [(c.value, c.confidence) for c in ex.fields["dates"]] == [(", ".join(week), AI_UNSURE)]
    assert best(ai({"proceeding_dates": week[:3]}, source="the trial of September 14-16, 2026"), "dates") == ", ".join(week[:3])
    ex = ai({"proceeding_dates": ["9/14/2026"]}, source="the hearing held on 14 September 2026")
    assert [(c.value, c.confidence) for c in ex.fields["dates"]] == [("9/14/2026", AI_CONF)]
    day = date.today() - timedelta(days=1)
    v = f"{day.month}/{day.day}/{day.year}"
    ex = ai({"proceeding_dates": [v]}, source="the minutes of yesterday's hearing please")
    assert [(c.value, c.confidence) for c in ex.fields["dates"]] == [(v, AI_UNSURE)]
    assert "dates" not in ai({"proceeding_dates": ["9/25/2026"]}, source="trial minutes, 9/14 through 9/18/2026").fields
    assert "dates" not in ai({"proceeding_dates": ["1/5/2019"]}, source="the minutes of yesterday's hearing").fields
    ex = ai({"proceeding_dates": ["9/28/2026"]}, source="on the 28th day of September, 2026 in Part 14")
    assert best(ex, "dates") == "9/28/2026"
    # the day's number and the month apart are no date: 'Part 14 ... September 2026' is no 9/14/2026
    assert "dates" not in ai({"proceeding_dates": ["9/14/2026"]}, source="Part 14, the hearing in September 2026").fields


def test_a_phone_number_after_p_is_no_page_range():
    """A signature's 'p. 212-555-0100' is a phone number, not pages 212 to 555; 'pp. 10-25' still counts 16."""
    mail = "From: Dana Smith <dana@smithlaw.example.com>\n\nThe minutes please.\n\nDana Smith\np. 212-555-0100\n"
    assert "est_pages" not in x.extract(ingest_text(mail)).fields
    assert best(x.extract(ingest_text(mail + "pp. 10-25\n")), "est_pages") == "16"


def test_a_capital_word_that_is_a_state_code_before_an_index_number():
    """'THE MINUTES IN 712345/2021' (IN, Indiana) and 'Roe v. Poe, OR 712345/2021' name index numbers: a ZIP+4 code
    is five digits, a dash and four."""
    for text in ("PLEASE SEND THE MINUTES IN 712345/2021\n", "Roe v. Poe, OR 712345/2021\n", "SEE ME 712345-2021\n"):
        assert best(x.extract(ingest_text(text)), "index_no") == "712345/2021", text
    assert "index_no" not in x.extract(ingest_text("Portland, OR 97201-2021\n")).fields
