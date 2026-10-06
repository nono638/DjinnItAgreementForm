"""Fills each form variant (UCS, clean, original) and checks the values that land in the PDF, including the
fields added on lines a form has none for (signatures, fax), with no "Lock fields" button; also the file names,
the short caption, and older settings files upgraded to the new form defaults."""
import pymupdf
import pytest

from minute_filler.fill import LOCK_BUTTON, fill_all, short_caption
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
    # lines the original has no field for get one added, so they can be changed too
    assert f["sig_attorney"] == "per email" and f["sig_reporter"] == "Pat Reporter"
    assert f["rep_fax"] == "" and LOCK_BUTTON not in f  # no Lock fields button any more (1.3.1)


def test_per_email_off(case, settings, tmp_path):
    settings.per_email = False
    settings.form_choice = "clean"
    f, _ = fields(fill_all(case, settings, tmp_path)[0])
    assert f["sig_attorney"] == ""


def test_ucs_form_is_default(case, settings, tmp_path):
    from minute_filler.forms import ucs_map
    assert Settings().form_choice == "ucs"
    settings.sign_reporter = True
    case.set("part", "MDP")
    paths = fill_all(case, settings, tmp_path)
    doc = pymupdf.open(paths[0])
    assert doc.page_count == 2  # the form and its instructions page
    by_key = {ucs_map.WIDGETS.get(w.field_name): w.field_value for w in doc[0].widgets()}
    assert by_key["court"] == "Supreme" and by_key["county"] == "Queens"
    assert by_key["part"] == "MDP"
    assert by_key["index_no"] == "700001/2020"
    assert by_key["proc_trial"] == "X" and by_key["proc_hearing"] == ""
    assert by_key["delivery_regular"] == "X"
    assert by_key["rate"] == "$3.30"
    assert by_key["atty_name"] == "Alex B. Counsel"
    assert by_key["atty_firm"] == "Counsel & Counsel"
    assert by_key["atty_address_1"] == "100 Main Avenue, Anytown, New York 10000"
    assert by_key["rep_name"] == "Pat Reporter"
    sig = {w.field_name: w.field_value for w in doc[0].widgets() if w.field_name.startswith("sig_")}
    assert sig == {"sig_attorney": "per email", "sig_reporter": "Pat Reporter"}  # fields added on the lines
    settings.include_instructions = False
    assert pymupdf.open(fill_all(case, settings, tmp_path / "x")[0]).page_count == 1


def test_file_name(case, settings):
    """The default file name, with the day of the minutes for a batch, custom patterns, and a bad pattern."""
    from datetime import date
    from minute_filler.fill import output_name
    t = date.today()
    today = f"{t.month}-{t.day}-{t.year}"
    atty = case.attorneys[0]
    assert output_name(case, atty, settings) == \
        f"Minute Agreement - Jane Roe v. X.Y. Holding Corporation - 700001-2020 - Alex B. Counsel - {today}.pdf"
    assert output_name(case, None, settings) == \
        f"Minute Agreement - Jane Roe v. X.Y. Holding Corporation - 700001-2020 - {today}.pdf"
    # several days of one case in a batch: the day of the minutes goes with the case
    assert output_name(case, None, settings, dated=True) == \
        f"Minute Agreement - Jane Roe v. X.Y. Holding Corporation (8-25-2026) - 700001-2020 - {today}.pdf"
    settings.filename_pattern = "{date} {case}"
    assert output_name(case, atty, settings, dated=True) == "8-25-2026 Jane Roe v. X.Y. Holding Corporation.pdf"
    settings.filename_pattern = "{index}"
    assert output_name(case, atty, settings, dated=True) == "700001-2020 - 8-25-2026.pdf"
    settings.filename_pattern = "{oops}"
    assert output_name(case, atty, settings) == "Minute Agreement - 700001-2020.pdf"


def test_old_settings_switch_to_ucs_form(tmp_path, monkeypatch):
    """A settings file from before version 2 (no settings_version) moves to the UCS form, the default since."""
    import json
    from minute_filler.settings import settings_dir
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (settings_dir() / "settings.json").write_text(json.dumps({"form_choice": "clean"}))
    assert Settings.load().form_choice == "ucs"


def test_settings_v2_get_the_new_defaults(tmp_path, monkeypatch):
    import json
    from minute_filler.settings import FILENAME_PATTERN, OLD_FILENAME_PATTERN, settings_dir
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = {"settings_version": 2, "form_choice": "clean", "include_instructions": False,
           "filename_pattern": OLD_FILENAME_PATTERN}
    (settings_dir() / "settings.json").write_text(json.dumps(old))
    s = Settings.load()
    assert s.include_instructions and s.filename_pattern == FILENAME_PATTERN and s.form_choice == "clean"
    # a choice made after the change, and a file name of the user's own, are kept
    s.include_instructions, s.filename_pattern = False, "{case}"
    s.save()
    s = Settings.load()
    assert not s.include_instructions and s.filename_pattern == "{case}"
    (settings_dir() / "settings.json").write_text(json.dumps(dict(old, filename_pattern="{index} {case}")))
    assert Settings.load().filename_pattern == "{index} {case}"


def test_short_caption():
    """The short caption keeps the first party on each side."""
    assert short_caption("John Smith, Mary Smith Estate v. Acme Hospital Group Inc., "
                         "Beta Medical Center") == "John Smith v. Acme Hospital Group Inc."
    assert short_caption("Matter of Jane Doe") == "Matter of Jane Doe"
