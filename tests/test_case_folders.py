"""A folder for each case (Settings.case_folders, on by default): each job's files go into a folder of its own
inside Save to, named by a pattern (pdfout.case_folder_name: "712345-2021" by default), with the characters
Windows forbids kept out. The run sheet and an output given a folder of its own keep theirs; the command line
stays flat. A folder already there with files in it is asked about (add to it, or a new one beside it:
"712345-2021 (2)"), or not, as Settings say; an empty one is simply used, and a job that chose its folder
isn't asked again. Generate all asks once for the days of a case, and the preview makes no folder of the real
ones. All names are made up."""
import os
import time
from pathlib import Path

import pytest

from minute_filler.batch import (CaseFolder, Job, case_folders, expand_paths, fill_jobs, group, out_dir_for,
                                 read_docs, settle_case_folders)
from minute_filler.models import CaseInfo
from minute_filler.pdfout import case_folder_name, free_folder
from minute_filler.settings import Settings, clean_folder_pattern

from helpers import ROE, invoice_text, make_case, pat_settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write(folder: Path, name: str, **kw) -> str:
    """A made-up e-mailed order (helpers.invoice_text) in `folder`; its path."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(invoice_text(**kw))
    return str(p)


# ------------------------------------------------------------------ the name
def test_the_folder_is_named_by_the_pattern_without_what_windows_forbids():
    roe = make_case({**ROE, "dates": "6/4/2026, 6/3/2026"})
    assert case_folder_name(roe, "{index}") == "712345-2021"
    assert case_folder_name(roe, "{index} - {case}") == "712345-2021 - Jane Roe v. X.Y. Holding Corporation"
    assert case_folder_name(roe, "{date}") == "6-3-2026" and case_folder_name(roe, "{dates}") == "6-3-2026 to 6-4-2026"
    assert case_folder_name(roe, "{county} {court} Part {part}") == "Queens Supreme Part 14"
    odd = make_case({"case_name": "Roe v. Poe: Part 2", "index_no": "712345/2021"})
    assert case_folder_name(odd, "{case}") == "Roe v. Poe- Part 2"  # (":" is no part of a folder name)
    # a blank placeholder goes with what is around it; none left: the caption, else words
    no_index = make_case({"case_name": "Jane Roe v. X.Y. Holding Corporation"})
    assert case_folder_name(no_index, "{index} ({dates})") == "Jane Roe v. X.Y. Holding Corporation"
    assert case_folder_name(make_case({**ROE, "dates": ""}), "{index} ({dates})") == "712345-2021"
    assert case_folder_name(make_case({**ROE, "index_no": ""}), "{index} - {case}") == \
        "Jane Roe v. X.Y. Holding Corporation"
    assert case_folder_name(CaseInfo(), "{index}") == "No index number"
    assert case_folder_name(roe, "{index} {fax}") == "712345-2021 {fax}"  # (not known: as typed)
    assert case_folder_name(roe, "CON") == "CON_" and case_folder_name(roe, "lpt1") == "lpt1_"  # (devices)
    long = case_folder_name(make_case({**ROE, "case_name": "Roe " * 40 + "v. Poe"}), "{case} {index}")
    assert len(long) <= 100 and not long.endswith((" ", "."))


def test_a_new_folder_beside_one_there(tmp_path):
    (tmp_path / "712345-2021").mkdir()
    assert free_folder(tmp_path / "712345-2021").name == "712345-2021 (2)"
    (tmp_path / "712345-2021 (2)").mkdir()
    assert free_folder(tmp_path / "712345-2021").name == "712345-2021 (3)"
    (tmp_path / "Roe v. Poe").mkdir()  # (a dot in the name is no file suffix)
    assert free_folder(tmp_path / "Roe v. Poe").name == "Roe v. Poe (2)"
    assert free_folder(tmp_path / "712222-2024") == tmp_path / "712222-2024"


def test_the_settings_keep_the_pattern_clean():
    s = Settings()
    assert (s.case_folders, s.case_folder_pattern, s.case_folder_existing) == (True, "{index}", "ask")
    assert clean_folder_pattern("{index} / {case}") == "{index} - {case}" and clean_folder_pattern("  ") == "{index}"
    loaded = Settings.from_dict({"case_folder_pattern": "{index}|{case}", "case_folder_existing": "bogus",
                                 "case_folders": False})
    assert (loaded.case_folders, loaded.case_folder_pattern, loaded.case_folder_existing) == \
        (False, "{index}-{case}", "ask")


# ------------------------------------------------------------------ where the files go
def jobs_of(s, *paths):
    docs, _ = read_docs(expand_paths(list(paths)), s)
    return group(docs, s)


def test_a_case_s_files_go_into_its_folder_and_the_others_keep_theirs(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    s.output_dirs = {"mofr": str(tmp_path / "MOFRs")}
    jobs = jobs_of(s, write(tmp_path / "in", "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    made = fill_jobs(jobs, s, outputs=["agreement", "mofr"])
    assert {p.parent for p in made} == {tmp_path / "out" / "712222-2024", tmp_path / "MOFRs"}
    assert jobs[0].folder == jobs[0].folder_named == "712222-2024"
    assert out_dir_for(jobs[0], s) == tmp_path / "out" / "712222-2024"
    s.case_folders = False  # (turned off: Save to itself)
    assert out_dir_for(jobs[0], s) == tmp_path / "out"
    # with Save to blank: next to the document
    s.case_folders, s.output_dir = True, ""
    other = jobs_of(s, write(tmp_path / "in2", "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026"))
    made = fill_jobs(other, s, outputs=["agreement"])
    assert [p.parent for p in made] == [tmp_path / "in2" / "700001-2025"]


def test_no_folder_is_asked_about_for_the_run_sheet_alone(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    jobs = jobs_of(s, write(tmp_path / "in", "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    (tmp_path / "out" / "712222-2024").mkdir(parents=True)
    (tmp_path / "out" / "712222-2024" / "old.pdf").write_bytes(b"")
    assert case_folders(jobs, s, ["runsheet"]) == []
    s.output_dirs = {"agreement": str(tmp_path / "Agreements")}  # (every output asked for has its own folder)
    assert case_folders(jobs, s, ["agreement"]) == []
    s.output_dirs = {}
    [f] = case_folders(jobs, s, ["agreement"])
    assert (f.path, f.files, f.jobs) == (tmp_path / "out" / "712222-2024", 1, jobs)


def test_a_folder_there_is_added_to_or_a_new_one_made_beside_it(tmp_path):
    s = pat_settings()
    s.output_dir = str(tmp_path / "out")
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "Minute Agreement - earlier.pdf").write_bytes(b"")
    jobs = jobs_of(s, write(tmp_path / "in", "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    [f] = case_folders(jobs, s)
    f.use(new=True)
    assert jobs[0].folder == "712222-2024 (2)" and jobs[0].folder_named == "712222-2024"
    assert case_folders(jobs, s) == []  # (chosen: the job's next Generate goes there again)
    jobs[0].case.set("index_no", "712223/2024")  # another number typed in: its own folder
    [g] = case_folders(jobs, s)
    assert g.name == "712223-2024" and g.files == 0
    g.use(new=True)  # (an empty one, or one not there, is used as it is)
    assert jobs[0].folder == "712223-2024"
    # fill_jobs, on its own (nobody to ask): as Settings say, "ask" adding to it
    again = jobs_of(s, write(tmp_path / "in3", "c.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    settle_case_folders(again, s)
    assert again[0].folder == "712222-2024"
    s.case_folder_existing = "new"
    later = jobs_of(s, write(tmp_path / "in4", "d.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"))
    settle_case_folders(later, s)
    assert later[0].folder == "712222-2024 (2)"


def test_the_command_line_keeps_every_file_in_its_folder(tmp_path):
    from minute_filler.main import batch
    s = pat_settings()
    s.save()
    write(tmp_path / "in", "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    assert batch(str(tmp_path / "cli"), [str(tmp_path / "in")], ["agreement"]) == 0
    assert [p.suffix for p in (tmp_path / "cli").iterdir() if p.name != "batch.json"] == [".pdf"]


# ------------------------------------------------------------------ the window
@pytest.fixture
def window(tmp_path, make_window, monkeypatch, qt):
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["agreement"]
    win = make_window(s)
    from minute_filler.gui.dialogs import ClarifyDialog
    monkeypatch.setattr(ClarifyDialog, "exec", lambda self: qt.QDialog.Accepted)
    return win


def answer(monkeypatch, qt, button: str, never: bool = False) -> list:
    """Every QMessageBox is answered with the button whose text starts with `button` (never: its "Don't ask
    again" ticked too); returns the texts of the boxes shown."""
    shown = []

    def exec_(box):
        shown.append(box.text())
        box._picked = next((b for b in box.buttons() if b.text().startswith(button)), None)
        if never and box.checkBox() is not None:
            box.checkBox().setChecked(True)
        return 0
    monkeypatch.setattr(qt.QMessageBox, "exec", exec_)
    monkeypatch.setattr(qt.QMessageBox, "clickedButton", lambda box: getattr(box, "_picked", None))
    return shown


def wait(win, qt, until, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        qt.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def one_job(win, qt, tmp_path, name="a.txt"):
    win.add_files([write(tmp_path / "in", name, title="Smith v Jones", index="712222-2024", date="5-22-2026")])
    wait(win, qt, lambda: win.cur.docs)


@pytest.mark.parametrize("button, folder", [("Add to it", "712222-2024"), ("New folder", "712222-2024 (2)")])
def test_generate_asks_about_a_folder_there(window, qt, tmp_path, monkeypatch, button, folder):
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "Invoice 2026-0001.pdf").write_bytes(b"")
    one_job(window, qt, tmp_path)
    shown = answer(monkeypatch, qt, button)
    window.fill()
    assert "A folder for this case is already there, with 1 file in it" in shown[0]
    assert "\"712222-2024 (2)\"" in shown[0]
    assert [p.parent.name for p in window.cur.saved] == [folder]
    shown.clear()
    window.fill()  # the job's next Generate: no question, the same folder
    assert not any("already there" in t for t in shown)
    assert {p.parent.name for p in window.cur.saved} == {folder}


def test_go_back_makes_nothing_and_dont_ask_again_is_kept(window, qt, tmp_path, monkeypatch):
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "notes.txt").write_text("x")
    one_job(window, qt, tmp_path)
    answer(monkeypatch, qt, "Go back")
    window.fill()
    assert not window.cur.saved and sorted(p.name for p in there.iterdir()) == ["notes.txt"]
    assert not window.cur.folder  # (asked again next time)
    answer(monkeypatch, qt, "New folder", never=True)
    window.fill()
    assert window.s.case_folder_existing == "new" and Settings.load().case_folder_existing == "new"
    assert [p.parent.name for p in window.cur.saved] == ["712222-2024 (2)"]


