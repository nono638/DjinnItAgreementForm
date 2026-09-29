"""Rate sheet parsing, the template, and how the active sheet drives the form."""
from minute_filler.merge import merge
from minute_filler.models import Extraction
from minute_filler.rates import BUNDLED_DIR, list_sheets, load_sheet, speed_key
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
    import json
    from minute_filler.settings import settings_dir
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / "MinuteAgreementFiller"
    (old / "Rate Sheets").mkdir(parents=True)
    (old / "settings.json").write_text(json.dumps({"profile": {"name": "Pat Reporter"}}))
    (old / "Rate Sheets" / "Mine.csv").write_text("Rate,Original\nRegular,$9.99\n")
    assert Settings.load().profile.name == "Pat Reporter"
    assert (settings_dir() / "Rate Sheets" / "Mine.csv").exists()
    assert settings_dir().name == "DjinnItAgreementForm"
