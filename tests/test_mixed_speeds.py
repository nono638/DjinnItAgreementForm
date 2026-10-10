"""Firms ordering the same pages at different speeds (a split order: Queens bills each firm the speed it committed
to). The one original of those pages is billed at the fastest speed ordered on them; each slower firm pays its
share as if every firm had ordered its speed, and the fastest firms split the rest (the Queens split rule,
courthouses/queens_supreme_civil/billing.py, from the reporter's workbook). Each firm pays its own copy, e-mailed
copy and index at its own speed.

The arithmetic is checked against the workbook's own examples and its two-speed table, which come out of the
bundled Sample Rates: Daily original $6.50, copy, e-mailed copy and index $1.25; Immediate $7.60 and $1.45; with
the index, a page of the original and the judge's index is $7.75 / $9.05 and a firm's own copy, e-mailed copy
and index $3.75 / $4.35.

Then the jobs: who ordered at which speed (Job.speeds, and a run's own in Job.portions), a firm alone still
choosing from its invoice, split orders asked about (speeds_to_ask) or given the agreement form's speed
(settle_split_speeds), three speeds on the same pages held for now, the speeds kept through renames and the
records; the forms (an agreement naming its firm's speed, or both of two with "see below"; the MOFR ticking
each), the math spelled out, the invoice and its note, the settings' new text row, and the window (the "Who
ordered what" card's Speed boxes, the question at Generate, the Excerpts window). All names are made up."""
import json
from decimal import Decimal
from fractions import Fraction

import pymupdf
import pytest

from minute_filler import courthouses
from minute_filler.batch import (Job, job_from_origin, job_origin, joint_invoice, settle_split_speeds,
                                 speeds_to_ask)
from minute_filler.courthouses.base import Party
from minute_filler.courthouses.queens_supreme_civil.billing import split
from minute_filler.invoice import firm_invoices, render, text_conditions
from minute_filler.invoice_calc import Share, quote_ordered, quote_shares
from minute_filler.models import Attorney

from helpers import ROE, make_case, pat_settings


@pytest.fixture
def sheet():
    return pat_settings().sheet()


def priced(sheet, speed, others, pages=10, n=None, indexed=False, email=True):
    """One firm's ordered quote for `pages` pages it ordered at `speed` with the other firms ((name, speed) each)."""
    n = n or 1 + len(others)
    return quote_ordered([Share(pages, n, indexed, speed, tuple(others))], sheet, courthouses.split_rule(), email)


# ------------------------------------------------------------------ the rule


def test_a_slower_firm_pays_its_own_speeds_share_and_the_fastest_the_rest():
    daily = Party("Counsel & Counsel", "Daily", Fraction("6.50"), 2)
    immediate = Party("Smith Law", "Immediate", Fraction("7.60"), 3)
    slow, fast = split([daily, immediate], 0), split([daily, immediate], 1)
    assert slow.per_page == Fraction("3.25") and fast.per_page == Fraction("4.35")
    assert slow.per_page + fast.per_page == Fraction("7.60")  # one original, at Immediate
    assert "as if both firms had ordered Daily: $6.50 ÷ 2 = $3.25 a page" in slow.steps[0][0]
    words = [w for w, _ in fast.steps]
    assert words[0] == "Counsel & Counsel ordered Daily and pays $6.50 ÷ 2 = $3.25 a page of it"
    assert words[-1] == "This firm pays the rest: $7.60 − $3.25 = $4.35 a page"


def test_the_firms_at_the_fastest_speed_split_the_rest():
    parties = [Party("A", "Daily", Fraction("6.50"), 2), Party("B", "Immediate", Fraction("7.60"), 3),
               Party("C", "Immediate", Fraction("7.60"), 3)]
    parts = [split(parties, i).per_page for i in range(3)]
    assert parts[0] == Fraction("6.50") / 3
    assert parts[1] == parts[2] == (Fraction("7.60") - Fraction("6.50") / 3) / 2
    assert sum(parts) == Fraction("7.60")
    assert "The 2 firms at Immediate split the rest" in split(parties, 1).steps[-1][0]


