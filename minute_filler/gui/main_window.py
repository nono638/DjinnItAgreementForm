"""Main window: drop zone + paste box on the left, editable extracted fields on the right."""
from __future__ import annotations

import calendar
import os
import re
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__, log as logfile
from ..log import error as log_error, log
from ..batch import Job, expand_paths, fill_jobs, group, make_doc, out_dir_for, remerge
from ..extract_llm import OllamaExtractor
from ..extract_regex import RegexExtractor
from ..fill import fill_all
from ..ingest import ingest_file, ingest_pil, ingest_text
from ..merge import refresh_delivery_date, refresh_rate
from ..models import (Attorney, CaseInfo, DELIVERY_TYPES, FIELD_LABELS, FieldState, PROC_TYPES, REQUIRED_KEYS,
                      SRC_AI, SRC_USER)
from ..settings import Settings
from .dialogs import ClarifyDialog, SettingsDialog
from .theme import apply_theme
from .workers import Runner

FILE_FILTER = ("Documents (*.pdf *.jpg *.jpeg *.png *.heic *.tif *.tiff *.bmp *.webp *.eml *.txt *.docx);;"
               "All files (*.*)")
ATT_COLS = ["", "Name", "Firm", "Address", "Phone", "Fax", "Email", "Party / role", "Source"]
ATT_FIELDS = [None, "name", "firm", "address", "phone", "fax", "email", "party", "source"]


ASSETS = Path(__file__).resolve().parent.parent / "assets"


def quick_date(days: int = 0, months: int = 0, today: date | None = None) -> str:
    """Today plus days/months as M/D/YYYY; a day on the weekend becomes the Monday after."""
    d = today or date.today()
    if months:
        y, m = divmod(d.month - 1 + months, 12)
        d = d.replace(year=d.year + y, month=m + 1, day=min(d.day, calendar.monthrange(d.year + y, m + 1)[1]))
    d += timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return f"{d.month}/{d.day}/{d.year}"


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _rounded(path: Path, width: int, radius: int):
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


# ------------------------------------------------------------------ widgets

class FieldRow(QWidget):
    """Editor + suggestions menu + source badge for one form field."""

    def __init__(self, key: str, on_edit, multiline: bool = False):
        super().__init__()
        self.key, self.on_edit, self.multiline = key, on_edit, multiline
        self.state = FieldState()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if multiline:
            self.edit = QPlainTextEdit()
            self.edit.setFixedHeight(58)
            self.edit.textChanged.connect(self._changed)
        else:
            self.edit = QLineEdit()
            self.edit.textEdited.connect(self._changed)
        self._loading = False
        lay.addWidget(self.edit, 1)
        self.alts = QToolButton()
        self.alts.setText("▾")
        self.alts.setPopupMode(QToolButton.InstantPopup)
        self.alts.setMenu(QMenu(self.alts))
        self.alts.setFixedWidth(28)
        lay.addWidget(self.alts)
        self.badge = QLabel("")
        self.badge.setObjectName("badge")
        self.badge.setFixedSize(58, 20)
        self.badge.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.badge, 0, Qt.AlignTop if multiline else Qt.AlignVCenter)

    def text(self) -> str:
        return self.edit.toPlainText().strip() if self.multiline else self.edit.text().strip()

    def set_text(self, t: str) -> None:
        self._loading = True
        if self.multiline:
            self.edit.setPlainText(t)
        else:
            self.edit.setText(t)
        self._loading = False

    def set_state(self, st: FieldState) -> None:
        self.state = st
        self.set_text(st.value)
        self._refresh()

    def _refresh(self) -> None:
        st = self.state
        self.badge.setText(st.source if st.value else "")
        self.badge.setProperty("src", st.source if st.value else "")
        tip = {"regex": "Found in the document", "AI": "Suggested by the AI model - please check",
               "default": "Your default setting", "derived": "Calculated", "you": "Entered by you"}
        self.badge.setToolTip(tip.get(st.source, ""))
        review = st.value and st.source != SRC_USER and (st.confidence < 0.6 or st.source == SRC_AI)
        self.edit.setProperty("review", bool(review))
        self.edit.setProperty("missing", self.key in REQUIRED_KEYS and not st.value)
        others = [a for a in st.alternatives if a != st.value]
        menu = self.alts.menu()
        menu.clear()
        for alt in st.alternatives:
            act = menu.addAction(alt if len(alt) < 110 else alt[:107] + "...")
            act.triggered.connect(lambda _=False, v=alt: self.choose(v))
        self.alts.setVisible(bool(others))
        self.alts.setToolTip(f"{len(others)} other suggestion(s)")
        for w in (self.badge, self.edit):
            repolish(w)

    def choose(self, value: str) -> None:
        self.set_text(value)
        self._changed()

    def _changed(self) -> None:
        if self._loading:
            return
        self.state = FieldState(self.text(), SRC_USER, 1.0, self.state.alternatives)
        self._refresh()
        self.on_edit(self.key)


class DropZone(QFrame):
    def __init__(self, on_files, on_text, on_image, on_browse):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.on_files, self.on_text, self.on_image = on_files, on_text, on_image
        self.setMinimumHeight(215)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self.icon = icon = QLabel("⭳")
        icon.setObjectName("dropIcon")
        icon.setAlignment(Qt.AlignCenter)
        self.djinn = QLabel()
        self.djinn.setAlignment(Qt.AlignCenter)
        self.djinn.setVisible(False)
        lay.addWidget(self.djinn)
        self.mood = None
        self.wanted = None
        self.compact = False
        t = QLabel("Drop documents here")
        t.setObjectName("dropText")
        t.setAlignment(Qt.AlignCenter)
        sub = QLabel("PDF, photo or text file - or many at once, or a whole folder: "
                     "documents about the same case and date become one form.")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        self.sub = sub
        b = QPushButton("Browse...")
        b.clicked.connect(on_browse)
        b.setFixedWidth(120)
        for w in (icon, t, sub):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(b, 0, Qt.AlignCenter)

    def set_mood(self, mood: str | None) -> None:
        """Shows the djinn: 'working', 'done' or 'stumped' (None hides him)."""
        self.wanted = mood
        width, height = (150, 170) if self.compact else (300, 330)
        self.sub.setVisible(not self.compact)
        if mood is None:
            self.djinn.setVisible(False)
            self.icon.setVisible(not self.compact)
            self.setMinimumHeight(110 if self.compact else 215)
            return
        self.icon.setVisible(False)
        self.djinn.setVisible(True)
        self.setMinimumHeight(height)
        if (mood, width) == self.mood and self.djinn.pixmap() and not self.djinn.pixmap().isNull():
            return
        self.mood = (mood, width)
        self.djinn.setPixmap(_rounded(ASSETS / f"djinn_{mood}.jpg", width, 12))
        self.djinn.setToolTip({"working": "The djinn is on it…", "done": "Ready to fill!",
                               "stumped": "Something needs your attention"}.get(mood, ""))

    def set_compact(self, on: bool) -> None:
        """A smaller picture leaves room for the list of jobs."""
        if on != self.compact:
            self.compact = on
            self.set_mood(self.wanted)

    def _hover(self, on: bool):
        self.setProperty("hover", on)
        repolish(self)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasText() or md.hasImage():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        handle_mime(e.mimeData(), self.on_files, self.on_text, self.on_image)
        e.acceptProposedAction()


