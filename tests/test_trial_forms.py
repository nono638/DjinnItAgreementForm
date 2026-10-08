"""One minute agreement per firm, and one MOFR, for the days of a case that share an invoice
(Settings.forms_per_case, batch.form_groups and case_forms): the user's trial of 4 days and 4 firms, one of them
absent the first day and ordering nothing of a day split with Excerpts... Each firm's agreement lists the days it
ordered (days in a row as a range: fill.date_ranges) and its own pages, the same as its invoice; the MOFR lists
every day; the rest (case, copies, proceedings, delivery date) is taken from all the days. Generate all again
makes the forms but no invoice. With the setting off, or an invoice for each day, the forms are one set per day as
before; so are they for a day held for Excerpts... or with nobody ticked, and a run of some of the days lists only
those. Generate all's count and its preview agree with what is made; a settings file from before has the setting
on. All names and numbers are made up."""
import pymupdf
import pytest

from minute_filler.batch import (_date_key, case_forms, expand_paths, files_to_make, fill_jobs, form_groups, group,
                                 read_docs)
from minute_filler.deliver import ledger_for
from minute_filler.fill import date_ranges
from minute_filler.models import Attorney
from minute_filler.settings import Settings

from helpers import pat_settings, transcript_pdf

DAYS = {"September 28, 2026": 30, "September 30, 2026": 20, "October 1, 2026": 40, "October 2, 2026": 50}


def alex():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def dana():
    return Attorney(name="Dana Smith", firm="Smith Law", checked=True)


def sam():
    return Attorney(name="Sam Advocate", firm="Advocate & Partners", checked=True)


def robin():
    return Attorney(name="Robin Example", firm="Example Firm LLP", checked=True)


A, B, C, D = (f().key() for f in (alex, dana, sam, robin))


