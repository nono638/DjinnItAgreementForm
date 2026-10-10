"""The index number of each document (batch.Doc.index_numbers, Job.index_number_notes): a document with none is
noted; documents of one job whose numbers don't match ("a.txt has 712345/2021; b.txt has 722222/2025") are noted
and Generate (and Generate all) asks before making anything, until the user says to keep them together. A title
page of cases tried together lists several numbers: a document sharing one of them is the same case, and so are
documents linked through it. The window shows a yellow note (with "Keep them together") and a ⚠ on the document's
row; the command line's batch.json lists them as "warnings". All names and numbers are made up."""
import os
import time
from pathlib import Path

import pytest

from minute_filler.batch import Job, index_number_text

from helpers import invoice_text, pat_settings, text_doc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NO_NUMBER = ("From: someone@examplefirm.com\nSubject: minutes\n\nPlease send the minutes in Smith v Jones, "
             "5/22/2026, Judge Lopez. About 40 pages.")
TOGETHER = """Invoice
To: Example Firm LLP, attn: billing@examplefirm.com
Title: Smith v Jones
Index Nos. 712222/2024, 722222/2025
Date of proceedings: 5/22/2026
"""


def order(s, name, index):
    return text_doc(s, invoice_text(index=index), name)


def test_a_document_without_an_index_number_is_noted():
    s = pat_settings()
    alone = Job(docs=[text_doc(s, NO_NUMBER, "email.txt")])
    assert alone.index_number_notes() == ["No index number found in the document"]
    two = Job(docs=[order(s, "a.txt", "712222-2024"), text_doc(s, NO_NUMBER, "email.txt")])
    assert two.index_number_notes() == ["email.txt: no index number found"]
    assert not two.index_number_asks() and not two.index_number_mismatch()
    assert Job(docs=[order(s, "a.txt", "712222-2024")]).index_number_notes() == []


def test_documents_whose_numbers_dont_match_are_noted_until_kept_together():
    s = pat_settings()
    job = Job(docs=[order(s, "a.txt", "712222-2024"), order(s, "b.txt", "722222/2025")])
    assert [[d.ing.name for d in g] for g in job.index_number_mismatch()] == [["a.txt"], ["b.txt"]]
    text = "These documents may not be about the same case: a.txt has 712222/2024; b.txt has 722222/2025"
    assert job.index_number_notes() == [text] and index_number_text(job.index_number_mismatch()) == text
    assert job.index_number_asks()
    assert any("index numbers don't match" in i for i in job.issues(["agreement"]))  # (the job list's ⚠)
    job.index_numbers_ok = [d.key() for d in job.docs]  # "Keep them together"
    assert not job.index_number_asks() and job.index_number_notes() == []
    job.docs.append(order(s, "c.txt", "733333/2022"))  # another document: asked again
    assert job.index_number_asks()
    assert job.index_number_notes() == ["These documents may not be about the same case: a.txt has 712222/2024; "
                                        "b.txt has 722222/2025; c.txt has 733333/2022"]


def test_a_title_page_of_several_numbers_links_the_documents():
    s = pat_settings()
    together = text_doc(s, TOGETHER, "title.txt")
    assert together.index_numbers() == {"712222/2024", "722222/2025"}
    job = Job(docs=[order(s, "a.txt", "712222-2024"), together])
    assert not job.index_number_mismatch() and job.index_number_notes() == []
    # through the title page, an order of the other case is the same case too
    job.docs.append(order(s, "b.txt", "722222/2025"))
    assert len(job.index_number_groups()) == 1 and not job.index_number_asks()
    # one that shares no number with any of them is not
    job.docs.append(order(s, "c.txt", "733333/2022"))
    assert [[d.ing.name for d in g] for g in job.index_number_mismatch()] == [["a.txt", "title.txt", "b.txt"],
                                                                                ["c.txt"]]
    assert job.index_number_notes() == ["These documents may not be about the same case: a.txt, title.txt and "
                                        "b.txt have 712222/2024 and 722222/2025; c.txt has 733333/2022"]