def test_an_empty_folder_is_used_without_a_question(window, qt, tmp_path, monkeypatch):
    (tmp_path / "out" / "712222-2024").mkdir(parents=True)
    one_job(window, qt, tmp_path)
    shown = answer(monkeypatch, qt, "OK")
    window.fill()
    assert not any("already there" in t for t in shown)
    assert [p.parent.name for p in window.cur.saved] == ["712222-2024"]


def test_generate_all_asks_once_for_the_days_of_a_case(window, qt, tmp_path, monkeypatch):
    there = tmp_path / "out" / "712222-2024"
    there.mkdir(parents=True)
    (there / "earlier.pdf").write_bytes(b"")
    window.add_files([write(tmp_path / "in", "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026"),
                      write(tmp_path / "in", "b.txt", title="Smith v Jones", index="712222-2024", date="5-26-2026"),
                      write(tmp_path / "in", "c.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")])
    wait(window, qt, lambda: len(window.jobs) == 3)
    shown = answer(monkeypatch, qt, "New folder")
    window.fill_all_jobs()
    wait(window, qt, lambda: all(j.saved for j in window.jobs))
    asked = [t for t in shown if "already there" in t]
    assert len(asked) == 1 and str(there) in asked[0]
    folders = sorted({p.parent.name for j in window.jobs for p in j.saved})
    assert folders == ["700001-2025", "712222-2024 (2)"]


