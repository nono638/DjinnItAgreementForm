"""Small building blocks shared by the windows: pictures, opening files, combo boxes, checkbox rows, errors."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QMessageBox, QWidget

from ..log import error as log_error

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def repolish(w: QWidget) -> None:
    """Re-applies the style sheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)


def rounded(path: Path, width: int, radius: int):
    """Scaled pixmap with rounded corners (HiDPI aware)."""
    from PySide6.QtGui import QPainter, QPainterPath, QPixmap
    src = QPixmap(str(path))
    if src.isNull():
        return src
    dpr = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    src = src.scaledToWidth(int(width * dpr), Qt.SmoothTransformation)
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, src.width(), src.height(), radius * dpr, radius * dpr)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def open_path(path) -> None:
    """Opens a file or folder with its usual program; nothing happens when it no longer exists."""
    if path and Path(path).exists():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def refill_combo(combo: QComboBox, items, keep=None, tips=None) -> None:
    """Replaces a combo box's (label, data) items without firing change signals, then selects the item whose
    data is `keep` (else the first). tips: a tooltip per item, or None."""
    with QSignalBlocker(combo):
        combo.clear()
        for i, (label, data) in enumerate(items):
            combo.addItem(label, data)
            if tips and tips[i]:
                combo.setItemData(i, tips[i], Qt.ToolTipRole)
        combo.setCurrentIndex(max(0, combo.findData(keep)))


def check_row(items: dict[str, str], checked, on_toggle=None, spacing: int = 6) -> tuple[QHBoxLayout, dict]:
    """A row of checkboxes, one per key -> label, ticked for the keys in `checked`."""
    row, boxes = QHBoxLayout(), {}
    for key, label in items.items():
        cb = QCheckBox(label)
        cb.setChecked(key in checked)
        if on_toggle:
            cb.toggled.connect(on_toggle)
        boxes[key] = cb
        row.addWidget(cb)
        row.addSpacing(spacing)
    return row, boxes


def set_checks(boxes: dict[str, QCheckBox], checked) -> None:
    """Ticks exactly the boxes whose key is in `checked`, without firing their signals."""
    for key, cb in boxes.items():
        with QSignalBlocker(cb):
            cb.setChecked(key in checked)


def show_save_error(parent: QWidget, e: Exception, what: str = "Could not save", note: str = "") -> None:
    """Tells the user why a file could not be written. A locked file (open in Excel or a PDF viewer) is the
    usual reason and is said plainly; anything else goes to the log too. note: added below the message."""
    if isinstance(e, PermissionError):
        QMessageBox.warning(parent, what, f"{e}\n\nIs the file open in another program (a PDF viewer or Excel)?"
                            + note)
    else:
        log_error(what.lower(), e)
        QMessageBox.critical(parent, what, f"{type(e).__name__}: {e}{note}")


def plural(n: int, word: str) -> str:
    """plural(1, 'file') -> '1 file'; plural(3, 'file') -> '3 files'."""
    return f"{n} {word}{'' if n == 1 else 's'}"
