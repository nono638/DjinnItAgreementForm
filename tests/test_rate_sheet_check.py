"""A rate sheet that leaves out prices (the user, 2026-10-09): not every speed has to be on it (not every agency
does Immediate, some courts offer Realtime), "but every peripheral should be spelled out": each speed on the sheet
needs its Original, Copy, Email and Index price. The Email and Index prices are also read from a column headed
close to the name ("E-mail", "Index Price"), unless that would be a guess (two close ones, a judge's index). The
window warns when the sheet in use is incomplete (picked, read again, after Settings, at start) and asks whether
to use it anyway; the answer is kept until the file changes. All names and prices are made up."""
import os
from decimal import Decimal

import pytest

from minute_filler.invoice_calc import extra_rate
from minute_filler.rates import BUNDLED_DIR, FALLBACK, load_sheet, missing_prices, sheet_fingerprint
from minute_filler.settings import LOCAL, Settings

from helpers import pat_settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GAPS = ("Rate,Original,Copy,Email,Index Price,Days\n"  # ("Index Price": read as the Index price)
        "Regular,$4.30,$1.00,,,21\n"
        "Daily,$6.50,$1.25,$0.00,n/a,1\n"
        "Immediate,,,,,0\n"            # a speed not offered: fine
        "Realtime,,$2.00,$2.00,$2.00,0\n"  # prices but no Original: not read at all
        "Note:,prices include tax\n"
        "Rates Last Updated:,5/2/2026\n")
COMPLETE = "Rate,Original,Copy,Email,Index\nRegular,$4.30,$1.00,$1.00,$1.00\n"


def sheet(tmp_path, text, name="Court Rates"):
    p = tmp_path / f"{name}.csv"
    p.write_text(text, encoding="utf-8")
    return load_sheet(p)


def test_the_bundled_sheet_is_complete():
    assert missing_prices(load_sheet(BUNDLED_DIR / "Sample Rates.csv")) == []
    assert missing_prices(FALLBACK) == [] and sheet_fingerprint(FALLBACK) == ""


def test_what_counts_as_missing(tmp_path):
    """$0.00 is a price, "n/a" and a blank aren't (the "Index Price" column is read as the Index). Immediate, left
    blank, is a speed not offered; Realtime has prices but no Original, so it isn't read at all; the note row is
    no speed."""
    sh = sheet(tmp_path, GAPS)
    assert [sp.name for sp in sh.speeds] == ["Regular", "Daily"] and sh.skipped == ["Realtime"]
    assert sh.read_as == {"Index Price": "Index"}
    assert missing_prices(sh) == [("Regular", ["Email", "Index"]), ("Daily", ["Index"]), ("Realtime", ["Original"])]
    assert missing_prices(sheet(tmp_path, "Rate,Original,Email\nRegular,$4.30,$1.00\n", "Short")) == \
        [("Regular", ["Copy", "Index"])]
    assert missing_prices(sheet(tmp_path, COMPLETE, "Fine")) == []


@pytest.mark.parametrize("email, index", [
    ("Email", "Index"), ("EMAIL", "INDEX"), ("E-Mail", "index"),          # the names, in any capitals
    ("E mail", "Index Price"), ("Emailed copy", "Index (per page)"),      # close to them
    ("e_mail price", "Indices"), ("Emial", "Idnex"),                       # ... and slips
])
def test_headings_close_to_email_or_index_are_read(tmp_path, email, index):
    """The user, 2026-10-09: the headings as named first, then ones close to them, so "the reading would still
    work". The invoice prices them, nothing is missing, and those not named so are listed (for the tooltip)."""
    sh = sheet(tmp_path, f"Rate,Original,Copy,{email},{index},Days\nRegular,$4.30,$1.00,$1.05,$1.10,21\n")
    sp, = sh.speeds
    assert (extra_rate(sp, "email", "e-mail"), extra_rate(sp, "index")) == (Decimal("1.05"), Decimal("1.10"))
    assert missing_prices(sh) == []
    assert sh.read_as == {h: label for h, label in ((email, "Email"), (index, "Index"))
                          if h.lower() not in ("email", "e-mail", "index")}