def test_the_rule_works_for_three_speeds_though_queens_allows_two_for_now():
    # (the workbook's rule; the cap is the profile's, speeds_together, until three speeds are settled)
    parties = [Party("A", "Regular", Fraction("4.30"), 0), Party("B", "Daily", Fraction("6.50"), 2),
               Party("C", "Immediate", Fraction("7.60"), 3)]
    parts = [split(parties, i).per_page for i in range(3)]
    assert parts[:2] == [Fraction("4.30") / 3, Fraction("6.50") / 3]
    assert sum(parts) == Fraction("7.60")
    assert courthouses.speeds_together() == 2


# ------------------------------------------------------------------ the workbook's examples


def test_the_workbooks_worked_example(sheet):
    """10 pages, no index: Daily $57.50, Immediate $72.50, together $130.00."""
    daily = priced(sheet, "Daily", [("Smith Law", "Immediate")])
    immediate = priced(sheet, "Immediate", [("Counsel & Counsel", "Daily")])
    assert (daily.speed, daily.per_party) == ("Daily", Decimal("57.50"))
    assert (immediate.speed, immediate.per_party) == ("Immediate", Decimal("72.50"))
    assert daily.due + immediate.due == Fraction(130)
    assert daily.ordered == ("Daily",) and daily.mixed and immediate.mixed


def test_the_workbooks_sample_job_with_the_index(sheet):
    """52 pages, an index: Daily $7.625 a page, $396.50; Immediate $9.525 a page, $495.30."""
    daily = priced(sheet, "Daily", [("Smith Law", "Immediate")], 52, indexed=True)
    immediate = priced(sheet, "Immediate", [("Counsel & Counsel", "Daily")], 52, indexed=True)
    assert daily.due == Fraction("7.625") * 52 and daily.per_party == Decimal("396.50")
    assert immediate.due == Fraction("9.525") * 52 and immediate.per_party == Decimal("495.30")
    assert [l.label for l in daily.lines] == ["Original", "Copy", "E-mailed copy", "Index", "Judge's index"]


TABLE = [(1, 1, "7.625", "9.525"), (2, 1, "6.333333", "8.233333"), (1, 2, "6.333333", "7.583333"),
         (3, 1, "5.6875", "7.5875"), (2, 2, "5.6875", "6.9375"), (1, 3, "5.6875", "6.720833"),
         (4, 1, "5.3", "7.2"), (3, 2, "5.3", "6.55"), (2, 3, "5.3", "6.333333"), (1, 4, "5.3", "6.225"),
         (5, 1, "5.041667", "6.941667"), (4, 2, "5.041667", "6.291667"), (3, 3, "5.041667", "6.075"),
         (2, 4, "5.041667", "5.966667"), (1, 5, "5.041667", "5.901667")]


@pytest.mark.parametrize("dailies, immediates, daily_pays, immediate_pays", TABLE)
def test_the_workbooks_two_speed_table(sheet, dailies, immediates, daily_pays, immediate_pays):
    """What a Daily firm and an Immediate firm pay a page (with the index), for 2 to 6 firms on the same pages:
    the workbook's "Rate Table" tab, rebuilt from Sample Rates."""
    firms = [(f"Daily firm {i}", "Daily") for i in range(dailies)] + \
            [(f"Immediate firm {i}", "Immediate") for i in range(immediates)]
    each = {}
    for i, (name, speed) in enumerate(firms):
        q = priced(sheet, speed, firms[:i] + firms[i + 1:], 60, indexed=True)
        each.setdefault(speed, set()).add(q.due / 60)
    assert len(each["Daily"]) == len(each["Immediate"]) == 1  # the firms of one speed pay alike
    d, i = each["Daily"].pop(), each["Immediate"].pop()
    assert abs(d - Fraction(daily_pays)) < Fraction(1, 10 ** 6)
    assert abs(i - Fraction(immediate_pays)) < Fraction(1, 10 ** 6)
    # every firm's part adds up to the whole bill: one original and judge's index at Immediate, own copies
    whole = Fraction("9.05") + immediates * Fraction("4.35") + dailies * Fraction("3.75")
    assert dailies * d + immediates * i == whole


