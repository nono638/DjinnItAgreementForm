"""The APPEARANCES of a transcript's title page: one entry per firm or office, with all its attorneys, its address,
phone and fax, and whom it represents (appearances.parse_appearances); and a firm being one party everywhere: its
key, the same firm on another day, an e-mail from one of its attorneys (or one quoting a title page), the AI's list
and a record of version 2.0 with a row per attorney (fictional names).

Each title page here copies a layout of real title pages where the rules of version 2.0 went wrong; the last ones
are those the 2.1 bug sweep found."""
import pytest

from minute_filler.appearances import parse_appearances, parse_names
from minute_filler.extract_regex import RegexExtractor, dedupe_attorneys
from minute_filler.ingest import Ingested
from minute_filler.merge import merge
from minute_filler.models import Attorney, CaseInfo, split_names

from helpers import PAT, pat_settings

CAPTION = """{first}



 SUPREME COURT OF THE STATE OF NEW YORK
      COUNTY OF QUEENS:  CIVIL TERM:  PART 14
 --------------------------------------X
 JANE ROE,
                        Plaintiff,         INDEX NO.
                                           712345-2021
            -against-

 SAM POE and EXAMPLE METAL & GLASS CORP.,
                        Defendants.        Jury Trial
 --------------------------------------X
 H E L D:
              123 Courthouse Plaza
              Anytown, New York 10000
              June 3, 2026
 B E F O R E :
              THE HONORABLE MARIA T. ALVAREZ
                   J U S T I C E
 A P P E A R A N C E S :
"""

FOOT = """


                                       PAT REPORTER
                                       Senior Court Reporter
                                                               pr
COPY
"""

BODY = """
                              PROCEEDINGS
              THE COURT:  Good morning.  Appearances, please.
              MR. COUNSEL:  Alex B. Counsel, Counsel & Counsel, for the plaintiff.
"""


def transcript(*title_pages: str) -> str:
    """A transcript's text: the caption, the title page(s) given, then a page of testimony."""
    pages = [CAPTION.format(first="310") + title_pages[0]] + list(title_pages[1:]) + [BODY]
    return "\n\f\n".join(pages)


def read(text: str, name: str = "Roe transcript.pdf") -> list[Attorney]:
    """The attorneys the rules read from a transcript PDF with this text."""
    ex = RegexExtractor(PAT).extract(Ingested(name, "pdf", text=text, page_count=60, first_page_no=310))
    return ex.attorneys


def by_firm(atts: list[Attorney]) -> dict[str, Attorney]:
    return {a.firm or a.name: a for a in atts}


# --- the usual layout: firm, role, address, BY (one firm with two attorneys over two lines), single spaced
USUAL = """
              COUNSEL & COUNSEL, LLP
                  Attorneys for the Plaintiff
              500 Sample Road, Suite 31
                  Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ. and
                   DANA SMITH, ESQ.

              ADVOCATE & PARTNERS LLP
              Attorneys for the Defendants
                  12 Example Avenue
              Hill Town, New York 10003
                  BY:  SAM ADVOCATE, ESQ.
""" + FOOT


def test_one_entry_per_firm_with_all_its_attorneys():
    atts = read(transcript(USUAL))
    assert [(a.firm, a.name) for a in atts] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith"), ("Advocate & Partners LLP", "Sam Advocate")]
    counsel, advocate = atts
    assert counsel.address == "500 Sample Road, Suite 31\nLake Town, New York 10002"
    assert counsel.party == "Plaintiff" and advocate.party == "Defendants"
    assert counsel.names() == ["Alex B. Counsel", "Dana Smith"]
    assert not any(a.checked for a in atts)  # a transcript lists who appeared, not who ordered


