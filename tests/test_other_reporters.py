"""Invoices for the other reporters of a transcript (2.0.0): Whose pages... ticks whose pages are billed - the
user's, any of the other reporters', or the whole transcript - and each reporter ticked gets invoices of their
own pages in their name: their details at the top and their payment text (Settings.reporters), numbers counted
apart from the user's ("DS-2026-0001"), file names that say whose they are, and records that keep them out of
the user's sums. All names are made up."""
import json
from datetime import date
from decimal import Decimal

import pymupdf
import pytest

from minute_filler.batch import files_to_make, fill_jobs, group, joint_invoice_sets, read_docs
from minute_filler.deliver import ledger_for
from minute_filler.models import Attorney
from minute_filler.records import Invoice, Ledger, period_stats, summarize
from minute_filler.settings import ReporterProfile, Settings

from helpers import pat_settings, transcript_pdf

PR, DS = "pr", "ds"
YEAR = date.today().year


def alex():
    return Attorney(name="Alex B. Counsel", firm="Counsel & Counsel", checked=True)


def sam():
    return Attorney(name="Sam Advocate", firm="Advocate LLP", checked=True)


@pytest.fixture
def s(tmp_path):
    s = pat_settings(initials="pr")
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    s.invoice_speeds = ["Regular"]
    s.reporters = {DS: ReporterProfile(name="Dana", full_name="Dana Smith", address1="9 Sample Lane",
                                       email="dsmith@example.com", payment="Zelle: (555) 010-0199")}
    return s


def shared_job(tmp_path, s, basis):
    """A 10-page transcript, 6 pages Pat Reporter's and 4 Dana Smith's, ordered by two firms."""
    path = transcript_pdf(tmp_path / "Roe 6-3-2026.pdf", 10, initials=[PR] * 6 + [DS] * 4)
    docs, errors = read_docs([str(path)], s)
    assert not errors
    job, = group(docs, s)
    job.case.attorneys = [alex(), sam()]
    job.page_basis = {job.docs[0].key(): basis}
    return job


def text_of(path) -> str:
    """The first page's text, its spaces and line breaks made single spaces."""
    with pymupdf.open(path) as doc:
        return " ".join(doc[0].get_text().split())


def test_my_invoices_and_dana_smiths_are_made_apart(tmp_path, s):
    job = shared_job(tmp_path, s, ["me", DS])
    assert job.bill_reporters() == ["me", DS]
    assert [(o.reporter, o.pages) for o in job.invoice_sets()] == [("", 6), (DS, 4)]
    assert files_to_make([job], ["invoice"], s) == 4  # two firms, two reporters
    fill_jobs([job], s, outputs=["invoice"])
    assert not job.error
    invoices = sorted(ledger_for(s).invoices(), key=lambda i: i.invoice_no)
    assert [(i.invoice_no, i.reporter, i.pages) for i in invoices] == [
        (f"{YEAR}-0001", "", 6), (f"{YEAR}-0002", "", 6), (f"DS-{YEAR}-0001", DS, 4), (f"DS-{YEAR}-0002", DS, 4)]
    theirs = [p for p in job.saved if "(DS)" in p.name]
    assert len(theirs) == 2 and all(f"DS-{YEAR}" in p.name for p in theirs)
    text = text_of(theirs[0])
    assert "Dana Smith" in text and "9 Sample Lane" in text and "Zelle: (555) 010-0199" in text
    assert "Pat Reporter" not in text and "123 Example Street" not in text  # nothing of the user's
    mine = text_of(next(p for p in job.saved if "(DS)" not in p.name))
    assert "Pat Reporter" in mine and "Dana Smith" not in mine.split("BILL TO")[0]


def test_only_dana_smiths_pages(tmp_path, s):
    job = shared_job(tmp_path, s, DS)
    assert job.bill_reporters() == [DS] and not job.ownership_problem()
    fill_jobs([job], s, outputs=["invoice"])
    assert {(i.reporter, i.pages) for i in ledger_for(s).invoices()} == {(DS, 4)}