def test_one_speed_for_every_firm_is_the_usual_split(sheet):
    """Ordered at one speed, the price is quote_shares' to the fraction of a cent."""
    for indexed in (False, True):
        mine = priced(sheet, "Daily", [("Smith Law", "Daily"), ("Poe LLP", "Daily")], 50, indexed=indexed)
        usual = quote_shares([Share(50, 3, indexed)], sheet.find("Daily"))
        assert mine.due == usual.due and mine.per_party == usual.per_party
        assert not mine.mixed


def test_a_parties_number_counts_parties_at_this_firms_speed(sheet):
    """A Parties number above the firms (the others aren't in the table) is billing one side: those parties
    are taken to order this firm's speed, as before."""
    mine = priced(sheet, "Daily", [], 30, n=2)
    assert mine.due == quote_shares([Share(30, 2)], sheet.find("Daily")).due


def test_a_speed_not_on_the_sheet_or_no_rule_is_refused(sheet):
    with pytest.raises(ValueError, match="no Realtime prices"):
        priced(sheet, "Realtime", [("Smith Law", "Daily")])
    with pytest.raises(ValueError, match="no rule"):
        quote_ordered([Share(10, 2, False, "Daily", (("Smith Law", "Immediate"),))], sheet, None)


# ------------------------------------------------------------------ the jobs


def counsel():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def smith():
    return Attorney(name="Dana Smith", firm="Smith Law", checked=True)


def poe():
    return Attorney(name="Sam Poe", firm="Poe LLP", checked=True)


A, B, C = counsel().key(), smith().key(), poe().key()


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_include_index = False  # (the examples below: original, copy and e-mailed copy)
    s.save_math = "off"
    return s


def typed_day(pages=120, date="6/3/2026", attorneys=None) -> Job:
    """A day of Roe v. X.Y. whose pages were typed in (no transcript), Counsel & Counsel and Smith Law ticked."""
    job = Job()
    job.case = make_case({**ROE, "dates": date, "est_pages": str(pages)},
                         attorneys if attorneys is not None else [counsel(), smith()])
    return job


def bills(s, *jobs) -> dict:
    """Each firm's invoice of these days: key -> [(speed, amount)]."""
    case, opts = joint_invoice(list(jobs))
    return {f.atty.key(): [(q.speed, q.per_party) for q in f.quotes] for f in firm_invoices(case, s, opts)}


def excerpt_example() -> Job:
    """Counsel & Counsel ordered pages 1-60 at Daily; Smith Law all 120 at Immediate."""
    job = typed_day()
    job.portions = [(60, [A, B]), (120, [B])]
    job.speeds = {A: "Daily", B: "Immediate"}
    return job


def test_the_excerpt_example_is_billed_at_each_firms_speed(s):
    """Pages 1-60: one original at Immediate ($7.60), Counsel & Counsel's share $6.50 ÷ 2 = $3.25 a page, Smith
    Law the rest, $4.35; pages 61-120 Smith Law alone, $7.60. Copies at each firm's own speed."""
    got = bills(s, excerpt_example())
    assert got[A] == [("Daily", Decimal("345.00"))]  # 60 x (3.25 + 1.25 + 1.25)
    assert got[B] == [("Immediate", Decimal("1065.00"))]  # 60 x (4.35 + 2.90) + 60 x (7.60 + 2.90)


def test_a_split_order_without_speeds_is_billed_and_asked_at_the_forms_speed(s):
    job = typed_day()
    assert bills(s, job) == {A: [("Expedite", Decimal("588.00"))], B: [("Expedite", Decimal("588.00"))]}
    asked = speeds_to_ask([job], s)
    assert [(a.key, a.speed, a.shared_with) for a in asked] == [(A, "Expedite", "Dana Smith"),
                                                                 (B, "Expedite", "Alex B. Counsel")]
    assert job.speeds_needed(s) == [A, B]
    settle_split_speeds([job], s, ["invoice"])
    assert job.speeds == {A: "Expedite", B: "Expedite"} and not speeds_to_ask([job], s)


def test_a_firm_alone_still_chooses_from_its_invoice(s):
    job = typed_day(attorneys=[counsel()])
    assert [sp for sp, _ in bills(s, job)[A]] == ["Regular", "Expedite"]
    assert not speeds_to_ask([job], s)
    job.speeds = {A: "Daily"}  # (a speed set for it anyway: billed alone)
    assert bills(s, job)[A] == [("Daily", Decimal("1080.00"))]