# --- the role line before the firm; three attorneys of one firm, on one line and on two; no ZIP code
ROLE_FIRST = """
              Attorneys for the Plaintiff
              COUNSEL & COUNSEL, LLP
              500 Sample Road
              Lake Town, New York
              BY:  ALEX B. COUNSEL, ESQ., DANA SMITH, ESQ. and
                   ROBIN EXAMPLE, ESQ.

              Attorneys for the Defendant Sam Poe
              ADVOCATE & PARTNERS LLP
              12 Example Avenue
              Hill Town, NY
              BY:  SAM ADVOCATE, ESQ., MORGAN BRIGHT, ESQ. and TAYLOR GREEN, ESQ.
""" + FOOT


def test_role_before_the_firm_and_three_attorneys():
    counsel, advocate = read(transcript(ROLE_FIRST))
    assert (counsel.firm, counsel.party, counsel.address) == (
        "Counsel & Counsel, LLP", "Plaintiff", "500 Sample Road\nLake Town, New York")
    assert counsel.names() == ["Alex B. Counsel", "Dana Smith", "Robin Example"]
    assert (advocate.firm, advocate.party, advocate.address) == (
        "Advocate & Partners LLP", "Defendant Sam Poe", "12 Example Avenue\nHill Town, NY")
    assert advocate.names() == ["Sam Advocate", "Morgan Bright", "Taylor Green"]


# --- double spaced: a blank line between all the lines of an entry, two between entries
DOUBLE = """
              COUNSEL & COUNSEL, LLP

              Attorneys for the Plaintiff

              500 Sample Road, Suite 31

              Lake Town, New York 10002

              BY:  ALEX B. COUNSEL, ESQ.


              ADVOCATE & PARTNERS LLP

              Attorneys for the Defendants

              12 Example Avenue

              Hill Town, New York 10003

              BY:  SAM ADVOCATE, ESQ.
""" + FOOT


def test_a_double_spaced_title_page():
    # (before: every line was a block of its own, and nobody was found)
    assert [(a.firm, a.name, a.address, a.party) for a in read(transcript(DOUBLE))] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel", "500 Sample Road, Suite 31\nLake Town, New York 10002",
         "Plaintiff"),
        ("Advocate & Partners LLP", "Sam Advocate", "12 Example Avenue\nHill Town, New York 10003", "Defendants")]


# --- a firm without a firm's mark (no LLP, no &): its name and address must not be lost
UNMARKED = """
              MERIDIAN HOLLOW
              Attorneys for the Plaintiff
              500 Sample Road
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.

              GRAY ROSE BENTON
              Attorneys for the Defendants
              12 Example Avenue
              Hill Town, New York 10003
              BY:  SAM ADVOCATE, ESQ.
""" + FOOT


def test_a_firm_without_a_firm_mark():
    # (before: the firm was taken for the client, and the address before the BY line was lost)
    assert [(a.firm, a.name, a.address, a.party) for a in read(transcript(UNMARKED))] == [
        ("Meridian Hollow", "Alex B. Counsel", "500 Sample Road\nLake Town, New York 10002", "Plaintiff"),
        ("Gray Rose Benton", "Sam Advocate", "12 Example Avenue\nHill Town, New York 10003", "Defendants")]


# --- city agencies: a head of office ("HON."), a name over two lines, a title under the attorney
AGENCIES = """
              HON. JORDAN EXAMPLE
              Corporation Counsel of the
              City of New York
              Attorneys for the Defendant City of New York
              100 Example Street
              New York, New York 10007
              BY:  DANA SMITH, ESQ.
                   Assistant Corporation Counsel

              OFFICE OF THE QUEENS COUNTY DISTRICT ATTORNEY
              For the People
              125-01 Sample Boulevard
              Kew Gardens, New York 11415
              BY:  CASEY STONE, ESQ.

              NEW YORK CITY POLICE DEPARTMENT
              Legal Bureau
              One Police Plaza, Room 1406
              New York, NY 10038
              BY:  MS. MORGAN BRIGHT
""" + FOOT


