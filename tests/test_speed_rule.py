"""The minute agreement form's one speed (2.1): always the Settings rule's (Expedited, else the slowest offered)
unless the user chose one for the job, never a speed a document mentions: when an e-mail asks for another speed,
the window asks which (Minute agreement form details' question and Keep, and a box at Generate). Every change of
what decides it applies it again (speeds ticked, the settings, a new job); the mouse wheel passing over the speed box
changes nothing. The speeds offered are ticked in the Invoice panel, the form's one speed under Minute agreement
form details; "Who pays what" shows each firm's invoice in short as things are ticked; an invoice is made from a page count typed in when there is no transcript,
its excerpts named by their place in the day. The documents and names are made up."""
import os
import re
from decimal import Decimal

import pytest

from minute_filler.batch import Job, make_doc
from minute_filler.extract_regex import DELIVERY_WORDS, RegexExtractor
from minute_filler.ingest import ingest_text
from minute_filler.merge import merge
from minute_filler.models import SRC_DEFAULT, SRC_USER, Attorney, FieldState

from helpers import pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DAILY = ("From: Alex B. Counsel <alex@example.com>\nSubject: Jane Roe v. Sam Poe\n\n"
         "Please send a daily copy of the minutes of 6/3/2026, Index No. 712345/2021.")


def email_job(s, text=DAILY) -> Job:
    """A job of one pasted e-mail, merged as the window does."""
    ing = ingest_text(text, "Pasted text 1")
    doc = make_doc(ing, RegexExtractor(s.profile).extract(ing), s)
    job = Job(docs=[doc])
    job.case = merge(doc.extractions(), s)
    return job


def test_an_email_asking_for_a_daily_copy_is_asked_about_not_applied():
    s = pat_settings()
    job = email_job(s)
    assert job.case.get("delivery") == "Expedite" and job.case.fields["delivery"].source == SRC_DEFAULT
    assert job.case.get("rate") == "5.40"  # the rule's speed's rate
    assert job.asked_speed()[:2] == ("Daily", "daily copy")
    assert job.speed_question() == 'the e-mail mentions "daily copy"'
    assert any("daily copy" in p for p in job.output_problems(["agreement"]))
    assert not any("daily copy" in p for p in job.output_problems(["invoice"]))  # (the invoice offers them all)
    job.case.fields["delivery"] = FieldState("Daily", SRC_USER, 1.0, ["Daily"])  # chosen: answered
    assert job.speed_question() == ""


def test_an_email_asking_for_the_rules_own_speed_asks_nothing():
    s = pat_settings()
    job = email_job(s, DAILY.replace("a daily copy", "an expedited copy"))
    assert job.asked_speed()[0] == "Expedited" and job.speed_question() == ""


def test_a_speed_word_inside_another_word_asks_for_no_speed():
    """The AI's speed was kept when "regular" was anywhere in the text, "irregular" too."""
    assert not re.search(DELIVERY_WORDS["Regular"], "an irregular schedule, abnormal hours")
    assert re.search(DELIVERY_WORDS["Regular"], "at your regular rate")
    assert re.search(DELIVERY_WORDS["Expedited"], "please expedite")


# ------------------------------------------------------------ in the window

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


def test_ticking_expedite_again_brings_the_form_back_to_it(window, tmp_path):
    window.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=12))])
    wait(window, lambda: window.cur.docs)
    window.inv_speed_boxes["Expedite"].setChecked(False)
    assert window.case.get("delivery") == "Regular" and window.rows["rate"].text() == "4.30"
    window.inv_speed_boxes["Expedite"].setChecked(True)  # it was left on Regular
    assert window.case.get("delivery") == "Expedite" and window.rows["rate"].text() == "5.40"
    assert window.delivery.currentData() == "Expedite"


def test_a_new_rule_in_settings_reaches_a_job_without_documents(window):
    assert window.case.get("delivery") == "Expedite"
    window.s.agreement_speed_first = "Regular"
    window._settings_changed()
    assert window.case.get("delivery") == "Regular" and window.delivery.currentData() == "Regular"
    assert window.rows["rate"].text() == "4.30"


def test_a_new_job_shows_the_rules_speed_not_the_last_one_chosen(window):
    window.delivery.setCurrentIndex(window.delivery.findData("Regular"))  # this job's own choice
    assert window.case.fields["delivery"].source == SRC_USER and not window.speed_reset.isHidden()
    window.new_job()
    assert window.case.get("delivery") == "Expedite" and window.delivery.currentData() == "Expedite"
    assert window.rows["rate"].text() == "5.40" and window.speed_reset.isHidden()


