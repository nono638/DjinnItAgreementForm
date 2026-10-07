"""What was added around the forms themselves: File -> Open recent; the settings exported and imported; the
daily backup of the records and putting one back; the look for a newer version (GitHub is never asked: its
answer is faked); the preview before saving (its "The math" tab first) and "Don't show previews anymore"; the
AI model box in Settings; printing (to a PDF, not a printer); Undo, Summary..., Mark paid..., a locked database
and the monthly and yearly recap in the Records window; a past job opened again from its record; and the
first-run welcome. All names and numbers are made up."""
import io
import json
import os
import time
from datetime import date, datetime, timedelta

import pytest

from helpers import pat_settings, transcript_pdf
from minute_filler.records import (Invoice, Ledger, backup_counts, backup_day, backups, period, period_stats,
                                   recap, stats_rows)
from minute_filler.settings import RECENT_MAX, Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def inv(no, created="2026-09-10", pages=10, amount="43.00", **kw):
    """A made-up invoice of Jane Roe v. X.Y. Holding Corporation; kw: other Invoice fields."""
    kw.setdefault("firm", "Counsel & Counsel")
    return Invoice(invoice_no=no, created=created, case_name="Jane Roe v. X.Y. Holding Corporation",
                   bill_to="Alex B. Counsel", pages=pages, amounts={"Regular": amount}, billed_speed="Regular", **kw)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "records.db", mirror_dir=tmp_path / "mirror")


def wait(win, until, seconds=20):
    """Runs the Qt event loop until no background task is left and until() is true."""
    from PySide6.QtWidgets import QApplication
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def window(tmp_path, monkeypatch, make_window, qt):
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)  # no dialogs waiting for a click
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.welcomed = True
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return make_window(s)


# ------------------------------------------------------------------ recent files
def test_recent_files_newest_first_once_and_not_too_many(tmp_path):
    s = Settings()
    for i in range(RECENT_MAX + 3):
        s.remember_file(str(tmp_path / f"doc{i}.pdf"))
    s.remember_file(str(tmp_path / "doc5.pdf"))  # opened again: moves to the top, not listed twice
    names = [os.path.basename(p) for p in s.recent_files]
    assert names[0] == "doc5.pdf" and names.count("doc5.pdf") == 1 and len(names) == RECENT_MAX
    s.save()
    assert Settings.load().recent_files == s.recent_files


def test_the_window_lists_what_was_opened_under_open_recent(window, tmp_path):
    a = tmp_path / "email.txt"
    a.write_text("Minutes in Smith v Jones, Index No. 712222/2024, 5/22/2026, Judge Lopez.")
    gone = tmp_path / "moved away.txt"
    window.s.remember_file(str(gone))
    window.add_files([str(a)])
    wait(window, lambda: window.cur.docs)
    window._fill_recent()
    acts = window.recent_menu.actions()
    assert acts[0].text().startswith("email.txt") and acts[0].isEnabled()
    assert acts[1].text().startswith("moved away.txt") and not acts[1].isEnabled()  # no longer there
    assert Settings.load().recent_files[0] == str(a)  # kept for next time
    acts[-1].trigger()  # Clear this list
    assert window.s.recent_files == []