def test_city_agencies():
    # (before: the agency's name was cut after its first line, an office without a firm's mark lost its
    # address, and the "Assistant Corporation Counsel" line got in the way)
    atts = read(transcript(AGENCIES))
    assert [(a.firm, a.name) for a in atts] == [
        ("Corporation Counsel of the City of New York", "Dana Smith"),
        ("Office of the Queens County District Attorney", "Casey Stone"),
        ("New York City Police Department Legal Bureau", "Morgan Bright")]
    corp, da, nypd = atts
    assert corp.party == "Defendant City of New York" and corp.address == "100 Example Street\nNew York, New York 10007"
    assert da.party == "People" and da.address == "125-01 Sample Boulevard\nKew Gardens, New York 11415"
    assert nypd.address == "One Police Plaza, Room 1406\nNew York, NY 10038"


# --- a firm whose attorneys are on the next page (the title goes on); the next page repeats the caption
PAGE_ONE = """
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.

              ADVOCATE & PARTNERS LLP
              Attorneys for the Defendant Sam Poe
              12 Example Avenue
              Hill Town, New York 10003

              (Title continues on next page.)
                                                               pr
COPY
"""
PAGE_TWO = """311
 SUPREME COURT OF THE STATE OF NEW YORK
 COUNTY OF QUEENS
 JANE ROE v. SAM POE, et al.
 Index No. 712345/2021
 A P P E A R A N C E S  (Continued):

              BY:  SAM ADVOCATE, ESQ. and
                   TAYLOR GREEN, ESQ.

              BRIGHT FOSTER HAYES
              AND GREEN, LLP
              Attorneys for Example Metal & Glass Corp.
              200 Sample Square
              Hill Town, New York 10003
              BY:  MORGAN BRIGHT, ESQ.
""" + FOOT


def test_a_firm_goes_on_to_the_next_page():
    # (before: the firm on page one and its attorneys on page two were two entries, and the attorneys' one,
    # without a firm or an address, was dropped; the repeated caption was taken for counsel)
    atts = read(transcript(PAGE_ONE, PAGE_TWO))
    assert [(a.firm, a.name) for a in atts] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel"), ("Advocate & Partners LLP", "Sam Advocate, Taylor Green"),
        ("Bright Foster Hayes and Green, LLP", "Morgan Bright")]
    assert atts[1].address == "12 Example Avenue\nHill Town, New York 10003"
    assert atts[1].party == "Defendant Sam Poe"
    assert atts[2].party == "Example Metal & Glass Corp." and atts[2].address == "200 Sample Square\nHill Town, New York 10003"


# --- no blank line between two firms; a firm with no BY line; a lone attorney (no firm)
CROWDED = """
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.
              ADVOCATE & PARTNERS LLP
              Attorneys for the Defendant Sam Poe
              12 Example Avenue
              Hill Town, New York 10003

              QUINN HARTWELL, LLP
              Attorneys for the Defendant Example Metal & Glass Corp.
              400 Orchard Lane
              Orchard Town, New York 10004

              ROBIN EXAMPLE, ESQ.
              Attorney for the Third-Party Defendant
              10 Example Lane
              Hill Town, New York 10003
""" + FOOT


def test_firms_without_a_blank_line_between_them_or_attorneys():
    # (before: the second firm's address and role were given to the first firm)
    atts = read(transcript(CROWDED))
    assert [(a.firm, a.name, a.party) for a in atts] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel", "Plaintiff"),
        ("Advocate & Partners LLP", "", "Defendant Sam Poe"),
        ("Quinn Hartwell, LLP", "", "Defendant Example Metal & Glass Corp."),
        ("", "Robin Example", "Third-Party Defendant")]
    assert [a.address.split("\n")[0] for a in atts] == [
        "500 Sample Road", "12 Example Avenue", "400 Orchard Lane", "10 Example Lane"]


