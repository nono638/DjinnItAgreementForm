"""The Help menu: the How to use guide (F1) with what goes in and comes out, the website link (the site, not the
source code) in the menu, the guide and About, and the guide's shortcuts matching the window's."""
import os

import pytest

from helpers import pat_settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WEBSITE = "https://nono638.github.io/YinItAgreementForm/"


@pytest.fixture
def window(tmp_path, make_window):
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    s.records_dir = str(tmp_path / "records")
    return make_window(s)


def help_menu(win):
    return next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")


def test_the_guide_says_what_goes_in_and_comes_out(qt):
    from minute_filler.gui.dialogs import GuideDialog, WEBSITE_URL
    assert WEBSITE_URL == WEBSITE
    d = GuideDialog()
    text = d.text.toPlainText()
    for words in ("What you can put in", "What it makes", "Step by step", "Tips", "Keyboard shortcuts",
                  "Transcript PDFs", "Photos and screenshots", ".eml", "Minute agreement", "Invoice", "MOFR",
                  "Run sheet", "Who ordered what", "Records"):
        assert words in text, words
    assert WEBSITE in d.text.toHtml() and "{website}" not in d.text.toHtml()
    d.deleteLater()


def test_the_help_menu_has_the_guide_and_the_website(window, monkeypatch):
    import minute_filler.gui.main_window as mw
    from minute_filler.gui import dialogs
    assert [a.text() for a in window.menuBar().actions()] == ["&File", "&Help"]  # no View menu: zoom is Ctrl +/-
    actions = {a.text().split("\t")[0]: a for a in help_menu(window).actions() if a.text()}
    assert list(actions)[0] == "&How to use YinItAgreementForm…"
    shown, opened = [], []
    monkeypatch.setattr(dialogs.GuideDialog, "exec", lambda self: shown.append(self.windowTitle()))
    monkeypatch.setattr(mw, "open_url", opened.append)
    actions["&How to use YinItAgreementForm…"].trigger()
    assert shown == ["How to use YinItAgreementForm"]
    next(a for t, a in actions.items() if "website" in t).trigger()
    assert opened == [WEBSITE]


def test_f1_opens_the_guide_and_its_shortcuts_are_the_windows(window, monkeypatch):
    from PySide6.QtGui import QKeySequence, QShortcut
    from minute_filler.gui import dialogs
    keys = {s.key().toString() for s in window.findChildren(QShortcut)}
    for k in ("F1", "Ctrl+O", "Ctrl+N", "Ctrl+Return", "Ctrl+Shift+Return", "Ctrl+R", "Ctrl+0"):
        assert QKeySequence(k).toString() in keys, k
    shown = []
    monkeypatch.setattr(dialogs.GuideDialog, "exec", lambda self: shown.append(1))
    next(s for s in window.findChildren(QShortcut) if s.key().toString() == "F1").activated.emit()
    assert shown == [1]


def test_about_links_the_website(qt):
    from PySide6.QtWidgets import QLabel
    from minute_filler.gui.dialogs import AboutDialog
    d = AboutDialog()
    assert any(WEBSITE in l.text() for l in d.findChildren(QLabel))
    d.deleteLater()