# ------------------------------------------------------------------ settings to and from a file
def test_settings_exported_and_imported_on_another_computer(tmp_path, monkeypatch):
    s = pat_settings(phone="(555) 010-0000")
    s.invoice_index_threshold, s.fuzzy_numbers, s.window_geometry = 75, False, "here"
    s.output_dir = str(tmp_path / "pat" / "Documents" / "Minutes")  # a folder only Pat's computer has
    s.records_dir = str(tmp_path)  # this one is on both
    s.recent_files, s.signature_image = ["C:/cases/Roe.pdf"], "C:/pat/signature.png"
    from minute_filler.rates import sheets_dir
    (sheets_dir() / "City Rates.csv").write_text("Rate,Original,Copy\nRegular,$5.00,$1.00\n", encoding="utf-8")
    s.rate_sheet = "City Rates"
    out = s.export_to(tmp_path / "YinIt settings.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "window_geometry" not in data and "recent_files" not in data and "signature_image" not in data
    assert "City Rates.csv" in data["rate_sheets"]

    # the other computer: its own appdata, its own window place and recent files, no City Rates yet
    monkeypatch.setenv("APPDATA", str(tmp_path / "other appdata"))
    mine = Settings()
    mine.window_geometry, mine.recent_files = "there", ["D:/dana.pdf"]
    new = Settings.import_from(out, mine)
    assert not (sheets_dir() / "City Rates.csv").exists()  # nothing is written until the user says yes
    new.write_imported_sheets()
    assert new.profile.name == "Pat Reporter" and new.profile.phone == "(555) 010-0000"
    assert new.invoice_index_threshold == 75 and new.fuzzy_numbers is False
    assert (new.window_geometry, new.recent_files, new.signature_image) == ("there", ["D:/dana.pdf"], "")
    assert new.output_dir == "" and new.records_dir == str(tmp_path)  # a folder that isn't here: the usual one
    assert new.sheet().name == "City Rates" and new.rate_for("Regular") == "5.00"


def test_settings_exported_without_personal_details_for_a_colleague(tmp_path, monkeypatch):
    s = pat_settings(phone="(555) 010-0000", address1="123 Example Street")
    s.reporters, s.records_dir, s.invoice_index_threshold = {"ds": "Dana"}, str(tmp_path), 75
    s.invoice_texts = [{"where": "payment", "when": "always", "text": "Zelle: (555) 010-0000, Pat Reporter"},
                       {"where": "footer", "when": "always", "text": "E-mail is the best way to reach me."}]
    out = s.export_to(tmp_path / "for a colleague.json", personal=False)
    text = out.read_text(encoding="utf-8")
    for mine in ("Pat Reporter", "010-0000", "Example Street", "Dana", "Zelle", tmp_path.name):
        assert mine not in text
    assert json.loads(text)["personal"] is False and "E-mail is the best way" in text

    monkeypatch.setenv("APPDATA", str(tmp_path / "other appdata"))  # the colleague's computer
    theirs = Settings()
    theirs.profile.name, theirs.reporters, theirs.output_dir = "Dana Smith", {"pr": "Pat"}, str(tmp_path)
    theirs.invoice_texts = [{"where": "payment", "when": "always", "text": "Check payable to Dana Smith"}]
    new = Settings.import_from(out, theirs)
    assert new.imported_personal is False and new.invoice_index_threshold == 75  # the options are taken
    assert (new.profile.name, new.reporters, new.output_dir) == ("Dana Smith", {"pr": "Pat"}, str(tmp_path))
    assert [r["text"] for r in new.invoice_texts] == ["E-mail is the best way to reach me.",
                                                      "Check payable to Dana Smith"]
    # a file with them (and one from 1.7.0, which had no mark) brings the reporter along
    with_mine = Settings.import_from(s.export_to(tmp_path / "mine.json"), theirs)
    assert with_mine.imported_personal is True and with_mine.profile.name == "Pat Reporter"
    old = json.loads((tmp_path / "mine.json").read_text(encoding="utf-8"))
    del old["personal"]
    (tmp_path / "old.json").write_text(json.dumps(old), encoding="utf-8")
    assert Settings.import_from(tmp_path / "old.json", theirs).profile.name == "Pat Reporter"


def test_export_settings_asks_about_personal_details(window, tmp_path, monkeypatch, qt):
    asked, saved_as = [], []

    def click(label):
        def run(box):
            asked.append(box.text())
            btn = next(b for b in box.buttons() if b.text() == label)
            monkeypatch.setattr(qt.QMessageBox, "clickedButton", lambda self: btn)
            return 0
        return run

    def save_name(parent, title, start, filt):
        saved_as.append(os.path.basename(start))
        return str(tmp_path / os.path.basename(start)), ""
    monkeypatch.setattr(qt.QFileDialog, "getSaveFileName", save_name)
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(qt.QMessageBox, "exec", click("Cancel"))
    window.export_settings()
    assert "Include your personal details" in asked[0] and saved_as == []  # cancelled: nothing saved
    monkeypatch.setattr(qt.QMessageBox, "exec", click("Leave them out"))
    window.export_settings()
    assert saved_as == ["YinIt settings (no personal details).json"]
    assert "Pat Reporter" not in (tmp_path / saved_as[0]).read_text(encoding="utf-8")
    monkeypatch.setattr(qt.QMessageBox, "exec", click("Include my details"))
    window.export_settings()
    assert saved_as[-1] == "YinIt settings.json"
    assert "Pat Reporter" in (tmp_path / "YinIt settings.json").read_text(encoding="utf-8")


def test_an_imported_rate_sheet_never_replaces_one_with_other_prices(tmp_path):
    from minute_filler.rates import sheets_dir
    sheet = sheets_dir() / "City Rates.csv"
    sheet.write_text("Rate,Original,Copy\nRegular,$5.00,$1.00\n", encoding="utf-8")
    s = Settings()
    s.rate_sheet = "City Rates"
    out = s.export_to(tmp_path / "settings.json")
    sheet.write_text("Rate,Original,Copy\nRegular,$6.25,$1.00\n", encoding="utf-8")  # this computer's own prices
    new = Settings.import_from(out, Settings())
    new.write_imported_sheets()
    assert "6.25" in sheet.read_text(encoding="utf-8")  # kept
    assert new.rate_sheet == "City Rates (imported)" and new.rate_for("Regular") == "5.00"
    again = Settings.import_from(out, Settings())  # the same file twice: no "(imported) (imported)"
    again.write_imported_sheets()
    assert sorted(p.name for p in sheets_dir().glob("City*")) == ["City Rates (imported).csv", "City Rates.csv"]
    assert again.rate_sheet == "City Rates (imported)"
    # an imported sheet the user has since changed is not written over either
    (sheets_dir() / "City Rates (imported).csv").write_text("Rate,Original,Copy\nRegular,$7.00,$1.00\n", encoding="utf-8")
    third = Settings.import_from(out, Settings())
    third.write_imported_sheets()
    assert third.rate_sheet == "City Rates (imported 2)" and third.rate_for("Regular") == "5.00"
    assert "7.00" in (sheets_dir() / "City Rates (imported).csv").read_text(encoding="utf-8")


def test_a_settings_file_with_an_odd_version_is_still_read(tmp_path):
    """"settings_version": "9" (or null) raised TypeError: the app would not start, and Import settings crashed."""
    s = pat_settings()
    s.path.write_text(json.dumps({"settings_version": "9", "profile": {"name": "Pat Reporter"}}), encoding="utf-8")
    assert Settings.load().profile.name == "Pat Reporter"
    s.path.write_text(json.dumps({"settings_version": None, "default_county": "Kings"}), encoding="utf-8")
    assert Settings.load().default_county == "Kings"


def test_a_file_that_is_not_settings_is_refused(tmp_path):
    other = tmp_path / "package.json"
    other.write_text('{"name": "something else"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a settings file"):
        Settings.import_from(other, Settings())
    other.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError, match="can't be read"):
        Settings.import_from(other, Settings())


def test_importing_settings_in_the_window(window, tmp_path, monkeypatch, qt):
    theirs = pat_settings()
    theirs.profile.name, theirs.default_county, theirs.fuzzy_numbers = "Dana Smith", "Kings", False
    src = theirs.export_to(tmp_path / "dana.json")
    window.s.save()
    monkeypatch.setattr(qt.QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))
    from minute_filler.rates import sheets_dir
    (sheets_dir() / "Dana Rates.csv").write_text("Rate,Original,Copy\nRegular,$5.00,$1.00\n", encoding="utf-8")
    src = theirs.export_to(tmp_path / "dana.json")
    (sheets_dir() / "Dana Rates.csv").unlink()
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.No)
    window.import_settings()
    assert window.s.profile.name == "Pat Reporter"  # not replaced without a yes
    assert not (sheets_dir() / "Dana Rates.csv").exists()  # and no rate sheet of theirs left behind
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.Yes)
    settings = window.s
    window.import_settings()
    assert window.s is settings and settings.profile.name == "Dana Smith" and settings.default_county == "Kings"
    assert Settings.load().profile.name == "Dana Smith" and (sheets_dir() / "Dana Rates.csv").exists()
    before = json.loads(settings.path.with_name("settings before import.json").read_text(encoding="utf-8"))
    assert before["profile"]["name"] == "Pat Reporter"  # the settings as they were are kept


# ------------------------------------------------------------------ backups
def test_the_records_are_backed_up_once_a_day_and_old_copies_go(ledger, tmp_path):
    folder = tmp_path / "Backups"
    assert ledger.backup(folder) is None and not backups(folder)  # nothing to copy yet
    ledger.add_invoice(inv("2026-0001"))
    day = datetime(2026, 10, 1, 9, 0)
    first = ledger.backup(folder, now=day)
    assert first.name == "records 2026-10-01.db" and backup_counts(first) == (1, 0)
    assert ledger.backup(folder, now=day + timedelta(hours=5)) is None  # today's is there
    for n in range(1, 13):
        ledger.backup(folder, now=day + timedelta(days=n))
    names = [p.name for p in backups(folder)]
    assert len(names) == 10 and names[0] == "records 2026-10-13.db" and "records 2026-10-01.db" not in names
    forced = ledger.backup(folder, now=day + timedelta(days=12, hours=3), force=True)
    assert forced.name == "records 2026-10-13 120000.db" and backup_day(forced) == "October 13, 2026, 12:00 PM"
    assert backup_day(first) == "October 1, 2026" and backup_counts(tmp_path / "mirror") is None
    # the day's own copy was made first: the one made later that day is the newest
    assert [p.name for p in backups(folder)][:2] == ["records 2026-10-13 120000.db", "records 2026-10-13.db"]
    # copies made by hand don't push out the older days': the last 5 of them are kept, and the 10 days
    for n in range(8):
        ledger.backup(folder, now=day + timedelta(days=12, hours=4, minutes=n), force=True)
    names = [p.name for p in backups(folder)]
    assert len([n for n in names if len(n) > len("records 2026-10-13.db")]) == 5
    assert len([n for n in names if len(n) == len("records 2026-10-13.db")]) == 10 and "records 2026-10-04.db" in names