# --- a party without an attorney, names without "Esq.", a role that runs on without a comma
PRO_SE = """
              COUNSEL & COUNSEL, LLP
              Attorneys for Roe, Hill, Marsh, and Example Health
              System of New York
              500 Sample Road
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL and DANA SMITH

              SAM POE, Defendant, Pro Se
              55 Example Court
              Hill Town, New York 10003

              ADVOCATE & PARTNERS LLP
              Attorneys for the Defendant Example Metal & Glass Corp.
              12 Example Avenue
              Hill Town, New York 10003
              BY:  MR. ADVOCATE
""" + FOOT


def test_pro_se_names_without_esq_and_a_long_role():
    # (before: the role's second line was dropped, "Alex B. Counsel and Dana Smith" was one name, and the
    # party appearing pro se was left out)
    counsel, poe, advocate = read(transcript(PRO_SE))
    assert counsel.names() == ["Alex B. Counsel", "Dana Smith"]
    assert counsel.party == "Roe, Hill, Marsh, and Example Health System of New York"
    assert (poe.name, poe.firm, poe.party, poe.address) == (
        "Sam Poe", "", "Defendant, pro se", "55 Example Court\nHill Town, New York 10003")
    assert not poe.is_placeholder()  # a party without an attorney may order the minutes too
    assert (advocate.firm, advocate.name) == ("Advocate & Partners LLP", "Mr. Advocate")


# --- text recognition (OCR) of a numbered page: the line numbers stay in front of the text
OCR = """310
1 SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF QUEENS: CIVIL TERM: PART 14
2 ---------------------------------------X
3 JANE ROE, Plaintiff,
4 -against- Index No. 712345/2021
5 SAM POE, Defendant.
6 ---------------------------------------X
7 B E F O R E: HONORABLE MARIA T. ALVAREZ
8 A P P E A R A N C E S:
9 COUNSEL & COUNSEL, LLP
10 Attorneys for the Plaintiff
11 500 Sample Road, Suite 31
12 Lake Town, New York 10002
13 BY: ALEX B. COUNSEL, ESQ.
14
15 ADVOCATE & PARTNERS LLP
16 Attorneys for the Defendant
17 12 Example Avenue
18 Hill Town, New York 10003
19 BY: SAM ADVOCATE, ESQ.
20
21
22
23 PAT REPORTER
24 Senior Court Reporter
25
"""


def test_line_numbers_left_by_text_recognition():
    # (before: "9 COUNSEL & COUNSEL, LLP" looked like a street address, so the firm was lost)
    atts = read(OCR + "\f" + BODY, "Roe scan.pdf")
    assert [(a.firm, a.name, a.address) for a in atts] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel", "500 Sample Road, Suite 31\nLake Town, New York 10002"),
        ("Advocate & Partners LLP", "Sam Advocate", "12 Example Avenue\nHill Town, New York 10003")]


# --- two reporters named under the last entry, no blank line apart
REPORTERS = """
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ. and
                   DANA SMITH, ESQ.



              CASEY STONE
              PAT REPORTER
              Senior Court Reporters
"""


def test_the_reporters_names_are_not_counsel():
    assert [(a.firm, a.name) for a in read(transcript(REPORTERS))] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith")]


# ------------------------------------------------------------------ names

@pytest.mark.parametrize("line, names", [
    ("BY: ALEX B. COUNSEL, ESQ. and DANA SMITH, ESQ.", ["Alex B. Counsel", "Dana Smith"]),
    ("BY:  SAM ADVOCATE, ESQ., MORGAN BRIGHT, ESQ. & TAYLOR GREEN, ESQ.",
     ["Sam Advocate", "Morgan Bright", "Taylor Green"]),
    ("By: Alex B. Counsel; Dana Smith", ["Alex B. Counsel", "Dana Smith"]),
    ("BY: MR. COUNSEL, ESQ.", ["Mr. Counsel"]),
    ("BY: MS. DANA SMITH", ["Dana Smith"]),
    ("By: Sam Poe, Jr., Esq.", ["Sam Poe, Jr."]),
    ("DANA SMITH, ESQ., of Counsel", ["Dana Smith"]),
    ("Of Counsel: Robin Example, Esq.", ["Robin Example"]),
    ("BY: CASEY STONE, ESQ., Assistant District Attorney", ["Casey Stone"]),
])
def test_attorney_lines(line, names):
    from minute_filler.extract_regex import tidy_name
    assert parse_names(line, tidy_name) == names