def test_the_preview_writes_nothing_in_the_real_folders(tmp_path, make_window, monkeypatch, qt):
    from minute_filler.gui.preview import PreviewDialog
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["agreement"]
    win = make_window(s, preview=True)
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: qt.QDialog.Rejected)  # Go back
    one_job(win, qt, tmp_path)
    answer(monkeypatch, qt, "OK")
    win.fill()
    assert not (tmp_path / "out").exists() and not win.cur.saved


def test_the_settings_window_refuses_what_windows_forbids(qt):
    from PySide6.QtTest import QTest
    from minute_filler.gui.dialogs import SettingsDialog
    dlg = SettingsDialog(pat_settings())
    assert dlg.o_case_folders.isChecked() and dlg.o_case_pattern.text() == "{index}"
    assert 'e.g. "712345-2021"' in dlg.o_case_hint.text()
    dlg.o_case_pattern.clear()
    QTest.keyClicks(dlg.o_case_pattern, "{index}/|:*?\"<> {case}")
    assert dlg.o_case_pattern.text() == "{index} {case}"
    assert 'e.g. "712345-2021 Jane Roe v. X.Y. Holding Corporation"' in dlg.o_case_hint.text()
    dlg.o_case_existing.setCurrentIndex(dlg.o_case_existing.findData("add"))
    dlg.accept()
    again = Settings.load()
    assert (again.case_folder_pattern, again.case_folder_existing) == ("{index} {case}", "add")
    dlg = SettingsDialog(again)
    dlg.o_case_folders.setChecked(False)
    assert not dlg.o_case_pattern.isEnabled()
    dlg.accept()
    assert Settings.load().case_folders is False