def test_three_speeds_on_the_same_pages_are_held_for_now(s):
    job = typed_day(attorneys=[counsel(), smith(), poe()])
    job.speeds = {A: "Daily", B: "Immediate", C: "Regular"}
    assert "3 different speeds" in job.invoice_hold() and "at most 2" in job.invoice_hold()
    job.speeds[C] = "Daily"  # two speeds: billed
    assert not job.invoice_hold()
    assert sum(q for k in (A, B, C) for _, q in bills(s, job)[k]) > 0


def test_a_trial_may_use_more_speeds_than_any_of_its_pages(s):
    """Counsel & Counsel Daily on 6/3 and Regular on 6/4, Smith Law Immediate on both: two speeds a day."""
    first, second = typed_day(60), typed_day(40, "6/4/2026")
    first.speeds = {A: "Daily", B: "Immediate"}
    second.speeds = {A: "Regular", B: "Immediate"}
    assert not first.invoice_hold() and not second.invoice_hold()
    got = bills(s, first, second)
    assert got[A][0][0] == "Daily + Regular" and got[B][0][0] == "Immediate"
    # Counsel & Counsel: 60 x (3.25 + 2.50) + 40 x (4.30 / 2 + 2.00) = 345.00 + 166.00
    assert got[A][0][1] == Decimal("511.00")


def test_a_speed_set_on_one_day_is_asked_for_the_others(s):
    first, second = typed_day(60), typed_day(40, "6/4/2026", [counsel()])
    first.speeds = {A: "Daily", B: "Immediate"}
    asked = speeds_to_ask([first, second], s)
    assert [(a.job, a.key, a.speed) for a in asked] == [(second, A, "Daily")]  # (no invoice is partly "choose one")


def test_the_speeds_follow_renames_and_the_records(s):
    job = excerpt_example()
    job.set_speed(A, "Regular", row=0)  # Counsel & Counsel's run at its own speed
    assert job.portions[0] == (60, [A, B], {A: "Regular"}) and job.speed_of(A, 0) == "Regular"
    job.rename_in_portions(A, "counsel and counsel llp")
    assert job.speeds["counsel and counsel llp"] == "Daily" and A not in job.speeds
    assert job.portions[0][2] == {"counsel and counsel llp": "Regular"}
    back = job_from_origin(json.loads(json.dumps(job_origin(job))), s)
    assert back.speeds == job.speeds and back.portions == job.portions
    old = job_from_origin({"job": {"portions": [[60, [A, B]], [120, [B]]]}}, s)  # (a record from before 2.7)
    assert old.portions == [(60, [A, B]), (120, [B])] and old.speeds == {}
    job.set_speed("counsel and counsel llp", "Daily")  # all its pages again: the run's own speed goes
    assert job.portions[0] == (60, ["counsel and counsel llp", B])


def test_each_firm_s_forms_name_its_speeds(s):
    from minute_filler.fill import agreement_case, build_values, speeds_case
    from minute_filler.mofr import build_values as mofr_values
    case = make_case({**ROE, "delivery": "Expedited", "rate": "5.40"}, [counsel()])
    on = agreement_case(case, counsel(), {A: 60}, {A: [("Daily", "6/3/2026")]}, s)
    v = build_values(on, counsel(), s)
    assert v["delivery_daily"] and not v["delivery_expedited"] and v["rate"] == "6.50" and v["est_pages"] == "60"
    assert case.get("delivery") == "Expedited" and case.get("rate") == "5.40"  # (the job's own are untouched)
    two = agreement_case(case, counsel(), {A: 100}, {A: [("Daily", "6/3/2026"), ("Regular", "6/4/2026")]}, s)
    v = build_values(two, counsel(), s)
    assert v["delivery_daily"] and v["delivery_regular"] and not v["delivery_expedited"]
    assert v["rate"] == "see below"
    assert v["speeds_note"] == "Daily $6.50 a page: 6/3/2026; Regular $4.30 a page: 6/4/2026"
    v = build_values(speeds_case(case, [("Immediate", "6/3/2026"), ("Daily", "6/4/2026")], s), counsel(), s)
    assert v["delivery_daily"] and v["delivery_other_check"] and v["delivery_other"] == "Immediate"
    m = mofr_values(speeds_case(case, [("Daily", ""), ("Regular", "")], s), s)
    assert m["daily"] and m["regular"] and not m["expedited"]


