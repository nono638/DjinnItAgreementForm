"""Rate sheet parsing, the template, the bundled sheet's prices (Daily copies, e-mailed copies and indexes at $1.25
a page) and an unedited copy of the old one brought up to date, and how the active sheet drives the form."""
from minute_filler.merge import merge
from minute_filler.models import Extraction
from minute_filler.rates import BUNDLED_DIR, list_sheets, speed_key
from minute_filler.settings import Settings

SAMPLE = "Sample Rates"


def test_sample_sheet_is_default():
    s = Settings()
    sheet = s.sheet()
    assert sheet.name == SAMPLE
    # listed cheapest to most expensive, not in file order
    assert [sp.name for sp in sheet.speeds] == ["Regular", "Expedite", "Daily", "Immediate"]
    assert s.rate_for("Regular") == "4.30"
    assert s.rate_for("Expedited") == "5.40"
    assert sheet.find("Immediate").copy == "1.45"
    assert sheet.updated == "5/2/2024"


def test_daily_copies_e_mails_and_indexes_are_1_25_a_page():
    """The bundled sheet's Daily row: $6.50 a page, and $1.25 (not $1.30) for a copy, an e-mailed copy and an
    index, read from the sheet like every other price."""
    from minute_filler.invoice_calc import extra_rate
    daily = Settings().sheet().find("Daily")
    assert (daily.original, daily.copy) == ("6.50", "1.25")
    assert str(extra_rate(daily, "email")) == "1.25" and str(extra_rate(daily, "index")) == "1.25"


def test_an_unedited_copy_of_the_old_sample_is_brought_up_to_date(tmp_path):
    """A user folder seeded by an earlier version holds Sample Rates as it shipped then ($1.30 for Daily): still
    exactly that (CRLF or LF), it is replaced by the sheet as it ships now; edited, or another sheet, it is
    left alone."""
    from minute_filler.rates import OLD_BUNDLED, seed
    new = (BUNDLED_DIR / "Sample Rates.csv").read_bytes()
    old = new.replace(b"Daily,$6.50,$1.25,$1.25,$1.25", b"Daily,$6.50,$1.30,$1.30,$1.30")
    import hashlib
    assert {hashlib.sha256(x).hexdigest() for x in (old, old.replace(b"\r\n", b"\n"))} == OLD_BUNDLED["Sample Rates.csv"]
    for i, text in enumerate((old, old.replace(b"\r\n", b"\n"))):
        d = tmp_path / f"unedited {i}"
        d.mkdir()
        (d / "Sample Rates.csv").write_bytes(text)
        seed(d)
        assert (d / "Sample Rates.csv").read_bytes() == new
    d = tmp_path / "edited"
    d.mkdir()
    edited = old.replace(b"$4.30", b"$4.35")  # the user's own price: kept, $1.30 and all
    (d / "Sample Rates.csv").write_bytes(edited)
    (d / "My Rates.csv").write_bytes(old)  # another sheet, the same bytes: not the sample, kept
    seed(d)
    assert (d / "Sample Rates.csv").read_bytes() == edited and (d / "My Rates.csv").read_bytes() == old


def test_template_is_blank_and_hidden():
    tpl = BUNDLED_DIR / "Rate Sheet TEMPLATE.csv"
    assert tpl.exists()
    names = [s.name for s in list_sheets()[0]]
    assert "Rate Sheet TEMPLATE" not in names


def test_custom_sheet_with_days(tmp_path):
    d = tmp_path / "sheets"
    d.mkdir()
    (d / "City Rates.csv").write_text(
        "Rate,Original,Copy,Days\nDaily,$3.00,$0.75,1\nRegular,$2.00,$0.50,30\nRates Last Updated:,1/1/2026\n")
    s = Settings()
    s.rate_sheets_dir = str(d)
    s.rate_sheet = "City Rates"
    assert {x.name for x in s.sheets()[0]} >= {"City Rates", SAMPLE}  # bundled sheets are seeded too
    assert s.rate_for("Regular") == "2.00"
    assert s.days_for("Regular") == 30
    case = merge([Extraction()], s)
    assert case.get("rate") == "2.00"


def test_speed_key():
    assert speed_key("Expedite") == speed_key("Expedited") == "expedited"
    assert speed_key("same day") == "immediate"


def test_bad_sheet_reported(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "Broken.csv").write_text("hello\nworld\n")
    sheets, problems = list_sheets(str(d))
    assert any("Broken.csv" in p for p in problems)


def test_settings_carry_over_from_old_app_name(tmp_path, monkeypatch):
    """Settings and rate sheets under the app's first name (MinuteAgreementFiller) move to YinItAgreementForm."""
    import json
    from minute_filler.settings import settings_dir
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / "MinuteAgreementFiller"
    (old / "Rate Sheets").mkdir(parents=True)
    (old / "settings.json").write_text(json.dumps({"profile": {"name": "Pat Reporter"}}))
    (old / "Rate Sheets" / "Mine.csv").write_text("Rate,Original\nRegular,$9.99\n")
    assert Settings.load().profile.name == "Pat Reporter"
    assert (settings_dir() / "Rate Sheets" / "Mine.csv").exists()
    assert settings_dir().name == "YinItAgreementForm"
