"""Fixtures for every test: settings and records kept in a temporary folder, no real Ollama, and an off-screen
Qt application and main window for the GUI tests."""
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Keep tests away from the real %APPDATA% settings, rate sheets and records, and from the real
    Documents folder (where the records' CSV copies go)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def pytest_configure(config):
    """Registers the "ollama" marker, so pytest does not warn about it."""
    config.addinivalue_line("markers", "ollama: the test may reach a real Ollama (not stubbed by no_ollama)")


@pytest.fixture(autouse=True)
def no_ollama(request, monkeypatch):
    """Tests never ask a real Ollama (it answers differently on every computer, and slowly): it is 'not
    running'. A test marked @pytest.mark.ollama gets the real thing."""
    if request.node.get_closest_marker("ollama"):
        return
    from minute_filler.extract_llm import OllamaExtractor

    def offline(self, timeout: float = 3):
        raise ConnectionError("no Ollama in the tests")
    monkeypatch.setattr(OllamaExtractor, "installed_models", offline)


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    """Tests never ask GitHub for the latest version (the window's daily look): there is no newer one."""
    from minute_filler import update
    monkeypatch.setattr(update, "newer", lambda *a, **k: None)


@pytest.fixture
def qt(monkeypatch):
    """PySide6's QtWidgets with a QApplication, drawn off screen (the test is skipped without PySide6). The
    preview before saving is answered "Save" at once (a box waiting for a click would stop the test); a test of
    the preview itself sets PreviewDialog.exec as it needs. "The math" after an invoice is made is closed at once
    too (a test of it sets MathDialog.exec)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    fonts = os.path.join(os.environ.get("WINDIR", ""), "Fonts")
    if os.path.isdir(fonts):  # the real fonts, so text is measured as on screen (see the layout tests)
        os.environ.setdefault("QT_QPA_FONTDIR", fonts)
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from minute_filler.gui.preview import MathDialog, PreviewDialog
    monkeypatch.setattr(PreviewDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    monkeypatch.setattr(MathDialog, "exec", lambda self: QtWidgets.QDialog.Accepted)
    return QtWidgets


@pytest.fixture
def make_window(qt):
    """make_window(settings) -> a MainWindow, closed again after the test. The preview before saving is off
    unless preview=True: Generate then makes the files once, as the tests of everything else expect."""
    from minute_filler.gui.main_window import MainWindow
    made = []
    app = qt.QApplication.instance()
    style = app.styleSheet()

    def make(s, preview=False):
        s.preview_before_saving = preview
        win = MainWindow(s, qt.QApplication.instance())
        made.append(win)
        return win

    yield make
    # Background work a window started (the look for a newer version, the backup) reports back before the
    # temporary APPDATA goes: an answer that came later saved this test's settings into the next test's folder
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    for win in made:
        win.gen += 1  # results of work still running are dropped
        win.deleteLater()
    # deleted now, not when an event loop next runs: windows left from earlier tests would each be styled again
    # by a later test that changes the style sheet (a zoom), which takes minutes with a hundred of them
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if app.styleSheet() != style:  # a test that saved settings (the theme is applied again) leaves the style
        app.setStyleSheet(style)   # sheet as it found it: the layout tests measure text with it
    from minute_filler.gui import zoom
    if zoom.zoom() != 1.0:  # a test that zoomed must not leave the next one zoomed (the zoom is the app's)
        from minute_filler.gui.theme import apply_theme
        zoom.set_zoom(1.0)
        apply_theme(qt.QApplication.instance(), "light")