def test_the_whole_transcript_is_billed_as_mine(tmp_path, s):
    job = shared_job(tmp_path, s, "*")
    assert job.bill_reporters() == ["me"]
    assert [(o.reporter, o.pages) for o in job.invoice_sets()] == [("", 10)]


def test_a_run_that_stopped_does_not_bill_an_attorney_twice_for_either_reporter(tmp_path, s):
    """Job.invoiced_keys says who was billed for which reporter ("alex b counsel@ds" for Dana Smith's pages)."""
    job = shared_job(tmp_path, s, ["me", DS])
    job.invoiced_keys = [alex().key(), f"{alex().key()}@{DS}"]  # invoiced before the run stopped
    sets = job.invoice_sets()
    from minute_filler.invoice import firm_invoices
    billed = [(o.reporter, f.atty.name) for o in sets for f in firm_invoices(job.case, s, o)]
    assert billed == [("", "Sam Advocate"), (DS, "Sam Advocate")]


def test_the_days_of_a_case_bill_each_reporter_together(tmp_path, s):
    a = transcript_pdf(tmp_path / "Roe 6-3.pdf", 6, date="June 3, 2026", initials=[PR] * 3 + [DS] * 3)
    b = transcript_pdf(tmp_path / "Roe 6-4.pdf", 4, date="June 4, 2026", initials=[PR] * 4)
    docs, _ = read_docs([str(a), str(b)], s)
    jobs = group(docs, s)
    assert len(jobs) == 2
    for j in jobs:
        j.case.attorneys = [alex()]
        for d in j.transcripts():
            j.page_basis[d.key()] = ["me", DS]
    sets = joint_invoice_sets(jobs)
    assert [(o.reporter, o.pages) for _, o in sets] == [("", 7), (DS, 3)]  # DS wrote nothing on 6/4


def test_a_typed_pages_number_is_my_own_invoices_alone(tmp_path, s):
    """With Pages typed in, Dana Smith's invoices billed the typed number too: the pages twice over."""
    job = shared_job(tmp_path, s, ["me", DS])
    job.case.set("est_pages", "8")
    assert job.pages_typed()
    assert [(o.reporter, o.pages) for o in job.invoice_sets()] == [("", 8), (DS, 4)]
    assert job.pages_typed()  # (the job itself keeps the number typed)


def test_dana_smiths_joint_invoice_names_only_her_days(tmp_path, s):
    """Her invoice for the days of a case printed every date of the user's (6/3 and 6/4), though she wrote
    pages of 6/3 only."""
    s.invoice_joint = True
    a = transcript_pdf(tmp_path / "Roe 6-3.pdf", 10, date="June 3, 2026", initials=[PR] * 6 + [DS] * 4)
    b = transcript_pdf(tmp_path / "Roe 6-4.pdf", 8, date="June 4, 2026", initials=[PR] * 8)
    docs, _ = read_docs([str(a), str(b)], s)
    jobs = group(docs, s)
    for j in jobs:
        j.case.attorneys = [alex()]
        for d in j.transcripts():
            if len(d.reporters()) > 1:
                j.page_basis[d.key()] = ["me", DS]
    made = fill_jobs(jobs, s, outputs=["invoice"])
    assert not [j.error for j in jobs if j.error]
    dates = {i.reporter: i.dates for i in ledger_for(s).invoices()}
    assert dates == {"": "6/3/2026, 6/4/2026", DS: "6/3/2026"}
    theirs, = [p for p in made if "(DS)" in p.name]
    assert "6/4/2026" not in text_of(theirs)


def test_my_pages_of_an_agreement_when_only_dana_smiths_pages_are_billed(tmp_path, s):
    """The agreement's record counted Dana Smith's 4 pages as the user's own (her set was taken for the
    user's, being first)."""
    job = shared_job(tmp_path, s, DS)
    fill_jobs([job], s, outputs=["agreement", "invoice"])
    rows = ledger_for(s).activity("agreement")
    assert rows and all(r.my_pages == 0 for r in rows)
    assert {(i.reporter, i.pages) for i in ledger_for(s).invoices()} == {(DS, 4)}


