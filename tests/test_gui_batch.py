"""Drives the main window (off screen): several documents become a batch of jobs."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from minute_filler.gui.main_window import MainWindow  # noqa: E402
from minute_filler.settings import Profile, Settings  # noqa: E402

INVOICE = """Invoice
To: Example Firm LLP, attn: billing@examplefirm.com
Title: {title}
Index No. {index}
Date of proceedings: {date}
Judge: Lopez
Part: 53
"""


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)  # no dialogs waiting for a click
    s = Settings()
    s.profile = Profile(name="Pat Reporter")
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    win = MainWindow(s, app)
    yield win
    win.gen += 1
    win.deleteLater()


def wait(win, until, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not win.runner._live and until():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def write(folder, name, **kw):
    p = folder / name
    p.write_text(INVOICE.format(**kw))
    return str(p)


def test_batch_of_documents(window, tmp_path):
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    b = write(tmp_path, "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")
    c = write(tmp_path, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([a, b, c, a])
    wait(window, lambda: len(window.jobs) == 2)
    assert [[d.ing.name for d in j.docs] for j in window.jobs] == [["a.txt", "c.txt"], ["b.txt"]]
    assert not window.job_list.isHidden() and window.job_list.count() == 2
    assert "2 documents" in window.job_list.item(0).text()
    assert window.rows["index_no"].text() == "712222/2024" and window.input_list.count() == 2

    # edits stay with their job when another one is selected
    window.rows["judge"].choose("Maria T. Lopez")
    window.job_list.setCurrentRow(1)
    assert window.rows["index_no"].text() == "700001/2025" and window.rows["judge"].text() == "Lopez"
    window.job_list.setCurrentRow(0)
    assert window.rows["judge"].text() == "Maria T. Lopez"

    # a document dropped later finds its job; one that is already loaded is skipped
    d = write(tmp_path, "d.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")
    window.add_files([d, b])
    wait(window, lambda: len(window.jobs[1].docs) == 2)
    assert len(window.jobs) == 2

    window.fill_all_jobs()
    wait(window, lambda: all(j.saved for j in window.jobs))
    saved = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(saved) == 2 and "712222-2024" in saved[1] and "700001-2025" in saved[0]
    assert window.job_list.item(0).text().startswith("✓")
    assert not any(j.include for j in window.jobs)  # nothing left for a second "Fill all"


def test_documents_about_one_case_stay_one_job(window, tmp_path):
    a = write(tmp_path, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    c = write(tmp_path, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([a, c])
    wait(window, lambda: len(window.cur.docs) == 2)
    assert len(window.jobs) == 1 and window.job_list.isHidden() and window.fill_all_btn.isHidden()
    assert window.rows["index_no"].text() == "712222/2024"
    # one more document goes to the job on screen, as before
    window.add_files([write(tmp_path, "b.txt", title="Roe v Doe", index="700001-2025", date="6-1-2026")])
    wait(window, lambda: len(window.cur.docs) == 3)
    assert len(window.jobs) == 1


def test_folder_and_split(window, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    write(src, "a.txt", title="Smith v Jones", index="712222-2024", date="5-22-2026")
    write(src, "c.txt", title="Smith v. Jones", index="712222/2024", date="5/22/2026")
    window.add_files([str(src)], batch=True)
    wait(window, lambda: len(window.cur.docs) == 2)
    assert len(window.jobs) == 1
    window.new_job()
    assert window.cur.is_empty() and window.input_list.count() == 0 and window.rows["index_no"].text() == ""