def test_putting_back_the_oldest_copy_does_not_delete_it_first(ledger, tmp_path):
    """With ten copies there, the copy made before a restore pushed out the oldest: the very one chosen. It was
    then made anew, empty, and copied over the records."""
    folder = tmp_path / "Backups"
    ledger.add_invoice(inv("2026-0001"))
    for n in range(10):
        ledger.backup(folder, now=datetime(2026, 9, 1 + n))
    oldest = backups(folder)[-1]
    ledger.add_invoice(inv("2026-0002"))
    ledger.restore_backup(oldest, folder)
    assert [i.invoice_no for i in ledger.invoices()] == ["2026-0001"] and backup_counts(oldest) == (1, 0)
    gone = folder / "records 2020-01-01.db"
    with pytest.raises(ValueError):
        ledger.restore_backup(gone, folder)
    assert not gone.exists() and len(ledger.invoices()) == 1  # a copy that is gone is not made anew


def test_backups_in_a_folder_with_a_hash_or_percent_in_its_name(ledger, tmp_path):
    for name in ("Records #1", "Smith %26 Roe"):
        folder = tmp_path / name
        copy = ledger.backup(folder, force=True) if not ledger.is_empty() else None
        if copy is None:
            ledger.add_invoice(inv("2026-0001"))
            copy = ledger.backup(folder, force=True)
        assert backup_counts(copy) == (1, 0)
        ledger.restore_backup(copy, folder)
        assert len(ledger.invoices()) == 1


def test_a_half_written_copy_left_behind_does_not_stop_the_backup(ledger, tmp_path):
    folder = tmp_path / "Backups"
    folder.mkdir()
    (folder / "records 2026-10-01.tmp").write_text("cut short when the app was closed")
    ledger.add_invoice(inv("2026-0001"))
    assert backup_counts(ledger.backup(folder, now=datetime(2026, 10, 1))) == (1, 0)


def test_an_invoice_row_with_damaged_amounts_does_not_break_the_records(ledger):
    ledger.add_invoice(inv("2026-0001"))
    ledger._run("UPDATE invoices SET amounts='[1, 2]'")
    one, = ledger.invoices()
    assert one.amounts == {} and str(one.billed) == "0.00"


def test_a_backup_put_back_and_the_numbers_given_since_stay_taken(ledger, tmp_path):
    folder = tmp_path / "Backups"
    ledger.add_invoice(inv("2026-0001"))
    copy = ledger.backup(folder, now=datetime(2026, 10, 1))
    ledger.mark_paid("2026-0001", "Regular", "43.00", "2026-10-02")
    ledger.add_invoice(inv("2026-0002"))
    ledger.delete(invoices=["2026-0001"])
    ledger.restore_backup(copy, folder)
    assert [(i.invoice_no, i.status) for i in ledger.invoices()] == [("2026-0001", "open")]
    assert ledger.next_invoice_no(today=date(2026, 10, 3))[0] == "2026-0003"  # 0002 was given to an attorney
    # the records as they were just before are a copy too: putting one back can be taken back
    latest = backups(folder)[0]
    assert latest != copy and backup_counts(latest) == (1, 0)
    ledger.restore_backup(latest)
    assert [i.invoice_no for i in ledger.invoices()] == ["2026-0002"]
    with pytest.raises(ValueError, match="not a copy of the records"):
        ledger.restore_backup(tmp_path / "mirror" / "invoices.csv")


def test_a_backup_from_an_older_version_is_brought_up_to_date_when_put_back(ledger, tmp_path):
    import sqlite3
    from test_records_trash import OLD_SCHEMA
    old = tmp_path / "records 2026-03-01.db"
    with sqlite3.connect(old) as db:
        db.executescript(OLD_SCHEMA)
    ledger.add_invoice(inv("2026-0009"))
    ledger.restore_backup(old)
    assert [i.invoice_no for i in ledger.invoices()] == ["2026-0007"]
    ledger.log_activity("agreement", case_name="Roe v. Poe", origin='{"sources": []}')  # the newer columns are there
    assert ledger.next_invoice_no(today=date(2026, 5, 1))[0] == "2026-0008"


def test_the_window_backs_the_records_up_when_it_opens(window):
    from minute_filler.deliver import backup_folder, backup_records, ledger_for
    assert backup_records(backup_folder(window.s)) is None  # no records yet: no empty database is made either
    from minute_filler.records import default_db
    assert not default_db().exists()
    ledger_for(window.s).add_invoice(inv("2026-0001"))
    window._startup()
    wait(window, lambda: backups(backup_folder(window.s)))
    assert backup_counts(backups(backup_folder(window.s))[0]) == (1, 0)


def test_backups_in_the_records_window(window, monkeypatch, qt):
    from minute_filler.deliver import backup_folder, ledger_for
    from minute_filler.gui.records_window import BackupsDialog
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001"))
    dlg = BackupsDialog(window.s, lg, window)
    assert dlg.list.count() == 0 and not dlg.restore_btn.isEnabled()
    dlg.backup_now()
    assert dlg.list.count() == 1 and "1 invoice, 0 files made" in dlg.list.item(0).text()
    lg.delete(invoices=["2026-0001"])
    # the button asks first (its "checked" used to be taken for "don't ask")
    asked = []
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: asked.append(1) or qt.QMessageBox.No)
    dlg.restore_btn.click()
    assert asked == [1] and not dlg.restored and lg.invoices() == []
    # not while Generate all is making files: its invoices would leave the records
    told = []
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    dlg.busy = lambda: True
    dlg.restore(ask=False)
    assert "Generate all" in told[0] and not dlg.restored
    dlg.busy = None
    dlg.restore(ask=False)
    assert dlg.restored and [i.invoice_no for i in lg.invoices()] == ["2026-0001"]
    assert len(backups(backup_folder(window.s))) == 2  # the one put back, and the records as they were


