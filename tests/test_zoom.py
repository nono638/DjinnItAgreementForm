"""Zoom (Ctrl + / Ctrl - / Ctrl 0, Ctrl + wheel) and a window that fits the screen: the style sheet's sizes are
scaled, fixed widget sizes follow, the zoom is saved, and the window is never bigger than the screen has room for
(at 150 % Windows scaling a 1920 x 1080 screen is about 1280 x 690)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402

from helpers import pat_settings  # noqa: E402
from minute_filler.gui import zoom  # noqa: E402
from minute_filler.gui.theme import QSS  # noqa: E402
from minute_filler.settings import Settings  # noqa: E402


def test_the_style_sheet_scales_fonts_and_spacing_but_not_colors_or_hairlines():
    out = zoom.scale_qss(QSS, 1.5)
    assert "font-size: 15pt" in out and "font-size: 10pt" not in out  # the base font, 10pt
    assert "padding: 9px 21px" in out                                 # "6px 14px" buttons
    assert "1px solid {border}" in out                                 # lines stay thin
    assert "top: -1px" in out                                          # a negative offset is left alone
    assert zoom.scale_qss("color: #1d4ed8;", 1.5) == "color: #1d4ed8;"
    assert zoom.scale_qss(QSS, 1.0) == QSS


def test_clamp():
    """The zoom is kept between 70 % and 160 %, to two decimals; a value that isn't a number (or NaN) is 100 %."""
    assert zoom.clamp(5) == 1.6 and zoom.clamp(0.1) == 0.7 and zoom.clamp(1.2999) == 1.3
    assert zoom.clamp("x") == 1.0 and zoom.clamp(float("nan")) == 1.0


@pytest.fixture
def window(make_window, tmp_path):
    s = pat_settings()
    s.use_ai = s.open_after = False
    s.output_dir = str(tmp_path / "out")
    return make_window(s)


def test_zoom_in_out_and_back_is_saved_and_resizes_fixed_widgets(window):
    badge = window.rows["index_no"].badge
    assert badge.width() == 58
    window.zoom_step(1)
    window.zoom_step(1)
    assert zoom.zoom() == pytest.approx(1.2)
    assert badge.width() == round(58 * 1.2)
    assert Settings.load().zoom == pytest.approx(1.2)          # kept for next time
    assert "15pt" not in window.app.styleSheet() and "12pt" in window.app.styleSheet()
    window.zoom_step(0)                                         # Ctrl+0
    assert zoom.zoom() == 1.0 and badge.width() == 58 and Settings.load().zoom == 1.0
    window.set_zoom(0.2)  # (each step styles every window again: one step, not twenty)
    assert zoom.zoom() == zoom.MIN


def test_ctrl_wheel_zooms(window, qt):
    window.show()
    target = window.rows["case_name"].edit.viewport()
    pos = QtCore.QPointF(5, 5)
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(pos, pos, QtCore.QPoint(), QtCore.QPoint(0, 120), QtCore.Qt.NoButton,
                     QtCore.Qt.ControlModifier, QtCore.Qt.NoScrollPhase, False)
    qt.QApplication.sendEvent(target, ev)
    assert zoom.zoom() == pytest.approx(1.1)


def test_the_window_fits_a_small_screen(window, monkeypatch):
    small = QtCore.QRect(0, 0, 1280, 680)  # 1920 x 1080 at 150 %, less the taskbar
    monkeypatch.setattr(type(window), "_avail", lambda self: small)
    window._fit_minimum()
    assert window.minimumWidth() <= 1280 * 0.95 and window.minimumHeight() <= 680 * 0.9
    window.resize(1320, 860)  # the old default size (or one saved on a bigger screen)
    window._fit_screen()
    assert window.height() <= 680 and window.width() <= 1280
    # the Outputs box can always be reached: the page scrolls when the window is shorter than its contents
    assert window.page_scroll.widget() is window.centralWidget().widget()


def test_the_form_never_scrolls_sideways(window, qt):
    """The Case and Order cards put their two columns one above the other when the form is too narrow for
    both (Windows scaling, a zoom): before, the Order card needed 1,050 px and the form scrolled sideways."""
    window.resize(1320, 860)
    window.show()
    for f in (1.0, 1.4):
        window.set_zoom(f)
        for _ in range(3):  # the new style sheet is applied, the cards measured again, then placed
            for _ in range(5):
                qt.QApplication.processEvents()
            window._place_pairs()
        form = window.form_scroll
        assert form.widget().minimumSizeHint().width() <= form.viewport().width(), f
    stacked = [g.itemAtPosition(0, 1) is None for g, _, _ in window._pairs]
    assert any(stacked)  # at 140 % the Order card's columns are one above the other


def test_ctrl_wheel_steps_once_a_notch_however_finely_it_turns(window, qt, monkeypatch):
    # a touchpad (or a fine wheel) sends a notch (120) in many small turns: one step, not one per turn
    window.show()
    target = window.rows["case_name"].edit.viewport()
    steps = []
    monkeypatch.setattr(window, "set_zoom", lambda f: steps.append(round(f, 2)))
    from PySide6.QtGui import QWheelEvent

    def turn(dy):
        pos = QtCore.QPointF(5, 5)
        qt.QApplication.sendEvent(target, QWheelEvent(pos, pos, QtCore.QPoint(), QtCore.QPoint(0, dy),
                                                      QtCore.Qt.NoButton, QtCore.Qt.ControlModifier,
                                                      QtCore.Qt.NoScrollPhase, False))
    for _ in range(10):
        turn(12)
    assert steps == [1.1]
    for _ in range(5):
        turn(12)  # half a notch more: nothing yet
    turn(-12)  # the other way: counted afresh
    assert steps == [1.1]
    turn(-120)
    assert steps == [1.1, 0.9]


def test_the_form_follows_the_splitter(window, qt):
    """Dragging the splitter narrows the form: its cards' columns go one above the other, as when the window
    is made narrower (before, only a window resize or a zoom placed them)."""
    window.resize(1320, 860)
    window.show()
    form = window.form_scroll
    for _ in range(5):
        qt.QApplication.processEvents()
    side_by_side = form.widget().minimumSizeHint().width()
    assert side_by_side <= form.viewport().width() and not any(g.itemAtPosition(0, 1) is None
                                                               for g, _, _ in window._pairs)
    split = form.parentWidget()
    total = sum(split.sizes())
    split.setSizes([total - side_by_side + 60, side_by_side - 60])  # the form a little too narrow for that
    for _ in range(5):
        qt.QApplication.processEvents()
    assert any(g.itemAtPosition(0, 1) is None for g, _, _ in window._pairs)
    assert form.widget().minimumSizeHint().width() <= form.viewport().width()
