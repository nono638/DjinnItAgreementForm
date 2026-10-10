"""Courthouse profiles (minute_filler/courthouses, MIGRATION.md Release A). Queens Supreme Court's profile lists
the four outputs the program has always made (the order they are made in, their panels' order, the modules the
build must bundle), and the tables that name the outputs come from it; an output described wrong is refused. A
made-up courthouse with a fifth output (a note, a text file made with each job's files, asking nothing) is made
and recorded by generate, counted, made by Generate all and the command line, kept in the settings, and shown in
the window with a box of its own, where Generate makes it without a question: nothing in deliver.py, batch.py,
records.py, preview.py, main.py or the window names it. All names are made up."""
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from minute_filler import courthouses, records
from minute_filler.courthouses.base import OutputSpec, resolve
from minute_filler.deliver import generate, ledger_for
from minute_filler.settings import OUTPUT_FOLDERS, OUTPUTS, Settings

from helpers import ROE, make_case, pat_settings, transcript_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Notes:
    """The made-up output's maker: a text file per job naming the case and its day."""

    @staticmethod
    def make(m) -> None:
        folder = m.folder("note")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Note - {m.case.get('index_no').replace('/', '-') or 'no index'}.txt"
        path.write_text(f"{m.case.get('case_name')}\n{m.case.get('dates')}\n", encoding="utf-8")
        m.record("note", path)


NOTE = OutputSpec("note", "Note", "doc", maker="test_courthouses:Notes")


@pytest.fixture
def with_note():
    """The program works for a made-up courthouse for the test: Queens's outputs and the note."""
    queens = courthouses.QUEENS_SUPREME_CIVIL
    made_up = replace(queens, key="made_up", name="Made-up County Court", outputs=queens.outputs + (NOTE,))
    before = courthouses.use(made_up)
    yield made_up
    courthouses.use(before)


def test_queens_lists_what_the_program_has_always_made():
    assert courthouses.current().key == "queens_supreme_civil"
    assert OUTPUTS == {"agreement": "Minute agreement", "mofr": "MOFR", "invoice": "Invoice", "runsheet": "Run sheet"}
    assert OUTPUT_FOLDERS == {"runsheet": "YinIt Run Sheets"} and records.KINDS is OUTPUTS
    # made: the run sheet first (one open in Excel stops the case), the invoices last
    assert courthouses.make_order(["invoice", "mofr", "runsheet", "agreement", "bogus"]) == \
        ["runsheet", "agreement", "mofr", "invoice"]
    assert [o.key for o in courthouses.panel_order()] == ["agreement", "invoice", "mofr", "runsheet"]
    for o in courthouses.outputs():
        maker = o.maker_impl()
        assert hasattr(maker, "make") and (o.group != "form" or hasattr(maker, "make_form")), o.key
        assert o.policy_impl() is not None and resolve(o.panel), o.key
    # what the build must bundle, as the code is imported by name only (YinItAgreementForm.spec)
    assert courthouses.QUEENS_SUPREME_CIVIL.modules() == [
        "minute_filler.fill", "minute_filler.batch", "minute_filler.gui.panels", "minute_filler.mofr",
        "minute_filler.invoice", "minute_filler.runsheet",
        "minute_filler.courthouses.queens_supreme_civil.billing"]  # (its split rule: firms at different speeds)
    spec = (Path(__file__).parents[1] / "YinItAgreementForm.spec").read_text(encoding="utf-8")
    assert "for c in BUILT_IN for m in c.modules()" in spec


def test_an_output_is_described_whole():
    """A wrong group, count or question is refused when the profile is made, not when a file is."""
    with pytest.raises(ValueError, match="group"):
        OutputSpec("x", "X", "letter", maker="m:f")
    with pytest.raises(ValueError, match="per"):
        OutputSpec("x", "X", "doc", maker="m:f", per="page")
    with pytest.raises(ValueError, match="question"):
        OutputSpec("x", "X", "doc", maker="m:f", asks=("weather",))
    with pytest.raises(ValueError, match="maker"):
        OutputSpec("x", "X", "doc")
    with pytest.raises(ValueError, match="twice"):
        replace(courthouses.QUEENS_SUPREME_CIVIL, outputs=(NOTE, NOTE))
    assert NOTE.count_words() == ("note", "notes") and courthouses.QUEENS_SUPREME_CIVIL.outputs[1].count_words() \
        == ("MOFR", "MOFRs")


def test_the_new_output_is_made_recorded_and_kept(with_note, tmp_path):
    assert OUTPUTS["note"] == "Note" and records.KINDS["note"] == "Note"
    s = pat_settings()
    case = make_case(ROE)
    made = generate(case, s, tmp_path / "out", ["note", "agreement"])
    assert [p.suffix for p in made] == [".pdf", ".txt"]  # (the forms first, then the documents)
    assert made[1].read_text(encoding="utf-8") == "Jane Roe v. X.Y. Holding Corporation\n6/3/2026\n"
    assert [a.kind for a in ledger_for(s).activity()] == ["note", "agreement"]  # (newest first)
    s.outputs, s.output_dirs = ["note", "bogus"], {"note": str(tmp_path / "Notes")}
    s.save()
    loaded = Settings.load()
    assert loaded.outputs == ["note"] and loaded.folder_for("note", tmp_path) == tmp_path / "Notes"


def test_generate_all_and_the_command_line_make_it(with_note, tmp_path):
    from minute_filler.batch import expand_paths, files_to_make, fill_jobs, group, read_docs
    from minute_filler.main import batch
    s = pat_settings()
    (tmp_path / "in").mkdir()
    transcript_pdf(tmp_path / "in" / "Transcript.pdf")
    docs, _ = read_docs(expand_paths([str(tmp_path / "in")]), s)
    jobs = group(docs, s)
    assert files_to_make(jobs, ["note"], s) == 1 and files_to_make(jobs, ["note", "mofr"], s) == 2
    s.output_dir = str(tmp_path / "all")
    done = fill_jobs(jobs, s, outputs=["note"])
    assert [p.name for p in done] == ["Note - 712345-2021.txt"] and not jobs[0].error
    s.save()
    assert batch(str(tmp_path / "cli"), [str(tmp_path / "in")], ["note"]) == 0
    assert (tmp_path / "cli" / "Note - 712345-2021.txt").exists()


def test_the_window_has_its_box_and_generate_asks_nothing(with_note, make_window, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    from minute_filler.gui.dialogs import ClarifyDialog
    shown = []

    def box(self):
        shown.append(self.windowTitle())
        return 0
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", box)
    for name in ("question", "warning", "information"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, lambda *a, n=name, **k: pytest.fail(f"asked: {n} {a[2:3]}"))
    monkeypatch.setattr(ClarifyDialog, "exec", lambda self: pytest.fail("asked about the fields"))
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir, s.outputs = str(tmp_path / "out"), ["note"]
    win = make_window(s)
    assert list(win.output_boxes) == ["agreement", "invoice", "mofr", "runsheet", "note"]
    assert win.output_boxes["note"].text() == "Note" and win.output_boxes["note"].isChecked()
    assert all(win.output_grid.indexOf(c) >= 0 for c in win._output_cols)  # (every panel laid out)
    win.add_files([str(transcript_pdf(tmp_path / "Transcript.pdf"))])
    end = time.time() + 20
    while not (win.cur.docs and not win.runner._live) and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    win._refresh_outputs()
    assert win.output_counts["note"].text() == "Will generate 1 note"
    win.fill()
    assert shown == ["Saved"] and [p.name for p in win.cur.saved] == ["Note - 712345-2021.txt"]