# ------------------------------------------------------------------ a newer version
class _Answer(io.BytesIO):
    """A faked urlopen() answer: its body, usable in a `with` block."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_newer_version_on_github_is_found(monkeypatch):
    import importlib
    from minute_filler import update
    update = importlib.reload(update)  # (the tests' own "no newer version" is set on the module: the real one)
    asked = []

    def github(req, timeout):
        asked.append((req.full_url, dict(req.header_items())))
        return _Answer(json.dumps({"tag_name": "v1.10.0", "html_url":
                                   "https://github.com/nono638/YinItAgreementForm/releases/tag/v1.10.0"}).encode())
    monkeypatch.setattr(update.urllib.request, "urlopen", github)
    assert update.newer("1.9.3") == ("1.10.0", "https://github.com/nono638/YinItAgreementForm/releases/tag/v1.10.0")
    assert update.newer("1.10.0") is None and update.newer("2.0.0") is None
    url, headers = asked[0]
    assert url == update.LATEST_API and set(headers) == {"User-agent", "Accept"}  # nothing about the user is sent
    # a link to anywhere else is not passed on: the project's own releases page instead
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: _Answer(
        json.dumps({"tag_name": "v9.0.0", "html_url": "https://example.com/get"}).encode()))
    assert update.newer("1.0.0") == ("9.0.0", update.RELEASES_PAGE + "/latest")
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: _Answer(b'{"message": "Not Found"}'))
    with pytest.raises(ValueError):
        update.latest()


def test_the_window_looks_once_a_day_and_shows_the_link(window, monkeypatch):
    from minute_filler import update
    looked = []
    monkeypatch.setattr(update, "newer", lambda *a, **k: looked.append(1) or ("9.9.0", update.RELEASES_PAGE))
    # (the window runs _startup by itself a moment after it is made)
    wait(window, lambda: getattr(window, "update_label", None) is not None)
    assert looked == [1] and "9.9.0" in window.update_label.text() and update.RELEASES_PAGE in window.update_label.text()
    assert window.s.update_checked == date.today().isoformat()
    window._startup()  # the same day: not asked again
    wait(window, lambda: True)
    assert looked == [1]
    window.s.update_checked, window.s.check_updates = "", False  # turned off in Settings: never asked
    window._startup()
    wait(window, lambda: True)
    assert looked == [1]


def test_github_out_of_reach_says_nothing_unless_asked(window, monkeypatch, qt):
    from minute_filler import update

    def offline(*a, **k):
        raise OSError("no connection")
    monkeypatch.setattr(update, "newer", offline)
    said = []
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: said.append(a[1]))
    window._check_update()
    wait(window, lambda: True)
    assert not said and window.s.update_checked == ""  # tried again next time
    window._check_update(asked=True)
    wait(window, lambda: said)
    assert said == ["Could not check"]


# ------------------------------------------------------------------ the preview before saving
@pytest.fixture
def previewing(tmp_path, monkeypatch, make_window, qt):
    """A window with the preview on and a 12-page transcript of Jane Roe v. X.Y. Holding Corporation read."""
    monkeypatch.setattr(qt.QMessageBox, "exec", lambda self: 0)
    s = pat_settings(initials="pr")
    s.use_ai = s.open_after = False
    s.welcomed = True
    s.output_dir, s.records_dir = str(tmp_path / "out"), str(tmp_path / "records")
    s.outputs = ["agreement", "invoice"]
    win = make_window(s, preview=True)
    win.add_files([str(transcript_pdf(tmp_path / "Transcript.pdf", 12))])
    wait(win, lambda: win.cur.docs)
    for a in win.cur.case.attorneys:
        a.checked = a is win.cur.case.attorneys[0]
    win.cur.att_touched = True
    win._show_case()
    return win


def test_the_preview_shows_the_files_and_going_back_saves_nothing(previewing, tmp_path, monkeypatch):
    """The preview has "The math" first, then a tab per file. Going back saves, records and numbers nothing."""
    from minute_filler.deliver import ledger_for
    from minute_filler.gui.preview import PreviewDialog
    seen = []

    def go_back(dlg):
        seen.append(([dlg.tabs.tabText(i) for i in range(dlg.tabs.count())], dlg.pages))
        return PreviewDialog.Rejected
    monkeypatch.setattr(PreviewDialog, "exec", go_back)
    previewing.fill()
    (tabs, pages), = seen
    assert len(tabs) == 3 and tabs[0] == "The math" and any(t.startswith("Invoice 20") for t in tabs) and pages >= 2
    assert not (tmp_path / "out").exists() and not previewing.cur.saved
    lg = ledger_for(previewing.s)
    assert lg.invoices() == [] and lg.activity() == []  # nothing recorded
    # ...and no number taken: the invoice saved next is the first
    monkeypatch.setattr(PreviewDialog, "exec", lambda dlg: PreviewDialog.Accepted)
    previewing.fill()
    year = date.today().year
    assert [i.invoice_no for i in lg.invoices()] == [f"{year}-0001"]
    assert sorted(p.name.split(" - ")[0] for p in previewing.cur.saved) == [f"Invoice {year}-0001", "Minute Agreement"]


def test_what_is_saved_is_what_the_preview_showed(previewing, monkeypatch):
    from minute_filler.gui.preview import PreviewDialog

    def edited_meanwhile(dlg):  # (an AI answer or a read that ends while the preview is up changes the job)
        previewing.cur.case.set("judge", "Somebody Else")
        return PreviewDialog.Accepted
    monkeypatch.setattr(PreviewDialog, "exec", edited_meanwhile)
    previewing.fill()
    import pymupdf
    form = next(p for p in previewing.cur.saved if p.name.startswith("Minute Agreement"))
    with pymupdf.open(form) as doc:
        values = " ".join(str(w.field_value) for w in doc[0].widgets())
    assert "Alvarez" in values and "Somebody Else" not in values


def test_a_preview_that_cannot_be_made_asks_before_saving(previewing, monkeypatch, qt):
    from minute_filler.gui import main_window as mw
    real, calls = mw.generate, []

    def first_fails(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("no preview today")
        return real(*a, **k)
    monkeypatch.setattr(mw, "generate", first_fails)
    asked = []
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or qt.QMessageBox.No)
    previewing.fill()
    assert len(asked) == 1 and "Save the files without it?" in asked[0] and not previewing.cur.saved


def test_a_file_name_with_an_ampersand_shows_whole_on_its_tab(qt, tmp_path):
    from minute_filler.gui.preview import PreviewDialog
    pdf = transcript_pdf(tmp_path / "Invoice - Roe - Counsel & Counsel.pdf", 1)
    dlg = PreviewDialog([pdf], pat_settings())
    assert dlg.tabs.tabText(0) == "Invoice - Roe - Counsel && Counsel" and dlg.pages == 1


def test_ctrl_wheel_over_the_math_zooms_the_preview(qt, tmp_path):
    """Ctrl and the wheel over The math zoomed only its text box (it isn't a QScrollArea), undone at the next
    zoom of the preview. Now it zooms the whole preview."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from helpers import ROE, make_case
    from minute_filler.invoice import InvoiceOpts, firm_invoices
    from minute_filler.models import Attorney
    from minute_filler.gui.preview import PreviewDialog
    s = pat_settings()
    case = make_case(ROE, [Attorney(name="Alex B. Counsel", checked=True)])
    math = [(f, "2026-0001") for f in firm_invoices(case, s, InvoiceOpts(10, 1, days=[("6/3/2026", 10)]))]
    dlg = PreviewDialog([transcript_pdf(tmp_path / "Invoice.pdf", 1)], s, math=math)
    view = dlg.math.text.viewport()
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                        Qt.ControlModifier, Qt.NoScrollPhase, False)
    qt.QApplication.sendEvent(view, wheel)
    assert dlg.zoom > 1.0


def test_a_document_added_while_the_preview_is_open_is_still_billed_later(previewing, monkeypatch, tmp_path):
    from minute_filler.gui.preview import PreviewDialog

    def added_meanwhile(dlg):
        previewing.cur.docs = previewing.cur.docs + [previewing.cur.docs[0]]
        return PreviewDialog.Accepted
    monkeypatch.setattr(PreviewDialog, "exec", added_meanwhile)
    previewing.fill()
    assert previewing.cur.saved and previewing.cur.invoiced is False


