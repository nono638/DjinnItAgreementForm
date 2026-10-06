"""The Minute Order Form/Receipt: the reporter's parts are filled from the case."""
import pymupdf
import pytest

from minute_filler.mofr import build_values, fill_mofr

from helpers import ROE, make_case, pat_settings


@pytest.fixture
def case():
    return make_case({**ROE, "copies": "2", "delivery": "Expedited", "est_pages": "26",
                      "proc_other": "Charge conference"}, procs={"Trial", "Sentence"})


@pytest.fixture
def s():
    return pat_settings(address1="Room 100")


def widgets(path):
    """The first page's form fields {name: value}, and the open document."""
    doc =pymupdf.open(path)
    return {w.field_name: w.field_value for w in doc[0].widgets()}, doc


def test_civil_mofr(case, s, tmp_path):
    """A civil form: the case, the reporter and the pages filled in, the speed and proceedings ticked, and the
    printed "PEOPLE V" covered."""
    p =fill_mofr(case, s, tmp_path, pages="30")
    f, doc = widgets(p)
    assert p.name.startswith("MOFR - Jane Roe v. X.Y. Holding Corporation - 712345-2021")
    assert doc.metadata["creator"] == "YinIt MOFR"
    assert (f["Text Field0"], f["Text Field1"], f["Text Field3"], f["Text Field4"]) == \
        ("Queens", "Jane Roe v. X.Y. Holding Corporation", "Pat Reporter", "Room 100")
    assert (f["Text Field5"], f["Text Field7"], f["Text Field8"], f["Text Field9"], f["Text Field34"]) == \
        ("712345/2021", "14", "Maria T. Alvarez", "6/3/2026", "2")
    assert f["Text Field12"] == "30"  # the transcript's pages win over the estimate
    on = {k for k, v in f.items() if k.startswith("Check") and v not in ("Off", "", None)}
    # civil; expedite in sections I and III; sentence; other (trial + the typed one)
    assert on == {"Check Box1", "Check Box4", "Check Box11", "Check Box5", "Check Box7"}
    assert f["Text Field35"] == "Trial, Charge conference"
    # the printed "PEOPLE V" is covered on a civil form, and the title starts where it was
    title = next(w for w in doc[0].widgets() if w.field_name == "Text Field1")
    assert title.rect.x0 < 145


def test_criminal_mofr_strips_people(case, s):
    s.mofr_division = "criminal"
    case.set("case_name", "The People of the State of New York v. John Doe")
    case.set("delivery", "Regular")
    v = build_values(case, s)
    assert v["title"] == "John Doe" and v["criminal"] and not v["civil"]
    assert v["regular"] and not v["expedited"] and v["pages"] == "26"
