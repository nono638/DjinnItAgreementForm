"""The preview before saving, printing, and the first-run welcome.

PreviewDialog shows the files Generate is about to make as pictures of their pages; nothing is saved until the
user says so (MainWindow.fill makes them in a temporary folder first, with a records database of its own, so
no invoice number is taken). Its "Don't show previews anymore" turns the preview off (Settings -> Options turns
it back on).

print_files sends PDFs to a printer through Qt's own print box: each page is drawn as a picture, so it prints
the same whatever PDF program the computer has (Edge, the default one, can't be asked to print a file). Other
files (the Excel run sheet) are handed to their own program's Print.

WelcomeDialog asks a new user the few things the forms can't do without.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from ..log import error as log_error
from ..settings import Settings
from .zoom import z

PREVIEW_DPI = 110  # the pages' pictures at 100 % zoom: a letter page is about 935 px wide
PRINT_DPI = 200    # the pages as sent to the printer
OFF_NOTE = "Previews are off from now on. Settings → Options turns them back on."


def page_images(pdf: Path | str, dpi: int) -> list[QImage]:
    """Each page of a PDF as a picture at `dpi`, the filled-in fields included."""
    import pymupdf
    out = []
    with pymupdf.open(pdf) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            out.append(img.copy())  # (its own memory: pix is gone after this loop)
    return out


class PreviewDialog(QDialog):
    """The files about to be saved, a tab each, their pages as pictures. accept() = save them. Clicking
    "Don't show previews anymore" sets Settings.preview_before_saving off at once (and saves the settings); the
    box stays open for the answer about these files."""

    def __init__(self, files: list[Path], s: Settings, parent=None, note: str = ""):
        """files: the PDFs to show; note: a line under the heading (e.g. that the run sheet is not shown)."""
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Preview")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        head = QLabel("This is what will be saved. <b>Nothing is saved yet.</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        if note:
            more = QLabel(note)
            more.setObjectName("muted")
            more.setWordWrap(True)
            lay.addWidget(more)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.pages = 0
        # as big as a letter page at this zoom, but never more than the screen has room for; the pages are
        # drawn to fit its width, so only up and down is scrolled
        screen = self.screen() or (parent.screen() if parent is not None else None)
        avail = screen.availableGeometry() if screen is not None else None
        w, h = z(1010), z(900)
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.95)), min(h, int(avail.height() * 0.9))
        widest = w - z(80)  # (margins, the frame and the scroll bar)
        for f in files:
            page = QWidget()
            col = QVBoxLayout(page)
            col.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            try:
                images = page_images(f, z(PREVIEW_DPI)) if Path(f).suffix.lower() == ".pdf" else []
            except Exception as e:  # the other files are still shown
                log_error("could not draw a preview", e)
                images = []
            for img in images:
                pic = QLabel()
                if img.width() > widest:
                    img = img.scaledToWidth(widest, Qt.SmoothTransformation)
                pic.setPixmap(QPixmap.fromImage(img))
                pic.setFrameShape(QLabel.Box)
                col.addWidget(pic)
            if not images:
                col.addWidget(QLabel("(no preview of this file)"))
            self.pages += len(images)
            scroll = QScrollArea()
            scroll.setWidget(page)
            scroll.setAlignment(Qt.AlignHCenter)
            name = Path(f).stem
            name = name if len(name) <= 34 else name[:33].rstrip() + "…"
            self.tabs.addTab(scroll, name.replace("&", "&&"))  # (a single & would be read as a shortcut mark)
            self.tabs.setTabToolTip(self.tabs.count() - 1, Path(f).name)

        row = QHBoxLayout()
        self.off = QPushButton("Don't show previews anymore")
        self.off.setFlat(True)
        self.off.setToolTip("Generate then saves at once, as before. Settings → Options turns previews back on.")
        self.off.clicked.connect(self._turn_off)
        row.addWidget(self.off)
        self.off_note = QLabel("")
        self.off_note.setObjectName("muted")
        row.addWidget(self.off_note)
        row.addStretch(1)
        back = QPushButton("Go back")
        back.setToolTip("Save nothing and return to the job")
        back.clicked.connect(self.reject)
        self.save = QPushButton("Save")
        self.save.setObjectName("primary")
        self.save.setDefault(True)
        self.save.clicked.connect(self.accept)
        for b in (self.off, back):
            b.setAutoDefault(False)
        row.addWidget(back)
        row.addWidget(self.save)
        lay.addLayout(row)
        self.resize(w, h)

    def _turn_off(self) -> None:
        """No more previews: saved in the settings now, whatever is answered about these files."""
        self.s.preview_before_saving = False
        try:
            self.s.save()
        except OSError as e:
            log_error("could not save the settings", e)
        self.off.setVisible(False)
        self.off_note.setText(OFF_NOTE)


def print_files(parent, files: list, printer=None) -> int:
    """Prints files and returns how many were sent. PDFs go to one printer, chosen once in the print box
    (printer: a QPrinter to use without asking, for the tests), every page of them: the box offers no page
    range. Other files (the Excel run sheet) are handed to their own program's Print. Says so when a file
    could not be printed."""
    from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
    files = [Path(f) for f in files if f and Path(f).exists()]
    pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
    others = [f for f in files if f not in pdfs]
    sent, failed = 0, []
    if pdfs:
        if printer is None:
            printer = QPrinter(QPrinter.HighResolution)
            dlg = QPrintDialog(printer, parent)
            dlg.setWindowTitle(f"Print {len(pdfs)} file{'' if len(pdfs) == 1 else 's'}")
            dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, False)
            if dlg.exec() != QDialog.Accepted:
                return 0
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(parent, "Print", "The printer could not be started.")
            return 0
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            first = True
            for f in pdfs:
                try:
                    images = page_images(f, PRINT_DPI)
                except Exception as e:
                    log_error("could not print a file", e)
                    failed.append(f.name)
                    continue
                for img in images:
                    if not first:
                        printer.newPage()
                    first = False
                    area = painter.viewport()
                    size = img.size().scaled(area.size(), Qt.KeepAspectRatio)
                    # the printer scales the picture: scaled here to a 1200 dpi page it is 350 MB, and seconds
                    painter.drawImage(QRect(area.x() + (area.width() - size.width()) // 2, area.y(),
                                            size.width(), size.height()), img)
                sent += 1
        finally:
            painter.end()
            QApplication.restoreOverrideCursor()
    for f in others:
        try:
            os.startfile(str(f), "print")  # (Windows: the file's own program prints it)
            sent += 1
        except (OSError, AttributeError) as e:
            log_error("could not print a file", e)
            failed.append(f.name)
    if failed:
        QMessageBox.warning(parent, "Print", "Could not print:\n\n" + "\n".join(failed)
                            + "\n\nOpen the file and print it from its own program.")
    return sent


class WelcomeDialog(QDialog):
    """First run: the few things the forms need (who the reporter is), the rate sheet and where files go.
    accept() ("Start") writes them into the Settings object and saves it; "Skip for now" changes nothing.
    Everything here is in Settings too."""

    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Welcome")
        self.setMinimumWidth(z(520))
        lay = QVBoxLayout(self)
        hello = QLabel("<b>Welcome!</b> A few details, written on every form and invoice. "
                       "You can change them, and much more, under Settings at any time.")
        hello.setWordWrap(True)
        lay.addWidget(hello)
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        p = s.profile
        self.name = QLineEdit(p.name)
        self.name.setPlaceholderText("as it should appear on the forms")
        self.initials = QLineEdit(p.initials)
        self.initials.setPlaceholderText("as at the foot of your transcript pages; blank = from your name")
        self.address1, self.address2 = QLineEdit(p.address1), QLineEdit(p.address2)
        self.address1.setPlaceholderText("e.g. Supreme Court, 123 Courthouse Plaza, Room 100")
        self.address2.setPlaceholderText("e.g. Anytown, NY 10000")
        self.phone, self.email = QLineEdit(p.phone), QLineEdit(p.email)
        self.sheet = QComboBox()
        for sh in s.sheets()[0]:
            self.sheet.addItem(sh.name, sh.name)
        self.sheet.setCurrentIndex(max(0, self.sheet.findData(s.sheet().name)))
        self.sheet.setToolTip("Your prices per page. Sheets are small files you can edit in Excel:\n"
                              "File → Open rate sheets folder.")
        self.folder = QLineEdit(s.output_dir)
        self.folder.setPlaceholderText("blank = next to the document you dropped")
        pick = QPushButton("Browse…")
        pick.setAutoDefault(False)
        pick.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        for label, w in (("Your name", self.name), ("Initials", self.initials), ("Address", self.address1),
                         ("", self.address2), ("Telephone", self.phone), ("E-mail", self.email),
                         ("Rate sheet", self.sheet), ("Save files to", row)):
            f.addRow(label, w)
        lay.addLayout(f)
        bb = QDialogButtonBox()
        start = bb.addButton("Start", QDialogButtonBox.AcceptRole)
        start.setObjectName("primary")
        start.setDefault(True)
        bb.addButton("Skip for now", QDialogButtonBox.RejectRole).setAutoDefault(False)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _pick(self) -> None:
        """Browse...: asks for the folder to save files to."""
        d = QFileDialog.getExistingDirectory(self, "Save files to", self.folder.text())
        if d:
            self.folder.setText(d)

    def accept(self):
        """Start: the answers go into the settings, which are saved."""
        s, p = self.s, self.s.profile
        p.name, p.phone, p.email = self.name.text().strip(), self.phone.text().strip(), self.email.text().strip()
        p.address1, p.address2 = self.address1.text().strip(), self.address2.text().strip()
        p.initials = self.initials.text().strip().lower().replace(".", "").replace(" ", "")
        s.rate_sheet = self.sheet.currentData() or s.rate_sheet
        s.output_dir = self.folder.text().strip()
        try:
            s.save()
        except OSError as e:
            log_error("could not save the settings", e)
        super().accept()