def test_back_to_the_rule(window):
    window.delivery.setCurrentIndex(window.delivery.findData("Regular"))
    window.speed_reset.click()
    assert window.case.get("delivery") == "Expedite" and window.case.fields["delivery"].source == SRC_DEFAULT


def test_the_mouse_wheel_doesnt_change_the_speed_box_it_passes_over(window, qt):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    before = window.delivery.currentData()
    window.delivery.clearFocus()
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    qt.QApplication.sendEvent(window.delivery, event)
    assert window.delivery.currentData() == before and window.case.fields["delivery"].source != SRC_USER


def test_the_form_details_card_asks_when_an_email_names_another_speed(window):
    window.add_text(DAILY)
    wait(window, lambda: window.cur.docs)
    assert not window.speed_ask.isHidden()
    assert '"daily copy"' in window.speed_ask.text() and "Please choose a speed" in window.speed_ask.text()
    assert "Daily isn't offered" in window.speed_ask.text()  # (only Regular and Expedite are ticked)
    assert window.delivery.property("ask") is True
    assert window.speed_keep.text() == "Keep Expedite"
    window.speed_keep.click()
    assert window.case.fields["delivery"].source == SRC_USER and window.case.get("delivery") == "Expedite"
    assert window.speed_ask.isHidden() and window.cur.speed_question() == ""


def test_generate_asks_which_speed_and_the_answer_is_the_users(window, monkeypatch):
    from minute_filler.gui import dialogs
    window.inv_speed_boxes["Daily"].setChecked(True)
    window.add_text(DAILY)
    wait(window, lambda: window.cur.docs)
    asked = {}

    def answer(self):
        asked["rows"] = [cb.currentData() for cb in self.combos]
        return dialogs.SpeedDialog.Accepted
    monkeypatch.setattr(dialogs.SpeedDialog, "exec", answer)
    assert window._ask_speeds([window.cur])
    assert asked["rows"] == ["Daily"]  # the e-mail's speed, preselected (offered now)
    assert window.case.get("delivery") == "Daily" and window.case.fields["delivery"].source == SRC_USER
    assert window.rows["rate"].text() == window.s.rate_for("Daily") and window.delivery.currentData() == "Daily"
    assert window.cur.speed_question() == ""


def test_going_back_from_the_speed_question_makes_nothing(window, monkeypatch):
    from minute_filler.gui import dialogs
    window.add_text(DAILY)
    wait(window, lambda: window.cur.docs)
    monkeypatch.setattr(dialogs.SpeedDialog, "exec", lambda self: dialogs.SpeedDialog.Rejected)
    assert not window._ask_speeds([window.cur])
    assert window.case.fields["delivery"].source == SRC_DEFAULT


def test_the_speeds_offered_are_the_invoices_and_the_card_is_the_forms(window):
    """The card that names the form's speed is "Minute agreement form details" (it was "Order"); the speeds the
    invoice offers are ticked in the Invoice panel, where they stay usable while Invoice is unticked, as the form
    names one of them."""
    from PySide6.QtWidgets import QLabel
    titles = [w.text() for w in window.findChildren(QLabel) if w.objectName() == "section"]
    assert "Minute agreement form details" in titles and "Order" not in titles
    panel = window._output_cols[list(window.output_boxes).index("invoice")]
    assert all(panel.isAncestorOf(cb) for cb in window.inv_speed_boxes.values())
    window.output_boxes["invoice"].setChecked(False)
    assert not window.output_opts["invoice"].isEnabled()
    assert all(cb.isEnabled() for cb in window.inv_speed_boxes.values())
    ticked = [n for n, cb in window.inv_speed_boxes.items() if cb.isChecked()]
    assert ticked == [window.delivery.itemData(i) for i in range(window.delivery.count() - 1)]  # (less Other)
    assert ticked == ["Regular", "Expedite"]  # cheapest first in both, as on the invoice
    assert window.inv_speed_boxes["Regular"].text() == "Regular  $4.30/pg"
    assert window.ag_speed.text() == "Expedite · $5.40 a page"


# ------------------------------------------------------------ who pays what

def test_how_a_firm_ordered_its_pages():
    from minute_filler.invoice_math import how_shared
    assert how_shared([(70, 1), (50, 2)]) == "70 alone + 50 shared by 2"
    assert how_shared([(30, 1)]) == "alone" and how_shared([(30, 3)]) == "shared by 3"