def test_a_two_speed_agreement_says_see_below_on_the_form(s, tmp_path):
    from minute_filler.deliver import generate
    case = make_case({**ROE, "dates": "6/3/2026, 6/4/2026", "delivery": "Expedited", "rate": "5.40"}, [counsel()])
    path, = generate(case, s, tmp_path, ["agreement"], ordered={A: 100, "": 100},
                     speeds={A: [("Daily", "6/3/2026"), ("Regular", "6/4/2026")]})
    with pymupdf.open(path) as doc:
        fields = {w.field_name: w.field_value for w in doc[0].widgets()}
    assert fields["speeds_note"] == "Daily $6.50 a page: 6/3/2026; Regular $4.30 a page: 6/4/2026"
    assert fields["7 Rate to be Charged Per Page"] == "see below"


def test_a_day_s_runs_at_two_speeds_say_which_pages(s):
    job = excerpt_example()
    job.set_speed(B, "Daily", row=1)  # Smith Law's pages 61-120 at Daily
    assert job.ordered_speeds()[B] == [("Immediate", "6/3/2026 pages 1–60"), ("Daily", "6/3/2026 pages 61–120")]
    assert job.ordered_speeds()[A] == [("Daily", "6/3/2026")]


# ------------------------------------------------------------------ the math, the invoice, the settings


def test_the_math_says_how_each_firm_s_part_of_the_original_is_reached(s):
    from minute_filler.invoice_math import MIXED_NOTE, explain
    case, opts = joint_invoice([excerpt_example()])
    made = [(f, "") for f in firm_invoices(case, s, opts)]
    html, plain = explain(made)
    assert "  Daily (ordered)\n" in plain and "  Immediate (ordered)\n" in plain and MIXED_NOTE in plain
    assert ("    Original: 60 pages ordered with Dana Smith (Immediate): 60 × $3.25 = $195.00. Billed once, at "
            "Immediate ($7.60 a page). This firm ordered Daily, so it pays as if both firms had ordered Daily: "
            "$6.50 ÷ 2 = $3.25 a page.\n") in plain
    assert ("      60 pages ordered with Alex B. Counsel (Daily): 60 × $4.35 = $261.00. Billed once, at Immediate "
            "($7.60 a page). Alex B. Counsel ordered Daily and pays $6.50 ÷ 2 = $3.25 a page of it. This firm "
            "pays the rest: $7.60 − $3.25 = $4.35 a page.\n") in plain
    assert "      60 pages ordered by this firm alone: 60 × $7.60 = $456.00\n" in plain
    assert "Copy: 120 pages × $1.45 = $174.00 (each firm pays for its own, at its own speed)" in plain
    assert "As ordered (Daily, Immediate): the 2 firms together pay $1,410.00 for work that costs $1,410.00" in plain
    assert "Billed once, at Immediate ($7.60 a page)" in html
    firms, _ = explain(made, "firms")
    assert "As ordered (each firm at its own speed)" in firms and "Billed once, divided by speed" in firms
    assert "60 pp. × $4.35 + 60 pp. × $7.60" in firms  # (Smith Law's original: its two parts, each priced)
    assert "In parts: each row below says how" in html


def test_the_invoice_bills_one_speed_and_says_how(s, tmp_path):
    case, opts = joint_invoice([excerpt_example()])
    f = next(f for f in firm_invoices(case, s, opts) if f.atty.key() == A)
    assert "mixed_speeds" in text_conditions(f.quotes, f.opts) and "one_speed" in text_conditions(f.quotes, f.opts)
    path = render(f.case, f.atty, s, f.quotes, "2026-0012", tmp_path / "invoice.pdf", f.opts)
    with pymupdf.open(path) as doc:
        text = " ".join(doc[0].get_text().replace("ﬀ", "ff").split())  # (the font's "ff" ligature)
        values = [w.field_value for w in doc[0].widgets()]
    assert "DELIVERY" in text and "CHOOSE ONE" not in text and "$345.00" in values
    assert "Pages ordered together at different speeds: the original is billed once, at the faster speed" in text