def test_the_name_comes_first_and_nothing_is_guessed(tmp_path):
    """A heading that is the name wins over one close to it. Two close ones, one close to both, a judge's index
    and other columns are left alone: the price is then missing, and the window says so. Original and Copy keep
    their own columns."""
    def read(headings):
        sh = sheet(tmp_path, f"Rate,Original,Copy,{headings}\nRegular,$4.30,$1.00,$1.01,$1.02,$1.03\n", "Odd")
        sp, = sh.speeds
        return extra_rate(sp, "email", "e-mail"), extra_rate(sp, "index"), missing_prices(sh)

    assert read("Email,Index Price,Index") == (Decimal("1.01"), Decimal("1.03"), [])
    assert read("Email,Index,Index Price") == (Decimal("1.01"), Decimal("1.02"), [])
    assert read("Email,Index Price,Index Fee") == (Decimal("1.01"), 0, [("Regular", ["Index"])])
    assert read("Email/Index,Judge's Index,Notes") == (0, 0, [("Regular", ["Email", "Index"])])
    assert read("Realtime,Media,Indemnity") == (0, 0, [("Regular", ["Email", "Index"])])
    sh = sheet(tmp_path, "Rate,Price,Copy Price,Email Price,Index Price\nRegular,$4.30,$1.00,$1.05,$1.10\n", "Words")
    sp, = sh.speeds
    assert (sp.original, sp.copy, sp.extras) == ("4.30", "1.00", {"Email": "$1.05", "Index": "$1.10"})


def test_the_fingerprint_follows_the_file(tmp_path):
    first = sheet_fingerprint(sheet(tmp_path, GAPS))
    assert first.startswith("Court Rates:") and first == sheet_fingerprint(sheet(tmp_path, GAPS))
    assert sheet_fingerprint(sheet(tmp_path, GAPS + "Hourly,$9.00,$2.00,$2.00,$2.00,0\n")) != first


def test_the_answers_are_this_computers_own():
    """Kept in the settings file, left out of an exported one; only text is read back."""
    assert "rate_sheets_ok" in LOCAL
    assert Settings.from_dict({"rate_sheets_ok": ["Court Rates:abc", 3, None, "Fine:def"]}).rate_sheets_ok == \
        ["Court Rates:abc", "Fine:def"]


# ------------------------------------------------------------------ the window

@pytest.fixture
def window(tmp_path, make_window):
    s = pat_settings()
    s.use_ai = False
    s.output_dir = str(tmp_path / "out")
    s.rate_sheets_dir = str(tmp_path / "sheets")
    (tmp_path / "sheets").mkdir()
    (tmp_path / "sheets" / "Court Rates.csv").write_text(GAPS, encoding="utf-8")
    return make_window(s)


@pytest.fixture
def boxes(monkeypatch):
    """The message boxes shown, and the button each is answered with (boxes.answer: its text; None: closed)."""
    from PySide6 import QtWidgets

    class Boxes(list):
        answer = None

    shown = Boxes()

    def exec_(box):
        shown.append((box.windowTitle(), box.text(), [b.text() for b in box.buttons()]))
        for b in box.buttons():
            if b.text() == shown.answer:
                b.click()
                return

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", exec_)
    return shown


def pick(window, name):
    window.sheet_box.setCurrentIndex(window.sheet_box.findData(name))