@pytest.fixture
def s(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return s


@pytest.fixture
def trial(tmp_path, s):
    """Jane Roe's trial: 9/28 (30 pp.), 9/30 (20), 10/1 (40) and 10/2 (50). Four firms ordered; Robin Example was
    not there on 9/28, and of 10/1 the first 15 pages are Alex's only and the rest Alex's, Dana's and Sam's (Robin,
    ticked that day, orders none of it)."""
    d = tmp_path / "in"
    d.mkdir()
    for day, n in DAYS.items():
        transcript_pdf(d / f"Roe {day}.pdf", n, date=day)
    docs, errors = read_docs(expand_paths([str(d)]), s)
    assert not errors
    jobs = sorted(group(docs, s), key=lambda j: _date_key(j.case.get("dates")))
    assert [j.case.get("dates") for j in jobs] == ["9/28/2026", "9/30/2026", "10/1/2026", "10/2/2026"]
    jobs[0].case.attorneys = [alex(), dana(), sam()]
    for job in jobs[1:]:
        job.case.attorneys = [alex(), dana(), sam(), robin()]
    jobs[2].portions = [(15, [A]), (40, [A, B, C])]
    assert not any(j.invoice_hold() for j in jobs)
    return jobs


def kinds(paths) -> list[str]:
    return sorted("Run sheet" if p.suffix == ".xlsx" else p.name.split(" - ")[0].split(" 20")[0] for p in paths)


def field_values(path) -> str:
    with pymupdf.open(path) as doc:
        return " | ".join(str(w.field_value) for w in doc[0].widgets())


def test_one_agreement_per_attorney_for_the_whole_trial(trial, s):
    outputs = ["agreement", "invoice", "runsheet"]
    assert files_to_make(trial, outputs, s) == 9
    made = fill_jobs(trial, s, outputs=outputs)
    assert kinds(made) == ["Invoice"] * 4 + ["Minute Agreement"] * 4 + ["Run sheet"]
    assert not any(j.error for j in trial) and all(j.invoiced for j in trial)
    ledger = ledger_for(s)
    agreements = {a.attorney: a for a in ledger.activity("agreement")}
    invoices = {i.bill_to: i for i in ledger.invoices()}
    assert set(agreements) == set(invoices) == {"Alex B. Counsel", "Dana Smith", "Sam Advocate", "Robin Example"}
    # each firm's own days and pages, the same as its invoice's
    expect = {"Alex B. Counsel": ("9/28/2026, 9/30/2026, 10/1/2026, 10/2/2026", 140),
              "Dana Smith": ("9/28/2026, 9/30/2026, 10/1/2026, 10/2/2026", 125),
              "Sam Advocate": ("9/28/2026, 9/30/2026, 10/1/2026, 10/2/2026", 125),
              "Robin Example": ("9/30/2026, 10/2/2026", 70)}
    for name, (dates, pages) in expect.items():
        row, inv = agreements[name], invoices[name]
        assert (row.dates, row.pages) == (dates, pages) == (inv.dates, inv.pages)
        values = field_values(row.file_path)
        # the records keep every day written out; the form lists days in a row as a range ("9/30/2026–10/2/2026"),
        # going on to the second line (dates_2) when it doesn't fit on the first
        with pymupdf.open(row.file_path) as doc:
            f = {w.field_name: w.field_value for w in doc[0].widgets()}
        shown = ", ".join(x for x in (f["4 Datess of Minutes Requested"], f.get("dates_2", "")) if x)
        assert shown == date_ranges(dates) and f"| {pages} |" in values
    # one file per firm, named for its span of days; the files are listed under the days they cover
    robins = agreements["Robin Example"].file_path
    assert "(9-30-2026 to 10-2-2026)" in robins and "(9-28-2026 to 10-2-2026)" in agreements["Alex B. Counsel"].file_path
    listed = [{p.name for p in j.saved if p.name.startswith("Minute Agreement")} for j in trial]
    assert [len(x) for x in listed] == [3, 4, 3, 4]  # Robin: not on 9/28, nor on 10/1 (it ordered nothing of it)
    assert len(ledger.activity("agreement")) == 4  # each recorded once


def test_one_mofr_for_the_trial(trial, s):
    outputs = ["agreement", "mofr", "invoice", "runsheet"]
    assert files_to_make(trial, outputs, s) == 10
    made = fill_jobs(trial, s, outputs=outputs)
    assert len(made) == 10 and kinds(made).count("MOFR") == 1
    mofr, = ledger_for(s).activity("mofr")
    assert mofr.dates == "9/28/2026, 9/30/2026, 10/1/2026, 10/2/2026" and mofr.pages == 140
    assert all(any(p.name.startswith("MOFR") for p in j.saved) for j in trial)  # listed under every day
    assert "140" in field_values(mofr.file_path)


def test_a_rerun_makes_the_forms_again_but_no_invoice(trial, s):
    outputs = ["agreement", "invoice"]
    fill_jobs(trial, s, outputs=outputs)
    assert files_to_make(trial, outputs, s) == 4  # the agreements only: every day is invoiced
    again = fill_jobs(trial, s, outputs=outputs)
    assert kinds(again) == ["Minute Agreement"] * 4
    assert len(ledger_for(s).invoices()) == 4 and not any(j.error for j in trial)


def test_per_day_when_the_setting_is_off(trial, s):
    s.forms_per_case = False
    assert form_groups(trial, s) == []
    outputs = ["agreement", "mofr", "invoice"]
    # agreements per day (none for Robin on 10/1: ordered none of it), a MOFR a day, invoices
    assert files_to_make(trial, outputs, s) == 3 + 4 + 3 + 4 + 4 + 4
    made = fill_jobs(trial, s, outputs=outputs)
    assert kinds(made).count("Minute Agreement") == 14 and kinds(made).count("MOFR") == 4
    assert kinds(made).count("Invoice") == 4


def test_per_day_without_the_joint_invoice(trial, s):
    s.invoice_joint = False
    assert form_groups(trial, s) == []
    made = fill_jobs(trial, s, outputs=["agreement", "mofr"])
    assert kinds(made).count("Minute Agreement") == 14 and kinds(made).count("MOFR") == 4


def test_only_the_days_in_the_run(trial, s):
    """Generate all of three of the days: the forms list those; a day held for Excerpts... keeps its own."""
    days = trial[1:]
    assert files_to_make(days, ["agreement"], s) == 4
    fill_jobs(days, s, outputs=["agreement"])
    rows = {a.attorney: a.dates for a in ledger_for(s).activity("agreement")}
    assert rows["Robin Example"] == "9/30/2026, 10/2/2026" and rows["Alex B. Counsel"].startswith("9/30/2026")
    trial[2].portions = [(10, [A])]  # rows that no longer fit the day: held, so it keeps its own forms
    assert trial[2].invoice_hold()
    assert [len(g) for g in form_groups(trial, s)] == [3]
    assert files_to_make(trial, ["agreement"], s) == 4 + 4  # the three days, and 10/1's own four


def test_a_day_with_nobody_ticked_keeps_forms_per_day(trial, s):
    for a in trial[1].case.attorneys:
        a.checked = False
    assert form_groups(trial, s) == []
    assert files_to_make(trial, ["agreement"], s) == 3 + 1 + 3 + 4  # (a blank attorney block on 9/30)


def test_the_combined_forms(trial, s):
    """What the forms say besides the days and pages: the first day's case, the copies of every firm, the
    proceedings of the days covered, the latest delivery date."""
    trial[0].case.proc_types = {"Trial"}
    for job in trial[1:]:
        job.case.proc_types = {"Hearing"}
    trial[3].case.set("delivery_date", "11/20/2026")
    trial[1].case.set("delivery_date", "11/2/2026")
    forms = case_forms(form_groups(trial, s)[0], s, ["agreement", "mofr"])
    by = {(f.atty.name if f.atty else f.kind): (f, days) for f, days in forms}
    alex_form, alex_days = by["Alex B. Counsel"]
    assert alex_form.case.get("judge") == trial[0].case.get("judge") and alex_form.case.get("copies") == "4"
    assert alex_form.case.proc_types == {"Trial", "Hearing"} and alex_form.case.get("delivery_date") == "11/20/2026"
    robin_form, robin_days = by["Robin Example"]
    assert robin_form.case.proc_types == {"Hearing"} and [j.case.get("dates") for j in robin_days] == \
        ["9/30/2026", "10/2/2026"]
    assert by["mofr"][0].pages == 140 and len(by["mofr"][1]) == 4


def test_generate_all_counts_and_previews_what_it_makes(trial, s, make_window, monkeypatch):
    """The window: the button says 9 files, and the preview (made in a folder of its own) shows the 4 agreements
    and 4 invoices (the run sheet is not shown); Save makes the same."""
    import time

    from PySide6.QtWidgets import QApplication, QMessageBox

    from minute_filler.gui.main_window import MainWindow
    from minute_filler.gui.preview import PreviewDialog
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(MainWindow, "_run_sheet_for", lambda self, j, button="Generate": "")  # (a new one, unasked)
    s.outputs = ["agreement", "invoice", "runsheet"]
    s.use_ai = s.open_after = False
    win = make_window(s, preview=True)
    win.jobs, win.cur = list(trial), trial[0]
    win._refresh_jobs()
    win._show_job()
    for name, box in win.output_boxes.items():
        box.setChecked(name in s.outputs)
    win._refresh_job_labels()
    assert win.fill_all_btn.text() == "Generate all  (9 files)"
    s.outputs.append("mofr")
    win.output_boxes["mofr"].setChecked(True)
    win._refresh_job_labels()
    assert win.fill_all_btn.text() == "Generate all  (10 files)"
    win.output_boxes["mofr"].setChecked(False)
    shown = []
    real_init = PreviewDialog.__init__

    def spy(self, files, *a, **k):
        shown.append(list(files))
        real_init(self, files, *a, **k)
    monkeypatch.setattr(PreviewDialog, "__init__", spy)
    win.fill_all_jobs()
    end = time.time() + 60
    while time.time() < end and not (shown and not win.runner._live and all(j.saved for j in win.jobs)):
        QApplication.processEvents()
        time.sleep(0.01)
    assert shown and kinds(shown[0]) == ["Invoice"] * 4 + ["Minute Agreement"] * 4
    assert len(ledger_for(s).activity("agreement")) == 4 and len(ledger_for(s).invoices()) == 4


def test_settings_load_the_new_choice(tmp_path):
    import json
    s = Settings()
    assert s.forms_per_case is True
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text(json.dumps({"settings_version": 10}), encoding="utf-8")
    assert Settings.load().forms_per_case is True  # a file from before has it on
    s.path.write_text(json.dumps({"forms_per_case": "no"}), encoding="utf-8")
    assert Settings.load().forms_per_case is True  # (a value of the wrong kind keeps the default)
    s.path.write_text(json.dumps({"forms_per_case": False}), encoding="utf-8")
    assert Settings.load().forms_per_case is False