def test_names_of_an_entry():
    assert split_names("Alex B. Counsel, Dana Smith and Sam Poe, Jr.") == ["Alex B. Counsel", "Dana Smith",
                                                                          "Sam Poe, Jr."]
    assert split_names("Dana Smith, Esq.") == ["Dana Smith"] and split_names("") == []


# ------------------------------------------------------------------ one firm, one party

def test_one_firm_is_one_party_and_one_invoice():
    case = CaseInfo()
    case.attorneys = read(transcript(USUAL))
    for a in case.attorneys:
        a.checked = True
    assert case.ordering_parties() == 2 and len(case.invoice_orderers()) == 2


def test_an_entry_in_a_few_words():
    """Columns and lines of the window (Excerpts..., Who ordered what, the prices) name a firm of several
    attorneys by the firm, an entry of one attorney by the name."""
    assert Attorney(name="Alex B. Counsel, Dana Smith", firm="Counsel & Counsel, LLP").label() == \
        "Counsel & Counsel, LLP"
    assert Attorney(name="Dana Smith", firm="Smith Law").label() == "Dana Smith"
    assert Attorney(firm="Smith Law").label() == "Smith Law" and Attorney(name="Sam Poe").label() == "Sam Poe"


def test_keys_follow_the_firm():
    a = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel, LLP")
    assert a.key() == "counsel and counsel"
    assert Attorney(name="Dana Smith", firm="COUNSEL AND COUNSEL").key() == a.key()  # an attorney added: same key
    assert Attorney(name="Dana Smith").key() == "dana smith"  # no firm: the name, as before
    assert {"alex b counsel", "counsel & counsel llp"} <= a.legacy_keys()


def test_an_email_from_one_attorney_joins_the_firm():
    s = pat_settings()
    title = RegexExtractor(PAT).extract(Ingested("Roe transcript.pdf", "pdf", text=transcript(USUAL),
                                                 page_count=60, first_page_no=310))
    mail = RegexExtractor(PAT).extract(Ingested("mail.eml", "email", text=(
        "From: Dana Smith <dsmith@counselcounsel.example>\nTo: Pat Reporter <preporter@example.com>\n"
        "Subject: Roe v. Poe minutes\n\nPlease send the minutes of 6/3/2026.\n\nDana Smith\n")))
    clerk = RegexExtractor(PAT).extract(Ingested("mail2.eml", "email", text=(
        "From: Robin Clerk <rclerk@counselcounsel.example>\nSubject: Roe v. Poe\n\nAn invoice, please.\n")))
    case = merge([title, mail, clerk], s)
    assert [(a.firm, a.name) for a in case.attorneys] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith, Robin Clerk"),
        ("Advocate & Partners LLP", "Sam Advocate")]
    counsel = case.attorneys[0]
    assert counsel.email == "dsmith@counselcounsel.example" and counsel.checked  # the sender orders
    assert case.ordering_parties() == 1


def test_the_same_firm_on_another_day_is_the_same_entry():
    day2 = USUAL.replace("COUNSEL & COUNSEL, LLP", "Counsel & Counsel L.L.P.").replace(
        "DANA SMITH, ESQ.", "ROBIN EXAMPLE, ESQ.")
    one = dedupe_attorneys(read(transcript(USUAL)) + read(transcript(day2)))
    assert [(a.firm, a.name) for a in one] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith, Robin Example"),
        ("Advocate & Partners LLP", "Sam Advocate")]
    assert read(transcript(USUAL))[0].key() == read(transcript(day2))[0].key()


