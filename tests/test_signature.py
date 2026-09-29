"""Signature picture on the forms, and the quick buttons for the delivery date."""
import os
from datetime import date

import pymupdf
import pytest
from PIL import Image, ImageDraw

from minute_filler.fill import fill_all
from minute_filler.models import CaseInfo
from minute_filler.settings import Profile, Settings
from minute_filler.signature import prepare, signature_path


def photo(path, paper=(205, 200, 190), size=(900, 500)):
    """A made-up signature: dark strokes in the middle of a greyish sheet of paper."""
    img = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(img)
    points = [(250, 300), (300, 180), (340, 320), (400, 200), (450, 310), (520, 230), (600, 300), (680, 260)]
    draw.line(points, fill=(20, 20, 60), width=7, joint="curve")
    img.save(path)
    return path


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.profile = Profile(name="Pat Reporter")
    s.signature_image = str(prepare(photo(tmp_path / "photo.jpg"), signature_path()))
    s.sign_reporter = True
    return s


def test_paper_becomes_transparent_and_margins_are_cut(tmp_path):
    out = Image.open(prepare(photo(tmp_path / "photo.jpg"), tmp_path / "sig.png"))
    assert out.mode == "RGBA"
    assert out.width < 500 and out.height < 200           # cropped to the strokes
    alpha = out.getchannel("A")
    assert alpha.getpixel((2, 2)) == 0                      # paper
    assert alpha.getextrema() == (0, 255)                   # ink
    with pytest.raises(ValueError, match="blank"):
        Image.new("RGB", (300, 100), "white").save(tmp_path / "blank.png")
        prepare(tmp_path / "blank.png", tmp_path / "x.png")


@pytest.mark.parametrize("form", ["ucs", "clean", "original"])
def test_signature_is_placed_on_the_reporter_line(form, settings, tmp_path):
    settings.form_choice = form
    case = CaseInfo()
    case.set("case_name", "A v. B")
    page = pymupdf.open(fill_all(case, settings, tmp_path / "with")[0])[0]
    box = max((page.get_image_bbox(i) for i in page.get_images(full=True)), key=lambda r: r.y0)
    assert box.x0 >= 55 and box.x1 <= 235 and 550 < box.y0 < box.y1 < 645 and box.height > 12
    from minute_filler.fill import build_values
    assert build_values(case, None, settings)["sig_reporter"] == ""  # the name is not typed as well

    settings.sign_reporter = False
    page = pymupdf.open(fill_all(case, settings, tmp_path / "without")[0])[0]
    assert len(page.get_images()) < len(pymupdf.open(fill_all(case, settings_on(settings), tmp_path / "again")[0])[0]
                                        .get_images())


def settings_on(s):
    s.sign_reporter = True
    return s


def test_name_is_typed_when_the_picture_is_gone(settings, tmp_path):
    os.remove(settings.signature_image)
    assert settings.signature() == ""
    page = pymupdf.open(fill_all(CaseInfo(), settings, tmp_path)[0])[0]
    assert page.get_text().count("Pat Reporter") == 2  # signature line and name


def test_quick_dates():
    from minute_filler.gui.main_window import quick_date
    wed = date(2026, 9, 30)
    assert quick_date(0, today=wed) == "9/30/2026"
    assert quick_date(1, today=wed) == "10/1/2026"
    assert quick_date(7, today=wed) == "10/7/2026"
    assert quick_date(0, 1, today=wed) == "10/30/2026"
    assert quick_date(1, today=date(2026, 10, 2)) == "10/5/2026"       # Friday -> Monday, not Saturday
    assert quick_date(0, 1, today=date(2027, 1, 31)) == "3/1/2027"      # 2/28 is a Sunday
    assert quick_date(0, 1, today=date(2026, 12, 15)) == "1/15/2027"


def test_quick_date_button_fills_the_field():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from minute_filler.gui.main_window import MainWindow, quick_date
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    s = Settings()
    s.profile = Profile(name="Pat Reporter")
    s.use_ai = False
    win = MainWindow(s, app)
    button = next(b for b in win.findChildren(QtWidgets.QToolButton) if b.text() == "1 week")
    button.click()
    assert win.rows["delivery_date"].text() == quick_date(7)
    assert win.cur.case.fields["delivery_date"].source == "you"
    win.delivery.setCurrentIndex(1)  # another speed must not overwrite a date the user chose
    assert win.rows["delivery_date"].text() == quick_date(7)
    win.deleteLater()


def test_settings_dialog_stores_the_signature(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from minute_filler.gui.dialogs import SettingsDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    s = Settings()
    s.use_ai = False
    src = str(photo(tmp_path / "photo.png"))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (src, "")))

    dlg = SettingsDialog(s)
    dlg._pick_signature()
    assert dlg.p_sign.isChecked() and s.signature_image == ""   # nothing is stored before Save
    dlg.reject()
    assert s.signature() == "" and not signature_path().exists()

    dlg = SettingsDialog(s)
    dlg._pick_signature()
    dlg.accept()
    assert s.sign_reporter and s.signature() == str(signature_path())
    assert Settings.load().signature() == str(signature_path())

    dlg = SettingsDialog(s)
    dlg._remove_signature()
    dlg.accept()
    assert s.signature_image == "" and not signature_path().exists()
