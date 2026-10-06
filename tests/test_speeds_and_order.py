"""The Order card (2.0.0): the speeds are chosen once, and the minute agreement form names one of the speeds the
invoice offers (Settings.agreement_speed: Expedited, else the slowest offered, as Settings say; a job's own
choice first). No. of copies follows the firms ticked, and the page count of a transcript is marked as counted
from the PDF."""
import json
import os

import pymupdf
import pytest

from minute_filler.fill import fill_all
from minute_filler.forms import ucs_map
from minute_filler.invoice import billed_speed
from minute_filler.invoice_calc import Quote
from minute_filler.merge import apply_defaults, refresh_copies, refresh_speed
from minute_filler.models import SRC_DERIVED, SRC_PDF, SRC_REGEX, Attorney, CaseInfo, FieldState
from minute_filler.settings import Settings

from helpers import ROE, make_case, pat_settings, transcript_pdf


def test_settings_keep_an_agreement_speed_named_by_a_rate_sheet(qt):
    """A first-choice speed with a rate sheet's own name showed as Regular in Settings, and OK saved Regular."""
    from minute_filler.gui.dialogs import SettingsDialog
    s = pat_settings()
    s.agreement_speed_first = "Two-Day"
    dlg = SettingsDialog(s)
    assert dlg.i_first.currentData() == "Two-Day"
    dlg.accept()
    assert s.agreement_speed_first == "Two-Day"


def test_the_agreement_form_speed_is_expedited_when_offered_else_the_slowest():
    """Settings.agreement_speed, in the sheet's spelling ("Expedited" -> "Expedite"): the first choice when it is
    offered, else the slowest offered, or the fastest when the fallback says so; with no speed ticked, any speed
    of the sheet counts as offered."""
    s = Settings()
    assert s.invoice_speeds == ["Regular", "Expedited"] and s.agreement_speed() == "Expedite"
    s.invoice_speeds = ["Regular"]
    assert s.agreement_speed() == "Regular"
    s.invoice_speeds = ["Daily", "Immediate"]
    assert s.agreement_speed() == "Daily"  # the slowest of those offered
    s.agreement_speed_fallback = "fastest"
    assert s.agreement_speed() == "Immediate"
    s.agreement_speed_first = "Daily"
    assert s.agreement_speed() == "Daily"
    s.invoice_speeds = []  # none ticked: any speed of the sheet
    s.agreement_speed_first = "Expedited"
    assert s.agreement_speed() == "Expedite"


def test_merge_names_an_offered_speed():
    """A speed read from a document stays when the invoice offers it, else gives way to agreement_speed; one
    chosen by the user stays until it is no longer offered (refresh_speed)."""
    s = Settings()
    case = CaseInfo()
    apply_defaults(case, s)
    assert case.get("delivery") == "Expedite"
    assert case.get("rate") == "5.40"  # the rate follows the speed on the form
    case = CaseInfo()
    case.fields["delivery"] = FieldState("Daily", SRC_REGEX, 0.8, ["Daily"])  # asked for, but not offered
    apply_defaults(case, s)
    assert case.get("delivery") == "Expedite"
    case = CaseInfo()
    case.fields["delivery"] = FieldState("regular", SRC_REGEX, 0.8, ["regular"])  # offered: it stays
    apply_defaults(case, s)
    assert case.get("delivery") == "Regular"
    case = make_case({"delivery": "Regular"})  # the user's choice stays while it is offered
    assert not refresh_speed(case, s) and case.get("delivery") == "Regular"
    s.invoice_speeds = ["Expedited"]
    assert refresh_speed(case, s) and case.get("delivery") == "Expedite"


@pytest.mark.parametrize("speeds, box", [(["Regular", "Expedited"], "delivery_expedited"),
                                         (["Regular"], "delivery_regular"),
                                         (["Daily"], "delivery_daily"),
                                         (["Immediate"], "delivery_other")])
def test_the_agreement_form_ticks_the_speed_the_invoice_offers(speeds, box, tmp_path):
    s = pat_settings()
    s.invoice_speeds = speeds
    case = make_case(ROE, [Attorney(name="Alex B. Counsel", checked=True)])
    case.fields["delivery"] = FieldState()
    apply_defaults(case, s)
    with pymupdf.open(fill_all(case, s, tmp_path)[0]) as doc:
        by_key = {ucs_map.WIDGETS.get(w.field_name): w.field_value for w in doc[0].widgets()}
    ticked = {k for k in ("delivery_regular", "delivery_expedited", "delivery_daily", "delivery_other")
              if by_key.get(k)}
    assert ticked == {box}
    assert by_key[box] == ("Immediate" if box == "delivery_other" else "X")  # Other names the speed