def test_different_firms_with_an_attorney_of_the_same_name_stay_apart():
    a = Attorney(name="Dana Smith", firm="Counsel & Counsel, LLP")
    b = Attorney(name="Dana Smith", firm="Advocate & Partners LLP")
    assert len(dedupe_attorneys([a, b])) == 2


def test_a_record_made_with_a_row_per_attorney_opens_with_a_row_per_firm():
    """A job of a record made by version 2.0 (one row per attorney, Excerpts... rows naming them by name)
    comes back with one row for the firm, and its rows name the firm (batch.one_row_per_firm)."""
    from minute_filler.batch import job_from_origin
    origin = {"case": {"fields": {}, "proc": [], "attorneys": [
        {"name": "Alex B. Counsel", "firm": "Counsel & Counsel, LLP", "address": "500 Sample Road", "checked": True},
        {"name": "Dana Smith", "firm": "Counsel & Counsel, LLP", "email": "ds@counselcounsel.example",
         "checked": False},
        {"name": "Sam Advocate", "firm": "Advocate & Partners LLP", "checked": True},
        {"name": "Robin Example", "firm": "", "checked": True}]},
        "job": {"portions": [[10, ["alex b counsel"]], [20, ["dana smith", "sam advocate"]],
                             [30, ["alex b counsel", "dana smith", "robin example"]]]}}
    job = job_from_origin(origin, pat_settings())
    assert [(a.firm, a.name, a.checked) for a in job.case.attorneys] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith", True),
        ("Advocate & Partners LLP", "Sam Advocate", True), ("", "Robin Example", True)]
    assert job.case.attorneys[0].email == "ds@counselcounsel.example"
    counsel, advocate = "counsel and counsel", "advocate and partners"
    assert job.portions == [(10, [counsel]), (20, [counsel, advocate]), (30, [counsel, "robin example"])]
    assert job.ticked_keys() == [counsel, advocate, "robin example"]  # every key in the rows is ticked


def test_the_same_firm_without_its_name_on_another_day_is_billed_once(tmp_path):
    """Day 2's row names an attorney of day 1's firm without the firm (typed by hand): the joint invoice and the
    agreements of the case count the firm once (batch.same_entries), and day 2's Excerpts... rows follow."""
    from minute_filler.batch import Job, group_attorneys, same_entries
    one, two = Job(), Job()
    one.case.attorneys = [Attorney(name="Alex B. Counsel, Dana Smith", firm="Counsel & Counsel, LLP"),
                          Attorney(name="Sam Advocate", firm="Advocate & Partners LLP")]
    two.case.attorneys = [Attorney(name="DANA SMITH"), Attorney(name="Sam Advocate", firm="ADVOCATE & PARTNERS")]
    two.portions = [(10, ["dana smith"]), (20, ["dana smith", "advocate and partners"])]
    same_entries([one, two])
    assert [(a.name, a.firm) for a in two.case.attorneys] == [("DANA SMITH", "Counsel & Counsel, LLP"),
                                                              ("Sam Advocate", "ADVOCATE & PARTNERS")]
    assert two.portions == [(10, ["counsel and counsel"]), (20, ["counsel and counsel", "advocate and partners"])]
    assert [a.key() for a in group_attorneys([one, two])] == ["counsel and counsel", "advocate and partners"]