def test_dont_show_previews_anymore(previewing, monkeypatch):
    from minute_filler.gui import preview
    shown = []

    def turn_off_and_save(dlg):
        shown.append(1)
        dlg.off.click()
        assert dlg.off.isHidden() and dlg.off_note.text() == preview.OFF_NOTE
        return preview.PreviewDialog.Accepted
    monkeypatch.setattr(preview.PreviewDialog, "exec", turn_off_and_save)
    previewing.fill()
    assert previewing.s.preview_before_saving is False and Settings.load().preview_before_saving is False
    previewing.fill()  # no preview this time
    assert shown == [1] and len(previewing.cur.saved) == 2


def test_the_run_sheet_is_not_made_for_the_preview(previewing, monkeypatch, tmp_path):
    from minute_filler.gui.preview import PreviewDialog
    notes = []
    monkeypatch.setattr(PreviewDialog, "exec", lambda dlg: notes.append(dlg.tabs.count()) or PreviewDialog.Rejected)
    # one reporter wrote it, so the box was left unticked for it: the user ticks it
    assert not previewing.output_boxes["runsheet"].isChecked()
    previewing.output_boxes["runsheet"].setChecked(True)
    previewing.s.outputs, previewing.s.runsheet_existing = ["runsheet"], "add"
    previewing.s.output_dirs = {"runsheet": str(tmp_path / "sheets")}
    monkeypatch.setattr(previewing, "_saved_box", lambda *a, **k: None)
    previewing.fill()  # only a run sheet: nothing to show, so it is made at once
    assert notes == [] and [p.suffix for p in previewing.cur.saved] == [".xlsx"]
    previewing.s.outputs = ["agreement", "runsheet"]
    previewing.fill()
    assert notes == [1] and len(list((tmp_path / "sheets").glob("*.xlsx"))) == 1  # going back added nothing to it


def test_preview_settings_checkbox(qt):
    from minute_filler.gui.dialogs import SettingsDialog
    s = pat_settings()
    dlg = SettingsDialog(s)
    assert dlg.o_preview.isChecked() and dlg.o_recaps.isChecked() and dlg.o_updates.isChecked()
    for box in (dlg.o_preview, dlg.o_recaps, dlg.o_updates):
        box.setChecked(False)
    dlg.accept()
    again = Settings.load()
    assert (again.preview_before_saving, again.recaps, again.check_updates) == (False, False, False)


def test_the_model_box_offers_both_gemma_models(qt, monkeypatch):
    """Settings -> AI: gemma4:e2b and gemma4:e4b are offered though neither is installed, with the warning that
    the larger one is much slower without a graphics card; other installed models are listed after them."""
    from minute_filler.extract_llm import OllamaExtractor
    from minute_filler.gui.dialogs import SettingsDialog
    from PySide6.QtCore import Qt
    dlg = SettingsDialog(pat_settings())  # (Ollama is "not running" in the tests)
    names = [dlg.a_model.itemText(i) for i in range(dlg.a_model.count())]
    assert names == ["gemma4:e2b", "gemma4:e4b"] and dlg.a_model.currentText() == "gemma4:e2b"
    assert "much slower on a laptop without a graphics card" in dlg.a_model.itemData(1, Qt.ToolTipRole)
    assert any("much slower on a laptop without a graphics card" in w.text() for w in dlg.findChildren(qt.QLabel))
    dlg.a_model.setCurrentIndex(1)
    dlg.accept()
    assert Settings.load().ollama_model == "gemma4:e4b"
    # with Ollama running: its other models follow, and the one chosen stays chosen
    monkeypatch.setattr(OllamaExtractor, "installed_models", lambda self, timeout=3: ["llava:7b", "gemma4:e4b"])
    again = SettingsDialog(Settings.load())
    names = [again.a_model.itemText(i) for i in range(again.a_model.count())]
    assert names == ["gemma4:e2b", "gemma4:e4b", "llava:7b"] and again.a_model.currentText() == "gemma4:e4b"


# ------------------------------------------------------------------ printing
def test_pdfs_are_printed_page_by_page(qt, tmp_path):
    import pymupdf
    from PySide6.QtPrintSupport import QPrinter
    from minute_filler.gui.preview import print_files
    a, b = transcript_pdf(tmp_path / "a.pdf", 2), transcript_pdf(tmp_path / "b.pdf", 3)
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    assert print_files(None, [a, b, tmp_path / "gone.pdf"], printer) == 2
    with pymupdf.open(tmp_path / "printed.pdf") as doc:
        assert doc.page_count == 5 and all(page.get_images() for page in doc)
    # at a printer's resolution the pages are not blown up in memory first (it took seconds a page)
    fine = QPrinter(QPrinter.HighResolution)
    fine.setOutputFormat(QPrinter.PdfFormat)
    fine.setOutputFileName(str(tmp_path / "fine.pdf"))
    start = time.time()
    assert print_files(None, [b], fine) == 1 and time.time() - start < 8
    assert (tmp_path / "fine.pdf").stat().st_size < 5_000_000
    assert qt.QApplication.overrideCursor() is None


def test_a_page_is_printed_at_its_actual_size_when_the_paper_is_big_enough(qt, tmp_path):
    """A letter form on letter paper is not shrunk to the printable area (it was, by 3 % or so); a page bigger
    than the paper is made to fit; a page on its side turns the paper."""
    import pymupdf
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPrintSupport import QPrinter
    from minute_filler.gui.preview import print_files, print_rect
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "out.pdf"))
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.Letter), QPageLayout.Portrait, QMarginsF(18, 18, 18, 18)))
    printer.setFullPage(True)
    dots = printer.resolution() / 72
    r = print_rect(printer, 612, 792)  # a letter page: every dot of the paper, the margins not taken off
    assert (r.x(), r.y()) == (0, 0) and abs(r.width() - 612 * dots) <= 1 and abs(r.height() - 792 * dots) <= 1
    small = print_rect(printer, 306, 396)  # half the size: as it is, in the middle
    assert abs(small.width() - 306 * dots) <= 1 and abs(small.x() - 153 * dots) <= 1
    big = print_rect(printer, 842, 1191)  # A3: made to fit inside the margins
    assert big.height() <= (792 - 36) * dots + 1 and big.y() >= 18 * dots - 1
    assert abs(big.width() / big.height() - 842 / 1191) < 0.01

    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 72), "upright")
    doc.new_page(width=792, height=612).insert_text((72, 72), "on its side")
    doc.save(tmp_path / "two.pdf")
    doc.close()
    assert print_files(None, [tmp_path / "two.pdf"], printer) == 1
    with pymupdf.open(tmp_path / "out.pdf") as out:
        sizes = [(round(p.rect.width), round(p.rect.height)) for p in out]
    assert sizes == [(612, 792), (792, 612)]


def test_the_saved_box_offers_print(window, monkeypatch, tmp_path, qt):
    from minute_filler.gui import preview
    pdf = transcript_pdf(tmp_path / "a.pdf", 1)
    printed = []
    monkeypatch.setattr(preview, "print_files", lambda parent, files: printed.append(files))
    labels = []

    def click_print(box):
        labels.extend(b.text() for b in box.buttons())
        return next(b for b in box.buttons() if b.text() == "Print…").click()
    monkeypatch.setattr(qt.QMessageBox, "exec", click_print)
    monkeypatch.setattr(qt.QMessageBox, "clickedButton",
                        lambda box: next(b for b in box.buttons() if b.text() == "Print…"))
    window._saved_box("Saved", "Saved 1 file", [tmp_path], files=[pdf])
    assert "Print…" in labels and printed == [[pdf]]