def test_their_invoices_are_left_out_of_my_sums(tmp_path):
    lg = Ledger(tmp_path / "r.db")
    today = date.today().isoformat()
    lg.add_invoice(Invoice(lg.next_invoice_no()[0], today, amounts={"Regular": "100.00"}, pages=10))
    no, year, seq = lg.next_invoice_no("DS-{year}-{seq:04}", reporter=DS)
    assert no == f"DS-{YEAR}-0001" and seq == 1
    lg.add_invoice(Invoice(no, today, amounts={"Regular": "40.00"}, pages=4, reporter=DS), year, seq)
    assert lg.next_invoice_no()[0] == f"{YEAR}-0002"  # the user's numbers go on as if theirs weren't there
    assert lg.next_invoice_no("DS-{year}-{seq:04}", reporter=DS)[0] == f"DS-{YEAR}-0002"
    every = lg.invoices()
    assert len(every) == 2 and summarize(every)[0].billed == Decimal("100.00")
    st = period_stats(every)
    assert st.invoices == 1 and st.pages == 10
    preview = lg.preview_copy(tmp_path / "preview")
    assert preview.next_invoice_no("DS-{year}-{seq:04}", reporter=DS)[0] == f"DS-{YEAR}-0002"


def test_an_old_records_database_gets_the_reporter_columns(tmp_path):
    """A database made before 2.0.0 opens: the user's numbers go on ("2026-0008"), Dana Smith's start at 1."""
    import sqlite3
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE invoices (invoice_no TEXT PRIMARY KEY, year INTEGER, seq INTEGER,
            created TEXT NOT NULL, amounts TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open');
            CREATE TABLE used_numbers (invoice_no TEXT PRIMARY KEY, year INTEGER, seq INTEGER);
            INSERT INTO invoices VALUES ('2026-0007', 2026, 7, '2026-06-03', '{}', 'open');""")
    lg = Ledger(path)
    assert lg.next_invoice_no(today=date(2026, 7, 1))[0] == "2026-0008"
    assert lg.next_invoice_no("DS-{year}-{seq:04}", today=date(2026, 7, 1), reporter=DS)[0] == "DS-2026-0001"


def test_their_invoice_numbers_dont_carry_my_mark(tmp_path):
    """The user numbering "PR-{year}-{seq:04}" made Dana Smith's "DS-PR-2026-0001"."""
    s = Settings.from_dict({"reporters": {"ds": "Dana"}})
    s.invoice_number_format = "PR-{year}-{seq:04}"
    assert s.number_format(DS) == "DS-{year}-{seq:04}"
    s.invoice_number_format = "{year}/{seq:03}"  # no text of the user's in front: the prefix is added
    assert s.number_format(DS) == "DS-{year}/{seq:03}"


def test_reporter_details_in_the_settings(tmp_path):
    """Settings.reporters takes a name alone ("ds": "Dana") or details; initials are tidied ("D.S." -> "ds").
    as_reporter gives settings in that reporter's name, without the user's payment text. The details are saved
    and loaded again."""
    s =Settings.from_dict({"reporters": {"D.S.": "Dana", "jr": {"name": "Jo", "full_name": "Jo Rivera",
                                                                  "address1": "1 Sample St", "bogus": 1}}})
    assert s.reporter_names() == {"ds": "Dana", "jr": "Jo"}
    assert not s.reporter(DS).has_details() and s.reporter("J.R.").has_details()
    assert s.number_format("jr") == "JR-{year}-{seq:04}"
    s.reporters["jr"].prefix = "RIV"
    theirs = s.as_reporter("jr")
    assert theirs.profile.name == "Jo Rivera" and theirs.invoice_number_format == "RIV-{year}-{seq:04}"
    assert not [t for t in theirs.invoice_texts if t["where"] in ("payment", "footer")]  # none of the user's
    assert s.profile.name == "" and any(t["where"] == "payment" for t in s.invoice_texts)  # unchanged
    s.save()
    again = Settings.load()
    assert again.reporters["jr"].full_name == "Jo Rivera" and again.reporters["jr"].prefix == "RIV"
    assert json.loads(s.path.read_text(encoding="utf-8"))["reporters"]["ds"]["name"] == "Dana"


def test_whose_pages_details_are_saved(qt, monkeypatch, s):
    """Invoice details entered from Whose pages... for a reporter are kept in the settings and saved."""
    from minute_filler.gui.dialogs import PagesOwnerDialog, ReporterDialog
    dlg = PagesOwnerDialog([("k", "Roe.pdf", [PR, PR, DS])], {PR}, {"k": ["me", DS]}, {}, settings=s)
    item = dlg.items[0]
    assert item["radios"]["me"].isChecked() and item["radios"][DS].isChecked()
    monkeypatch.setattr(ReporterDialog, "exec", lambda self: ReporterDialog.Accepted)
    monkeypatch.setattr(ReporterDialog, "value", lambda self: ReporterProfile(name="Dana", full_name="Dana Q. Smith"))
    dlg._details(DS)
    assert s.reporter(DS).full_name == "Dana Q. Smith" and Settings.load().reporter(DS).full_name == "Dana Q. Smith"


def test_the_settings_keep_details_of_a_reporter_without_a_line(qt, monkeypatch, s):
    """Details saved from Whose pages... for a reporter with no line under Run sheet → Reporters were dropped
    by Settings → OK; and a name changed in Invoice details... was put back by the box's line."""
    from dataclasses import replace
    from minute_filler.gui.dialogs import ReporterDialog, SettingsDialog
    s.reporters["kl"] = ReporterProfile(full_name="Kim Lee", address1="1 Sample St")
    dlg = SettingsDialog(s)
    assert [dlg.r_who.itemData(i) for i in range(dlg.r_who.count())] == [DS, "kl"]
    dlg.accept()
    assert s.reporter("kl").full_name == "Kim Lee" and s.reporter("kl").name == ""
    dlg = SettingsDialog(s)
    dlg.r_who.setCurrentIndex(dlg.r_who.findData(DS))
    monkeypatch.setattr(ReporterDialog, "exec", lambda self: ReporterDialog.Accepted)
    monkeypatch.setattr(ReporterDialog, "value", lambda self: replace(s.reporter(DS), name="Danielle"))
    dlg._edit_reporter()
    assert dlg.r_names.toPlainText() == "ds = Danielle"
    dlg.accept()
    assert s.reporter_names() == {"ds": "Danielle"} and s.reporter(DS).address1 == "9 Sample Lane"


def test_the_settings_keep_each_reporters_invoice_details(qt, monkeypatch, s):
    """Settings → Run sheet → Reporters: a reporter with details is marked ✓, details entered for a new line are
    kept with the others', and the details box shows and gives back what was saved (a prefix " K L " -> "KL")."""
    from minute_filler.gui.dialogs import ReporterDialog, SettingsDialog
    real_value = ReporterDialog.value
    dlg = SettingsDialog(s)
    assert [dlg.r_who.itemData(i) for i in range(dlg.r_who.count())] == [DS]
    assert dlg.r_who.itemText(0).endswith("✓")  # details are kept for Dana
    dlg.r_names.setPlainText("ds = Dana\nkl = Kim\n")
    dlg.r_who.setCurrentIndex(dlg.r_who.findData("kl"))
    monkeypatch.setattr(ReporterDialog, "exec", lambda self: ReporterDialog.Accepted)
    monkeypatch.setattr(ReporterDialog, "value",
                        lambda self: ReporterProfile(name="Kim", full_name="Kim Lee", phone="(555) 010-0111"))
    dlg._edit_reporter()
    dlg.accept()
    assert s.reporter_names() == {"ds": "Dana", "kl": "Kim"}
    assert s.reporter("kl").full_name == "Kim Lee" and s.reporter(DS).address1 == "9 Sample Lane"
    monkeypatch.setattr(ReporterDialog, "value", real_value)
    real = ReporterDialog("kl", s.reporter("kl"))  # what it shows, and gives back
    assert real.full_name.text() == "Kim Lee"
    real.prefix.setText(" K L ")
    assert real.value().prefix == "KL" and real.value().phone == "(555) 010-0111"