def test_the_ai_lists_a_firm_once():
    """A model naming a firm's attorneys one by one gives one entry for the firm, as the rules do."""
    from minute_filler.extract_llm import OllamaExtractor
    src = ("APPEARANCES: COUNSEL & COUNSEL, LLP, 500 Sample Road, Lake Town, New York 10002, BY: ALEX B. COUNSEL, "
           "ESQ. and DANA SMITH, ESQ.; ADVOCATE & PARTNERS LLP, BY: SAM ADVOCATE, ESQ.")
    ex = OllamaExtractor(pat_settings())._to_extraction({"attorneys": [
        {"name": "Alex B. Counsel, Esq.", "firm": "Counsel & Counsel, LLP", "address": "500 Sample Road, Lake Town, "
         "New York 10002", "phone": "", "email": "", "party": "", "is_requester": False},
        {"name": "Dana Smith", "firm": "COUNSEL & COUNSEL LLP", "address": "", "phone": "", "email": "",
         "party": "", "is_requester": True},
        {"name": "Sam Advocate", "firm": "Advocate & Partners LLP", "address": "", "phone": "", "email": "",
         "party": "", "is_requester": False}]}, src)
    assert [(a.firm, a.name, a.checked) for a in ex.attorneys] == [
        ("Counsel & Counsel, LLP", "Alex B. Counsel, Dana Smith", True), ("Advocate & Partners LLP", "Sam Advocate",
                                                                          False)]
    assert ex.attorneys[0].address == "500 Sample Road\nLake Town, New York 10002"


def test_parse_appearances_alone():
    found = parse_appearances("A P P E A R A N C E S:\n\nCOUNSEL & COUNSEL, LLP\n500 Sample Road\n"
                              "Lake Town, New York 10002\nBY: ALEX B. COUNSEL, ESQ.\n")
    assert [(a.firm, a.name, a.address) for a in found] == [
        ("COUNSEL & COUNSEL, LLP", "ALEX B. COUNSEL", "500 Sample Road\nLake Town, New York 10002")]


# --- found in the 2.1 bug sweep

def test_a_heading_for_the_people_over_an_office():
    """'For the People:' heading the District Attorney's lines: the office is the firm (this stopped the whole
    transcript with an error, so its date, index number and pages were lost too)."""
    atts = read(transcript("""
              For the People:
              OFFICE OF THE QUEENS COUNTY DISTRICT ATTORNEY
              125-01 Sample Boulevard
              Kew Gardens, New York 11415
              BY:  CASEY STONE, ESQ.
""" + FOOT))
    assert [(a.firm, a.name, a.party) for a in atts] == [
        ("Office of the Queens County District Attorney", "Casey Stone", "People")]


def test_a_firm_under_its_role_is_not_more_of_the_role():
    """A firm in capitals under 'Attorneys for the Plaintiff' is the firm, not the rest of the role (a capital
    letter was taken for a line going on)."""
    atts = read(transcript("""
              Attorneys for the Plaintiff
              MERIDIAN HOLLOW
              500 Sample Road
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.
""" + FOOT))
    assert [(a.firm, a.name, a.party) for a in atts] == [("Meridian Hollow", "Alex B. Counsel", "Plaintiff")]


def test_a_firm_with_a_street_word_after_another_firm():
    """'BROADWAY LEGAL GROUP' after a finished entry is the next firm, not the first line of its address."""
    atts = read(transcript("""
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.

              BROADWAY LEGAL GROUP
              Attorneys for the Defendant Sam Poe
              12 Example Avenue
              Hill Town, New York 10003
              BY:  SAM ADVOCATE, ESQ.
""" + FOOT))
    assert [(a.firm, a.address) for a in atts][1] == ("Broadway Legal Group", "12 Example Avenue\nHill Town, New York 10003")


def test_a_party_named_above_pro_se():
    """'SAM POE' / 'Defendant, Pro Se': the entry keeps the party's name."""
    atts = read(transcript("""
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.

              SAM POE
              Defendant, Pro Se
""" + FOOT))
    assert [(a.name, a.party) for a in atts][1] == ("Sam Poe", "Defendant, pro se")


def test_names_on_lines_of_their_own_above_the_reporters_title():
    """BY: / DANA SMITH, then (after blank lines) the reporter's line: Dana Smith is counsel, not a reporter."""
    atts = read(transcript("""
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL
                   DANA SMITH



                     PAT REPORTER
                     Senior Court Reporter
"""))
    assert atts[0].name == "Alex B. Counsel, Dana Smith"