# ------------------------------------------------------------------ sums, the recap
def test_a_period_summed_up():
    invoices = [inv("2026-0001", "2026-09-03", pages=100, amount="430.00"),
                inv("2026-0002", "2026-09-20", pages=143, amount="440.00", status="paid", paid_speed="Regular",
                    amount_paid="440.00", paid_date="2026-10-02", firm="Smith LLP"),
                inv("2026-0003", "2026-09-21", pages=50, amount="215.00", status="void"),
                inv("2026-0004", "2026-08-30", pages=20, amount="86.00", status="paid", paid_speed="Regular",
                    amount_paid="86.00", paid_date="2026-09-05")]
    today = date(2026, 10, 3)
    assert period("Last month", today) == ("2026-09-01", "2026-09-30", "September 2026")
    assert period("This month", date(2026, 12, 31)) == ("2026-12-01", "2026-12-31", "December 2026")
    assert period("Last month", date(2026, 1, 15))[:2] == ("2025-12-01", "2025-12-31")
    assert period("Last year", today) == ("2025-01-01", "2025-12-31", "2025") and period("All time") == ("", "", "All time")
    st = period_stats(invoices, *period("Last month", today)[:2])
    assert (st.invoices, st.pages, str(st.billed), str(st.paid), str(st.outstanding)) == (
        2, 243, "870.00", "440.00", "430.00")  # the void one counts for nothing
    assert str(st.received) == "86.00"  # paid in September, for an invoice of August
    assert (st.unpaid, st.oldest_unpaid) == (1, "2026-09-03") and [n for n, _ in st.firms] == ["Smith LLP", "Counsel & Counsel"]
    rows = dict(stats_rows(st))
    assert rows["Billed"] == "$870.00" and rows["Pages billed"] == "243" and rows["Billed per page"] == "$3.58"
    assert rows["Unpaid invoices"] == "1 (the oldest from 9/3/2026)"
    assert dict(stats_rows(period_stats([])))["Billed per invoice"] == "$0.00"


def test_the_recap_once_a_month_and_once_a_year():
    invoices = [inv("2026-0001", "2026-09-03", pages=100, amount="430.00"),
                inv("2026-0002", "2026-09-20", pages=143, amount="440.00", status="paid", paid_speed="Regular",
                    amount_paid="440.00", paid_date="2026-10-02"),
                inv("2025-0001", "2025-03-01", pages=1, amount="4.30", status="paid", paid_speed="Regular",
                    amount_paid="4.30", paid_date="2025-03-09")]
    never = lambda: 1.0  # (never "But who's counting?", see test_show_the_math)
    lines, month, year = recap(invoices, date(2026, 10, 3), rng=never)
    assert lines == ["Last month (September 2026) you made $870.00 with 243 pages (2 invoices). "
                     "$440.00 of it is paid so far.",
                     "Last year (2025) you made $4.30 with 1 page (1 invoice)."]
    assert (month, year) == ("2026-10", "2026")
    assert recap(invoices, date(2026, 10, 20), month, year)[0] == []  # the same month: said once
    assert recap(invoices, date(2026, 11, 2), month, year)[0] == []  # a month without invoices says nothing
    lines, month, year = recap(invoices, date(2027, 1, 4), "2026-12", "2026", rng=never)
    assert lines == ["Last year (2026) you made $870.00 with 243 pages (2 invoices). $440.00 of it is paid so far."]


def test_the_recap_pops_up_when_records_opens_and_can_be_turned_off(window, monkeypatch, qt):
    from minute_filler import records
    from minute_filler.deliver import ledger_for
    monkeypatch.setattr(records, "april_fools", lambda today=None: None)  # (one box, even on April 1)
    last = (date.today().replace(day=1) - timedelta(days=1)).replace(day=10)
    ledger_for(window.s).add_invoice(inv("X-0001", last.isoformat(), pages=243, amount="870.00"))
    shown = []

    def box(self):
        shown.append((self.text(), self.checkBox().text()))
        self.checkBox().setChecked(len(shown) == 2)
        return 0
    monkeypatch.setattr(qt.QMessageBox, "exec", box)
    window.open_records()
    assert len(shown) == 1 and "you made $870.00 with 243 pages (1 invoice)" in shown[0][0]
    assert shown[0][1] == "Don't show me monthly or annual recaps anymore"
    window.open_records()
    assert len(shown) == 1 and window.s.recap_month == f"{date.today():%Y-%m}"  # once a month
    window.s.recap_month = ""  # (as when the next month begins)
    window.open_records()  # this time the box is ticked: no more recaps
    assert len(shown) == 2 and window.s.recaps is False and Settings.load().recaps is False
    window.s.recap_month = ""
    window.open_records()
    assert len(shown) == 2


def test_summary_in_the_records_window(window, qt):
    from minute_filler.deliver import ledger_for
    from minute_filler.gui.records_window import SummaryDialog
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001", "2026-09-03", pages=100, amount="430.00"))
    dlg = SummaryDialog(lg.invoices(), None, today=date(2026, 10, 3))
    assert dlg.period.currentText() == "This year" and dlg.plain.startswith("2026\nInvoices made: 1")
    assert "Counsel & Counsel: $430.00 (1 invoice)" in dlg.plain
    dlg.period.setCurrentText("This month")
    assert dlg.plain.startswith("October 2026\nInvoices made: 0") and "No invoices" in dlg.text.toPlainText()
    dlg._copy()
    assert qt.QApplication.clipboard().text() == dlg.plain


def test_a_payment_must_be_an_amount(qt):
    """Mark paid… recorded "sixty three" as $0.00 paid, and "-5" as a payment."""
    from minute_filler.gui.records_window import PaidDialog
    dlg = PaidDialog(inv("2026-0001"))
    for bad in ("sixty three", "-5", ""):
        dlg.amount.setText(bad)
        dlg.setResult(0)
        dlg.accept()
        assert dlg.result() != qt.QDialog.Accepted and dlg.error.text(), bad
    dlg.amount.setText("$63")
    dlg.accept()
    assert dlg.result() == qt.QDialog.Accepted and dlg.values()[1] == "63.00"


def test_void_and_notes_say_so_when_the_records_cant_be_changed(window, monkeypatch, qt):
    """A locked database raised out of the menu's slot: nothing said, the change lost."""
    import sqlite3
    from minute_filler.deliver import ledger_for
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001", date.today().isoformat()))
    window.s.recaps = False
    window.open_records()
    win = window._records_win
    said = []
    monkeypatch.setattr(qt.QMessageBox, "warning", lambda *a, **k: said.append(a[1]))
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.Yes)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    for name in ("void", "mark_unpaid", "set_notes"):
        monkeypatch.setattr(win.ledger, name, locked)
    monkeypatch.setattr(qt.QInputDialog, "getText", lambda *a, **k: ("call Monday", True))
    win._set_void(lg.invoice("2026-0001"))
    win._unvoid(lg.invoice("2026-0001"))
    win._notes(lg.invoice("2026-0001"))
    assert said == ["Could not save"] * 3 and lg.invoice("2026-0001").status == "open"