def test_who_pays_what_rows():
    """Alex ordered 40 pages, Dana the last 30 of them: Alex 10 alone and 30 shared, Dana 30 shared."""
    from minute_filler.batch import joint_invoice
    from minute_filler.invoice import firm_invoices
    from minute_filler.invoice_math import who_pays
    s = pat_settings()
    s.invoice_include_index = False
    job = Job()
    job.case.set("est_pages", "40")
    a = Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)
    d = Attorney(name="Dana Smith", firm="Smith Law", checked=True)
    job.case.attorneys = [a, d]
    job.portions = [(10, [a.key()]), (40, [a.key(), d.key()])]
    case, opts = joint_invoice([job])
    rows = who_pays(firm_invoices(case, s, opts))
    assert [(r.name, r.pages, r.how) for r in rows] == [
        (a.label(), 40, "10 alone + 30 shared by 2"), (d.label(), 30, "shared by 2")]
    assert [sp for sp, _, _ in rows[0].prices] == ["Regular", "Expedite"]
    for r in rows:
        for _, pays, each in r.prices:
            assert pays > 0 and each == (pays / r.pages).quantize(Decimal("0.01"))
    # Alex pays the whole original of its 10 pages alone and half of the 30 shared, Dana half of those
    alex, dana = (r.prices[0][1] for r in rows)
    assert alex > dana
    from minute_filler.invoice_math import page_rates
    assert page_rates(firm_invoices(case, s, opts)) == ("A page of the original: Regular $4.30 alone, $2.15 "
                                                        "shared by 2 · Expedite $5.40 alone, $2.70 shared by 2")


def test_the_card_follows_the_ticks_and_the_invoice_box(window):
    from PySide6.QtCore import Qt
    window.output_boxes["invoice"].setChecked(True)
    window.case.set("est_pages", "40")
    window.case.attorneys = [Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True),
                             Attorney(name="Dana Smith", firm="Smith Law", checked=False)]
    window._show_case()
    assert not window.pays_card.isHidden()
    text = window.pays.text()
    assert "Minute agreement forms: <b>Expedite at $5.40 a page</b>" in text
    assert "Alex B. Counsel" in text and "Dana Smith" not in text
    assert "Expedite (form)" in text and "How these amounts are worked out" in text
    assert text.count("<tr>") == 2  # (the heading and Alex)
    window.att.item(1, 0).setCheckState(Qt.Checked)  # Dana ordered too: both rows, the pages shared
    text = window.pays.text()
    assert text.count("<tr>") == 3 and "Dana Smith" in text and "shared by 2" in text
    window.output_boxes["invoice"].setChecked(False)
    assert window.pays_card.isHidden()


def test_the_invoices_are_priced_once_per_change(window, monkeypatch):
    from minute_filler import invoice
    window.output_boxes["invoice"].setChecked(True)
    window.case.set("est_pages", "40")
    window.case.attorneys = [Attorney(name="Alex B. Counsel", checked=True)]
    calls = []
    real = invoice.firm_invoices
    monkeypatch.setattr(invoice, "firm_invoices", lambda *a, **k: calls.append(1) or real(*a, **k))
    window._refresh_outputs()
    assert len(calls) == 1


# ------------------------------------------------------------ no transcript

def test_an_invoice_from_a_caption_and_pages_typed(tmp_path):
    """An e-mail (or a caption page) and "12" typed in Est. number of pages are enough for an invoice; a number
    read from the e-mail is not; the run sheet still needs a transcript."""
    from minute_filler.batch import fill_jobs
    from minute_filler.deliver import NO_INVOICE
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    job = email_job(s, DAILY + "\nAbout 50 pages.")
    job.case.attorneys = [Attorney(name="Alex B. Counsel", checked=True)]
    assert job.invoice_pages() == 0 and job.output_problems(["invoice"]) == [NO_INVOICE]
    job.case.set("est_pages", "12")
    assert job.invoice_pages() == 12 and job.invoice_days() and job.output_problems(["invoice"]) == []
    assert job.makeable(["invoice", "runsheet"]) == ["invoice"]
    assert job.ordered_pages() == {Attorney(name="Alex B. Counsel").key(): 12, "": 12}
    fill_jobs([job], s, outputs=["invoice", "runsheet"])
    assert any("Invoice" in p.name for p in job.saved)
    assert job.error == "no run sheet: no transcript PDF among the inputs"


def test_excerpts_of_pages_typed_bill_by_place():
    a = Attorney(name="Alex B. Counsel", checked=True)
    d = Attorney(name="Dana Smith", firm="Smith Law", checked=True)
    job = Job()
    job.case.set("est_pages", "40")
    job.case.attorneys = [a, d]
    assert job.portions_unavailable() == ""
    job.portions = [(10, [a.key()]), (40, [a.key(), d.key()])]
    assert job.ordered_pages() == {a.key(): 40, d.key(): 30, "": 40}
    assert [p.span for p in job.invoice_orders()[0].portions] == ["pages 1–10", "pages 11–40"]
