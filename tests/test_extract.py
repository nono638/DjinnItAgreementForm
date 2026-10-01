"""Extraction tests against the fictional samples in tests/samples.

Real documents go in samples_internal/ (git-ignored) and are covered by test_private_samples.py.
"""

from minute_filler.extract_regex import RegexExtractor, smart_title
from minute_filler.ingest import Ingested, ingest_text
from minute_filler.merge import merge
from minute_filler.settings import Settings

from helpers import PAT as PROFILE, SAMPLES, best


def run(name, as_pdf=False, **pdf):
    """Extract from a sample; as_pdf pretends the text came from a PDF's text layer."""
    text = (SAMPLES / name).read_text()
    ing = Ingested(name.replace(".txt", ".pdf"), "pdf", text=text, **pdf) if as_pdf else ingest_text(text, name)
    return RegexExtractor(PROFILE).extract(ing)


def test_transcript_cover_page():
    ex = run("transcript_cover.txt", as_pdf=True, page_count=30, first_page_no=101)
    assert best(ex, "index_no") == "712345/2021"
    assert best(ex, "court") == "Supreme"
    assert best(ex, "county") == "Queens"
    assert best(ex, "part") == "14"
    assert best(ex, "judge") == "Maria T. Alvarez"
    assert best(ex, "dates") == "6/3/2026"
    assert best(ex, "case_name") == ("Jane Roe v. X.Y. Holding Corporation and Acme Widget Metal & Glass Corp.")
    assert ex.proc_types.get("Trial", 0) >= 0.6
    assert best(ex, "est_pages") == "30"
    names = {a.name for a in ex.attorneys}
    assert names == {"Samuel R. Advocate", "Dana White", "Alex B. Counsel"}  # not the reporter
    counsel = next(a for a in ex.attorneys if a.name == "Alex B. Counsel")
    assert counsel.firm == "Counsel & Counsel"
    assert counsel.party == "Defendants"
    assert counsel.address == "500 Sample Road\nLake Town, New York 10002"


def test_invoice():
    ex = run("invoice.txt")
    assert best(ex, "index_no") == "712222/2024"
    assert best(ex, "part") == "53"
    assert best(ex, "judge") == "Lopez"
    assert best(ex, "dates") == "5/22/2026"
    assert best(ex, "case_name") == "Smith v. Jones"
    [a] = ex.attorneys
    assert a.firm == "Example Firm LLP" and a.email == "billing@examplefirm.com" and a.checked
    # the reporter's own e-mail on the invoice must not become an attorney
    assert all("preporter" not in x.email for x in ex.attorneys)


def test_informal_email():
    ex = run("email_informal.txt")
    assert best(ex, "index_no") == "812345/2023"
    assert best(ex, "part") == "19"
    assert best(ex, "judge") == "Rodriguez"
    assert best(ex, "dates") == "9/14/2026, 9/15/2026"
    assert best(ex, "delivery") == "Expedited"
    assert best(ex, "copies") == "1"
    assert "Garcia v. Metro Transit" in best(ex, "case_name")
    [a] = ex.attorneys
    assert (a.name, a.firm, a.phone, a.fax) == ("John Doe", "Doe & Roe, LLP", "(212) 555-1212", "(212) 555-1313")
    assert a.checked  # the sender is the one ordering


def test_lowercase_email():
    ex = run("email_short.txt")
    assert best(ex, "index_no") == "700123/2025"
    assert best(ex, "part") == "7"
    assert best(ex, "judge") == "Lopez"
    assert best(ex, "case_name") == "Smith v. Jones"


def test_merge_defaults_and_derived():
    s = Settings()
    s.profile = PROFILE
    case = merge([run("email_informal.txt")], s)
    assert case.get("court") == "Supreme" and case.fields["court"].source == "default"
    assert case.get("delivery") == "Expedite"  # the rate sheet's spelling
    assert case.get("rate") == "5.40"
    assert case.get("delivery_date")
    assert len(case.attorneys) == 1 and case.attorneys[0].checked


def test_smart_title():
    assert smart_title("X.Y. HOLDING CORPORATION and ACME CORP.") == "X.Y. Holding Corporation and Acme Corp."
    assert smart_title("SMITH JONES & BROWN PLLC") == "Smith Jones & Brown PLLC"
    assert smart_title("JOHN MCDONALD O'BRIEN") == "John McDonald O'Brien"


def test_ai_answers_are_checked_against_the_text():
    """Invented values from a small model (e.g. a county the e-mail never mentions) are dropped."""
    from minute_filler.extract_llm import OllamaExtractor
    x = OllamaExtractor(Settings())
    src = "can I get the transcript for smith v jones 10/1/2026, index 700123-2025, judge lopez"
    ex = x._to_extraction({"county": "Bronx", "court": "Family", "judge": "Lopez", "case_name": "Smith v Jones",
                           "index_number": "700123-2025", "attorneys": [
                               {"name": "Invented Person", "firm": "Nowhere LLP", "address": "", "phone": "",
                                "email": "", "party": "", "is_requester": True}]}, src)
    assert "county" not in ex.fields and "court" not in ex.fields
    assert best(ex, "judge") == "Lopez" and best(ex, "index_no") == "700123/2025"
    assert ex.attorneys == []


def test_part_letters_and_numbers():
    from minute_filler.models import Extraction
    x = RegexExtractor(PROFILE)
    for text, want in [("COUNTY OF QUEENS:  CIVIL TERM:  PART MDP \n", "MDP"),
                       ("COUNTY OF QUEENS: CIVIL TERM: PART 25\n", "25"),
                       ("Part: TAP-A\n", "TAP-A"),
                       ("Judge: Lopez\nPart: 53\n", "53"),
                       ("SUPREME COURT, IAS PART 12\n", "12")]:
        ex = Extraction()
        x._part(text, text, ex)
        assert best(ex, "part") == want, text
    ex = Extraction()
    x._part("that is part of the record. PART OF THE RECORD", "", ex)
    assert "part" not in ex.fields