# ------------------------------------------------------------------ undo
def test_undo_in_the_records_window(window, monkeypatch, qt):
    from minute_filler.deliver import ledger_for
    from minute_filler.gui import records_window as rw
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001", date.today().isoformat(), notes="first"))
    window.s.recaps = False
    window.open_records()
    win = window._records_win
    assert not win.undo_btn.isEnabled()
    monkeypatch.setattr(rw.PaidDialog, "exec", lambda self: qt.QDialog.Accepted)
    monkeypatch.setattr(rw.PaidDialog, "values", lambda self: ("Regular", "40.00", "2026-10-02"))
    win._set_paid("2026-0001", True)
    assert lg.invoice("2026-0001").status == "paid" and "2026-0001 marked paid" in win.undo_btn.toolTip()
    monkeypatch.setattr(rw.AmountsDialog, "exec", lambda self: qt.QDialog.Accepted)
    monkeypatch.setattr(rw.AmountsDialog, "values", lambda self: {"Regular": "50.00"})
    win._change_amounts(lg.invoice("2026-0001"))
    win.delete("invoices", [lg.invoice("2026-0001")], ask=False)
    assert lg.invoices() == []
    win.undo()  # the delete
    assert [i.invoice_no for i in lg.invoices()] == ["2026-0001"] and win.inv_table.rowCount() == 1
    win.undo()  # the amounts
    assert lg.invoice("2026-0001").amounts == {"Regular": "43.00"} and lg.invoice("2026-0001").status == "paid"
    win._undo_key()  # Ctrl+Z: the payment
    back = lg.invoice("2026-0001")
    assert (back.status, back.amount_paid, back.paid_date, back.notes) == ("open", "", "", "first")
    assert not win.undo_btn.isEnabled()
    win.undo()  # nothing left: nothing happens
    # Ctrl+Z while typing in the search box undoes the typing, not the records
    win._set_paid("2026-0001", True)
    win.i_text.setFocus()
    win.i_text.insert("counsel")
    monkeypatch.setattr(win, "focusWidget", lambda: win.i_text)
    win._undo_key()
    assert win.i_text.text() == "" and lg.invoice("2026-0001").status == "paid"


# ------------------------------------------------------------------ a past job opened again
def test_a_past_job_is_opened_again_from_its_record(window, tmp_path, monkeypatch, qt):
    from minute_filler.deliver import ledger_for
    window.s.outputs = ["agreement", "invoice"]
    src = transcript_pdf(tmp_path / "Transcript.pdf", 12)
    window.add_files([str(src)])
    wait(window, lambda: window.cur.docs)
    names = [a.name for a in window.cur.case.attorneys]
    assert len(names) >= 2
    for a in window.cur.case.attorneys:
        a.checked = a.name == names[1]
    window.cur.att_touched = True
    window.cur.invoice_detail = True
    window._show_case()
    window.cur.case.set("judge", "Hon. Typed By Hand")
    window._show_case()
    monkeypatch.setattr(window, "_saved_box", lambda *a, **k: None)
    window.fill()
    assert len(window.cur.saved) == 2
    lg = ledger_for(window.s)
    made = lg.activity()
    assert all(json.loads(a.origin)["sources"] == [str(src)] for a in made)
    number = lg.invoices()[0].invoice_no
    assert json.loads(lg.origin_of(number))["case"]["fields"]["judge"][:2] == ["Hon. Typed By Hand", "you"]

    window.new_job()
    assert window.cur.is_empty()
    window.open_records()
    win = window._records_win
    win.reopen(lg.invoices()[0])
    wait(window, lambda: window.cur.docs)
    job = window.cur
    assert [d.path for d in job.docs] == [str(src)] and len(window.jobs) == 1
    assert job.case.get("judge") == "Hon. Typed By Hand" and job.case.get("index_no") == "712345/2021"
    assert [a.name for a in job.case.attorneys if a.checked] == [names[1]]  # who ordered, as it was left
    assert job.invoice_detail is True and job.invoice_pages() == 12
    assert job.case.get("agreement_date")  # today's again
    # work on screen is not thrown away without a yes
    monkeypatch.setattr(qt.QMessageBox, "question", lambda *a, **k: qt.QMessageBox.No)
    assert window.open_past_job(made[0].origin) is False and window.cur is job


def test_a_past_job_whose_documents_are_gone_still_comes_back(window, tmp_path, monkeypatch, qt):
    origin = {"sources": [str(tmp_path / "moved away.pdf")],
              "case": {"fields": {"case_name": ["Jane Roe v. X.Y. Holding Corporation", "regex", 0.9],
                                  "index_no": ["712345/2021", "you", 1.0], "agreement_date": ["1/2/2020", "default", 0.9],
                                  "bogus": ["x", "you", 1.0], "judge": "not a field state"},
                       "proc": ["Trial", 7], "attorneys": [{"name": "Alex B. Counsel", "firm": "Counsel & Counsel",
                                                            "checked": True, "unknown": 1}, "nobody"]},
              "job": {"parties": 2, "portions": [[5, ["alex b counsel"]], [12, ["alex b counsel"]]],
                      "invoice_index": "sideways", "page_basis": {"x.pdf": "*", "y.pdf": 3}}}
    assert window.open_past_job(json.dumps(origin)) is True
    job = window.cur
    assert window.rows["index_no"].text() == "712345/2021" and job.case.fields["index_no"].source == "you"
    assert job.case.get("agreement_date") != "1/2/2020" and job.case.get("judge") == ""
    assert job.case.proc_types == {"Trial"} and job.proc_touched
    assert [(a.name, a.checked) for a in job.case.attorneys] == [("Alex B. Counsel", True)]
    # (the record's rows name Alex by the key of version 2.0: now the firm's, see batch.one_row_per_firm)
    assert job.parties == 2 and job.portions == [(5, ["counsel and counsel"]), (12, ["counsel and counsel"])]
    assert job.invoice_index is None and job.page_basis == {"x.pdf": "*"}
    assert "no longer where it was" in window.statusBar().currentMessage()
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: None)
    assert window.open_past_job("not json") is False and window.cur is job


def test_an_attorney_unticked_stays_unticked_when_the_documents_are_read_again(window, tmp_path):
    """An e-mail ticks its sender. Unticked by the user, the sender was ticked again by every re-merge (an AI
    answer, Settings saved, a past job opened again), and would have been invoiced."""
    from minute_filler.batch import remerge
    mail = tmp_path / "order.eml"
    mail.write_text("From: Alex B. Counsel <acounsel@example.com>\nTo: preporter@example.com\n"
                    "Subject: minutes\n\nPlease send the minutes in Jane Roe v. X.Y. Holding Corporation, "
                    "Index No. 712345/2021, 6/3/2026.\n\nAlex B. Counsel\nCounsel & Counsel\n(555) 010-0101\n")
    window.add_files([str(mail)])
    wait(window, lambda: window.cur.docs)
    job = window.cur
    ticked = [a for a in job.case.attorneys if a.checked]
    assert ticked
    for a in job.case.attorneys:
        a.checked = False
    job.att_touched = True
    remerge(job, window.s)
    assert not any(a.checked for a in job.case.attorneys)