def test_the_note_is_added_to_earlier_settings_once():
    from minute_filler.settings import Settings, default_invoice_texts
    rows = [r for r in default_invoice_texts() if r["when"] != "mixed_speeds"]
    s = Settings.from_dict({"settings_version": 11, "invoice_texts": rows})
    whens = [r["when"] for r in s.invoice_texts]
    assert whens.count("mixed_speeds") == 1 and whens.index("mixed_speeds") == whens.index("split_even") + 1
    again = Settings.from_dict({"settings_version": 12, "invoice_texts": rows})  # (removed by the user: stays so)
    assert "mixed_speeds" not in [r["when"] for r in again.invoice_texts]
    own = Settings.from_dict({"invoice_texts": [{"where": "footer", "when": "always", "text": "Thank you."}]})
    assert [r["when"] for r in own.invoice_texts] == ["always", "mixed_speeds"]  # (no amounts rows: last)
    assert any(r["when"] == "mixed_speeds" for r in default_invoice_texts())


# ------------------------------------------------------------------ the window


@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui.dialogs import ClarifyDialog
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(ClarifyDialog, "exec", lambda self: qt.QDialog.Accepted)
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.outputs = ["invoice"]
    s.save_math = "off"
    s.invoice_include_index = False
    return make_window(s)


def wait(win, qt, until, seconds=20):
    """Runs the Qt event loop until no background task is left and until() is true."""
    import time
    end = time.time() + seconds
    while time.time() < end:
        qt.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def one_day(win, qt, tmp_path) -> Job:
    """A 30-page transcript of 6/3/2026 ordered by Counsel & Counsel and Smith Law, shown in the window."""
    from helpers import transcript_pdf
    win.add_files([str(transcript_pdf(tmp_path / "a.pdf", pages=30, date="June 3, 2026"))])
    wait(win, qt, lambda: len(win.jobs) == 1 and win.jobs[0].docs)
    job = win.jobs[0]
    job.case.attorneys = [counsel(), smith()]
    win._show_job()
    return job


def speed_box(win, name):
    """The "Who ordered what" card's Speed box of this attorney's row."""
    from minute_filler.gui.main_window import ORDER_SPEED
    row = next(r for r in range(win.orders.rowCount()) if win.orders.item(r, 1).text() == name)
    return win.orders.cellWidget(row, ORDER_SPEED)


def test_the_card_s_speed_box_sets_a_firm_s_speed(window, qt, tmp_path):
    job = one_day(window, qt, tmp_path)
    box = speed_box(window, "Alex B. Counsel")
    # (it shares pages with Smith Law: it commits to a speed, so "Chooses on the invoice" isn't offered)
    assert box.currentText() == "Speed? (Generate asks)" and box.findData("") == -1
    box.setCurrentIndex(box.findData("Daily"))
    box.activated.emit(box.currentIndex())
    wait(window, qt, lambda: speed_box(window, "Alex B. Counsel") is not box)  # (the card is filled again)
    assert job.speeds == {A: "Daily"} and speed_box(window, "Alex B. Counsel").currentText() == "Daily"
    assert "Each firm's own speed" in window._form_speed_text()
    # Who pays what: Smith Law priced at the form's speed (Expedite) meanwhile, so at different speeds
    assert "at its own speed. Each firm also pays" in window.pays.text()  # (one full stop between them)