def test_picking_an_incomplete_sheet_warns_and_can_go_back(window, boxes):
    assert window.s.rate_sheet == "Sample Rates" and boxes == []  # (complete: nothing said at start)
    boxes.answer = "Go back to Sample Rates"
    pick(window, "Court Rates")
    (title, text, buttons), = boxes
    assert title == "Your rate sheet is incomplete"
    assert text.startswith('The rate sheet "Court Rates" is missing some prices:\n\n'
                           "•  Regular: no Email or Index price\n•  Daily: no Index price\n"
                           "•  Realtime: no Original price (so it isn't offered at all)\n\n"
                           "Leaving off a speed you don't offer is fine, but every speed on the sheet needs all four "
                           "prices: Original, Copy, Email and Index.")
    assert ' The Email and Index prices are read from the columns headed so, or close to it ("E-mail", "Index ' \
           'Price") when only one column is.\n\nUse this rate sheet anyway?' in text
    assert buttons == ["Use it anyway", "Go back to Sample Rates", "Open the rate sheets folder"]
    assert window.s.rate_sheet == "Sample Rates" and window.sheet_box.currentData() == "Sample Rates"
    assert window.s.rate_sheets_ok == []
    assert '\nThe "Index Price" column is read as the Index price\n⚠ Missing: Regular: Email, Index; Daily: Index; ' \
           'Realtime: Original' in window.sheet_box.itemData(window.sheet_box.findData("Court Rates"), 3)  # (tooltip)


def test_use_it_anyway_until_the_file_changes(window, boxes, tmp_path):
    boxes.answer = "Use it anyway"
    pick(window, "Court Rates")
    assert window.s.rate_sheet == "Court Rates" and len(boxes) == 1
    assert window.s.rate_sheets_ok == [sheet_fingerprint(window.s.sheet())]
    assert Settings.load().rate_sheets_ok == window.s.rate_sheets_ok  # (saved)
    window._reload_sheets()  # read again, unchanged: not asked again
    window._check_rate_sheet()
    assert len(boxes) == 1
    (tmp_path / "sheets" / "Court Rates.csv").write_text(GAPS + "Hourly,$9.00,,,,0\n", encoding="utf-8")
    window._reload_sheets()  # edited in Excel, still incomplete: asked again
    assert len(boxes) == 2 and "•  Hourly: no Copy, Email or Index price" in boxes[-1][1]
    assert "Go back to" not in " ".join(boxes[-1][2])  # (the same sheet: nothing to go back to)


def test_open_the_folder_to_fill_it_in(window, boxes, monkeypatch, tmp_path):
    """The folder opens and nothing is kept: not asked again this session unless picked or read again (⟳),
    and gone once the sheet is filled in."""
    opened = []
    monkeypatch.setattr(window, "_open_sheets_folder", lambda: opened.append(True))
    boxes.answer = "Open the rate sheets folder"
    pick(window, "Court Rates")
    assert opened == [True] and window.s.rate_sheet == "Court Rates" and window.s.rate_sheets_ok == []
    window._check_rate_sheet()  # (at start, after Settings: once a session)
    assert len(boxes) == 1
    window._reload_sheets()
    assert len(boxes) == 2
    (tmp_path / "sheets" / "Court Rates.csv").write_text(COMPLETE, encoding="utf-8")
    window._reload_sheets()
    assert len(boxes) == 2


def test_after_settings_the_sheet_in_use_is_checked(window, boxes):
    """Settings → Defaults picked the incomplete sheet: asked, with the one before to go back to."""
    boxes.answer = "Go back to Sample Rates"
    window.s.rate_sheet = "Court Rates"
    window._settings_changed(sheet="Sample Rates")
    assert len(boxes) == 1 and window.s.rate_sheet == "Sample Rates"


def test_the_headings_hint_only_when_email_or_index_is_missing(window, boxes, tmp_path):
    (tmp_path / "sheets" / "No Copy.csv").write_text("Rate,Original,Copy,Email,Index\nRegular,$4.30,,$1.00,$1.00\n",
                                                     encoding="utf-8")
    window._reload_sheets()
    boxes.answer = "Go back to Sample Rates"
    pick(window, "No Copy")
    (_, text, _), = boxes
    assert "•  Regular: no Copy price\n" in text and "headed" not in text