def handle_mime(md, on_files, on_text, on_image) -> bool:
    files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
    if files:
        on_files(files)
        return True
    if md.hasImage():
        img = md.imageData()
        if img is not None and not img.isNull():
            on_image(img)
            return True
    if md.hasText() and md.text().strip():
        on_text(md.text())
        return True
    return False


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    fr = QFrame()
    fr.setObjectName("card")
    lay = QVBoxLayout(fr)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("section")
        lay.addWidget(lbl)
    return fr, lay


# -------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, app: QApplication):
        super().__init__()
        self.s, self.app = settings, app
        self.runner = Runner()
        self.ai = OllamaExtractor(settings)
        self.ai_ok = False
        self.gen = 0  # bumped by "New job": results of work started before it are dropped
        self.jobs: list[Job] = [Job()]  # more than one = a batch
        self.cur = self.jobs[0]         # the job shown in the editor
        self.ai_pending = 0

        self.setWindowTitle("DjinnItAgreementForm")
        self.setMinimumSize(1080, 720)
        self._build()
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
        else:
            self.resize(1320, 860)
        self._show_case()
        self._set_status("Drop a document to begin", "")
        QTimer.singleShot(50, self._startup)

    # The editor always works on the current job.
    @property
    def case(self) -> CaseInfo:
        return self.cur.case

    @case.setter
    def case(self, value: CaseInfo) -> None:
        self.cur.case = value

    @property
    def proc_touched(self) -> bool:
        return self.cur.proc_touched

    @proc_touched.setter
    def proc_touched(self, value: bool) -> None:
        self.cur.proc_touched = value

    @property
    def att_touched(self) -> bool:
        return self.cur.att_touched

    @att_touched.setter
    def att_touched(self, value: bool) -> None:
        self.cur.att_touched = value

    # ---------------------------------------------------------- layout
    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # header
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t = QLabel("DjinnItAgreementForm")
        t.setObjectName("title")
        sub = QLabel("Drop a document, check the details, fill the form.")
        sub.setObjectName("subtitle")
        title_box.addWidget(t)
        title_box.addWidget(sub)
        head.addLayout(title_box)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("status")
        head.addWidget(self.status)
        new_btn = QPushButton("New job")
        new_btn.setToolTip("Clear everything and start over (Ctrl+N)")
        new_btn.clicked.connect(self.new_job)
        set_btn = QPushButton("⚙  Settings")
        set_btn.clicked.connect(self.open_settings)
        head.addWidget(new_btn)
        head.addWidget(set_btn)
        root.addLayout(head)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)
        root.addWidget(self.busy)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # left column
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(10)
        self.drop = DropZone(self.add_files, self.add_text, self.add_qimage, self.browse)
        ll.addWidget(self.drop)
        self.jobs_label = QLabel("Jobs")
        self.jobs_label.setObjectName("fieldLabel")
        ll.addWidget(self.jobs_label)
        self.job_list = QListWidget()
        self.job_list.setToolTip("One job per case and date. Click a job to check or edit it;\n"
                                 "untick the ones \"Fill all\" should leave out.")
        self.job_list.currentRowChanged.connect(self._job_selected)
        self.job_list.itemChanged.connect(self._job_ticked)
        self.job_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.job_list.setTextElideMode(Qt.ElideRight)
        self.job_list.setMinimumHeight(140)
        self.job_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.job_list.customContextMenuRequested.connect(self._job_menu)
        ll.addWidget(self.job_list, 3)
        lbl = QLabel("Inputs for this job")
        lbl.setObjectName("fieldLabel")
        ll.addWidget(lbl)
        self.input_list = QListWidget()
        self.input_list.setMaximumHeight(110)
        self.input_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.input_list.customContextMenuRequested.connect(self._input_menu)
        ll.addWidget(self.input_list)
        lbl = QLabel("…or paste an e-mail or notes")
        lbl.setObjectName("fieldLabel")
        ll.addWidget(lbl)
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("e.g. \"Please send the minutes for Smith v. Jones, Index 712345/2024, "
                                      "before Justice Lopez on 9/14/2026, expedited…\"")
        ll.addWidget(self.paste, 1)
        pb = QPushButton("Extract from text")
        pb.clicked.connect(self._extract_paste)
        ll.addWidget(pb)
        self.ai_label = QLabel("Checking AI…")
        self.ai_label.setObjectName("muted")
        self.ai_label.setWordWrap(True)
        self.ai_label.setTextFormat(Qt.RichText)
        self.ai_label.linkActivated.connect(self._ai_help)
        ll.addWidget(self.ai_label)
        split.addWidget(left)

        # right column (scrollable form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        rl = QVBoxLayout(inner)
        rl.setContentsMargins(8, 0, 8, 0)
        rl.setSpacing(12)
        scroll.setWidget(inner)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 940])

        self.rows: dict[str, FieldRow] = {}

        def row(form: QFormLayout, key: str, multiline=False):
            r = FieldRow(key, self._field_edited, multiline)
            self.rows[key] = r
            lab = QLabel(FIELD_LABELS[key])
            lab.setObjectName("fieldLabel")
            form.addRow(lab, r)
            return r

        def form_in(lay: QVBoxLayout) -> QFormLayout:
            f = QFormLayout()
            f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f.setHorizontalSpacing(12)
            f.setVerticalSpacing(8)
            lay.addLayout(f)
            return f

        # Case card: two columns
        c, cl = card("Case")
        grid = QHBoxLayout()
        grid.setSpacing(18)
        colA, colB = QVBoxLayout(), QVBoxLayout()
        grid.addLayout(colA, 1)
        grid.addLayout(colB, 1)
        cl.addLayout(grid)
        fa, fb = form_in(colA), form_in(colB)
        for k in ("court", "county", "part"):
            row(fa, k)
        for k in ("index_no", "judge", "dates"):
            row(fb, k)
        fc = form_in(cl)
        row(fc, "case_name", multiline=True)
        rl.addWidget(c)

        # Proceeding card
        c, cl = card("Type of proceeding")
        pr = QHBoxLayout()
        self.proc_boxes: dict[str, QCheckBox] = {}
        for p in PROC_TYPES:
            cb = QCheckBox(p)
            cb.toggled.connect(self._proc_toggled)
            self.proc_boxes[p] = cb
            pr.addWidget(cb)
        pr.addStretch(1)
        cl.addLayout(pr)
        f = form_in(cl)
        row(f, "proc_other")
        rl.addWidget(c)

        # Order card
        c, cl = card("Order")
        grid = QHBoxLayout()
        grid.setSpacing(18)
        colA, colB = QVBoxLayout(), QVBoxLayout()
        grid.addLayout(colA, 1)
        grid.addLayout(colB, 1)
        cl.addLayout(grid)
        fa, fb = form_in(colA), form_in(colB)
        # Rate sheet + speed pickers
        sheet_row = QHBoxLayout()
        self.sheet_box = QComboBox()
        self.sheet_box.setMinimumContentsLength(18)
        self.sheet_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sheet_box.currentIndexChanged.connect(self._sheet_changed)
        folder = QToolButton()
        folder.setText("📂")
        folder.setToolTip("Open the rate sheets folder (add or edit CSV files, then click ⟳)")
        folder.clicked.connect(self._open_sheets_folder)
        reload_btn = QToolButton()
        reload_btn.setText("⟳")
        reload_btn.setToolTip("Reload rate sheets")
        reload_btn.clicked.connect(self._reload_sheets)
        sheet_row.addWidget(self.sheet_box, 1)
        sheet_row.addWidget(folder)
        sheet_row.addWidget(reload_btn)
        lab = QLabel("Rate sheet")
        lab.setObjectName("fieldLabel")
        fa.addRow(lab, sheet_row)
        self.delivery = QComboBox()
        self.delivery.currentIndexChanged.connect(self._delivery_changed)
        lab = QLabel("Speed")
        lab.setObjectName("fieldLabel")
        fa.addRow(lab, self.delivery)
        for k in ("rate", "copies", "est_pages"):
            row(fa, k)
        row(fb, "delivery_date")
        quick = QHBoxLayout()
        quick.setSpacing(4)
        for label, days, months in (("Today", 0, 0), ("Tomorrow", 1, 0), ("1 week", 7, 0), ("2 weeks", 14, 0),
                                    ("3 weeks", 21, 0), ("1 month", 0, 1)):
            b = QToolButton()
            b.setText(label)
            b.setObjectName("quick")
            when = quick_date(days, months)
            b.setToolTip(f"Estimated delivery {when}" + ("" if label in ("Today", "Tomorrow") else " - from today")
                         + "\n(a Saturday or Sunday becomes the Monday after)")
            b.clicked.connect(lambda _=False, d=days, m=months: self.rows["delivery_date"].choose(quick_date(d, m)))
            quick.addWidget(b)
        quick.addStretch(1)
        fb.addRow("", quick)
        row(fb, "agreement_date")
        self.rate_info = QLabel("")
        self.rate_info.setObjectName("muted")
        self.rate_info.setWordWrap(True)
        fb.addRow("", self.rate_info)
        rl.addWidget(c)
        self._fill_sheet_box()

        # Attorneys card
        c, cl = card("Attorneys  —  one form is made for each checked row")
        self.att = QTableWidget(0, len(ATT_COLS))
        self.att.setHorizontalHeaderLabels(ATT_COLS)
        self.att.verticalHeader().setVisible(False)
        self.att.setAlternatingRowColors(True)
        self.att.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.att.setWordWrap(True)
        hh = self.att.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        for i, w in enumerate([30, 140, 190, 230, 115, 105, 190, 170, 60]):
            self.att.setColumnWidth(i, w)
        self.att.setMinimumHeight(240)
        self.att.itemChanged.connect(self._att_changed)
        cl.addWidget(self.att)
        br = QHBoxLayout()
        add = QPushButton("+ Add attorney")
        add.clicked.connect(self._add_att_row)
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_att_rows)
        hint = QLabel("Double-click a cell to edit. Separate address lines with  /")
        hint.setObjectName("muted")
        br.addWidget(add)
        br.addWidget(rem)
        br.addStretch(1)
        br.addWidget(hint)
        cl.addLayout(br)
        rl.addWidget(c)
        rl.addStretch(1)

        # footer / fill bar
        foot = QFrame()
        foot.setObjectName("card")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 10, 16, 10)
        self.per_email = QCheckBox("Write \"per email\" in attorney signature spot")
        self.per_email.setChecked(self.s.per_email)
        self.per_email.toggled.connect(lambda v: self._set_opt("per_email", v))
        self.sign_rep = QCheckBox("Sign as court reporter")
        self.sign_rep.setToolTip("Puts your signature picture on the court reporter line - or types your name,\n"
                                 "if no picture is chosen in Settings → My info.")
        self.sign_rep.setChecked(self.s.sign_reporter)
        self.sign_rep.toggled.connect(lambda v: self._set_opt("sign_reporter", v))
        from ..fill import FORMS
        self.form_choice = QComboBox()
        for key, (_, label) in FORMS.items():
            self.form_choice.addItem(label, key)
        self.form_choice.setCurrentIndex(max(0, self.form_choice.findData(self.s.form_choice)))
        self.form_choice.currentIndexChanged.connect(
            lambda _: self._set_opt("form_choice", self.form_choice.currentData()))
        fl.addWidget(self.per_email)
        fl.addSpacing(12)
        fl.addWidget(self.sign_rep)
        fl.addStretch(1)
        fl.addWidget(QLabel("Form:"))
        fl.addWidget(self.form_choice)
        fl.addSpacing(12)
        self.fill_btn = QPushButton("Fill Form")
        self.fill_btn.setObjectName("primary")
        self.fill_btn.clicked.connect(self.fill)
        fl.addWidget(self.fill_btn)
        self.fill_all_btn = QPushButton("Fill all")
        self.fill_all_btn.setObjectName("primary")
        self.fill_all_btn.setToolTip("Fill the forms of every ticked job (Ctrl+Shift+Enter)")
        self.fill_all_btn.clicked.connect(self.fill_all_jobs)
        fl.addWidget(self.fill_all_btn)
        root.addWidget(foot)
        self._refresh_jobs()

        self._build_menu()
        QShortcut(QKeySequence.Paste, self, activated=self._paste_shortcut)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_job)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.browse)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.fill)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, activated=self.fill_all_jobs)

    def _build_menu(self):
        from .dialogs import AboutDialog, CONTACT_EMAIL
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction("&New job", self.new_job)          # Ctrl+N handled by the window shortcut
        m.addAction("&Open documents…", self.browse)
        m.addAction("Open a &folder of documents (batch)…", self.browse_folder)
        m.addSeparator()
        m.addAction("Open &rate sheets folder", self._open_sheets_folder)
        m.addAction("&Settings…", self.open_settings)
        m.addSeparator()
        m.addAction("E&xit", self.close)
        m = mb.addMenu("&Help")
        m.addAction("Set up the &AI helper (Ollama)…", self._ai_help)
        m.addAction("Send &feedback…", lambda: QDesktopServices.openUrl(
            QUrl(f"mailto:{CONTACT_EMAIL}?subject=DjinnItAgreementForm%20{__version__}")))
        m.addAction("Open the &log folder", self._open_log_folder)
        m.addAction("&Copy details for a problem report", self._copy_diagnostics)
        m.addSeparator()
        m.addAction("&About DjinnItAgreementForm", lambda: AboutDialog(self).exec())

    # --------------------------------------------------------- startup
    def _startup(self):
        if not self.s.profile.name:
            self.open_settings(first_run=True)
        self._check_ai()

    def _ai_help(self, _link=""):
        from .dialogs import OllamaHelpDialog
        OllamaHelpDialog(self.s.ollama_model, self.s.ollama_host, self).exec()
        self._check_ai()

    def _check_ai(self):
        if not self.s.use_ai:
            self.ai_ok = False
            self.ai_label.setText("AI is off (Settings → AI). Using rules only.")
            return
        self.ai_label.setText("Checking AI…")
        self.ai = OllamaExtractor(self.s)

        def done(res):
            self.ai_ok, msg = res
            link = "" if self.ai_ok else '  <a href="setup">How to set up</a>'
            self.ai_label.setText(("● " if self.ai_ok else "○ ") + msg + link)
            # inputs dropped while the check was still running
            waiting = [d for d in self.cur.docs if d.ai is None]
            if self.ai_ok and waiting and self.ai_pending == 0 and len(self.jobs) == 1:
                self._maybe_ai(self.cur, waiting)

        self.runner.start(self.ai.status, on_done=done, on_error=lambda m: done((False, m)))

    # ------------------------------------------------------ inputs
    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose document(s)", "", FILE_FILTER)
        if files:
            self.add_files(files)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of documents")
        if folder:
            self.add_files([folder], batch=True)

    def _paste_shortcut(self):
        if self.focusWidget() in (self.paste,) or isinstance(self.focusWidget(), (QLineEdit, QPlainTextEdit)):
            fw = self.focusWidget()
            fw.paste()
            return
        md = QApplication.clipboard().mimeData()
        if not handle_mime(md, self.add_files, self.add_text, self.add_qimage):
            self._toast("Nothing to paste.")

    def _extract_paste(self):
        text = self.paste.toPlainText().strip()
        if text:
            self.add_text(text)

    def add_text(self, text: str):
        n = sum(1 for j in self.jobs for d in j.docs if d.ing.name.startswith("Pasted text")) + 1
        self._ingest([lambda: ingest_text(text, f"Pasted text {n}")], [f"Pasted text {n}"])
        if self.paste.toPlainText().strip() != text.strip():
            self.paste.setPlainText(text)

    def add_qimage(self, qimg):
        from PIL import Image
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        qimg.save(buf, "PNG")
        import io
        pil = Image.open(io.BytesIO(bytes(buf.data())))
        self._ingest([lambda: ingest_pil(pil, "Pasted image")], ["Pasted image"])

    def add_files(self, paths: list[str], batch: bool = False):
        """Files and folders. Several documents about different cases (or batch=True) start a batch."""
        gen = self.gen
        loaded = {str(Path(d.path).resolve()).lower() for j in self.jobs for d in j.docs if d.path}

        def find():  # off the UI thread: a big folder takes a while to go through
            found = expand_paths(paths)
            return found, [p for p in found if str(Path(p).resolve()).lower() not in loaded]

        def found(result):
            if gen != self.gen:
                return
            everything, fresh = result
            if len(fresh) < len(everything):
                self._toast(f"Skipped {len(everything) - len(fresh)} document(s) that are already loaded.")
            if fresh:
                self._ingest([(lambda p=p: ingest_file(p)) for p in fresh], [Path(p).name for p in fresh],
                             fresh, batch)
            else:
                self.busy.setVisible(self.ai_pending > 0)
                self._update_status()
                if not everything:
                    self._toast("No documents found there.")

        def failed(msg):
            self.busy.setVisible(self.ai_pending > 0)
            self._update_status()
            QMessageBox.warning(self, "Could not open that", msg)

        if any(Path(p).is_dir() for p in paths):
            self._set_status("Looking for documents…", "busy")
            self.busy.setVisible(True)
        self.runner.start(find, on_done=found, on_error=failed)

    def _ingest(self, loaders, names, paths=None, batch=False):
        gen, target, s = self.gen, self.cur, self.s
        paths = paths or [""] * len(loaders)
        self._set_status(f"Reading {names[0]}…" if len(names) == 1 else f"Reading {len(names)} documents…", "busy")
        self.busy.setVisible(True)
        extractor = RegexExtractor(s.profile, s.title_case_names)

        def work(progress):
            docs, errors = [], []
            for i, (load, name, path) in enumerate(zip(loaders, names, paths)):
                progress(i, len(loaders), name)
                try:
                    ing = load()
                    docs.append(make_doc(ing, extractor.extract(ing), s, path))
                except Exception as e:  # one bad file must not stop the rest
                    errors.append(f"{name}: {e}")
                    log.info("skipped a document that could not be read")
            return docs, errors

        def step(i, n, name):
            if gen == self.gen and n > 1:
                self.busy.setRange(0, n)
                self.busy.setValue(i)
                self._set_status(f"Reading {i + 1} of {n}:  {name[:40]}", "busy")

        def done(result):
            if gen != self.gen:
                return
            docs, errors = result
            self.busy.setRange(0, 0)
            self.busy.setVisible(self.ai_pending > 0)
            for d in docs:
                for w in d.ing.warnings:
                    self._toast(w)
            if docs:
                self._sync_from_ui()
                one_job = len(self.jobs) == 1 and target in self.jobs and not batch
                if one_job and (len(docs) == 1 or len(group(docs, s)) == 1):
                    # the usual way: everything dropped is about the job on screen
                    target.docs += docs
                    remerge(target, s)
                    self._show_job()
                    self._maybe_ai(target, docs)
                else:
                    before = [j for j in self.jobs if not j.is_empty()]
                    count = len(before)
                    self.jobs = group(docs, s, before) or [Job()]
                    if self.cur not in self.jobs:
                        self.cur = self.jobs[0]
                    self._refresh_jobs()
                    self._show_job()
                    new = len(self.jobs) - count
                    self._toast(f"{len(docs)} document(s) read:  {new} new job(s), "
                                f"{len(self.jobs)} in total.")
            else:
                self._update_status()
            log.info("read %d of %d document(s)%s", len(docs), len(loaders), " (batch)" if batch else "")
            if errors:
                self._unreadable(errors, len(loaders))

        def failed(msg):
            self.busy.setRange(0, 0)
            self.busy.setVisible(self.ai_pending > 0)
            self._set_status("Could not read that input", "warn")
            QMessageBox.warning(self, "Could not read input", msg)

        self.runner.start(work, on_done=done, on_error=failed, on_progress=step)

    def _unreadable(self, errors: list[str], total: int):
        if total == 1:
            self._set_status("Could not read that input", "warn")
            QMessageBox.warning(self, "Could not read input", errors[0])
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Some documents could not be read")
        box.setText(f"{len(errors)} of {total} documents could not be read and were left out:\n\n"
                    + "\n".join(e[:110] for e in errors[:8]) + ("\n…" if len(errors) > 8 else ""))
        box.setDetailedText("\n".join(errors))
        box.exec()

    def _maybe_ai(self, job: Job, docs: list):
        """Asks the model about documents added to the job on screen (batches use the rules only)."""
        if not (self.s.use_ai and self.ai_ok):
            return
        todo = []
        for d in docs:
            if d.ing.kind == "image":
                todo.append(d)
            elif d.ing.kind in ("email", "text") and self.s.ai_for_text:
                todo.append(d)
            elif d.ing.kind == "pdf" and job.case.missing_required():
                todo.append(d)
        if not todo:
            return
        gen = self.gen
        self.ai_pending += len(todo)
        self.busy.setVisible(True)
        self._update_status()
        for d in todo:
            def done(ex, d=d):
                if gen != self.gen:
                    return
                self.ai_pending -= 1
                if job in self.jobs and d in job.docs:
                    d.ai = ex
                    self._remerge(job)
                self._update_status()

            def failed(msg, d=d):
                if gen != self.gen:
                    return
                self.ai_pending -= 1
                self.ai_label.setText(f"○ AI failed on {d.ing.name}: {msg[:120]}")
                self._update_status()

            self.runner.start(self.ai.extract, d.ing, on_done=done, on_error=failed)

    def _input_menu(self, pos):
        item = self.input_list.itemAt(pos)
        if not item:
            return
        idx = self.input_list.row(item)
        doc = self.cur.docs[idx]
        m = QMenu(self)
        view = m.addAction("Show extracted text")
        split = m.addAction("Move to a job of its own") if len(self.cur.docs) > 1 else None
        rem = m.addAction("Remove from job")
        act = m.exec(self.input_list.mapToGlobal(pos))
        if act is None:
            return
        if act == view:
            box = QMessageBox(self)
            box.setWindowTitle(doc.ing.name)
            box.setText("Text read from this input:")
            box.setDetailedText(doc.ing.text or "(no text)")
            box.exec()
        elif act in (rem, split):
            self._sync_from_ui()
            del self.cur.docs[idx]
            remerge(self.cur, self.s)
            if act == split:
                job = Job(docs=[doc], batch=True)
                remerge(job, self.s)
                self.jobs.insert(self.jobs.index(self.cur) + 1, job)
            self._refresh_jobs()
            self._show_job()

    # ------------------------------------------------------------ jobs
    def _job_text(self, job: Job) -> str:
        mark = "✓ " if job.saved and not job.error else "⚠ " if job.error or job.problems() else ""
        title = job.title()  # kept short so the date, which tells a case's jobs apart, stays in view
        bits = [title if len(title) <= 30 else title[:29].rstrip() + "…"]
        bits += [v for v in (job.case.get("dates"), job.case.get("index_no")) if v]
        if len(job.docs) > 1:
            bits.append(f"{len(job.docs)} documents")
        return mark + "  ·  ".join(dict.fromkeys(bits))

    def _job_tip(self, job: Job) -> str:
        tip = [d.ing.name for d in job.docs] or ["(no documents)"]
        tip += ["⚠ " + p for p in job.problems()]
        who = [a.name or a.firm for a in job.case.attorneys if a.checked]
        tip.append("Form for: " + ("; ".join(who) if who else "(blank attorney block)"))
        if job.error:
            tip.append("Not saved - " + job.error)
        tip += [f"Saved: {p.name}" for p in job.saved]
        return "\n".join(tip)

    def _refresh_jobs(self):
        """Rebuilds the list of jobs; it is only shown for a batch (more than one job)."""
        multi = len(self.jobs) > 1
        for w in (self.jobs_label, self.job_list, self.fill_all_btn):
            w.setVisible(multi)
        self.drop.set_compact(multi)
        self.input_list.setMaximumHeight(72 if multi else 110)
        self.paste.setMaximumHeight(64 if multi else 16777215)
        self.fill_btn.setText("Fill this form" if multi else "Fill Form")
        self.fill_btn.setObjectName("" if multi else "primary")
        repolish(self.fill_btn)
        self.job_list.blockSignals(True)
        self.job_list.clear()
        for job in self.jobs:
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            self.job_list.addItem(item)
        self.job_list.setCurrentRow(self.jobs.index(self.cur))
        self.job_list.blockSignals(False)
        self._refresh_job_labels()

    def _refresh_job_labels(self):
        self.job_list.blockSignals(True)
        for row, job in enumerate(self.jobs):
            item = self.job_list.item(row)
            if item is None:
                continue
            item.setText(self._job_text(job))
            item.setToolTip(self._job_tip(job))
            item.setCheckState(Qt.Checked if job.include else Qt.Unchecked)
        self.job_list.blockSignals(False)
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        need = sum(1 for j in self.jobs if j.problems() and not j.saved)
        saved = sum(1 for j in self.jobs if j.saved)
        text = f"Jobs: {len(self.jobs)}"
        if need:
            text += f"  ·  {need} to check (⚠)"
        if saved:
            text += f"  ·  {saved} saved (✓)"
        self.jobs_label.setText(text)
        forms = sum(j.form_count() for j in chosen)
        self.fill_all_btn.setText(f"Fill all  ({forms} form{'s' if forms != 1 else ''})")
        self.fill_all_btn.setEnabled(bool(chosen))

    def _show_job(self):
        """Puts the current job in the editor."""
        self.input_list.clear()
        for d in self.cur.docs:
            ing = d.ing
            label = f"{ing.name}   ·   {ing.kind}"
            if ing.page_count:
                label += f", {ing.page_count} pp"
            if ing.ocr_used:
                label += ", OCR"
            item = QListWidgetItem(label)
            item.setToolTip(ing.text[:1500] or "(no text)")
            self.input_list.addItem(item)
        self._show_case()
        self._update_status()

    def _job_selected(self, row: int):
        if not 0 <= row < len(self.jobs) or self.jobs[row] is self.cur:
            return
        self._sync_from_ui()
        self.cur = self.jobs[row]
        self._show_job()

    def _job_ticked(self, item):
        row = self.job_list.row(item)
        if 0 <= row < len(self.jobs):
            self.jobs[row].include = item.checkState() == Qt.Checked
            self._refresh_job_labels()

    def _job_menu(self, pos):
        item = self.job_list.itemAt(pos)
        m = QMenu(self)
        rem = m.addAction("Remove this job") if item else None
        m.addSeparator()
        tick = m.addAction("Tick all")
        untick = m.addAction("Untick all")
        act = m.exec(self.job_list.mapToGlobal(pos))
        if act is None:
            return
        if act in (tick, untick):
            for j in self.jobs:
                j.include = act == tick
            self._refresh_job_labels()
            return
        self._sync_from_ui()
        del self.jobs[self.job_list.row(item)]
        if not self.jobs:
            self.jobs = [Job()]
        if self.cur not in self.jobs:
            self.cur = self.jobs[0]
        self._refresh_jobs()
        self._show_job()

    # ----------------------------------------------------- merge/show
    def _remerge(self, job: Job | None = None):
        job = job or self.cur
        if job is self.cur:
            self._sync_from_ui()
        remerge(job, self.s)
        if job is self.cur:
            self._show_case()

    def _show_case(self):
        for key, r in self.rows.items():
            r.set_state(self.case.fields[key])
        self._select_speed(self.case.get("delivery") or self.s.delivery_name(self.s.default_delivery))
        for p, cb in self.proc_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in self.case.proc_types)
            cb.blockSignals(False)
        self._show_attorneys()

    def _show_attorneys(self):
        self.att.blockSignals(True)
        self.att.setRowCount(0)
        for a in self.case.attorneys:
            self._append_att(a)
        self.att.resizeRowsToContents()
        self.att.blockSignals(False)

    def _append_att(self, a: Attorney):
        r = self.att.rowCount()
        self.att.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if a.checked else Qt.Unchecked)
        self.att.setItem(r, 0, chk)
        for c, f in enumerate(ATT_FIELDS[1:], 1):
            val = getattr(a, f) or ""
            if f == "address":
                val = " / ".join(l for l in val.splitlines() if l.strip())
            it = QTableWidgetItem(val)
            if f == "source":
                it.setFlags(Qt.ItemIsEnabled)
            if a.is_placeholder():
                it.setForeground(self.palette().placeholderText())
            self.att.setItem(r, c, it)

    def _read_attorneys(self) -> list[Attorney]:
        out = []
        for r in range(self.att.rowCount()):
            vals = {}
            for c, f in enumerate(ATT_FIELDS[1:], 1):
                it = self.att.item(r, c)
                vals[f] = it.text().strip() if it else ""
            # " / " separates lines; "c/o" and "12-1/2" are left alone
            vals["address"] = "\n".join(p.strip() for p in re.split(r"\s+/\s*|\s*/\s+", vals["address"])
                                        if p.strip())
            a = Attorney(**vals)
            a.checked = self.att.item(r, 0).checkState() == Qt.Checked if self.att.item(r, 0) else False
            out.append(a)
        return out

    def _sync_from_ui(self):
        for key, r in self.rows.items():
            if r.state.source == SRC_USER:
                self.case.fields[key] = FieldState(r.text(), SRC_USER, 1.0, r.state.alternatives)
        # (delivery edits are recorded directly by _delivery_changed)
        self.case.proc_types = {p for p, cb in self.proc_boxes.items() if cb.isChecked()}
        self.case.attorneys = self._read_attorneys()

    # ---------------------------------------------------- UI events
    def _field_edited(self, key: str):
        self.case.fields[key] = self.rows[key].state
        self._update_status()

    def _proc_toggled(self, _):
        self.proc_touched = True

    def _att_changed(self, _item):
        self.att_touched = True

    def _add_att_row(self):
        self.att.blockSignals(True)
        self._append_att(Attorney(source=SRC_USER, checked=True))
        self.att.blockSignals(False)
        self.att_touched = True
        self.att.editItem(self.att.item(self.att.rowCount() - 1, 1))

    def _remove_att_rows(self):
        rows = sorted({i.row() for i in self.att.selectedIndexes()}, reverse=True)
        for r in rows:
            self.att.removeRow(r)
        if rows:
            self.att_touched = True

    # ------------------------------------------------ rate sheets / speed
    def _fill_sheet_box(self):
        sheets, problems = self.s.sheets()
        current = self.s.sheet()
        self.sheet_box.blockSignals(True)
        self.sheet_box.clear()
        for sh in sheets or [current]:
            self.sheet_box.addItem(sh.name, sh.name)
            tip = "\n".join(sp.label() + (f"  ·  {sp.days} days" if sp.days is not None else "")
                            for sp in sh.speeds)
            if sh.updated:
                tip += f"\nRates last updated {sh.updated}"
            self.sheet_box.setItemData(self.sheet_box.count() - 1, tip, Qt.ToolTipRole)
        self.sheet_box.setCurrentIndex(max(0, self.sheet_box.findData(current.name)))
        self.sheet_box.blockSignals(False)
        for p in problems:
            self._toast(f"Rate sheet skipped - {p}")
        self._fill_speeds()

    def _fill_speeds(self):
        keep = self.delivery.currentData() or self.case.get("delivery")
        sheet = self.s.sheet()
        self.delivery.blockSignals(True)
        self.delivery.clear()
        for sp in sheet.speeds:
            days = self.s.days_for(sp.name)
            label = sp.label() + (f"  ·  {days} day{'s' if days != 1 else ''}" if days is not None else "")
            self.delivery.addItem(label, sp.name)
        self.delivery.addItem("Other (type the rate yourself)", "Other")
        self.delivery.blockSignals(False)
        self._select_speed(keep or self.s.delivery_name(self.s.default_delivery))

    def _select_speed(self, name: str):
        """Selects `name` (matched loosely, e.g. 'Expedited' -> 'Expedite') without firing change events."""
        sp = self.s.sheet().find(name)
        target = sp.name if sp else ("Other" if name else self.delivery.itemData(0))
        self.delivery.blockSignals(True)
        self.delivery.setCurrentIndex(max(0, self.delivery.findData(target)))
        self.delivery.blockSignals(False)
        self._show_rate_info()

    def _show_rate_info(self):
        sheet = self.s.sheet()
        sp = sheet.find(self.delivery.currentData() or "")
        bits = []
        if sp and sp.copy:
            bits.append(f"Copies ${sp.copy}/pg")
        if sp:
            bits += [f"{k} ${v.lstrip('$')}" for k, v in sp.extras.items()]
        if sheet.updated:
            bits.append(f"rates updated {sheet.updated}")
        self.rate_info.setText("  ·  ".join(bits))

    def _sheet_changed(self, _idx):
        name = self.sheet_box.currentData()
        if not name or name == self.s.rate_sheet:
            return
        self.s.rate_sheet = name
        self.s.save()
        self._fill_speeds()
        self._apply_speed()
        self._remerge_others()

    def _remerge_others(self):
        """New rates or settings also apply to the batch's other jobs."""
        for job in self.jobs:
            if job is not self.cur and job.docs:
                remerge(job, self.s)

    def _delivery_changed(self, _idx):
        name = self.delivery.currentData() or ""
        self.case.fields["delivery"] = FieldState(name, SRC_USER, 1.0, [name])
        self._apply_speed()

    def _apply_speed(self):
        """Re-derives rate and delivery date from the chosen sheet/speed (unless typed by the user)."""
        self.case.fields["delivery"].value = self.delivery.currentData() or ""
        if self.case.fields["delivery"].value == "Other" and self.rows["rate"].state.source != SRC_USER:
            self.case.fields["rate"] = FieldState()
        refresh_rate(self.case, self.s)
        if self.s.fill_delivery_date:
            refresh_delivery_date(self.case, self.s)
        for k in ("rate", "delivery_date"):
            if self.rows[k].state.source != SRC_USER:
                self.rows[k].set_state(self.case.fields[k])
        self._show_rate_info()

    def _open_log_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logfile.log_dir())))

    def _copy_diagnostics(self):
        QApplication.clipboard().setText(logfile.diagnostics(self.s))
        self._toast("Copied: paste it into your message. It has no case details.")

    def _open_sheets_folder(self):
        from ..rates import sheets_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(sheets_dir(self.s.rate_sheets_dir))))

    def _reload_sheets(self):
        self.s.reload_rates()
        self._fill_sheet_box()
        self._apply_speed()
        self._remerge_others()
        self._toast(f"Loaded {len(self.s.sheets()[0])} rate sheet(s).")

    def _set_opt(self, name, value):
        setattr(self.s, name, value)
        self.s.save()

    # ------------------------------------------------------- status
    def _set_status(self, text: str, state: str, mood: str | None = None):
        self.status.setText(text)
        self.status.setProperty("state", state)
        repolish(self.status)
        if self.s.show_djinn:
            self.drop.set_mood(mood or {"busy": "working", "ok": "done", "warn": "stumped"}.get(state, "working"))
        else:
            self.drop.set_mood(None)

    def _update_status(self):
        self._job_status()
        if len(self.jobs) > 1:
            self._refresh_job_labels()

    def _job_status(self):
        busy = self.ai_pending > 0
        self.busy.setVisible(busy)
        if not self.cur.docs:
            self._set_status("Drop a document to begin", "")
            return
        self._sync_from_ui()
        missing = [FIELD_LABELS[k] for k in self.case.missing_required()]
        review = [k for k, r in self.rows.items() if r.edit.property("review")]
        if busy:
            self._set_status(f"Asking {self.s.ollama_model}…  (you can keep editing)", "busy")
        elif missing or review:
            parts = []
            if missing:
                parts.append(f"{len(missing)} missing")
            if review:
                parts.append(f"{len(review)} to review")
            # the djinn is only stumped when something required is missing
            self._set_status("Ready to fill  ·  " + ", ".join(parts), "warn", "stumped" if missing else "done")
            self.status.setToolTip("Missing: " + ", ".join(missing) if missing else "Highlighted fields are guesses")
        else:
            self._set_status("✓  Ready to fill", "ok")
            self.status.setToolTip("")

    def _toast(self, msg: str):
        self.statusBar().showMessage(msg, 8000)

    # --------------------------------------------------------- fill
    def fill(self):
        self._sync_from_ui()
        case = self.case
        # Ask about required fields that are blank, and fields with competing values
        questions = []
        for key in REQUIRED_KEYS:
            fs = case.fields[key]
            if not fs.value or (fs.source != SRC_USER and len([a for a in fs.alternatives if a != fs.value]) > 0
                                and fs.confidence < 0.8):
                questions.append((key, fs.value, fs.alternatives))
        real = [a for a in case.attorneys if not a.is_placeholder() and (a.name or a.firm)]
        ask_att = real and not any(a.checked for a in case.attorneys)
        if questions or ask_att:
            dlg = ClarifyDialog(questions, case.attorneys if ask_att else None, self)
            if dlg.exec() != ClarifyDialog.Accepted:
                return
            for key, val in dlg.answers().items():
                case.fields[key] = FieldState(val, SRC_USER, 1.0, case.fields[key].alternatives)
                self.rows[key].set_state(case.fields[key])
            chosen = dlg.checked_attorneys()
            if chosen is not None:
                for i, a in enumerate(case.attorneys):
                    a.checked = i in chosen
                self._show_attorneys()

        out_dir = out_dir_for(self.cur, self.s)
        try:
            paths = fill_all(case, self.s, out_dir)
        except PermissionError as e:
            QMessageBox.warning(self, "Could not save", f"{e}\n\nIs the PDF open in another program?")
            return
        except Exception as e:
            log_error("could not fill the form", e)
            QMessageBox.critical(self, "Could not fill the form", f"{type(e).__name__}: {e}")
            return
        log.info("filled %d form(s), form type %s", len(paths), self.s.form_choice)
        if self.s.open_after:
            for p in paths:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        box = QMessageBox(self)
        box.setWindowTitle("Saved")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Saved {len(paths)} form{'s' if len(paths) != 1 else ''}:\n\n" +
                    "\n".join(p.name for p in paths) + f"\n\nin {out_dir}")
        open_folder = box.addButton("Open folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() == open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))
        self.cur.saved, self.cur.error = paths, ""
        self._update_status()
        self._set_status(f"✓  Saved {len(paths)} form(s)", "ok")

    def fill_all_jobs(self):
        """Fills the forms of every ticked job without asking questions."""
        if len(self.jobs) < 2:
            return self.fill()
        self._sync_from_ui()
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        if not chosen:
            return
        gaps = [j for j in chosen if j.problems()]
        if gaps:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Some jobs are incomplete")
            box.setText(f"{len(gaps)} of {len(chosen)} jobs (marked ⚠) would have blanks on the form:\n\n"
                        + "\n".join(f"•  {j.title()}:  {', '.join(j.problems())}" for j in gaps[:8])
                        + ("\n…" if len(gaps) > 8 else ""))
            ready = box.addButton(f"Fill the {len(chosen) - len(gaps)} complete ones", QMessageBox.AcceptRole)
            everything = box.addButton("Fill all, leave blanks", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            ready.setEnabled(len(chosen) > len(gaps))
            box.exec()
            if box.clickedButton() == ready:
                chosen = [j for j in chosen if not j.problems()]
            elif box.clickedButton() != everything:
                return
        gen = self.gen
        self.fill_all_btn.setEnabled(False)
        self.fill_btn.setEnabled(False)
        self.busy.setVisible(True)

        def step(i, n, name):
            self.busy.setRange(0, n)
            self.busy.setValue(i)
            self._set_status(f"Filling {i + 1} of {n}…", "busy")

        def done(paths):
            self.busy.setRange(0, 0)
            self.fill_btn.setEnabled(True)
            if gen != self.gen:
                return
            failed = [j for j in chosen if j.error]
            for j in chosen:
                if j.saved and not j.error:
                    j.include = False  # "Fill all" again only does what is left
            self._update_status()
            folders = list(dict.fromkeys(str(p.parent) for p in paths))
            text = f"Saved {len(paths)} form{'s' if len(paths) != 1 else ''} for " \
                   f"{len(chosen) - len(failed)} job{'s' if len(chosen) - len(failed) != 1 else ''}"
            text += f" in\n{folders[0]}" if len(folders) == 1 else f" in {len(folders)} folders." if folders else "."
            if failed:
                text += f"\n\n{len(failed)} could not be saved (is a PDF open in another program?):\n" + \
                        "\n".join(f"•  {j.title()}: {j.error[:90]}" for j in failed[:6])
            box = QMessageBox(self)
            box.setWindowTitle("Batch finished")
            box.setIcon(QMessageBox.Warning if failed else QMessageBox.Information)
            box.setText(text)
            box.setDetailedText("\n".join(str(p) for p in paths))
            open_folder = box.addButton("Open folder", QMessageBox.ActionRole) if folders else None
            box.addButton(QMessageBox.Ok)
            box.exec()
            if open_folder is not None and box.clickedButton() == open_folder:
                for f in folders[:3]:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(f))
            self._set_status(f"✓  Saved {len(paths)} form(s)", "warn" if failed else "ok")

        def crashed(msg):
            self.busy.setRange(0, 0)
            self.fill_btn.setEnabled(True)
            self._update_status()
            QMessageBox.critical(self, "Could not fill the forms", msg)

        self.runner.start(fill_jobs, chosen, self.s, batch=list(self.jobs),
                          on_done=done, on_error=crashed, on_progress=step)

    # ------------------------------------------------------- misc
    def new_job(self):
        if len(self.jobs) > 1 and QMessageBox.question(
                self, "New job", f"Clear all {len(self.jobs)} jobs of this batch and start over?"
        ) != QMessageBox.Yes:
            return
        self.gen += 1
        self.jobs = [Job()]
        self.cur = self.jobs[0]
        self.ai_pending = 0
        self.busy.setRange(0, 0)
        self._refresh_jobs()
        self.input_list.clear()
        self.paste.clear()
        self._show_case()
        self.busy.setVisible(False)
        self._set_status("Drop a document to begin", "")

    def open_settings(self, first_run: bool = False):
        dlg = SettingsDialog(self.s, self, first_run=first_run)
        if dlg.exec():
            apply_theme(self.app, self.s.theme)
            self.per_email.setChecked(self.s.per_email)
            self.sign_rep.setChecked(self.s.sign_reporter)
            self.form_choice.setCurrentIndex(max(0, self.form_choice.findData(self.s.form_choice)))
            self.s.reload_rates()
            self._fill_sheet_box()
            self._check_ai()
            if self.cur.docs:
                self._remerge()
            self._apply_speed()
            self._remerge_others()
            self._update_status()

    def closeEvent(self, e):
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.s.save()
        super().closeEvent(e)
