"""The preview of many files (Generate all of a trial): its tabs scroll, with arrows styled in both themes; "Files ▾"
lists every file to go to it (and is hidden for one file); Ctrl+Tab / Ctrl+Shift+Tab and Ctrl+PgDn / Ctrl+PgUp go
from file to file; side by side scrolls across (a bar along the bottom, Shift and the wheel, the wheel alone when
there is nothing to scroll down), Ctrl and the wheel still zooming. The files are made up."""
import pymupdf
import pytest

from helpers import pat_settings


def files(folder, n=20):
    """n one-page PDFs, an agreement, an invoice and a MOFR in turn, marked as this app marks them."""
    from minute_filler.fill import mark
    out = []
    for i in range(n):
        kind, name = (("agreement", "Minute Agreement"), ("invoice", "Invoice"), ("MOFR", "MOFR"))[i % 3]
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), f"{name} {i}: Jane Roe v. Sam Poe", fontsize=12)
        mark(doc, kind)
        path = folder / f"{name} - Jane Roe v. Sam Poe - Alex B. Counsel - {i}.pdf"
        doc.save(path)
        out.append(path)
    return out


@pytest.fixture
def preview(qt, tmp_path):
    from minute_filler.gui.preview import PreviewDialog
    dlg = PreviewDialog(files(tmp_path), pat_settings())
    dlg.resize(900, 650)
    dlg.show()
    qt.QApplication.processEvents()
    yield dlg
    dlg.close()


def wheel(qt, widget, dy=0, dx=0, modifiers=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(dx, dy), Qt.NoButton,
                        modifiers if modifiers is not None else Qt.NoModifier, Qt.NoScrollPhase, False)
    qt.QApplication.sendEvent(widget, event)


def test_the_tabs_scroll_and_the_files_menu_goes_to_any_file(preview, qt):
    bar = preview.tabs.tabBar()
    assert preview.tabs.usesScrollButtons() and bar.count() == 20
    assert sum(bar.tabRect(i).width() for i in range(20)) > bar.width()  # (more than fit across)
    arrows = [b for b in bar.findChildren(qt.QToolButton) if b.isVisible()]
    assert len(arrows) == 2  # the arrows to scroll the tabs
    menu = preview.files_menu.actions()
    assert len(menu) == 20 and not preview.files_btn.isHidden()
    assert menu[19].text() == "Invoice - Jane Roe v. Sam Poe - Alex B. Counsel - 19"  # the whole name
    assert not menu[0].icon().isNull()  # (a square in the tab's colour)
    menu[19].trigger()
    assert preview.tabs.currentIndex() == 19
    assert bar.tabRect(19).right() <= bar.width()  # scrolled to it


def test_ctrl_tab_goes_from_file_to_file(preview, qt):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(preview, Qt.Key_Tab, Qt.ControlModifier)
    assert preview.tabs.currentIndex() == 1
    QTest.keyClick(preview, Qt.Key_PageDown, Qt.ControlModifier)
    assert preview.tabs.currentIndex() == 2
    QTest.keyClick(preview, Qt.Key_PageUp, Qt.ControlModifier)
    QTest.keyClick(preview, Qt.Key_Backtab, Qt.ControlModifier | Qt.ShiftModifier)  # (Ctrl+Shift+Tab)
    assert preview.tabs.currentIndex() == 0
    preview.next_file(-1)  # round to the last
    assert preview.tabs.currentIndex() == 19
    # Qt keeps the modifiers of the last key event as held: let go, or the next tests' selectRow() would toggle
    # rows as with Ctrl held
    QTest.keyRelease(preview, Qt.Key_Shift)
    QTest.keyRelease(preview, Qt.Key_Control)


def test_side_by_side_scrolls_across(preview, qt):
    from PySide6.QtCore import Qt
    preview.side.setChecked(True)
    qt.QApplication.processEvents()
    scroll = preview.side_scroll
    across = scroll.horizontalScrollBar()
    assert across.maximum() > 0 and across.isVisible()
    # one page each: nothing to scroll down, so the wheel alone scrolls across
    assert scroll.verticalScrollBar().maximum() == 0
    wheel(qt, scroll.viewport(), dy=-120)
    assert across.value() > 0
    start = across.value()
    wheel(qt, scroll.viewport(), dy=120, modifiers=Qt.ShiftModifier)  # Shift and the wheel: back
    assert across.value() < start
    for _ in range(200):  # to the end: the last column comes into view
        wheel(qt, scroll.viewport(), dy=-120)
    assert across.value() == across.maximum()
    last = preview.side_titles[-1]
    assert last.x() - across.value() + last.width() <= scroll.viewport().width()
    zoom = preview.zoom
    wheel(qt, scroll.viewport(), dy=120, modifiers=Qt.ControlModifier)  # Ctrl and the wheel still zoom
    assert preview.zoom > zoom


def test_the_files_menu_side_by_side_brings_the_column_into_view(preview, qt):
    preview.side.setChecked(True)
    qt.QApplication.processEvents()
    preview.files_menu.actions()[15].trigger()
    across = preview.side_scroll.horizontalScrollBar()
    title = preview.side_titles[15]
    assert 0 <= title.x() - across.value() < preview.side_scroll.viewport().width()


def test_one_file_has_no_files_button(qt, tmp_path):
    from minute_filler.gui.preview import PreviewDialog
    dlg = PreviewDialog(files(tmp_path, 1), pat_settings())
    assert dlg.files_btn.isHidden()


def test_the_scroll_arrows_are_styled_in_both_themes(qt):
    from minute_filler.gui.theme import QSS
    assert "QTabBar QToolButton {" in QSS and "QScrollBar:horizontal" in QSS
