import os

import pytest


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Keep tests away from the real %APPDATA% settings, rate sheets and records, and from the real
    Documents folder (where the records' CSV copies go)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def qt():
    """PySide6's QtWidgets with a QApplication, drawn off screen (the test is skipped without PySide6)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return QtWidgets


@pytest.fixture
def make_window(qt):
    """make_window(settings) -> a MainWindow, closed again after the test."""
    from minute_filler.gui.main_window import MainWindow
    made = []

    def make(s):
        win = MainWindow(s, qt.QApplication.instance())
        made.append(win)
        return win

    yield make
    for win in made:
        win.gen += 1  # results of work still running are dropped
        win.deleteLater()
