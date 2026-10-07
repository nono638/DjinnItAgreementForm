"""The preview before saving, the math of the invoices made, printing, and the first-run welcome.

PreviewDialog shows the files Generate is about to make as pictures of their pages; nothing is saved until the
user says so (MainWindow.fill makes them in a temporary folder first, with a records database of its own, so
no invoice number is taken). With invoices among the files, its first tab is "The math" (math_view): how each
amount is reached, shown before anything is saved. Each kind of file has a colour of its own (KIND_COLORS, told
by file_kind from the mark in the PDF) on its tab (ColorTabBar); with many files the tabs scroll (arrows at the
end of the bar), "Files ▾" lists every file to go to it, and Ctrl+Tab / Ctrl+Shift+Tab (Ctrl+PgDn / Ctrl+PgUp)
go to the next one and back; "Side by side" shows every file at once, scrolled across with the bar along the
bottom, Shift and the wheel (or the wheel alone when there is nothing to scroll down); the preview has its own
zoom (Ctrl + / Ctrl - / Ctrl 0, Ctrl and the mouse wheel). Its "Don't show previews anymore" turns the preview
off (Settings -> Options turns it back on). Generate all shows one too, for every file of the batch.

print_files sends PDFs to a printer through Qt's own print box: each page is drawn as a picture (300 dpi),
so it prints the same whatever PDF program the computer has (Edge, the default one, can't be asked to print a
file). A page is printed at its actual size when the paper is big enough for it (a court form is not shrunk),
else made to fit. Other files (the Excel run sheet) are handed to their own program's Print.

MathDialog spells out the math of the invoices Generate just made, when previews are off (invoice_math.explain):
a glance and OK. It can also copy the text or save it as a PDF, and its "Don't show this anymore" turns it off
(Settings -> Options turns it back on). The main window's "Who pays what" opens it too (live=True), on the
invoices as they would be made now, with no numbers yet.

WelcomeDialog asks a new user the few things the forms can't do without.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QAbstractScrollArea, QMenu, QPushButton, QScrollArea, QStackedWidget, QTabBar, QTabWidget, QTextBrowser,
    QToolButton, QVBoxLayout, QWidget,
)

from ..log import error as log_error
from ..settings import Settings
from .zoom import z

PREVIEW_DPI = 110  # the pages' pictures at 100 % zoom: a letter page is about 935 px wide
PRINT_DPI = 300    # the pages as sent to the printer: the usual resolution for printed text
OFF_NOTE = "Previews are off from now on. Settings → Options turns them back on."
MATH_OFF_NOTE = "Off from now on. Settings → Options turns it back on."
# the math's lines: a line each, close together
MATH_CSS = "p {margin: 0 0 3px 0;} h2 {margin: 14px 0 2px 0;} h3 {margin: 8px 0 3px 0;}"


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


# Each kind of file has a colour of its own: its tab, and its heading side by side (the math first)
KIND_COLORS = {"math": "#0d9488", "agreement": "#2563eb", "invoice": "#16a34a", "detailed": "#84cc16",
               "MOFR": "#9333ea", "other": "#64748b"}
KIND_NAMES = {"math": "The math", "agreement": "Minute agreement", "invoice": "Invoice",
              "detailed": "Invoice (detailed copy)", "MOFR": "MOFR", "other": "File"}
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.35, 2.5, 1.15  # the preview's own zoom (Ctrl + / Ctrl - / Ctrl 0, Ctrl+wheel)
SIDE_WIDTH = 300  # a page's width side by side at zoom 1 (px at 100 % app zoom): several files fit across


def file_kind(path: Path | str) -> str:
    """Which kind of file this app made (a key of KIND_COLORS): from the mark it puts in each PDF (fill.mark),
    "detailed" for an invoice's detailed copy; "other" when it can't tell."""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            creator = (doc.metadata or {}).get("creator", "")
    except Exception:
        return "other"
    from ..fill import MARK, OLD_MARKS
    kind = next((creator.removeprefix(m) for m in (MARK, *OLD_MARKS) if creator.startswith(m)), "").strip()
    if kind == "invoice" and Path(path).stem.endswith("(detailed)"):
        return "detailed"
    return kind if kind in KIND_COLORS else "other"


class ColorTabBar(QTabBar):
    """A tab bar whose tabs are tinted with the colour in their tab data (a "#rrggbb"), with a bar of it along
    the bottom: stronger on the tab shown."""

    def paintEvent(self, _e) -> None:
        from PySide6.QtWidgets import QStyle, QStyleOptionTab, QStylePainter
        painter = QStylePainter(self)
        for i in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, i)
            painter.drawControl(QStyle.CE_TabBarTabShape, opt)
            color = self.tabData(i)
            if color:
                c = QColor(color)
                selected = i == self.currentIndex()
                c.setAlpha(95 if selected else 45)
                r = opt.rect.adjusted(1, 1, -1, 0)
                painter.fillRect(r, c)
                c.setAlpha(255)
                painter.fillRect(r.adjusted(0, r.height() - (4 if selected else 2), 0, 0), c)
            painter.drawControl(QStyle.CE_TabBarTabLabel, opt)


class PreviewDialog(QDialog):
    """The files about to be saved, a tab each, their pages as pictures; each kind of file (agreement, invoice,
    MOFR, the math) has a colour of its own (KIND_COLORS). "Files ▾" and Ctrl+Tab go from file to file (go_to).
    "Side by side" shows every file at once, next to each other and smaller; Ctrl + / Ctrl - / Ctrl 0 (or Ctrl
    and the mouse wheel) zoom either view. accept() = save them. Clicking "Don't show previews anymore" sets
    Settings.preview_before_saving off at once (and saves the settings); the box stays open for the answer about
    these files."""

    def __init__(self, files: list[Path], s: Settings, parent=None, note: str = "", math: list | None = None):
        """files: the files to show (only PDFs get pictures); note: a line under the heading (e.g. that the run
        sheet is not shown); math: (FirmInvoice, invoice number) of each invoice about to be saved, shown on a
        first tab, "The math" (see math_view), when there are any."""
        from PySide6.QtGui import QKeySequence, QShortcut
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Preview")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        head = QLabel("This is what will be saved. <b>Nothing is saved yet.</b>")
        head.setWordWrap(True)
        top.addWidget(head, 1)
        self.zoom_label = QLabel("")
        self.zoom_label.setObjectName("muted")
        self.zoom_label.setToolTip("Ctrl + and Ctrl - zoom, Ctrl 0 goes back (or Ctrl and the mouse wheel)")
        top.addWidget(self.zoom_label)
        self.side = QPushButton("Side by side")
        self.side.setCheckable(True)
        self.side.setAutoDefault(False)
        self.side.setToolTip("Every file at once, next to each other (smaller): Ctrl + and Ctrl - zoom")
        self.side.toggled.connect(self._side_by_side)
        # every file in a list, to go to one (with many files the tabs don't all fit across)
        self.files_btn = QToolButton()
        self.files_btn.setText("Files ▾")
        self.files_btn.setObjectName("quick")
        self.files_btn.setPopupMode(QToolButton.InstantPopup)
        self.files_btn.setToolTip("Go to a file (Ctrl+Tab and Ctrl+Shift+Tab, or Ctrl+PgDn and Ctrl+PgUp, go to "
                                  "the next and the one before)")
        self.files_menu = QMenu(self.files_btn)
        self.files_btn.setMenu(self.files_menu)
        top.addWidget(self.files_btn)
        top.addWidget(self.side)
        lay.addLayout(top)
        if note:
            more = QLabel(note)
            more.setObjectName("muted")
            more.setWordWrap(True)
            lay.addWidget(more)
        self.tabs = QTabWidget()
        self.tabs.setTabBar(ColorTabBar())
        # with many files the tabs scroll, with arrows at the end of the bar (Windows' style would rather squeeze
        # them, and with the style sheet they ran off the edge instead)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        lay.addWidget(self.stack, 1)
        self.pages = 0
        self.zoom = 1.0
        # the pages' pictures, to draw again at each zoom: (label, image, width at zoom 1, side by side)
        self.pics: list[tuple[QLabel, QImage, int, bool]] = []
        self.texts: list[QTextBrowser] = []  # the math, zoomed with the pages
        self.side_titles: list[QLabel] = []  # side by side: the heading of each column, in the tabs' order
        self.side_scroll: QScrollArea | None = None
        # as big as a letter page at this zoom, but never more than the screen has room for; the pages are
        # drawn to fit its width, so only up and down is scrolled
        screen = self.screen() or (parent.screen() if parent is not None else None)
        avail = screen.availableGeometry() if screen is not None else None
        w, h = z(1010), z(900)
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.95)), min(h, int(avail.height() * 0.9))
        widest = w - z(80)  # (margins, the frame and the scroll bar)
        self.math = None
        shown: list[tuple[str, str, list[QImage], str]] = []  # (name, kind, pages, tooltip) of each file
        if math:
            self.math = math_view(math, "How the amounts on " + ("this invoice" if len(math) == 1 else "these invoices")
                                  + " are reached. The numbers are the ones they get when you click Save.")
            self.texts.append(self.math.text)
            self._add_tab(self.math, "The math", "math", "How each amount is reached, line by line")
        for f in files:
            page = QWidget()
            col = QVBoxLayout(page)
            col.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            try:  # (drawn sharper than shown, so zooming in stays clear)
                images = page_images(f, int(z(PREVIEW_DPI) * 1.5)) if Path(f).suffix.lower() == ".pdf" else []
            except Exception as e:  # the other files are still shown
                log_error("could not draw a preview", e)
                images = []
            for img in images:
                pic = QLabel()
                pic.setFrameShape(QLabel.Box)
                self.pics.append((pic, img, min(img.width() * 2 // 3, widest), False))
                col.addWidget(pic)
            if not images:
                col.addWidget(QLabel("(no preview of this file)"))
            self.pages += len(images)
            scroll = QScrollArea()
            scroll.setWidget(page)
            scroll.setWidgetResizable(False)
            scroll.setAlignment(Qt.AlignHCenter)
            name = Path(f).stem
            kind = file_kind(f)
            shown.append((name, kind, images, Path(f).name))
            short = name if len(name) <= 34 else name[:33].rstrip() + "…"
            self._add_tab(scroll, short.replace("&", "&&"), kind, Path(f).name, name)  # (a lone & is a shortcut mark)
        self.stack.addWidget(self._side_view(shown, math))
        self.files_btn.setVisible(self.tabs.count() > 1)

        row = QHBoxLayout()
        self.off = QPushButton("Don't show previews anymore")
        self.off.setFlat(True)
        self.off.setToolTip("Generate and Generate all then save at once. Settings → Options turns previews back "
                            "on.")
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
        for keys, step in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.zoom_step(s))
        for keys, step in (("Ctrl+Tab", 1), ("Ctrl+PgDown", 1), ("Ctrl+Shift+Tab", -1), ("Ctrl+Backtab", -1),
                           ("Ctrl+PgUp", -1)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.next_file(s))
        self._draw()
        self.resize(w, h)

    def _add_tab(self, widget: QWidget, text: str, kind: str, tip: str, line: str = "") -> None:
        """A tab in the colour of its kind of file (KIND_COLORS), its kind said in its tooltip; and its line in
        the Files menu (line: the whole file name; default the tab's text), with a square of that colour."""
        i = self.tabs.addTab(widget, text)
        self.tabs.tabBar().setTabData(i, KIND_COLORS[kind])
        self.tabs.setTabToolTip(i, f"{KIND_NAMES[kind]}: {tip}")
        self._zoom_with_wheel(widget)
        swatch = QPixmap(z(12), z(12))
        swatch.fill(QColor(KIND_COLORS[kind]))
        action = self.files_menu.addAction(QIcon(swatch), line.replace("&", "&&") or text)  # (&&: a lone &)
        action.setToolTip(f"{KIND_NAMES[kind]}: {tip}")
        action.triggered.connect(lambda _=False, n=i: self.go_to(n))

    def go_to(self, i: int) -> None:
        """Shows file number i (the tabs' order, the math first): its tab, or side by side its column."""
        if not 0 <= i < self.tabs.count():
            return
        self.tabs.setCurrentIndex(i)
        if self.side.isChecked() and i < len(self.side_titles):  # (its heading at the left edge)
            self.side_scroll.horizontalScrollBar().setValue(self.side_titles[i].x() - z(8))

    def next_file(self, step: int) -> None:
        """Ctrl+Tab / Ctrl+PgDn (step 1) and Ctrl+Shift+Tab / Ctrl+PgUp (-1): the next file, or the one before,
        round from the last to the first."""
        n = self.tabs.count()
        if n:
            self.go_to((self.tabs.currentIndex() + step) % n)

    def _side_view(self, shown: list, math: list | None) -> QWidget:
        """Side by side: a column per file (the math first), each headed by its name in its colour, its pages
        under it, scrolled across and down together."""
        from ..invoice_math import explain
        box = QWidget()
        cols = QHBoxLayout(box)
        cols.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        cols.setSpacing(z(14))

        def column(name: str, kind: str, tip: str) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignTop)
            title = QLabel(name if len(name) <= 40 else name[:39].rstrip() + "…")
            title.setToolTip(f"{KIND_NAMES[kind]}: {tip}")
            title.setStyleSheet(f"color: white; background: {KIND_COLORS[kind]}; border-radius: 4px; "
                                "padding: 3px 8px; font-weight: 600;")
            col.addWidget(title)
            self.side_titles.append(title)
            cols.addLayout(col)
            return col

        if math:
            col = column("The math", "math", "How each amount is reached")
            text = QTextBrowser()
            text.document().setDefaultStyleSheet(MATH_CSS)
            text.setHtml(explain(math)[0])
            text.setMinimumWidth(z(SIDE_WIDTH + 60))
            text.setMinimumHeight(z(560))
            self.texts.append(text)
            col.addWidget(text)
        for name, kind, images, tip in shown:
            col = column(name, kind, tip)
            for img in images:
                pic = QLabel()
                pic.setFrameShape(QLabel.Box)
                self.pics.append((pic, img, z(SIDE_WIDTH), True))
                col.addWidget(pic)
        cols.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(box)
        scroll.setWidgetResizable(True)
        # across as well as down: a bar along the bottom, Shift and the wheel, or the wheel alone when there is
        # nothing to scroll down (see eventFilter)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.side_scroll = scroll
        self._zoom_with_wheel(scroll)
        return scroll

    def _zoom_with_wheel(self, widget: QWidget) -> None:
        """Ctrl and the mouse wheel over a page, or over the math, zoom the preview (the scroll area would
        scroll instead, and the math's text box would zoom only itself, and only until the next draw)."""
        areas = [widget] if isinstance(widget, QAbstractScrollArea) else []
        for scroll in areas + widget.findChildren(QAbstractScrollArea):
            scroll.viewport().installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:
        """Ctrl and the mouse wheel over the viewports _zoom_with_wheel watches: the preview's zoom, not a
        scroll."""
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.Wheel and e.modifiers() & Qt.ControlModifier:
            self.zoom_step(1 if e.angleDelta().y() > 0 else -1)
            return True
        side = self.side_scroll
        if e.type() == QEvent.Wheel and side is not None and obj is side.viewport():
            # side by side, the columns run off to the right: Shift and the wheel scroll across, and so does the
            # wheel alone when there is nothing to scroll down (a tilt of the wheel, or a touchpad, already does)
            across, down = side.horizontalScrollBar(), side.verticalScrollBar()
            delta = e.angleDelta().y() or e.angleDelta().x()
            if delta and not e.angleDelta().x() and (e.modifiers() & Qt.ShiftModifier or down.maximum() == 0):
                across.setValue(across.value() - round(delta / 120 * max(across.singleStep() * 3, z(120))))
                return True
        return super().eventFilter(obj, e)

    def zoom_step(self, step: int) -> None:
        """Zooms in (1), out (-1) or back to the start (0), within ZOOM_MIN and ZOOM_MAX."""
        before = self.zoom
        self.zoom = 1.0 if step == 0 else min(ZOOM_MAX, max(ZOOM_MIN, self.zoom * ZOOM_STEP ** step))
        if abs(self.zoom - 1.0) < 0.01:
            self.zoom = 1.0
        if self.zoom != before:
            self._draw(before)

    def _draw(self, before: float = 1.0) -> None:
        """Draws the pages at the zoom (the math's text too), and says the zoom when it isn't 100 %."""
        for pic, img, width, _ in self.pics:
            pic.setPixmap(QPixmap.fromImage(img.scaledToWidth(max(40, int(width * self.zoom)),
                                                              Qt.SmoothTransformation)))
            pic.adjustSize()
            pic.parentWidget().adjustSize()
        for text in self.texts:  # (scaled from the size it started at, kept on it, so zooms don't add up)
            font = text.font()
            base = getattr(text, "_base_pt", None) or font.pointSizeF()
            text._base_pt = base
            font.setPointSizeF(max(6.0, base * self.zoom))
            text.setFont(font)
        self.zoom_label.setText("" if self.zoom == 1.0 else f"{round(self.zoom * 100)} %")

    def _side_by_side(self, on: bool) -> None:
        """Side by side: every file at once (the button stays pressed), or the tabs again."""
        self.stack.setCurrentIndex(1 if on else 0)
        self.side.setText("One at a time" if on else "Side by side")

    def _turn_off(self) -> None:
        """No more previews: saved in the settings now, whatever is answered about these files."""
        self.s.preview_before_saving = False
        try:
            self.s.save()
        except OSError as e:
            log_error("could not save the settings", e)
        self.off.setVisible(False)
        self.off_note.setText(OFF_NOTE)