def test_a_past_job_keeps_what_the_user_emptied_or_unticked(window):
    from minute_filler.batch import Job, job_from_origin, job_origin
    from minute_filler.deliver import case_snapshot
    job = Job()
    job.case.set("part", "")  # emptied by hand
    job.case.set("case_name", "Jane Roe v. X.Y. Holding Corporation")
    job.proc_touched = job.att_touched = True  # every type unticked, every attorney removed
    origin = json.loads(json.dumps({**job_origin(job), "case": case_snapshot(job.case)}))
    back = job_from_origin(origin, window.s)
    assert back.case.fields["part"].source == "you" and back.case.get("part") == ""
    assert back.proc_touched and back.att_touched
    # a damaged record is refused, not a crash
    for bad in ({"case": {"proc": 3}}, {"case": {"fields": [1]}}, {"case": {"attorneys": 5}}):
        assert job_from_origin(bad, window.s).is_empty()


def test_a_past_job_with_a_document_gone_keeps_what_was_read_from_it(window, tmp_path):
    here = tmp_path / "email.txt"
    here.write_text("Minutes in Jane Roe v. X.Y. Holding Corporation, Index No. 712345/2021, 6/3/2026.")
    origin = {"sources": [str(here), str(tmp_path / "moved away.pdf")],
              "case": {"fields": {"case_name": ["Jane Roe v. X.Y. Holding Corporation", "regex", 0.9],
                                  "index_no": ["712345/2021", "regex", 0.9], "dates": ["6/3/2026", "regex", 0.9],
                                  "judge": ["Maria T. Alvarez", "regex", 0.9], "part": ["14", "regex", 0.9],
                                  "copies": ["1", "default", 0.7]}}}
    assert window.open_past_job(json.dumps(origin)) is True
    wait(window, lambda: window.cur.docs)
    case = window.cur.case
    assert (case.get("judge"), case.get("part")) == ("Maria T. Alvarez", "14")  # read from the document that is gone
    assert case.fields["copies"].source == "default"


def test_a_past_job_of_documents_about_different_days_comes_back_as_one_job(window, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("Minutes in Smith v Jones, Index No. 712222/2024, 5/22/2026, Judge Lopez.")
    b.write_text("Minutes in Roe v Doe, Index No. 700001/2025, 6/1/2026, Judge Lopez.")
    origin = {"sources": [str(a), str(b)],
              "case": {"fields": {"case_name": ["Smith v Jones", "you", 1.0], "part": ["99", "you", 1.0]}}}
    assert window.open_past_job(json.dumps(origin)) is True
    wait(window, lambda: len(window.cur.docs) == 2)
    assert len(window.jobs) == 1 and window.cur.case.get("part") == "99"


def test_records_closes_when_a_job_is_opened_again(window, qt):
    from minute_filler.deliver import ledger_for
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001"))
    window.s.recaps = False
    window.open_records()
    win = window._records_win
    assert win.isVisible()
    win.reopen(lg.invoices()[0])
    assert not win.isVisible()


def test_asking_for_a_newer_version_says_so_when_there_is_one(window, monkeypatch, qt):
    from minute_filler import update
    monkeypatch.setattr(update, "newer", lambda *a, **k: ("9.9.0", update.RELEASES_PAGE))
    said = []
    monkeypatch.setattr(qt.QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    window._check_update(asked=True)
    wait(window, lambda: said)
    assert "9.9.0" in said[-1]


def test_a_record_from_before_origins_were_kept_opens_from_its_columns(window, qt, monkeypatch):
    from minute_filler.deliver import ledger_for
    lg = ledger_for(window.s)
    lg.add_invoice(inv("2026-0001", judge="Maria T. Alvarez", index_no="712345/2021", dates="6/3/2026",
                       email="acounsel@example.com", court="Supreme", part="14"))
    window.s.recaps = False
    window.open_records()
    window._records_win.reopen(lg.invoices()[0])
    case = window.cur.case
    assert (case.get("case_name"), case.get("index_no"), case.get("dates"), case.get("part")) == (
        "Jane Roe v. X.Y. Holding Corporation", "712345/2021", "6/3/2026", "14")
    a, = case.attorneys
    assert (a.name, a.firm, a.email, a.checked) == ("Alex B. Counsel", "Counsel & Counsel", "acounsel@example.com", True)


def test_a_joint_invoice_opens_its_days_again_from_their_documents(window, tmp_path, monkeypatch):
    from minute_filler.deliver import ledger_for
    window.output_boxes["agreement"].setChecked(False)
    window.output_boxes["invoice"].setChecked(True)
    a = transcript_pdf(tmp_path / "Day 1.pdf", 6)
    b = transcript_pdf(tmp_path / "Day 2.pdf", 7, date="June 4, 2026")
    window.add_files([str(a), str(b)])
    wait(window, lambda: len(window.jobs) == 2 and all(j.docs for j in window.jobs))
    window.fill_all_jobs()
    wait(window, lambda: not window.filling)
    lg = ledger_for(window.s)
    one, = lg.invoices()
    origin = json.loads(lg.origin_of(one.invoice_no))
    assert origin["joint"] is True and sorted(origin["sources"]) == sorted([str(a), str(b)])
    window._clear_jobs()
    assert window.open_past_job(json.dumps(origin)) is True
    wait(window, lambda: len(window.jobs) == 2)
    assert sorted(d.path for j in window.jobs for d in j.docs) == sorted([str(a), str(b)])


# ------------------------------------------------------------------ the welcome
def test_the_welcome_is_shown_once_to_a_new_user(tmp_path, monkeypatch, make_window, qt):
    from minute_filler.gui import preview
    shown = []

    def answer(dlg):
        shown.append(dlg)
        dlg.name.setText("Pat Reporter")
        dlg.initials.setText("P.R.")
        dlg.folder.setText(str(tmp_path / "Minutes"))
        dlg.accept()
        return qt.QDialog.Accepted
    monkeypatch.setattr(preview.WelcomeDialog, "exec", answer)
    s = Settings()
    s.use_ai = s.check_updates = False
    win = make_window(s)
    win._startup()
    assert len(shown) == 1 and s.welcomed
    saved = Settings.load()
    assert (saved.profile.name, saved.profile.initials, saved.output_dir, saved.welcomed) == (
        "Pat Reporter", "pr", str(tmp_path / "Minutes"), True)
    win._startup()
    assert len(shown) == 1


def test_the_welcome_skipped_is_not_shown_again(monkeypatch, make_window, qt):
    from minute_filler.gui import preview
    shown = []
    monkeypatch.setattr(preview.WelcomeDialog, "exec", lambda dlg: shown.append(1) or qt.QDialog.Rejected)
    s = Settings()
    s.use_ai = s.check_updates = False
    win = make_window(s)
    win._startup()
    win._startup()
    assert shown == [1] and s.profile.name == "" and Settings.load().welcomed is True


def test_a_user_with_a_name_is_not_welcomed(window, monkeypatch):
    from minute_filler.gui import preview
    window.s.welcomed = False  # (settings from before the welcome existed)
    monkeypatch.setattr(preview.WelcomeDialog, "exec", lambda dlg: pytest.fail("welcomed"))
    window._startup()