def test_batch_json_has_the_warnings(tmp_path):
    import json
    from minute_filler.main import batch
    pat_settings().save()
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "email.txt").write_text(NO_NUMBER)
    batch(str(tmp_path / "out"), [str(tmp_path / "in")], ["agreement"])
    report = json.loads((tmp_path / "out" / "batch.json").read_text(encoding="utf-8"))
    assert [j["warnings"] for j in report["jobs"]] == [["No index number found in the document"]]


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


def answer(monkeypatch, qt, button: str) -> list:
    """Every QMessageBox is answered with the button whose text starts with `button`; returns their texts."""
    shown = []

    def exec_(box):
        shown.append(box.text())
        box._picked = next((b for b in box.buttons() if b.text().startswith(button)), None)
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


def write(folder: Path, name: str, text: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    return str(folder / name)


def doc_row(win, i: int):
    """The row of the job's i-th document in the list on the left (under the job's own row)."""
    return win.job_list.topLevelItem(0).child(i)


def mismatched(win, qt, tmp_path):
    """A job of two orders with different index numbers: the second dropped onto the job on screen."""
    win.add_files([write(tmp_path / "in", "a.txt", invoice_text(index="712222-2024"))])
    wait(win, qt, lambda: len(win.cur.docs) == 1)
    win.add_files([write(tmp_path / "in", "b.txt", invoice_text(title="Roe v Doe", index="700001-2025"))])
    wait(win, qt, lambda: len(win.cur.docs) == 2)


def test_the_window_notes_it_and_generate_asks(window, qt, tmp_path, monkeypatch):
    mismatched(window, qt, tmp_path)
    assert len(window.jobs) == 1
    note = window.index_note.text()
    assert not window.index_note.isHidden() and "a.txt has 712222/2024; b.txt has 700001/2025" in note
    assert "Keep them together" in note
    assert not doc_row(window, 0).text(0).startswith("⚠") and doc_row(window, 1).text(0).startswith("⚠")
    assert "Its index number (700001/2025) isn't this job's (712222/2024)" in doc_row(window, 1).toolTip(0)
    shown = answer(monkeypatch, qt, "Go back")
    window.fill()
    assert "These documents may not be about the same case" in shown[0] and not window.cur.saved
    shown = answer(monkeypatch, qt, "Generate anyway")
    window.fill()
    assert window.cur.saved and window.cur.index_numbers_ok
    assert window.index_note.isHidden()
    shown.clear()
    window.fill()  # kept together: not asked again
    assert not any("same case" in t for t in shown)


def test_keep_them_together_from_the_note(window, qt, tmp_path, monkeypatch):
    mismatched(window, qt, tmp_path)
    window._keep_index_numbers()  # (the note's link)
    assert window.index_note.isHidden() and not doc_row(window, 1).text(0).startswith("⚠")
    shown = answer(monkeypatch, qt, "OK")
    window.fill()
    assert not any("same case" in t for t in shown) and window.cur.saved


def test_a_document_without_a_number_is_marked_in_the_window(window, qt, tmp_path):
    window.add_files([write(tmp_path / "in", "a.txt", invoice_text(index="712222-2024"))])
    wait(window, qt, lambda: len(window.cur.docs) == 1)
    assert window.index_note.isHidden()
    window.add_files([write(tmp_path / "in", "email.txt", NO_NUMBER)])
    wait(window, qt, lambda: len(window.cur.docs) == 2)
    assert window.index_note.text() == "⚠ email.txt: no index number found"
    assert doc_row(window, 1).text(0).startswith("⚠")
    assert doc_row(window, 1).toolTip(0).startswith("⚠ No index number found in this document")


def test_generate_all_lists_the_jobs_first(window, qt, tmp_path, monkeypatch):
    mismatched(window, qt, tmp_path)
    window.add_files([write(tmp_path / "in2", "c.txt", invoice_text(title="Poe v Coe", index="733333-2022")),
                      write(tmp_path / "in2", "d.txt", invoice_text(title="Doe v Moe", index="744444-2023"))])
    wait(window, qt, lambda: len(window.jobs) == 3)
    shown = answer(monkeypatch, qt, "Go back")
    window.fill_all_jobs()
    assert "a.txt has 712222/2024; b.txt has 700001/2025" in shown[0]
    assert not any(j.saved for j in window.jobs)
    shown = answer(monkeypatch, qt, "Generate all anyway")
    window.fill_all_jobs()
    wait(window, qt, lambda: all(j.saved for j in window.jobs))