def math_view(made: list, heading: str) -> QWidget:
    """The math of invoices (invoice_math.explain), as the preview's "The math" tab shows it: a heading, the
    text, and Copy all. made: (FirmInvoice, invoice number) for each invoice. The widget returned keeps its
    text box as .text (PreviewDialog zooms it) and the plain text as .plain."""
    from ..invoice_math import explain
    html, plain = explain(made)
    w = QWidget()
    col = QVBoxLayout(w)
    head = QLabel(heading)
    head.setWordWrap(True)
    col.addWidget(head)
    text = QTextBrowser()
    text.document().setDefaultStyleSheet(MATH_CSS)
    text.setHtml(html)
    col.addWidget(text, 1)
    row = QHBoxLayout()
    note = QLabel("")
    note.setObjectName("muted")
    row.addWidget(note)
    row.addStretch(1)
    copy = QPushButton("Copy all")
    copy.setAutoDefault(False)
    copy.setToolTip("Put all of it on the clipboard as text, to paste into an e-mail")
    copy.clicked.connect(lambda: (QApplication.clipboard().setText(plain), note.setText("Copied.")))
    row.addWidget(copy)
    col.addLayout(row)
    w.text, w.plain = text, plain
    return w


class MathDialog(QDialog):
    """How the amounts of the invoices just made were reached, line by line. OK closes it; Copy all puts it on
    the clipboard as text; Save as PDF... saves it (starting in `folder`, the invoices' folder); "Don't show
    this anymore" sets Settings.show_math off at once (and saves the settings). `live`: the invoices not made
    yet, as the main window's "Who pays what" shows them (no numbers yet, and nothing to turn off)."""

    def __init__(self, made: list, s: Settings, parent=None, folder: Path | None = None, live: bool = False):
        """made: (FirmInvoice, invoice number) for each invoice made (deliver.generate's `math`; the number ""
        when `live`)."""
        from ..invoice_math import explain
        super().__init__(parent)
        self.s, self.made, self.folder = s, made, folder
        self.html, self.plain = explain(made)
        self.setWindowTitle("The math")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        shown = all(f.opts.detail for f, _ in made)  # (granular detail ticked: the invoice shows it too)
        if live:
            head = QLabel("How the amounts of " + ("this invoice" if len(made) == 1 else "these invoices")
                          + " are reached, as things stand now (nothing is made until you Generate).")
        else:
            head = QLabel("How the amounts on " + ("this invoice" if len(made) == 1 else "these invoices")
                          + " were reached." + ("" if shown else " The invoice itself shows only the amounts."))
        head.setWordWrap(True)
        lay.addWidget(head)
        self.text = QTextBrowser()
        self.text.document().setDefaultStyleSheet(MATH_CSS)
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
        self.off.setVisible(not live)
        self.resize(z(620), z(520))

    def _copy(self) -> None:
        """Copy all: the math as plain text on the clipboard."""
        QApplication.clipboard().setText(self.plain)
        self.note.setText("Copied.")

    def default_name(self) -> str:
        """'Invoice 2026-0012 - the math.pdf' ('Invoices 2026-0012 to 2026-0013 - ...' for several)."""
        numbers = [n for _, n in self.made if n]  # (none yet: the live math of the main window)
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
    range. Each page is drawn at 300 dpi at its actual size, or made to fit (see print_rect), upright or
    sideways as the page is; the copies asked for are made here when the printer can't make them itself. Other
    files (the Excel run sheet) are handed to their own program's Print. Says so when a file could not be
    printed, or the printer stopped."""
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