@pytest.mark.parametrize("line, firm, party, name", [
    ("Appearing for the Plaintiff:  DANA SMITH, ESQ.", "", "Plaintiff", "Dana Smith"),
    ("For the Defendant:  Unrepresented", "", "Defendant", "Unrepresented"),
    ("For the Plaintiff:  COUNSEL & COUNSEL, LLP", "Counsel & Counsel, LLP", "Plaintiff", ""),
])
def test_a_role_with_who_appeared_after_a_colon(line, firm, party, name):
    atts = read(transcript(f"""
              {line}
""" + FOOT))
    assert [(a.firm, a.party, a.name) for a in atts] == [(firm, party, name)]


def test_a_firm_and_its_role_on_one_line():
    """'COUNSEL & COUNSEL, LLP, Attorneys for the Plaintiff': the firm alone (else its key differs from the same
    firm on another day, and it is billed twice)."""
    atts = read(transcript("""
              COUNSEL & COUNSEL, LLP, Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              BY:  ALEX B. COUNSEL, ESQ.
""" + FOOT))
    assert [(a.firm, a.party) for a in atts] == [("Counsel & Counsel, LLP", "Plaintiff")]


def test_phone_and_fax_on_one_line():
    atts = read(transcript("""
              COUNSEL & COUNSEL, LLP
              Attorneys for the Plaintiff
              500 Sample Road, Suite 31
              Lake Town, New York 10002
              Phone: (555) 010-1234   Fax: (555) 010-1235
              BY:  ALEX B. COUNSEL, ESQ.
""" + FOOT))
    assert (atts[0].phone, atts[0].fax) == ("(555) 010-1234", "(555) 010-1235")


def test_the_same_firm_twice_spelled_apart_keeps_both_clients():
    atts = parse_appearances("""A P P E A R A N C E S:

COUNSEL & COUNSEL, LLP
Attorneys for the Defendant Sam Poe
BY: ALEX B. COUNSEL, ESQ.

COUNSEL & COUNSEL LLP
Attorneys for the Defendant Example Corp.
BY: DANA SMITH, ESQ.
""")
    assert len(atts) == 1 and "Sam Poe" in atts[0].party and "Example Corp." in atts[0].party
    assert atts[0].name == "ALEX B. COUNSEL, DANA SMITH"


def test_an_email_with_a_title_page_pasted_in_keeps_its_sender():
    """The sender of an e-mail is who orders, even when the e-mail quotes a title page's APPEARANCES."""
    text = ("From: Dana Smith <dsmith@counselcounsel.example>\nTo: Pat Reporter <preporter@example.com>\n"
            "Subject: Roe v. Poe minutes\n\nPlease send the minutes of June 3, 2026.\n\nA P P E A R A N C E S:\n\n"
            "COUNSEL & COUNSEL, LLP\nAttorneys for the Plaintiff\n500 Sample Road\nLake Town, New York 10002\n"
            "BY: DANA SMITH, ESQ.\n\nADVOCATE & PARTNERS LLP\nAttorneys for the Defendant\n12 Example Avenue\n"
            "Hill Town, New York 10003\nBY: SAM ADVOCATE, ESQ.\n")
    atts = RegexExtractor(PAT).extract(Ingested("order.eml", "email", text=text)).attorneys
    ticked = [a for a in atts if a.checked]
    assert [(a.firm, a.name) for a in ticked] == [("Counsel & Counsel, LLP", "Dana Smith")]
    assert any(a.firm == "Advocate & Partners LLP" and not a.checked for a in atts)


def test_two_senders_on_a_public_mail_domain_stay_apart():
    a = Attorney(name="Dana Smith", email="dsmith@gmail.com")
    b = Attorney(email="robin@gmail.com", party="sender")
    assert len(dedupe_attorneys([a, b])) == 2
