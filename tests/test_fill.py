"""Fills both form variants and checks the values that land in the PDF."""
import pymupdf
import pytest

from minute_filler.fill import fill_all, short_caption
from minute_filler.forms import original_map
from minute_filler.models import Attorney, CaseInfo
from minute_filler.settings import Profile, Settings


@pytest.fixture
def case():
    c = CaseInfo()
    for k, v in {"court": "Supreme", "county": "Queens", "part": "11", "judge": "Maria T. Alvarez",
                 "case_name": "Jane Roe v. X.Y. Holding Corporation and Acme Architectural Metal & Glass Corp.",
                 "index_no": "700001/2020", "dates": "8/25/2026", "rate": "3.30", "delivery": "Regular",
                 "copies": "1", "est_pages": "26", "delivery_date": "10/20/2026"}.items():
        c.set(k, v)
    c.proc_types = {"Trial"}
    c.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel",
                            address="100 Main Avenue\nAnytown, New York 10000", checked=True),
                   Attorney(name="Sam Advocate", firm="Advocate & Partners, LLP", checked=True),
                   Attorney(name="Unrepresented", checked=False)]
    return c


@pytest.fixture
def settings():
    s = Settings()
    s.profile = Profile(name="Pat Reporter", address1="123 Courthouse Plaza", address2="Anytown, NY 10000",
                        phone="(555) 010-0000", email="pat@example.com")
    s.per_email = True
    return s


def fields(path):
    doc = pymupdf.open(path)
    return {w.field_name: w.field_value for w in doc[0].widgets()}, doc[0]


def test_clean_form(case, settings, tmp_path):
    settings.form_choice = "clean"
    paths = fill_all(case, settings, tmp_path)
    assert len(paths) == 2  # one per checked attorney
    f, _ = fields(paths[0])
    assert f["index_no"] == "700001/2020"
    assert f["sig_attorney"] == "per email"
    assert f["atty_name"] == "Alex B. Counsel"
    assert f["atty_address_1"] == "100 Main Avenue"
    assert f["rep_name"] == "Pat Reporter"
    assert f["proc_trial"] not in (False, "Off", "")
    assert f["proc_hearing"] in (False, "Off", "")
    assert f["delivery_regular"] not in (False, "Off", "")
    assert f["case_name_1"].startswith("Jane Roe")
    assert "Glass Corp." in " ".join(f[k] for k in ("case_name_1", "case_name_2", "case_name_3"))


def test_original_form(case, settings, tmp_path):
    settings.form_choice = "original"
    settings.sign_reporter = True
    paths = fill_all(case, settings, tmp_path)
    f, page = fields(paths[1])
    by_key = {original_map.WIDGETS.get(k.removeprefix("Text-")): v for k, v in f.items()}
    assert by_key["index_no"] == "700001/2020"
    assert by_key["proc_trial"] == "X"
    assert by_key["rate"] == "$3.30"
    assert by_key["atty_name"] == "Sam Advocate"
    text = page.get_text()
    assert "per email" in text and "Pat Reporter" in text  # stamped overlays


def test_per_email_off(case, settings, tmp_path):
    settings.per_email = False
    f, _ = fields(fill_all(case, settings, tmp_path)[0])
    assert f["sig_attorney"] == ""


def test_short_caption():
    assert short_caption("John Smith, Mary Smith Estate v. Acme Hospital Group Inc., "
                         "Beta Medical Center") == "John Smith v. Acme Hospital Group Inc."
    assert short_caption("Matter of Jane Doe") == "Matter of Jane Doe"