def test_generate_asks_the_speeds_of_a_split_order_first(window, qt, tmp_path, monkeypatch):
    from minute_filler.gui.dialogs import SplitSpeedsDialog
    job = one_day(window, qt, tmp_path)
    seen = []

    def go_back(dlg):
        seen.append([cb.currentData() for cb in dlg.combos])
        texts = [x.text() for x in dlg.findChildren(qt.QLabel)]
        assert texts.count(job.title()) == 1 and texts.count("6/3/2026") == 2  # (the case once, above its days)
        return SplitSpeedsDialog.Rejected

    monkeypatch.setattr(SplitSpeedsDialog, "exec", go_back)
    window.fill()
    assert seen == [["Expedite", "Expedite"]] and not job.saved and job.speeds == {}  # (the form's speed)

    def daily_and_immediate(dlg):
        dlg.combos[0].setCurrentIndex(dlg.combos[0].findData("Daily"))
        dlg.combos[1].setCurrentIndex(dlg.combos[1].findData("Immediate"))
        return SplitSpeedsDialog.Accepted

    monkeypatch.setattr(SplitSpeedsDialog, "exec", daily_and_immediate)
    window.fill()
    wait(window, qt, lambda: job.saved)
    assert job.speeds == {A: "Daily", B: "Immediate"}
    from minute_filler.deliver import ledger_for
    got = {i.bill_to: (i.billed_speed, i.amounts) for i in ledger_for(window.s).invoices()}
    # 30 pages: Daily 30 x (3.25 + 2.50), Immediate 30 x (4.35 + 2.90)
    assert got == {"Alex B. Counsel": ("Daily", {"Daily": "172.50"}),
                   "Dana Smith": ("Immediate", {"Immediate": "217.50"})}


def test_the_excerpts_window_sets_the_speeds(window, qt, tmp_path):
    from minute_filler.gui.excerpts import FIRST_FIRM, PAGES
    job = one_day(window, qt, tmp_path)
    window._who_ordered()
    w = window.excerpts
    assert set(w.firm_speed_boxes) == {A, B}
    w._firm_speed_chosen(A, "Daily")
    w._firm_speed_chosen(B, "Immediate")
    wait(window, qt, lambda: job.speeds == {A: "Daily", B: "Immediate"})
    wait(window, qt, lambda: w.table.item(0, FIRST_FIRM).text().startswith("Daily · "))
    assert w.table.item(0, FIRST_FIRM).text() == "Daily · $172.50"
    assert w.table.item(0, FIRST_FIRM + 1).text() == "Immediate · $217.50"
    assert "(one original, at Immediate)" in w.summary.text()
    # every run at the speeds ordered: no "Prices for" boxes, no "Each pays · <speed>" columns
    heads = [w.table.horizontalHeaderItem(c).text() for c in range(w.table.columnCount())]
    assert not any(h.startswith("Each pays") for h in heads) and w.speeds_label.isHidden()
    w.table.item(0, PAGES).setText("101-110")  # two runs, both firms on each (printed pages 101-130)
    assert [r.speeds for r in w.runs] == [{A: "Daily", B: "Immediate"}] * 2
    w.table.selectRow(1)
    w._set_run_speed(B, "Daily")  # (its right-click: Smith Law on pages 111-130 at Daily)
    assert job.portions == [(10, [A, B]), (30, [A, B], {B: "Daily"})]
    # the boxes of the row shown before are gone at once: two showings in a row once left a box of the first,
    # never laid out, over the window (640 x 480 at its top left) until Qt's event loop deleted it
    w.show()
    qt.QApplication.processEvents()
    from minute_filler.gui.widgets import QuietCombo
    assert {b for b in w.findChildren(QuietCombo) if b.isVisible()} == set(w.firm_speed_boxes.values())
    assert job.ordered_speeds()[B] == [("Immediate", "6/3/2026 pp. 101–110"), ("Daily", "6/3/2026 pp. 111–130")]
    assert w.table.item(1, FIRST_FIRM + 1).text().startswith("Daily · ")
    menu, _ = w._run_menu(w.runs[1], B)
    texts = [a.text() for a in menu.actions()]
    assert "Dana Smith on this run at…" in texts and "Dana Smith: same as its other pages (Immediate)" in texts
    w._set_run_speed(B, None)
    assert job.portions == [(10, [A, B]), (30, [A, B])]  # (the runs alike again; kept as two runs)
    # a firm's invoice at the speed it committed to is listed under the table even with that speed's prices
    # unticked under "Prices for" (they are for firms that choose)
    window.s.excerpt_hidden_speeds = ["Expedite"]
    w._firm_speed_chosen(A, "Expedite")
    wait(window, qt, lambda: job.speeds.get(A) == "Expedite" and "Alex B. Counsel: Expedite $" in w.summary.text())