def test_the_records_name_the_speed_on_the_agreement_form():
    quotes = [Quote("Regular", 30, 2), Quote("Expedite", 30, 2)]
    assert billed_speed(make_case({"delivery": "Expedite"}), quotes) == "Expedite"
    assert billed_speed(make_case({"delivery": "Daily"}), quotes) == "Regular"
    assert billed_speed(make_case({}), []) == ""


def test_an_old_default_speed_becomes_the_first_choice():
    """Settings saved before version 9 had default_delivery (now removed): one other than Regular becomes
    agreement_speed_first; Regular, the old default, gives way to Expedited. A fallback it can't be loads as
    "slowest"."""
    s = Settings()
    s.path.write_text(json.dumps({"settings_version": 8, "default_delivery": "Daily"}), encoding="utf-8")
    assert Settings.load().agreement_speed_first == "Daily"
    s.path.write_text(json.dumps({"settings_version": 8, "default_delivery": "Regular"}), encoding="utf-8")
    assert Settings.load().agreement_speed_first == "Expedited"  # the old default gives way
    s.path.write_text(json.dumps({"agreement_speed_fallback": "sometimes"}), encoding="utf-8")
    assert Settings.load().agreement_speed_fallback == "slowest"


def test_copies_follow_the_firms_ticked():
    """No. of copies is the number of different firms ticked (two attorneys of Counsel & Counsel are one;
    "Unrepresented" orders nothing), over a number read from a document but not one the user typed; with nobody
    ticked it is the default."""
    s = Settings()
    case = make_case({}, [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel"),
                          Attorney(name="Robin Counsel", firm="Counsel & Counsel"),
                          Attorney(name="Dana Smith", firm="Smith Law"),
                          Attorney(name="Unrepresented"),
                          Attorney(name="Sam Advocate", firm="Advocate LLP", checked=False)])
    case.fields["copies"] = FieldState("5", SRC_REGEX, 0.8, ["5"])  # read from a document: the firms win
    refresh_copies(case, s)
    assert case.get("copies") == "2" and case.fields["copies"].source == SRC_DERIVED
    case.set("copies", "3")  # typed by the user: it stays
    refresh_copies(case, s)
    assert case.get("copies") == "3"
    nobody = make_case({}, [Attorney(name="Alex B. Counsel", checked=False)])
    nobody.fields["copies"] = FieldState()
    refresh_copies(nobody, s)
    assert nobody.get("copies") == s.default_copies


# ------------------------------------------------------------ in the window

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    return make_window(s)


def wait(win, until, seconds=20):
    """Runs the event loop until no work is running and `until()` holds; fails after `seconds`."""
    import time
    from PySide6 import QtWidgets
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_the_order_card_offers_the_speeds_and_names_one_for_the_form(window, tmp_path):
    """The Agreement form box lists only the speeds ticked under Speeds offered (and Other); unticking the
    speed it names moves it to another one, and the rate follows."""
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=12))])
    wait(window, lambda: window.cur.docs)
    assert window.case.get("delivery") == "Expedite"
    shown = [window.delivery.itemData(i) for i in range(window.delivery.count())]
    assert shown == ["Regular", "Expedite", "Other"]  # the speeds offered, and Other
    window.inv_speed_boxes["Expedite"].setChecked(False)
    assert window.case.get("delivery") == "Regular" and window.rows["rate"].text() == "4.30"
    window.inv_speed_boxes["Daily"].setChecked(True)
    window.delivery.setCurrentIndex(window.delivery.findData("Daily"))  # this job's own choice
    assert window.case.get("delivery") == "Daily"
    window.inv_speed_boxes["Regular"].setChecked(False)
    assert window.case.get("delivery") == "Daily"  # still offered: it stays


def test_a_transcripts_pages_are_marked_as_counted_from_the_pdf(window, tmp_path):
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=12))])
    wait(window, lambda: window.cur.docs)
    st = window.rows["est_pages"].state
    assert st.value == "12" and st.source == SRC_PDF
    assert window.rows["est_pages"].badge.text() == "PDF"
    assert "PDF" in window.rows["est_pages"].badge.toolTip()
