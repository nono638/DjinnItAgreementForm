"""The preview before saving, the math of the invoices made, printing, and the first-run welcome.

PreviewDialog shows the files Generate is about to make as pictures of their pages; nothing is saved until the
user says so (MainWindow.fill makes them in a temporary folder first, with a records database of its own, so
no invoice number is taken). Its "Don't show previews anymore" turns the preview off (Settings -> Options turns
it back on).

print_files sends PDFs to a printer through Qt's own print box: each page is drawn as a picture (300 dpi),
so it prints the same whatever PDF program the computer has (Edge, the default one, can't be asked to print a
file). A page is printed at its actual size when the paper is big enough for it (a court form is not shrunk),
else made to fit. Other files (the Excel run sheet) are handed to their own program's Print.

MathDialog spells out the math of the invoices Generate just made (invoice_math.explain): a glance and OK. It can
also copy the text or save it as a PDF, and its "Don't show this anymore" turns it off (Settings -> Options
turns it back on).

WelcomeDialog asks a new user the few things the forms can't do without.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..log import error as log_error
from ..settings import Settings
from .zoom import z

PREVIEW_DPI = 110  # the pages' pictures at 100 % zoom: a letter page is about 935 px wide
PRINT_DPI = 300    # the pages as sent to the printer: the usual resolution for printed text
OFF_NOTE = "Previews are off from now on. Settings → Options turns them back on."
MATH_OFF_NOTE = "Off from now on. Settings → Options turns it back on."


def page_image(page, dpi: int) -> QImage:
    """A page of an open PDF (a pymupdf Page) as a picture at `dpi`, the filled-in fields included."""
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()  # (its own memory)


def page_images(pdf: Path | str, dpi: int) -> list[QImage]:
    """Each page of a PDF as a picture at `dpi`."""
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return [page_image(page, dpi) for page in doc]


def print_rect(printer, width: float, height: float) -> QRect:
    """Where on the paper a page of width x height points (1/72 inch) is drawn, in the printer's dots (with
    QPrinter.setFullPage(True): counted from the paper's corner). At its actual size, centred, when the paper
    is big enough (a letter page on letter paper: a form stays the size the court made it); a page bigger than
    the paper is made to fit the part of it the printer can print on."""
    layout = printer.pageLayout()
    paper = layout.fullRectPixels(printer.resolution())
    dots = printer.resolution() / 72
    w, h = width * dots, height * dots
    if w > paper.width() * 1.01 or h > paper.height() * 1.01:  # (1 %: letter and "letter" differ by a dot)
        # the paper less the printer's margins (worked out here: with setFullPage the layout's own "paint
        # rectangle" is the whole paper)
        area = paper.marginsRemoved(layout.marginsPixels(printer.resolution()))
        scale = min(area.width() / w, area.height() / h)
        w, h = w * scale, h * scale
        return QRect(round(area.x() + (area.width() - w) / 2), round(area.y()), round(w), round(h))
    return QRect(round((paper.width() - w) / 2), round((paper.height() - h) / 2), round(w), round(h))


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


class MathDialog(QDialog):
    """How the amounts of the invoices just made were reached, line by line. OK closes it; Copy all puts it on
    the clipboard as text; Save as PDF... saves it (starting in `folder`, the invoices' folder); "Don't show
    this anymore" sets Settings.show_math off at once (and saves the settings)."""

    def __init__(self, made: list, s: Settings, parent=None, folder: Path | None = None):
        """made: (FirmInvoice, invoice number) for each invoice made (deliver.generate's `math`)."""
        from ..invoice_math import explain
        super().__init__(parent)
        self.s, self.made, self.folder = s, made, folder
        self.html, self.plain = explain(made)
        self.setWindowTitle("The math")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        shown = all(f.opts.detail for f, _ in made)  # (granular detail ticked: the invoice shows it too)
        head = QLabel("How the amounts on " + ("this invoice" if len(made) == 1 else "these invoices")
                      + " were reached." + ("" if shown else " The invoice itself shows only the amounts."))
        head.setWordWrap(True)
        lay.addWidget(head)
        self.text = QTextBrowser()
        self.text.document().setDefaultStyleSheet("p {margin: 0 0 3px 0;} h2 {margin: 14px 0 2px 0;} "
                                                  "h3 {margin: 8px 0 3px 0;}")  # (a line each, close together)
        self.text.setHtml(self.html)
        lay.addWidget(self.text, 1)

        row = QHBoxLayout()
        self.off = QPushButton("Don't show this anymore")
        self.off.setFlat(True)
        self.off.setToolTip("Settings → Options turns it back on.")
        self.off.clicked.connect(self._turn_off)
        row.addWidget(self.off)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        row.addWidget(self.note)
        row.addStretch(1)
        copy = QPushButton("Copy all")
        copy.setToolTip("Put all of it on the clipboard as text, to paste into an e-mail")
        copy.clicked.connect(self._copy)
        save = QPushButton("Save as PDF…")
        save.clicked.connect(self._save)
        ok = QPushButton("OK")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        for b in (self.off, copy, save):
            b.setAutoDefault(False)
        for b in (copy, save, ok):
            row.addWidget(b)
        lay.addLayout(row)
        self.resize(z(620), z(520))

    def _copy(self) -> None:
        """Copy all: the math as plain text on the clipboard."""
        QApplication.clipboard().setText(self.plain)
        self.note.setText("Copied.")

    def default_name(self) -> str:
        """'Invoice 2026-0012 - the math.pdf' ('Invoices 2026-0012 to 2026-0013 - ...' for several)."""
        numbers = [n for _, n in self.made]
        if len(numbers) == 1:
            name = f"Invoice {numbers[0]}"
        else:
            name = f"Invoices {numbers[0]} to {numbers[-1]}" if numbers else "Invoice"
        return f"{name} - the math.pdf"

    def _save(self) -> None:
        """Save as PDF...: asks where, then saves it there."""
        from ..invoice_math import to_pdf
        from ..fill import safe_filename
        folder = self.folder or self.s.records_folder()
        p, _ = QFileDialog.getSaveFileName(self, "Save the math", str(Path(folder) / safe_filename(self.default_name())),
                                           "PDF (*.pdf)")
        if not p:
            return
        try:
            out = to_pdf(self.html, Path(p))
        except Exception as e:
            log_error("could not save the math", e)
            QMessageBox.warning(self, "Not saved", f"The PDF could not be saved ({type(e).__name__}: {e}).")
            return
        self.note.setText(f"Saved as {out.name}.")

    def _turn_off(self) -> None:
        """No more of these: saved in the settings now."""
        self.s.show_math = False
        try:
            self.s.save()
        except OSError as e:
            log_error("could not save the settings", e)
        self.off.setVisible(False)
        self.note.setText(MATH_OFF_NOTE)


def print_files(parent, files: list, printer=None) -> int:
    """Prints files and returns how many were sent. PDFs go to one printer, chosen once in the print box
    (printer: a QPrinter to use without asking, for the tests), every page of them: the box offers no page
    range. Each page is drawn at 300 dpi at its actual size (see print_rect), upright or sideways as the page
    is; the copies asked for are made here when the printer can't make them itself. Other files (the Excel run
    sheet) are handed to their own program's Print. Says so when a file could not be printed, or the printer
    stopped."""
    import pymupdf
    from PySide6.QtGui import QPageLayout
    from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
    files = [Path(f) for f in files if f and Path(f).exists()]
    pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
    others = [f for f in files if f not in pdfs]
    sent, failed, stopped = 0, [], False
    if pdfs:
        if printer is None:
            printer = QPrinter(QPrinter.HighResolution)  # (the printer's own resolution, not the screen's)
            dlg = QPrintDialog(printer, parent)
            dlg.setWindowTitle(f"Print {len(pdfs)} file{'' if len(pdfs) == 1 else 's'}")
            dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, False)
            if dlg.exec() != QDialog.Accepted:
                return 0
        printer.setFullPage(True)  # positions are counted from the paper's corner (print_rect)
        # the job's name in the printer's queue
        printer.setDocName(pdfs[0].stem + (f" and {len(pdfs) - 1} more" if len(pdfs) > 1 else ""))
        # most printers make the copies themselves; for one that can't, the pages are sent that many times
        copies = 1 if printer.supportsMultipleCopies() else max(1, printer.copyCount())
        painter = QPainter()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for copy in range(copies):
                for f in pdfs:
                    if stopped or f.name in failed:
                        continue
                    try:
                        with pymupdf.open(f) as doc:
                            for page in doc:  # a page at a time: a long transcript is not held whole in memory
                                printer.setPageOrientation(QPageLayout.Landscape if page.rect.width > page.rect.height
                                                           else QPageLayout.Portrait)
                                if not painter.isActive():
                                    stopped = not painter.begin(printer)
                                else:
                                    stopped = not printer.newPage()
                                if stopped or printer.printerState() in (QPrinter.Aborted, QPrinter.Error):
                                    stopped = True
                                    break
                                # the printer scales the picture to its own resolution: scaled here to a 1200 dpi
                                # page it would be 350 MB, and take seconds
                                painter.drawImage(print_rect(printer, page.rect.width, page.rect.height),
                                                  page_image(page, PRINT_DPI))
                    except Exception as e:
                        log_error("could not print a file", e)
                        failed.append(f.name)
                        continue
                    if copy == 0 and not stopped:
                        sent += 1
        finally:
            if painter.isActive():
                painter.end()
            QApplication.restoreOverrideCursor()
        if stopped:
            QMessageBox.warning(parent, "Print", "The printer stopped before everything was printed (is it on, "
                                "and does it have paper?).")
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
