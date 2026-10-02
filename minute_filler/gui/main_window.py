"""Main window: drop zone, job list and paste box on the left, the current job's editable fields on the right,
and the Outputs box at the bottom: the Generate buttons and a panel per output with its options (greyed out
while the output is unticked). The Invoice panel's speeds, Extras... and Customize... set what the current
job's invoice offers and shows, and Who ordered... which attorney ordered which pages of the day; with Generate
all, the days of one case share one invoice per attorney (batch.joint_invoice).
File -> Lock finished PDFs saves copies whose fields can no longer be changed.

Documents are read, the AI is asked and batches are made on a thread pool (workers.Runner). Their results
are applied on the UI thread; the results of work started before "New job" are dropped (see MainWindow.gen)."""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QGridLayout, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import log as logfile
from ..log import log
from ..batch import (Job, expand_paths, files_to_make, fill_jobs, group, ident, input_folders, out_dir_for,
                     read_loaders, remerge, same_case)
from ..extract_llm import OllamaExtractor
from ..dates import quick_date
from ..deliver import generate
from ..ingest import ingest_file, ingest_pil, ingest_text
from ..merge import refresh_delivery_date, refresh_rate
from ..models import (Attorney, CaseInfo, FIELD_LABELS, FieldState, PROC_TYPES, REQUIRED_KEYS, SRC_AI,
                      SRC_USER)
from ..runsheet import find_sheets, run_sheet_summary, runsheets_folder, transcript_pages
from ..settings import OUTPUTS, Settings
from .dialogs import ClarifyDialog, RunSheetDialog, SettingsDialog
from .theme import apply_theme
from .widgets import (ASSETS, open_path, open_url, plural, refill_combo, repolish, rounded, set_checks,
                      show_save_error)
from .workers import Runner

FILE_FILTER = ("Documents (*.pdf *.jpg *.jpeg *.png *.heic *.tif *.tiff *.bmp *.webp *.eml *.txt *.docx);;"
               "All files (*.*)")
ATT_COLS = ["", "Name", "Firm", "Address", "Phone", "Fax", "Email", "Party / role", "Source"]
ATT_FIELDS = [None, "name", "firm", "address", "phone", "fax", "email", "party", "source"]


PARTIES_TIP = ("How many parties ordered: the original and the index are split between them,\n"
               "and each gets their own copy (normally the number of ticked attorneys)")
WHO_TIP = ("Which attorney ticked on this day ordered which of its pages: pages ordered together\n"
           "share the original and the index, and each attorney pays for their own copy")


def _price_lines(firms) -> tuple[str, bool]:
    """The Invoice panel's prices, two speeds a line so the panel stays narrow enough for a small window:
    "Regular $63.00 · Expedite $76.00 each" when every attorney pays the same, else a line per attorney
    ("Alex B. Counsel: Regular $815.50 · Expedite $977.00"), and whether it is a line per attorney.
    firms: invoice.firm_invoices."""
    from ..invoice_calc import fmt

    def pairs(quotes) -> list[str]:
        each = [f"{q.speed} {fmt(q.per_party)}" for q in quotes]
        return [" · ".join(each[i:i + 2]) for i in range(0, len(each), 2)]

    if not firms:
        return "", False
    if len({tuple(q.per_party for q in f.quotes) for f in firms}) == 1:
        shared = len(firms) > 1 or any(q.parties > 1 for q in firms[0].quotes)
        return "\n".join(pairs(firms[0].quotes)) + (" each" if shared else ""), False
    lines, seen = [], set()
    for f in firms:
        name = (f.atty.name or f.atty.firm) if f.atty else "(no attorney)"
        if (name, id(f.quotes)) in seen:
            continue
        seen.add((name, id(f.quotes)))
        name = name if len(name) <= 24 else name[:23] + "…"
        rows = pairs(f.quotes)
        lines.append(f"{name}: {rows[0]}" if rows else name)
        lines += ["      " + r for r in rows[1:]]
    return "\n".join(lines), True


def _billing_changed(job, copy) -> bool:
    """The job was given what changes its invoice while Generate all worked on its copy (made when the batch
    started): other documents, another page count or other Who ordered... rows. What the batch billed is then
    not all there is to bill."""
    return ([id(d) for d in job.docs] != [id(d) for d in copy.docs] or job.portions != copy.portions
            or job.invoice_pages() != copy.invoice_pages())


def _reason(error: str, width: int = 160) -> str:
    """An error for the batch's message: without "PermissionError: " in front, and long enough to say why."""
    error = re.sub(r"^(?:PermissionError|OSError|ValueError): ", "", error)
    return error if len(error) <= width else error[:width - 1] + "…"


# ------------------------------------------------------------------ widgets

class FieldRow(QWidget):
    """Editor + suggestions menu + source badge for one form field.

    Typing makes the value the user's own (source "you") and calls on_edit(key). Values set by the program
    (set_text, set_state) do not call on_edit.
    """

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
        # QPlainTextEdit has no textEdited (user-only) signal, so set_text raises this flag instead.
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
        """What the box shows, without leading or trailing spaces."""
        return self.edit.toPlainText().strip() if self.multiline else self.edit.text().strip()

    def set_text(self, t: str) -> None:
        """Shows t without treating it as typed by the user."""
        shown = self.edit.toPlainText() if self.multiline else self.edit.text()
        if shown == t or (self.edit.hasFocus() and shown.strip() == t):
            return  # unchanged: leave the cursor (and a space just typed) where they are
        self._loading = True
        if self.multiline:
            self.edit.setPlainText(t)
        else:
            self.edit.setText(t)
        self._loading = False

    def set_state(self, st: FieldState) -> None:
        """Shows a field's value, source badge and other suggestions."""
        self.state = st
        self.set_text(st.value)
        self._refresh()

    def _refresh(self) -> None:
        """Updates the badge, the review/missing highlight and the suggestions menu from self.state.
        A value not typed by the user is marked for review when the AI suggested it or its confidence is
        below 0.6."""
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
        """Takes a suggestion from the menu, as if the user had typed it."""
        self.set_text(value)
        self._changed()

    def _changed(self) -> None:
        """The user changed the text: it becomes their value (source "you", full confidence)."""
        if self._loading:
            return
        self.state = FieldState(self.text(), SRC_USER, 1.0, self.state.alternatives)
        self._refresh()
        self.on_edit(self.key)


COMPACT_BELOW = 960  # before the window is shown: window height (px) under which the drop zone is small


class DropZone(QFrame):
    """The big drop target. Dropped files go to on_files(paths), text to on_text(str), a picture to
    on_image(QImage); the Browse button calls on_browse(). It also shows the djinn pictures."""

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
        self.mood = None     # (mood, width) of the picture loaded now, so it is not reloaded for nothing
        self.wanted = None   # the mood asked for last, shown again at the new size by set_compact
        self.compact = False
        # (mood, width) -> picture, made once: the window's _size_drop switches between the sizes as it measures
        self._pixmaps: dict = {}
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
        if (mood, width) not in self._pixmaps:
            self._pixmaps[(mood, width)] = rounded(ASSETS / f"djinn_{mood}.jpg", width, 12)
        self.djinn.setPixmap(self._pixmaps[(mood, width)])
        self.djinn.setToolTip({"working": "The djinn is on it…", "done": "Ready to fill!",
                               "stumped": "Something needs your attention"}.get(mood, ""))

    def set_compact(self, on: bool) -> None:
        """Switches to the small layout (on) or back: a smaller picture leaves room for the list of jobs, or
        for the inputs in a short window."""
        if on != self.compact:
            self.compact = on
            self.set_mood(self.wanted)

    def _hover(self, on: bool):
        """Highlights the drop zone while something is dragged over it (the style sheet's [hover="true"])."""
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
    """Passes dropped or pasted data to one handler: local files first, then a picture, then text.
    Returns False when there was nothing it could use."""
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


def _path_key(path: str) -> str:
    """One spelling per file, to tell whether it is loaded already."""
    return str(Path(path).resolve()).lower()


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """A section card (with a heading when title is given) and the layout to put its contents in."""
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
    """The app's window. It holds a list of jobs (one per case and date; more than one is a batch) and shows
    one of them, self.cur, in the editor on the right.

    Background work and the state that guards it:
      gen       bumped by "New job"; a result whose gen is old is dropped.
      work      reads and batches still running (the progress bar shows while it is above 0).
      ai_pending  AI questions still running.
      filling   "Generate all" is making files on another thread. It works on copies of the jobs and the
                settings. Until it is done, Generate, Generate all and New job only say "one moment".
      _loading  files being read now, so dropping the same file again does not load it twice.
    """

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
        self.work = 0                   # documents being read / batches being made in the background
        self.filling = False            # "Generate all" is running on another thread
        self._loading: set[str] = set()  # files being read right now (so a second drop doesn't add them twice)

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
        """The current job's case (what the editor shows)."""
        return self.cur.case

    @case.setter
    def case(self, value: CaseInfo) -> None:
        self.cur.case = value

    @property
    def proc_touched(self) -> bool:
        """The user changed the current job's proceeding types, so a new merge must keep them."""
        return self.cur.proc_touched

    @proc_touched.setter
    def proc_touched(self, value: bool) -> None:
        self.cur.proc_touched = value

    @property
    def att_touched(self) -> bool:
        """The user changed the current job's attorney table, so a new merge must keep it."""
        return self.cur.att_touched

    @att_touched.setter
    def att_touched(self, value: bool) -> None:
        self.cur.att_touched = value

    # ---------------------------------------------------------- layout
    def _build(self):
        """Builds the window: header, the left column (drop zone, jobs, inputs, paste box), the scrolling form
        on the right, the Outputs box (with the Generate buttons) at the bottom, the menu and the shortcuts."""
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
        rec_btn = QPushButton("Records")
        rec_btn.setToolTip("Invoices (mark them paid) and everything made so far (Ctrl+R)")
        rec_btn.clicked.connect(self.open_records)
        head.addWidget(rec_btn)
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
                                 "untick the ones \"Generate all\" should leave out.")
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
        self.input_list.setMinimumHeight(44)  # (a short window: room for the big djinn picture, see _size_drop)
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
        self.paste.setMinimumHeight(52)
        # it takes the room left over, but doesn't ask for any: otherwise the left column's scroll area counts
        # its preferred 190 px and shows a scroll bar in a window with room to spare
        self.paste.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
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
        # In a short window the column scrolls, rather than its parts being drawn over each other or the
        # Outputs box being squeezed (the column needs about 620 px in a batch).
        self.left_scroll = left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setWidget(left)
        left_scroll.setMinimumWidth(left.minimumSizeHint().width() + 12)  # (room for the scroll bar)
        split.addWidget(left_scroll)

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

        # footer: the Outputs box - the Generate buttons by its title, then a column per output with its own options
        foot = QFrame()
        foot.setObjectName("card")
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(16, 10, 16, 10)
        fv.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel("Outputs")
        title.setObjectName("section")
        top.addWidget(title)
        top.addStretch(1)
        self.fill_btn = QPushButton("Generate")
        self.fill_btn.setObjectName("primary")
        self.fill_btn.clicked.connect(self.fill)
        top.addWidget(self.fill_btn)
        self.fill_all_btn = QPushButton("Generate all")
        self.fill_all_btn.setObjectName("primary")
        self.fill_all_btn.setToolTip("Make the ticked outputs for every ticked job (Ctrl+Shift+Enter)")
        self.fill_all_btn.clicked.connect(self.fill_all_jobs)
        top.addWidget(self.fill_all_btn)
        fv.addLayout(top)
        self.output_grid = QGridLayout()  # a panel per output, four in a row or in three columns (_place_output_cols)
        self.output_grid.setHorizontalSpacing(10)
        self.output_grid.setVerticalSpacing(10)
        fv.addLayout(self.output_grid)
        self._output_cols: list[QWidget] = []
        self._cols_per_row = 0
        self.output_boxes: dict[str, QCheckBox] = {}
        self.output_opts: dict[str, QWidget] = {}  # each output's options, greyed out while it is unticked
        tips = {"agreement": "The UCS Court Reporter Minute Agreement Form, one for each ticked attorney",
                "mofr": "The court's Minute Order Form/Receipt (your parts of it)"}

        def panel(key: str) -> QFormLayout:
            """A bordered panel headed by the output's make-box, with a line under it; returns the form for
            its options (label: value rows)."""
            holder = QFrame()
            holder.setObjectName("outputPanel")
            col = QVBoxLayout(holder)
            col.setContentsMargins(12, 8, 12, 10)
            col.setSpacing(6)
            cb = QCheckBox(OUTPUTS[key])
            cb.setObjectName("outputHead")
            cb.setChecked(key in self.s.outputs)
            cb.setToolTip(tips.get(key, ""))
            cb.toggled.connect(self._outputs_changed)
            self.output_boxes[key] = cb
            col.addWidget(cb)
            rule = QFrame()
            rule.setObjectName("outputRule")
            rule.setFixedHeight(1)
            col.addWidget(rule)
            body = QWidget()
            form = QFormLayout(body)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(6)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
            col.addWidget(body)
            col.addStretch(1)
            self.output_opts[key] = body
            self._output_cols.append(holder)
            return form

        # Minute agreement
        ag = panel("agreement")
        from ..fill import FORMS
        self.form_choice = QComboBox()
        for key, (_, label) in FORMS.items():
            self.form_choice.addItem(label, key)
        self.form_choice.setCurrentIndex(max(0, self.form_choice.findData(self.s.form_choice)))
        self.form_choice.currentIndexChanged.connect(
            lambda _: self._set_opt("form_choice", self.form_choice.currentData()))
        ag.addRow("Form:", self.form_choice)
        self.per_email = QCheckBox("\"Per email\" on attorney signature")
        self.per_email.setToolTip("Writes \"per email\" in the attorney's signature spot")
        self.per_email.setChecked(self.s.per_email)
        self.per_email.toggled.connect(lambda v: self._set_opt("per_email", v))
        ag.addRow("Signing:", self.per_email)
        self.sign_rep = QCheckBox("Sign as court reporter")
        self.sign_rep.setToolTip("Puts your signature picture on the court reporter line - or types your name,\n"
                                 "if no picture is chosen in Settings → My info.")
        self.sign_rep.setChecked(self.s.sign_reporter)
        self.sign_rep.toggled.connect(lambda v: self._set_opt("sign_reporter", v))
        ag.addRow("", self.sign_rep)

        # Invoice
        inv = panel("invoice")
        speeds = QWidget()
        self.inv_speeds_grid = QGridLayout(speeds)
        self.inv_speeds_grid.setContentsMargins(0, 0, 0, 0)
        self.inv_speeds_grid.setHorizontalSpacing(12)
        self.inv_speeds_grid.setVerticalSpacing(2)
        self.inv_speed_boxes: dict[str, QCheckBox] = {}
        self._fill_invoice_speeds()
        inv.addRow("Speeds:", speeds)
        self.inv_parties = QSpinBox()
        self.inv_parties.setRange(1, 20)
        self.inv_parties.setToolTip(PARTIES_TIP)
        self.inv_parties.valueChanged.connect(self._invoice_parties_changed)
        parties = QHBoxLayout()
        parties.setSpacing(8)
        parties.addWidget(self.inv_parties)
        self.inv_who = QPushButton("Who ordered…")
        self.inv_who.clicked.connect(self._who_ordered)
        parties.addWidget(self.inv_who)
        parties.addStretch(1)
        inv.addRow("Parties:", parties)

        def with_button(w: QWidget, text: str, tip: str, slot) -> QHBoxLayout:
            """[widget] [button…] on one row."""
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(w)
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            row.addStretch(1)
            return row

        self.inv_extras_info = QLabel("")
        self.inv_extras_info.setObjectName("muted")
        inv.addRow("Includes:", with_button(
            self.inv_extras_info, "Extras…", "The e-mailed copy and the index, for this job's invoice only\n"
            "(your defaults are in Settings → Invoice)", self._invoice_extras))
        self.inv_detail = QCheckBox("Show granular detail")
        self.inv_detail.setToolTip("For this job's invoice only (off for every new job): also show what Customize…\n"
                                   "lists, such as the page count, the price per page and the charges in each amount.\n"
                                   "Unticked, the invoice shows each speed's turnaround and amount only.")
        self.inv_detail.toggled.connect(self._invoice_detail_changed)
        inv.addRow("Shows:", with_button(
            self.inv_detail, "Customize…", "What granular detail shows on this job's invoice\n"
            "(your defaults are in Settings → Invoice)", self._invoice_show))
        self.inv_info = QLabel("")
        self.inv_info.setObjectName("muted")
        inv.addRow("Prices:", self.inv_info)

        # MOFR
        mo = panel("mofr")
        self.mofr_division = QComboBox()
        self.mofr_division.addItem("Civil", "civil")
        self.mofr_division.addItem("Criminal", "criminal")
        self.mofr_division.setToolTip("Which box is ticked on the MOFR (civil cases also get the case's own title\n"
                                      "instead of the printed \"People v.\")")
        self.mofr_division.setCurrentIndex(max(0, self.mofr_division.findData(self.s.mofr_division)))
        self.mofr_division.currentIndexChanged.connect(
            lambda _: self._set_opt("mofr_division", self.mofr_division.currentData()))
        mo.addRow("Case:", self.mofr_division)

        # Run sheet
        rs = panel("runsheet")
        self.rs_existing = QComboBox()
        for key, label in (("ask", "Ask me"), ("add", "Add to it"), ("new", "Start a new one")):
            self.rs_existing.addItem(label, key)
        self.rs_existing.setToolTip("When the case already has a run sheet (the same index number or case name)")
        self.rs_existing.setCurrentIndex(max(0, self.rs_existing.findData(self.s.runsheet_existing)))
        self.rs_existing.currentIndexChanged.connect(
            lambda _: self._set_opt("runsheet_existing", self.rs_existing.currentData()))
        rs.addRow("If one exists:", self.rs_existing)
        self.rs_info = QLabel("")
        self.rs_info.setObjectName("muted")
        self.rs_info.setVisible(False)
        rs.addRow(self.rs_info)
        # under the options, not among them: the folder can be opened while Run sheet is unticked too
        self.rs_folder = QLabel('<a href="open">Open the run sheets folder</a>')
        self.rs_folder.setToolTip("Opens the folder the run sheets are kept in (Settings → Run sheet)")
        self.rs_folder.linkActivated.connect(lambda _: self._open_run_sheets_folder())
        col = self._output_cols[-1].layout()
        col.insertWidget(col.count() - 1, self.rs_folder)  # above the stretch
        self._place_output_cols()
        root.addWidget(foot)
        self._refresh_jobs()

        self._build_menu()
        QShortcut(QKeySequence.Paste, self, activated=self._paste_shortcut)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_job)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.browse)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.fill)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, activated=self.fill_all_jobs)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.open_records)

    def _build_menu(self):
        """The File and Help menus."""
        from .dialogs import AboutDialog, FEEDBACK_URL
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction("&New job", self.new_job)          # Ctrl+N handled by the window shortcut
        m.addAction("&Open documents…", self.browse)
        m.addAction("Open a &folder of documents (batch)…", self.browse_folder)
        m.addSeparator()
        m.addAction("Open &rate sheets folder", self._open_sheets_folder)
        m.addAction("&Settings…", self.open_settings)
        m.addSeparator()
        m.addAction("&Records (invoices and history)…", self.open_records)
        m.addAction("Save the invoice &spreadsheet template…", self._save_invoice_template)
        m.addAction("&Lock finished PDFs (no more changes)…", self.lock_pdfs)
        m.addSeparator()
        m.addAction("E&xit", self.close)
        m = mb.addMenu("&Help")
        m.addAction("Set up the &AI helper (Ollama)…", self._ai_help)
        m.addAction("Send &feedback…", lambda: open_url(FEEDBACK_URL))
        m.addAction("Open the &log folder", self._open_log_folder)
        m.addAction("&Copy details for a problem report", self._copy_diagnostics)
        m.addSeparator()
        m.addAction("&About DjinnItAgreementForm", lambda: AboutDialog(self).exec())

    # --------------------------------------------------------- startup
    def _startup(self):
        """Runs once the window is on screen: Settings on the first run (no name yet), then the AI check."""
        if not self.s.profile.name:
            self.open_settings(first_run=True)
        self._check_ai()

    def _ai_help(self, _link=""):
        """Shows the Ollama setup help, then checks the AI again (a model may have been installed)."""
        from .dialogs import OllamaHelpDialog
        OllamaHelpDialog(self.s.ollama_model, self.s.ollama_host, self).exec()
        self._check_ai()

    def _ai_text(self, text: str):
        """Shows the AI's state under the paste box. A longer or shorter line changes the left column's height,
        so the drop zone is sized again."""
        self.ai_label.setText(text)
        self._size_drop_later()

    def _check_ai(self):
        """Asks Ollama in the background whether the model is ready and shows the answer under the paste box.
        Documents of a single job dropped while it was checking are then sent to the AI."""
        if not self.s.use_ai:
            self.ai_ok = False
            self._ai_text("AI is off (Settings → AI). Using rules only.")
            return
        self._ai_text("Checking AI…")
        self.ai = OllamaExtractor(self.s)

        def done(res):
            self.ai_ok, msg = res
            link = "" if self.ai_ok else '  <a href="setup">How to set up</a>'
            self._ai_text(("● " if self.ai_ok else "○ ") + msg + link)
            # inputs dropped while the check was still running were not sent to the AI
            waiting = [d for d in self.cur.docs if d.ai is None]
            if self.ai_ok and waiting and self.ai_pending == 0 and len(self.jobs) == 1:
                self._maybe_ai(self.cur, waiting)

        self.runner.start(self.ai.status, on_done=done, on_error=lambda m: done((False, m)))

    # ------------------------------------------------------ inputs
    def browse(self):
        """Asks for one or more documents and reads them (Ctrl+O)."""
        files, _ = QFileDialog.getOpenFileNames(self, "Choose document(s)", "", FILE_FILTER)
        if files:
            self.add_files(files)

    def browse_folder(self):
        """Asks for a folder and reads every document in it as a batch."""
        folder =QFileDialog.getExistingDirectory(self, "Choose a folder of documents")
        if folder:
            self.add_files([folder], batch=True)

    def _paste_shortcut(self):
        """Ctrl+V. In a text box it pastes as usual; anywhere else the clipboard's files, picture or text
        are read as a new input."""
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
        """Reads pasted or dropped text as an input named "Pasted text 1", "Pasted text 2"… and shows it in the
        paste box."""
        n =sum(1 for j in self.jobs for d in j.docs if d.ing.name.startswith("Pasted text")) + 1
        self._ingest([lambda: ingest_text(text, f"Pasted text {n}")], [f"Pasted text {n}"])
        if self.paste.toPlainText().strip() != text.strip():
            self.paste.setPlainText(text)

    def add_qimage(self, qimg):
        """Reads a pasted or dropped picture (a QImage), e.g. a screenshot of an e-mail, with OCR."""
        from PIL import Image
        from PySide6.QtCore import QBuffer, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        qimg.save(buf, "PNG")
        import io
        pil = Image.open(io.BytesIO(bytes(buf.data())))
        self._ingest([lambda: ingest_pil(pil, "Pasted image")], ["Pasted image"])

    def add_files(self, paths: list[str], batch: bool = False):
        """Reads files, and the documents in folders. Files already loaded or still being read are skipped.
        Several documents about different cases (or batch=True) start a batch."""
        gen = self.gen

        def find():  # off the UI thread: a big folder takes a while to go through
            return [(p, _path_key(p)) for p in expand_paths(paths)]

        def found(everything):
            if not self._work_done(gen):
                return
            # judged now, not when dropped: the same file dropped twice in a row is still being read
            loaded = {_path_key(d.path) for j in self.jobs for d in j.docs if d.path} | self._loading
            fresh = [(p, key) for p, key in everything if key not in loaded]
            if len(fresh) < len(everything):
                self._toast(f"Skipped {len(everything) - len(fresh)} document(s) that are already loaded.")
            if fresh:
                files = [p for p, _ in fresh]
                self._ingest([(lambda p=p: ingest_file(p)) for p in files], [Path(p).name for p in files],
                             files, batch, {key for _, key in fresh})
            else:
                self._update_status()
                if not everything:
                    self._toast("No documents found there.")

        def failed(msg):
            if not self._work_done(gen):
                return
            self._update_status()
            QMessageBox.warning(self, "Could not open that", msg)

        self.work += 1
        if any(Path(p).is_dir() for p in paths):
            self._set_status("Looking for documents…", "busy")
            self.busy.setVisible(True)
        self.runner.start(find, on_done=found, on_error=failed)

    def _work_done(self, gen: int) -> bool:
        """A background read or batch has ended. False when "New job" was clicked since it started:
        its result is then dropped (and the counters were already reset)."""
        if gen != self.gen:
            return False
        self.work = max(0, self.work - 1)
        self._idle()
        return True

    def _ingest(self, loaders, names, paths=None, batch=False, keys=frozenset()):
        """Reads inputs in the background and adds them to the jobs.

        loaders: one function per input that returns the Ingested document; names: what to call each in
        messages; paths: the files (None for pasted text or pictures); keys: the files' _path_key, held in
        self._loading until the read ends. With a single job and documents all about one case, they are
        added to that job; otherwise (or with batch=True) they are sorted into jobs by case and date.
        """
        # the reading thread gets its own copy of the settings: Settings may change them meanwhile
        gen, target, s = self.gen, self.cur, deepcopy(self.s)
        self.work += 1
        self._loading |= keys
        self._set_status(f"Reading {names[0]}…" if len(names) == 1 else f"Reading {len(names)} documents…", "busy")
        self.busy.setVisible(True)

        def work(progress):
            return read_loaders(loaders, names, s, paths, progress)

        def step(i, n, name):
            if gen == self.gen and n > 1:
                self.busy.setRange(0, n)
                self.busy.setValue(i)
                self._set_status(f"Reading {i + 1} of {n}:  {name[:40]}", "busy")

        def done(result):
            if not self._work_done(gen):
                return
            self._loading -= keys
            docs, errors = result
            for d in docs:
                for w in d.ing.warnings:
                    self._toast(w)
            if docs:
                self._sync_from_ui()
                one_job = len(self.jobs) == 1 and target in self.jobs and not batch
                if one_job and (len(docs) == 1 or len(group(docs, self.s)) == 1):
                    # the usual way: everything dropped is about the job on screen
                    target.docs += docs
                    target.unbill()  # new documents may mean new pages to bill
                    remerge(target, self.s)
                    self._show_job()
                    self._maybe_ai(target, docs)
                else:
                    before = [j for j in self.jobs if not j.is_empty()]
                    count = len(before)
                    self.jobs = group(docs, self.s, before) or [Job()]
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
            if not self._work_done(gen):
                return
            self._loading -= keys
            self._set_status("Could not read that input", "warn")
            QMessageBox.warning(self, "Could not read input", msg)

        self.runner.start(work, on_done=done, on_error=failed, on_progress=step)

    def _unreadable(self, errors: list[str], total: int):
        """Says which inputs could not be read: the error itself for a single input, else a list (first 8)."""
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
        """Asks the model about documents added to a single job (batches use the rules only): pictures always,
        e-mails and text when Settings say so, PDFs only while a required field is still blank. Each answer
        merges the job again, unless "New job" was clicked or the document was removed meanwhile."""
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
                self._ai_text(f"○ AI failed on {d.ing.name}: {msg[:120]}")
                self._update_status()

            self.runner.start(self.ai.extract, d.ing, on_done=done, on_error=failed)

    def _input_menu(self, pos):
        """Right-click on an input: show its text, move it to a job of its own, or remove it."""
        item = self.input_list.itemAt(pos)
        if not item:
            return
        idx = self.input_list.row(item)
        if not 0 <= idx < len(self.cur.docs):
            return
        job, doc = self.cur, self.cur.docs[idx]
        m = QMenu(self)
        view = m.addAction("Show extracted text")
        split = m.addAction("Move to a job of its own") if len(self.cur.docs) > 1 else None
        rem = m.addAction("Remove from job")
        act = m.exec(self.input_list.mapToGlobal(pos))
        if act is None or job is not self.cur or doc not in job.docs:  # (the job changed while the menu was open)
            return
        if act == view:
            box = QMessageBox(self)
            box.setWindowTitle(doc.ing.name)
            box.setText("Text read from this input:")
            box.setDetailedText(doc.ing.text or "(no text)")
            box.exec()
        elif act in (rem, split):
            self._sync_from_ui()
            job.docs.remove(doc)
            remerge(self.cur, self.s)
            if act == split:
                job = Job(docs=[doc], batch=True)
                remerge(job, self.s)
                self.jobs.insert(self.jobs.index(self.cur) + 1, job)
            self._refresh_jobs()
            self._show_job()

    # ------------------------------------------------------------ jobs
    def _job_text(self, job: Job) -> str:
        """A job's line in the list, e.g. "✓ Jane Roe v. Sam Poe  ·  9/14/2026  ·  712345/2021".
        ✓ = saved (or billed on another day's invoice), ⚠ = it failed or needs a look."""
        done = (job.saved or job.invoiced) and not job.error
        mark = "✓ " if done else "⚠ " if job.error or job.problems() else ""
        title = job.title()  # kept short so the date, which tells a case's jobs apart, stays in view
        bits = [title if len(title) <= 30 else title[:29].rstrip() + "…"]
        bits += [v for v in (job.case.get("dates"), job.case.get("index_no")) if v]
        if len(job.docs) > 1:
            bits.append(f"{len(job.docs)} documents")
        return mark + "  ·  ".join(dict.fromkeys(bits))

    def _job_tip(self, job: Job) -> str:
        """A job's tooltip: its documents, its problems, who the form is for and what was saved."""
        tip = [d.ing.name for d in job.docs] or ["(no documents)"]
        tip += ["⚠ " + p for p in job.output_problems(self.s.outputs)]
        who = [a.name or a.firm for a in job.case.attorneys if a.checked]
        tip.append("Form for: " + ("; ".join(who) if who else "(blank attorney block)"))
        if job.error:
            tip.append("Not saved - " + job.error)
        tip += [f"Saved: {p.name}" for p in job.saved]
        if job.invoiced:
            tip.append("Invoiced: Generate all won't bill this day again")
        return "\n".join(tip)

    def _refresh_jobs(self):
        """Rebuilds the list of jobs; it is only shown for a batch (more than one job)."""
        multi = len(self.jobs) > 1
        for w in (self.jobs_label, self.job_list, self.fill_all_btn):
            w.setVisible(multi)
        self._size_drop()
        self.input_list.setMaximumHeight(72 if multi else 110)
        self.paste.setMaximumHeight(64 if multi else 16777215)
        self.fill_btn.setText("Generate this job" if multi else "Generate")
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
        self._size_drop_later()  # measured again once the column's new contents are laid out

    def _refresh_job_labels(self):
        """Updates each job's line, tooltip and tick, the count above the list and the Generate all button."""
        self.job_list.blockSignals(True)  # setting the ticks must not look like the user clicking them
        for row, job in enumerate(self.jobs):
            item = self.job_list.item(row)
            if item is None:
                continue
            item.setText(self._job_text(job))
            item.setToolTip(self._job_tip(job))
            item.setCheckState(Qt.Checked if job.include else Qt.Unchecked)
        self.job_list.blockSignals(False)
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        need = sum(1 for j in self.jobs if j.output_problems(self.s.outputs) and not j.saved)
        saved = sum(1 for j in self.jobs if j.saved or j.invoiced)
        text = f"Jobs: {len(self.jobs)}"
        if need:
            text += f"  ·  {need} to check (⚠)"
        if saved:
            text += f"  ·  {saved} saved (✓)"
        self.jobs_label.setText(text)
        files = files_to_make(chosen, self.s.outputs, self.s)  # days of a case share a run sheet, invoices too
        self.fill_all_btn.setText(f"Generate all  ({plural(files, 'file')})")
        self.fill_all_btn.setEnabled(bool(chosen) and bool(self.s.outputs) and not self.filling)

    def _show_job(self):
        """Puts the current job in the editor."""
        self.input_list.clear()
        for d in self.cur.docs:
            ing = d.ing
            label = f"{ing.name}   ·   {ing.kind}"
            if ing.page_count:  # a transcript's own pages, without the word index after them
                label += f", {transcript_pages(ing) if d.regex.doc_kind == 'transcript' else ing.page_count} pp"
            if ing.ocr_used:
                label += ", OCR"
            item = QListWidgetItem(label)
            item.setToolTip(ing.text[:1500] or "(no text)")
            self.input_list.addItem(item)
        self._show_case()
        self._update_status()

    def _job_selected(self, row: int):
        """A job was clicked in the list: keep the edits made to the one on screen, then show it."""
        if not 0 <= row < len(self.jobs) or self.jobs[row] is self.cur:
            return
        self._sync_from_ui()
        self.cur = self.jobs[row]
        self._show_job()

    def _job_ticked(self, item):
        """A job's tick changed: Generate all includes or leaves out that job."""
        row = self.job_list.row(item)
        if 0 <= row < len(self.jobs):
            self.jobs[row].include = item.checkState() == Qt.Checked
            self._refresh_job_labels()

    def _job_menu(self, pos):
        """Right-click on the job list: remove a job, tick all or untick all."""
        item = self.job_list.itemAt(pos)
        row = self.job_list.row(item) if item else -1
        job = self.jobs[row] if 0 <= row < len(self.jobs) else None
        m = QMenu(self)
        rem = m.addAction("Remove this job") if job else None
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
        if act is not rem or job not in self.jobs:  # (the list may have changed while the menu was open)
            return
        self._sync_from_ui()
        self.jobs.remove(job)
        if not self.jobs:
            self.jobs = [Job()]
        if self.cur not in self.jobs:
            self.cur = self.jobs[0]
        self._refresh_jobs()
        self._show_job()

    # ----------------------------------------------------- merge/show
    def _remerge(self, job: Job | None = None):
        """Merges a job's documents again (default: the current job). For the job on screen, the edits in
        the editor are taken first and the editor shows the result."""
        job = job or self.cur
        if job is self.cur:
            self._sync_from_ui()
        remerge(job, self.s)
        if job is self.cur:
            self._show_case()

    def _show_case(self):
        """Shows the current job's case in the editor: fields, speed, proceeding boxes, attorneys, invoice line."""
        for key, r in self.rows.items():
            r.set_state(self.case.fields[key])
        self._select_speed(self.case.get("delivery") or self.s.delivery_name(self.s.default_delivery))
        for p, cb in self.proc_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(p in self.case.proc_types)
            cb.blockSignals(False)
        self._show_attorneys()
        self._refresh_outputs()

    def _show_attorneys(self):
        """Fills the attorney table from the current case (without counting as an edit by the user)."""
        self.att.blockSignals(True)
        self.att.setRowCount(0)
        for a in self.case.attorneys:
            self._append_att(a)
        self.att.resizeRowsToContents()
        self.att.blockSignals(False)

    def _append_att(self, a: Attorney):
        """Adds a table row for one attorney. Address lines show joined by " / "; a placeholder row is greyed."""
        r =self.att.rowCount()
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
        """The attorney table as Attorney objects, with " / " in the address turned back into line breaks."""
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
        """Copies the user's edits in the editor into the current job's case: typed fields, the proceeding
        boxes and the attorney table. Call it before the case is merged again, switched or filled."""
        for key, r in self.rows.items():
            if r.state.source == SRC_USER:
                self.case.fields[key] = FieldState(r.text(), SRC_USER, 1.0, r.state.alternatives)
        # (delivery edits are recorded directly by _delivery_changed)
        self.case.proc_types = {p for p, cb in self.proc_boxes.items() if cb.isChecked()}
        self.case.attorneys = self._read_attorneys()

    # ---------------------------------------------------- UI events
    def _field_edited(self, key: str):
        """The user typed in a field (or picked a suggestion): it goes into the case straight away."""
        self.case.fields[key] = self.rows[key].state
        self._update_status()

    def _proc_toggled(self, _):
        """A proceeding box was clicked: later merges keep the user's choice."""
        self.proc_touched = True

    def _att_changed(self, _item):
        """A cell or tick of the attorney table changed: later merges keep the user's table."""
        self.att_touched = True
        self._attorneys_edited()

    def _attorneys_edited(self):
        """A tick or an edit in the attorney table: the invoice's parties and the file counts follow, and so
        do the Who ordered... rows when an attorney's name (or firm) was changed."""
        old, new = self.case.attorneys, self._read_attorneys()
        if len(old) == len(new):  # the same rows (none added or removed): the attorney of each row is the same
            keys = {a.key() for a in new}
            for before, after in zip(old, new):
                if before.key() != after.key() and before.key() not in keys:
                    self.cur.rename_in_portions(before.key(), after.key())
        self.case.attorneys = new
        self._update_status()

    def _add_att_row(self):
        """Adds a ticked, empty attorney row and starts editing its Name cell."""
        self.att.blockSignals(True)
        self._append_att(Attorney(source=SRC_USER, checked=True))
        self.att.blockSignals(False)
        self.att_touched = True
        self._attorneys_edited()
        self.att.editItem(self.att.item(self.att.rowCount() - 1, 1))

    def _remove_att_rows(self):
        """Removes the selected attorney rows."""
        rows = sorted({i.row() for i in self.att.selectedIndexes()}, reverse=True)
        for r in rows:
            self.att.removeRow(r)
        if rows:
            self.att_touched = True
            self._attorneys_edited()

    # ------------------------------------------------ rate sheets / speed
    def _fill_sheet_box(self):
        """Lists the rate sheets (each tooltip shows its speeds), says which files were skipped, then the speeds."""
        sheets, problems = self.s.sheets()
        current = self.s.sheet()
        shown = sheets or [current]
        tips = ["\n".join(sp.label() + (f"  ·  {sp.days} days" if sp.days is not None else "") for sp in sh.speeds)
                + (f"\nRates last updated {sh.updated}" if sh.updated else "") for sh in shown]
        refill_combo(self.sheet_box, [(sh.name, sh.name) for sh in shown], current.name, tips)
        for p in problems:
            self._toast(f"Rate sheet skipped - {p}")
        self._fill_speeds()

    def _fill_speeds(self):
        """Lists the chosen sheet's speeds (with their turnaround days) plus "Other", keeping the speed chosen."""
        keep = self.delivery.currentData() or self.case.get("delivery")
        sheet = self.s.sheet()
        items = []
        for sp in sheet.speeds:
            days = self.s.days_for(sp.name)
            items.append((sp.label() + (f"  ·  {plural(days, 'day')}" if days is not None else ""), sp.name))
        refill_combo(self.delivery, items + [("Other (type the rate yourself)", "Other")])
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
        """The line under the Order card: the copy rate, the speed's other prices and when the rates changed."""
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
        """Another rate sheet was picked: it becomes the default, and every job is priced again from it."""
        name = self.sheet_box.currentData()
        if not name or name == self.s.rate_sheet:
            return
        self.s.rate_sheet = name
        self.s.save()
        self._fill_speeds()
        self._fill_invoice_speeds()
        self._apply_speed()
        self._remerge_others()

    def _remerge_others(self):
        """New rates or settings also apply to the batch's other jobs."""
        for job in self.jobs:
            if job is not self.cur and job.docs:
                remerge(job, self.s)

    def _delivery_changed(self, _idx):
        """The user picked a speed: it counts as their choice, and the rate and delivery date follow it."""
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
        self._refresh_outputs()

    def _open_log_folder(self):
        open_path(logfile.log_dir())

    def _copy_diagnostics(self):
        """Copies the app and system details for a problem report (no case details) to the clipboard."""
        QApplication.clipboard().setText(logfile.diagnostics(self.s))
        self._toast("Copied: paste it into your message. It has no case details.")

    def _open_sheets_folder(self):
        from ..rates import sheets_dir
        open_path(sheets_dir(self.s.rate_sheets_dir))

    def _reload_sheets(self):
        """Reads the rate sheet files again (after they were edited) and prices every job again."""
        self.s.reload_rates()
        self._fill_sheet_box()
        self._fill_invoice_speeds()  # the sheet's speeds may have changed
        self._apply_speed()
        self._remerge_others()
        self._toast(f"Loaded {len(self.s.sheets()[0])} rate sheet(s).")

    def _set_opt(self, name, value):
        """Saves an option changed in the Outputs box (the outputs ticked and each one's options) as the
        default."""
        setattr(self.s, name, value)
        self.s.save()

    # ------------------------------------------------------- status
    def _set_status(self, text: str, state: str, mood: str | None = None):
        """Sets the status pill at the top. state: "" (plain), "busy", "ok" or "warn". When the djinn is on
        (Settings → Options), state also picks his picture unless mood names one."""
        self.status.setText(text)
        self.status.setProperty("state", state)
        repolish(self.status)
        if self.s.show_djinn:
            self.drop.set_mood(mood or {"busy": "working", "ok": "done", "warn": "stumped"}.get(state, "working"))
        else:
            self.drop.set_mood(None)

    def _update_status(self):
        """Brings the invoice line, the status pill and (in a batch) the job list up to date."""
        self._refresh_outputs()
        self._job_status()
        if len(self.jobs) > 1:
            self._refresh_job_labels()

    def _job_status(self):
        """The status pill for the job on screen: asking the AI, fields missing or to review, or ready."""
        busy = self.ai_pending > 0
        self.busy.setVisible(busy or self.work > 0)
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

    def _idle(self):
        """A piece of background work is done: the progress bar goes back to 'busy' style, and stays only
        while the AI or another read is still going."""
        self.busy.setRange(0, 0)
        self.busy.setVisible(self.ai_pending > 0 or self.work > 0)

    def _saved_box(self, title: str, text: str, folders: list, details: str = "", warn: bool = False):
        """The 'files saved' message, with a button that opens the folder(s)."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning if warn else QMessageBox.Information)
        box.setText(text)
        if details:
            box.setDetailedText(details)
        open_folder = box.addButton("Open folder", QMessageBox.ActionRole) if folders else None
        box.addButton(QMessageBox.Ok)
        box.exec()
        if open_folder is not None and box.clickedButton() == open_folder:
            for f in folders[:3]:
                open_path(f)

    def _toast(self, msg: str):
        """A short note at the bottom of the window that goes away after 8 seconds."""
        self.statusBar().showMessage(msg, 8000)

    # --------------------------------------------------------- fill
    def _batch_running(self) -> bool:
        """True (with a note to the user) while Generate all is still making files."""
        if self.filling:
            self._toast("Still making the files of the batch - one moment.")
        return self.filling

    def _commit_edit(self):
        """A cell of the attorney table still being typed in (Ctrl+Enter pressed in it) is taken as typed."""
        editor = QApplication.focusWidget()
        if editor is not None and editor is not self.att and self.att.isAncestorOf(editor):
            self.att.commitData(editor)
            self.att.closeEditor(editor, QAbstractItemDelegate.NoHint)

    def fill(self):
        """Generate: makes the ticked outputs for the job on screen, on the UI thread (Ctrl+Enter).

        Asks first, as needed: whether to go on without the invoice or run sheet when there is no transcript;
        about blank required fields and competing values; who ordered, when no attorney is ticked; and where
        the run sheet takes go. Background work can finish while a question is on screen, so after each one
        the job is checked to still exist and is taken as it is now.
        """
        if self._batch_running():
            return
        self._commit_edit()
        self._sync_from_ui()
        job = self.cur
        case = job.case
        outputs = list(self.s.outputs)
        if not outputs:
            QMessageBox.information(self, "Nothing to make", "Tick what to make first: minute agreement, MOFR, "
                                    "invoice and/or run sheet (in the Outputs box).")
            return
        if job.makeable(outputs) != outputs:
            left_out = " or ".join(OUTPUTS[o].lower() for o in outputs if o not in job.makeable(outputs))
            why = ("An invoice needs pages to bill, and the Pages field says 0." if job.transcript_pages() else
                   "An invoice or a run sheet needs a transcript PDF (for its pages), and this job has none.")
            if QMessageBox.question(
                    self, f"No {left_out}", f"{why}\n\nMake the other outputs without the {left_out}?"
            ) != QMessageBox.Yes:
                return
            if job not in self.jobs or self._batch_running():
                return
            case = job.case  # an AI answer that came in meanwhile merged the job again
            outputs = job.makeable(outputs)
            if not outputs:
                return
        outputs = self._without_unchecked_invoice(job, outputs)
        if not outputs:
            return
        # Ask about required fields that are blank, and fields with competing values (only the case name
        # when neither the agreement nor the MOFR is made)
        questions = []
        for key in REQUIRED_KEYS if {"agreement", "mofr"} & set(outputs) else ["case_name"]:
            fs = case.fields[key]
            if not fs.value or (fs.source != SRC_USER and len([a for a in fs.alternatives if a != fs.value]) > 0
                                and fs.confidence < 0.8):
                questions.append((key, fs.value, fs.alternatives))
        real = [a for a in case.attorneys if not a.is_placeholder() and (a.name or a.firm)]
        # who ordered: only agreements and invoices are addressed to an attorney
        ask_att = real and not any(a.checked for a in case.attorneys) and {"agreement", "invoice"} & set(outputs)
        if questions or ask_att:
            asked = list(case.attorneys)
            dlg = ClarifyDialog(questions, asked if ask_att else None, self)
            if dlg.exec() != ClarifyDialog.Accepted or job not in self.jobs or self._batch_running():
                return
            # While the question was on screen an AI answer may have come in (the job was merged again) or
            # documents still being read may have put another job in the editor: the answers go to the job
            # they were asked about, as it is now.
            case = job.case
            for key, val in dlg.answers().items():
                case.fields[key] = FieldState(val, SRC_USER, 1.0, case.fields[key].alternatives)
            chosen = dlg.checked_attorneys()
            if chosen is not None:
                if len(case.attorneys) == len(asked):
                    for i, a in enumerate(case.attorneys):
                        a.checked = i in chosen
                else:
                    keys = {asked[i].key() for i in chosen}
                    for a in case.attorneys:
                        a.checked = a.key() in keys
                job.att_touched = True  # a later AI answer must not undo the choice
            if job is self.cur:
                self._show_case()

        sheet = None
        if "runsheet" in outputs:
            target = self._run_sheet_for(job)
            if target is None or job not in self.jobs or self._batch_running():
                return
            sheet = job.runsheet_opts(self.s)
            sheet.target = target
        # the files are made from the job as it is now: an answer from the AI or a read that ended while a
        # question was on screen merged it again (and what is on screen with it)
        case = job.case
        if "invoice" in outputs and job.portions_problem():  # (an attorney was unticked meanwhile)
            outputs = self._without_unchecked_invoice(job, outputs)
            if not outputs or job not in self.jobs:
                return
        out_dir = out_dir_for(job, self.s)
        keys: list[str] = []  # the attorneys invoiced
        try:
            paths = generate(case, self.s, out_dir, outputs, job.invoice_opts(), runsheet=sheet,
                             folders=input_folders(job), invoiced=keys)
        except Exception as e:
            made = list(getattr(e, "made", []))
            job.saved, job.error = made, f"{type(e).__name__}: {e}"
            if keys:  # some attorneys' invoices were made: trying again doesn't bill them twice
                job.invoiced_keys = list(dict.fromkeys(job.billed_keys() + keys))
            show_save_error(self, e, "Could not make the files", "\n\nSaved before the problem:\n"
                            + "\n".join(p.name for p in made) if made else "")
            self._update_status()
            self._records_changed()
            return
        log.info("made %d file(s): %s, form type %s", len(paths), "+".join(outputs), self.s.form_choice)
        job.saved, job.error = paths, ""
        job.invoiced |= "invoice" in outputs  # Generate all won't bill this day again
        if self.s.open_after:
            for p in paths:
                open_path(p)
        made = [p for p in paths if not (sheet and p == sheet.path)]
        text = f"Saved {plural(len(made), 'file')}:\n\n" + "\n".join(p.name for p in made) + f"\n\nin {out_dir}" \
            if made else ""
        folders = [out_dir] if made else []
        if sheet and sheet.path:
            text += ("\n\n" if text else "") + run_sheet_summary(sheet)
            folders.append(sheet.path.parent)
        self._saved_box("Saved", text, list(dict.fromkeys(folders)))
        self._update_status()
        self._set_status(f"✓  Saved {len(paths)} file(s)", "ok")
        self._records_changed()

    def _without_unchecked_invoice(self, job: Job, outputs: list[str]) -> list[str]:
        """The outputs to make, less the invoice when the day's Who ordered... rows need checking: asks whether
        to make the others without it ([] = make nothing)."""
        if "invoice" not in outputs or not job.portions_problem():
            return outputs
        others = [o for o in outputs if o != "invoice"]
        why = (f"{job.portions_check()}.\n\nNo invoice is made for this day until you check it: open Who ordered… "
               "in the Invoice panel and tick who ordered which pages, or choose \"Everyone ordered every page\".")
        if not others:
            QMessageBox.warning(self, "Who ordered… needs checking", why)
            return []
        if QMessageBox.question(self, "Who ordered… needs checking",
                                why + "\n\nMake the other outputs without the invoice?") != QMessageBox.Yes:
            return []
        if job not in self.jobs or self._batch_running():
            return []
        return others

    def _run_sheet_for(self, job: Job) -> str | None:
        """The run sheet to add the job's takes to ("" = a new one), as Settings → Run sheet says; asks when the
        case seems to have one already. None = cancelled."""
        if self.s.runsheet_existing == "new":
            return ""
        found = find_sheets(job.case, [runsheets_folder(self.s), *input_folders(job)])
        if not found:
            return ""
        if self.s.runsheet_existing == "add":
            return str(found[0].path)
        dlg = RunSheetDialog(found, job.title(), self)
        return dlg.choice() if dlg.exec() == RunSheetDialog.Accepted else None

    def fill_all_jobs(self):
        """Generate all: makes the outputs of every ticked job on another thread (Ctrl+Shift+Enter).

        Blank fields are not asked about, but incomplete jobs are listed first, to leave out or make anyway.
        Where the takes go is asked once per case when it has a run sheet already (Settings → Run sheet); the
        jobs of one case share a run sheet group, so its days go on one sheet. The batch works on copies of
        the jobs and the settings; when it ends, each job's saved files, error and whether it was invoiced are
        copied back.
        With a single job this is the same as Generate.
        """
        if len(self.jobs) < 2:
            return self.fill()
        if self._batch_running():
            return
        self._commit_edit()
        self._sync_from_ui()
        chosen = [j for j in self.jobs if j.include and not j.is_empty()]
        if not chosen:
            return
        outputs = list(self.s.outputs)
        if not outputs:
            return
        gaps = [j for j in chosen if j.output_problems(outputs)]
        if gaps:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Some jobs are incomplete")
            box.setText(f"{len(gaps)} of {len(chosen)} jobs (marked ⚠) are incomplete:\n\n"
                        + "\n".join(f"•  {j.title()}:  {', '.join(j.output_problems(outputs))}" for j in gaps[:8])
                        + ("\n…" if len(gaps) > 8 else ""))
            ready = box.addButton(f"Do the {len(chosen) - len(gaps)} complete ones", QMessageBox.AcceptRole)
            everything = box.addButton("Do all (blanks left, no invoice or run sheet without a transcript)",
                                       QMessageBox.ActionRole)
            box.addButton(QMessageBox.Cancel)
            ready.setEnabled(len(chosen) > len(gaps))
            box.exec()
            if box.clickedButton() == ready:
                chosen = [j for j in chosen if not j.output_problems(outputs)]
            elif box.clickedButton() != everything:
                return
        # Where each case's takes go: asked once per case (the days of a trial share one run sheet)
        sheet_for: dict[int, tuple[str, int]] = {}  # id(job) -> (the run sheet chosen, its case's number)
        asked: list[tuple[Job, str]] = []
        for j in chosen:
            if "runsheet" not in j.makeable(outputs):
                continue
            case_no = next((n for n, (k, _) in enumerate(asked) if same_case(ident(k.case), ident(j.case))), None)
            if case_no is None:
                target = self._run_sheet_for(j)
                if target is None or self._batch_running():
                    return
                case_no = len(asked)
                asked.append((j, target))
            sheet_for[id(j)] = (asked[case_no][1], case_no)
        # A read that ended while a question was on screen may have joined two jobs into one
        chosen = [j for j in chosen if j in self.jobs]
        if not chosen:
            return
        sheet_for = {k: v for k, v in sheet_for.items() if k in {id(j) for j in chosen}}
        # The batch is made on another thread while the window stays usable, so it gets its own copy of the
        # jobs and the settings: editing a job, an AI answer or a change in Settings can't reach files half made.
        copies = {id(j): replace(j, case=deepcopy(j.case), docs=list(j.docs), saved=[], error="",
                                 invoiced_keys=list(j.invoiced_keys), portions=deepcopy(j.portions),
                                 runsheet_to=sheet_for.get(id(j), (None, None))[0],
                                 runsheet_group=sheet_for.get(id(j), (None, None))[1])
                  for j in self.jobs}
        todo, settings = [copies[id(j)] for j in chosen], deepcopy(self.s)
        self.filling = True
        self.work += 1
        self.fill_all_btn.setEnabled(False)
        self.fill_btn.setEnabled(False)
        self.busy.setVisible(True)

        # No self.gen check here: New job is refused while self.filling, so these jobs are still the window's.
        def ended():
            self.filling = False
            self.work = max(0, self.work - 1)
            self._idle()
            self.fill_btn.setEnabled(True)

        def step(i, n, name):
            self.busy.setRange(0, n)
            self.busy.setValue(i)
            self._set_status(f"Making {i + 1} of {n}…", "busy")

        def done(paths):
            ended()
            changed = set()  # (ids of) jobs given new pages to bill while the batch ran: billable and ticked still
            for j in chosen:
                copy = copies[id(j)]
                j.saved, j.error = copy.saved, copy.error
                if _billing_changed(j, copy):
                    changed.add(id(j))
                else:
                    j.invoiced, j.invoiced_keys = copy.invoiced, copy.invoiced_keys
            failed = [j for j in chosen if j.error and not j.saved]
            partial = [j for j in chosen if j.error and j.saved]
            for j in chosen:
                if (j.saved or j.invoiced) and not j.error and id(j) not in changed:
                    j.include = False  # "Generate all" again only does what is left
            changed = [j for j in chosen if id(j) in changed and any(j is k for k in self.jobs)]
            self._update_status()
            folders = list(dict.fromkeys(str(p.parent) for p in paths))
            text = f"Saved {len(paths)} file{'s' if len(paths) != 1 else ''} for " \
                   f"{len(chosen) - len(failed)} job{'s' if len(chosen) - len(failed) != 1 else ''}"
            text += f" in\n{folders[0]}" if len(folders) == 1 else f" in {len(folders)} folders." if folders else "."
            if failed:
                text += f"\n\n{len(failed)} could not be saved (is a file - a PDF or the Excel run sheet - open in " \
                        "another program?):\n" + "\n".join(f"•  {j.title()}: {_reason(j.error)}" for j in failed[:6])
            if partial:
                text += f"\n\n{len(partial)} job(s) are not complete:\n" + \
                        "\n".join(f"•  {j.title()}: {_reason(j.error)}" for j in partial[:6])
            if changed:
                text += (f"\n\n{len(changed)} job(s) changed while the files were made (a document was added, or "
                         "the pages or Who ordered… changed), so they are still ticked: Generate all again to bill "
                         "them as they are now:\n" + "\n".join(f"•  {j.title()}" for j in changed[:6]))
            self._saved_box("Batch finished", text, folders, "\n".join(str(p) for p in paths), warn=bool(failed))
            self._set_status(f"✓  Saved {len(paths)} file(s)", "warn" if failed else "ok")
            self._records_changed()

        def crashed(msg):
            ended()
            self._update_status()
            QMessageBox.critical(self, "Could not fill the forms", msg)

        self.runner.start(fill_jobs, todo, settings, batch=list(copies.values()), outputs=outputs,
                          on_done=done, on_error=crashed, on_progress=step)

    # ------------------------------------------------------- misc
    def new_job(self):
        """Clears every job and starts over (Ctrl+N), asking first in a batch. Work still running in the
        background is not stopped, but its results are dropped (self.gen goes up)."""
        if self._batch_running():
            return
        if len(self.jobs) > 1 and QMessageBox.question(
                self, "New job", f"Clear all {len(self.jobs)} jobs of this batch and start over?"
        ) != QMessageBox.Yes:
            return
        self.gen += 1
        self.jobs = [Job()]
        self.cur = self.jobs[0]
        self.ai_pending = self.work = 0
        self._loading.clear()
        self.busy.setRange(0, 0)
        self._refresh_jobs()
        self.input_list.clear()
        self.paste.clear()
        self._show_case()
        self.busy.setVisible(False)
        self._set_status("Drop a document to begin", "")

    def open_settings(self, first_run: bool = False):
        """Opens Settings. After Save, the theme, the window's options, the rate sheets and the AI check are
        refreshed and every job is merged and priced again."""
        dlg = SettingsDialog(self.s, self, first_run=first_run)
        if dlg.exec():
            apply_theme(self.app, self.s.theme)
            self.per_email.setChecked(self.s.per_email)
            self.sign_rep.setChecked(self.s.sign_reporter)
            for combo, value in ((self.form_choice, self.s.form_choice), (self.mofr_division, self.s.mofr_division),
                                 (self.rs_existing, self.s.runsheet_existing)):
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(max(0, combo.findData(value)))
            set_checks(self.output_boxes, self.s.outputs)
            self.s.reload_rates()
            self._fill_sheet_box()
            self._fill_invoice_speeds()
            self._check_ai()
            if self.cur.docs:
                self._remerge()
            self._apply_speed()
            self._remerge_others()
            self._update_status()

    # ------------------------------------------------------- outputs and records
    def _outputs_changed(self, _=None):
        """The outputs ticked are saved straight away, as the default for next time."""
        self._set_opt("outputs", [k for k in OUTPUTS if self.output_boxes[k].isChecked()])
        self._refresh_outputs()
        if len(self.jobs) > 1:
            self._refresh_job_labels()

    def _refresh_outputs(self):
        """Greys out the options of the outputs not ticked. For the current job: the tooltips and warnings of
        the invoice and the run sheet (both need a transcript), and the Invoice panel's parties and prices."""
        if not hasattr(self, "rs_info"):  # still building the window
            return
        for key, body in self.output_opts.items():
            body.setEnabled(self.output_boxes[key].isChecked())
        job = self.cur
        pages = job.invoice_pages()
        box = self.output_boxes["invoice"]
        box.setToolTip("An invoice for each ticked attorney, priced from the rate sheet" if pages else
                       "Needs a transcript PDF among the inputs (for the page count).\n"
                       "Jobs without one get no invoice.")
        self.output_boxes["runsheet"].setToolTip(
            "Adds the transcript's takes (who wrote which pages, from the initials on each page)\n"
            "to the case's run sheet in Excel, or starts one (Settings → Run sheet)" if job.transcript_pages() else
            "Needs a transcript PDF among the inputs (the initials on its pages).\n"
            "Jobs without one get no run sheet.")
        self.rs_info.setText("" if job.transcript_pages() or not job.docs else
                             "⚠ no transcript PDF: no run sheet for this job")
        self.rs_info.setVisible(bool(self.rs_info.text()))
        self._show_invoice_prices()
        self._place_output_cols()  # the prices may need more room than the panels have

    def _show_invoice_prices(self):
        """The Invoice panel's parties, extras and prices for the current job. When Generate all bills it with
        other days of its case, the first line says so and the prices are the joint invoice's. When the
        attorneys don't all pay the same (they ordered different days or pages), a line per attorney."""
        from ..batch import joint_invoice
        job = self.cur
        group = self._invoice_group()
        case, opts = joint_invoice(group)
        split = job.portions is not None  # Who ordered... decides this day's parties (or needs checking)
        check = job.portions_check()
        with QSignalBlocker(self.inv_parties):
            self.inv_parties.setValue(opts.parties)
        self.inv_parties.setEnabled(not split)
        self.inv_parties.setToolTip("This day's pages are split between the attorneys under Who ordered…" if split
                                    else PARTIES_TIP)
        why = self._who_ordered_why(job)
        self.inv_who.setEnabled(not why or split)  # rows that need checking can always be opened, to fix them
        self.inv_who.setText("⚠ Who ordered…" if check else "✓ Who ordered…" if split else "Who ordered…")
        self.inv_who.setToolTip(check + "\n(click to check it)" if check else why or WHO_TIP)
        with QSignalBlocker(self.inv_detail):
            self.inv_detail.setChecked(job.invoice_detail)  # this job's own (Generate this job uses it)
        self.inv_extras_info.setText(self._extras_text(opts))
        self.inv_info.setToolTip("")
        if not job.invoice_pages():
            if not job.docs:
                self.inv_info.setText("")  # an empty job has nothing to warn about yet
            elif job.transcript_pages():
                self.inv_info.setText("⚠ the Pages field says 0: no invoice for this job")
            else:
                self.inv_info.setText("⚠ no transcript PDF: no invoice for this job")
            return
        if check:  # no prices: nothing is billed for this day until the rows are checked
            self.inv_info.setText(f"⚠ {check}")
            self.inv_info.setToolTip("No invoice is made for this day until Who ordered… is checked:\n"
                                     "tick who ordered which pages, or choose \"Everyone ordered every page\".")
            return
        try:
            from ..invoice import firm_invoices
            firms = firm_invoices(case, self.s, opts)
        except Exception as e:  # a broken rate sheet must not break the window
            self.inv_info.setText(f"⚠ {e}")
            return
        prices, per_firm = _price_lines(firms)
        if len(group) > 1:
            self.inv_info.setText(f"Generate all: one invoice for {len(group)} days ({opts.pages} pp.)\n{prices}")
            self.inv_info.setToolTip(
                f"Generate all bills these {len(group)} days of the case on one invoice for each attorney\n"
                f"(the days it is ticked on, and the pages it ordered).\n"
                f"\"Generate this job\" bills the day shown alone ({job.invoice_pages()} pp.).")
        else:
            self.inv_info.setText(f"{opts.pages} pp." + ("\n" if per_firm else " · ") + prices)

    def _who_ordered_why(self, job: Job) -> str:
        """Why Who ordered… can't be used for this job ("" when it can): it needs one day with pages, and two
        attorneys ticked on it to split them between."""
        return job.portions_unavailable()

    def _portion_attorneys(self, job: Job) -> list[Attorney]:
        """The attorneys Who ordered… lists for a job: those ticked on that day, each once (an attorney ticked
        only on another day of the invoice orders none of its pages)."""
        return [a for a in job.case.invoice_orderers() if a is not None]

    def _who_ordered(self):
        """Who ordered…: which attorney ordered which pages of the day shown (Job.portions). Rows that need
        checking (Job.portions_problem) are shown again when they still end on the day's pages (else the window
        starts from everyone ordering every page), or can be cleared when the day can no longer be split."""
        from .dialogs import PortionsDialog
        job = self.cur
        title = "Who ordered which pages"
        why = self._who_ordered_why(job)
        if why:
            if job.portions is None:
                QMessageBox.information(self, title, why)
            elif QMessageBox.question(
                    self, title, f"{why}\n\nThis day's pages were split under Who ordered… before, so no invoice is "
                    "made for it until that is checked. Clear it, so that every attorney ticked on the day orders "
                    "every page?") == QMessageBox.Yes and any(job is j for j in self.jobs):
                job.portions = None
            self._update_status()
            return
        pages = job.invoice_pages()
        attorneys = self._portion_attorneys(job)
        keys = [a.key() for a in attorneys]
        # rows that no longer end on the day's pages start again from the default; ticks of attorneys no longer
        # ticked on the day are left out (a row left with none must be ticked again)
        rows = job.portions if job.portions is not None and job.portions_fit_pages() else None
        dlg = PortionsDialog(attorneys, pages, rows, keys, self)
        accepted = dlg.exec()
        if not accepted:
            return
        if not any(job is j for j in self.jobs):  # joined with another job by a read that ended meanwhile
            QMessageBox.information(self, title, "This day was joined with another job while the window was open "
                                    "(a document was read), so nothing was changed. Please open Who ordered… again.")
        elif dlg.values() is None:  # everyone ordered every page: right whatever changed meanwhile
            job.portions = None
        elif job.invoice_pages() != pages or [a.key() for a in self._portion_attorneys(job)] != keys:
            QMessageBox.information(self, title, "The pages of this day, or the attorneys ticked on it, changed "
                                    "while the window was open, so nothing was changed. Please choose again.")
        else:
            job.portions = dlg.values()
        self._update_status()  # the prices, and the files Generate all makes

    def _invoice_group(self, job: Job | None = None) -> list[Job]:
        """The days Generate all bills on one invoice with a job (default: the current one), as
        Settings.invoice_joint says: the ticked jobs of its case not invoiced yet (and whose Who ordered... rows
        don't need checking: see batch.fill_jobs). Just the job itself when Generate all leaves it out."""
        from ..batch import invoice_groups
        cur = job or self.cur
        billed = [j for j in self.jobs if j.include and not j.invoiced and not j.is_empty()
                  and not j.portions_problem()]
        if cur.invoice_pages() and cur in billed:
            for g in invoice_groups(billed, self.s):
                if any(j is cur for j in g):
                    return g
        return [cur]

    def _extras_text(self, opts) -> str:
        """'E-mailed copy, index from 50 pp.' - what the invoice includes; '(this job)' when it isn't as
        Settings say."""
        s = self.s
        email = s.invoice_include_email if opts.email is None else opts.email
        index = ("auto" if s.invoice_include_index else "off") if opts.index is None else opts.index
        text = ", ".join(("e-mailed copy" if email else "no e-mailed copy",
                          {"auto": f"index from {s.invoice_index_threshold} pp.", "on": "index",
                           "off": "no index"}[index]))
        own = opts.email is not None or opts.index is not None
        return text[0].upper() + text[1:] + (" (this job)" if own else "")

    def _invoice_extras(self):
        """Extras…: the e-mailed copy and the index for this job's invoice, and the other days on it."""
        from ..batch import joint_invoice
        from .dialogs import InvoiceExtrasDialog
        job = self.cur
        group = self._invoice_group(job)
        opts = joint_invoice(group)[1]
        dlg = InvoiceExtrasDialog(opts.email, opts.index, self.s, len(group), self)
        if dlg.exec():
            email, index = dlg.values()
            for day in self._invoice_group(job):  # as it is now: a read may have ended meanwhile
                day.invoice_email, day.invoice_index = email, index
            self._update_status()

    def _invoice_show(self):
        """Customize…: what granular detail shows on this job's invoice, and the other days on it."""
        from ..batch import joint_invoice
        from .dialogs import InvoiceShowDialog
        job = self.cur
        dlg = InvoiceShowDialog(joint_invoice(self._invoice_group(job))[1].show, self.s, self)
        if dlg.exec():
            items = dlg.values()
            for day in self._invoice_group(job):  # as it is now: a read may have ended meanwhile
                day.invoice_show = items

    def _place_output_cols(self):
        """Lays the Outputs panels out four in a row or, when the window is too narrow for that, in three
        columns with the two short ones (MOFR, run sheet) one above the other. Panels side by side are as tall
        as each other."""
        if len(getattr(self, "_output_cols", [])) < 4:  # still building the window
            return
        widths = [c.sizeHint().width() for c in self._output_cols]
        room = self.width() - 36 - 32 - 8  # the window's and the box's margins
        per_row = 4 if sum(widths) + 10 * 3 <= room else 3
        if per_row == self._cols_per_row:
            return
        self._cols_per_row = per_row
        for c in self._output_cols:
            self.output_grid.removeWidget(c)
        agreement, invoice, mofr, runsheet = self._output_cols
        if per_row == 4:
            for i, c in enumerate(self._output_cols):
                self.output_grid.addWidget(c, 0, i)
        else:
            self.output_grid.addWidget(agreement, 0, 0, 2, 1)
            self.output_grid.addWidget(invoice, 0, 1, 2, 1)
            self.output_grid.addWidget(mofr, 0, 2)
            self.output_grid.addWidget(runsheet, 1, 2)
        for col in range(5):
            self.output_grid.setColumnStretch(col, 1 if col < per_row else 0)  # the panels share the width

    def _size_drop(self):
        """The drop zone shows the small picture for a batch (room for the list of jobs), and the big one when
        the left column has room for it above the inputs and the paste box. It is tried and measured, since the
        column's height depends on fonts and wrapped text."""
        if len(self.jobs) > 1:
            self.drop.set_compact(True)
            return
        scroll = getattr(self, "left_scroll", None)
        if scroll is None or not scroll.isVisible():
            self.drop.set_compact(self.height() < COMPACT_BELOW)
            return
        self.drop.set_compact(False)
        left = scroll.widget()
        left.layout().activate()
        width = scroll.viewport().width()
        # the height the column asks for at that width (wrapped text included)
        need = left.heightForWidth(width) if left.hasHeightForWidth() else left.minimumSizeHint().height()
        if max(need, left.minimumSizeHint().height()) > scroll.viewport().height():
            self.drop.set_compact(True)

    def _size_drop_later(self):
        """_size_drop once Qt has laid the window out (sizes measured before that are not the final ones)."""
        QTimer.singleShot(0, self._size_drop)

    def resizeEvent(self, e):
        """The Outputs columns follow the window's width, and the drop zone its height."""
        super().resizeEvent(e)
        self._place_output_cols()
        if hasattr(self, "drop"):
            self._size_drop()

    def showEvent(self, e):
        """When the window first shows, the drop zone is sized again once the window is laid out: measured
        earlier, the left column looks shorter than it is."""
        super().showEvent(e)
        self._size_drop_later()

    def _fill_invoice_speeds(self):
        """A box per speed of the current rate sheet, ticked for the speeds invoices offer."""
        from ..rates import speed_key
        while self.inv_speeds_grid.count():
            item = self.inv_speeds_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.inv_speed_boxes = {}
        offered = {speed_key(x) for x in self.s.invoice_speeds}
        try:
            names = [sp.name for sp in self.s.sheet().speeds]
        except Exception:  # a broken rate sheet must not break the window
            names = []
        per_row = 2 if len(names) > 3 else 3  # four boxes in a row would make the Invoice panel too wide
        for i, name in enumerate(reversed(names)):  # fastest first, as on the rate sheet
            cb = QCheckBox(name)
            cb.setChecked(speed_key(name) in offered)
            cb.toggled.connect(self._invoice_speeds_changed)
            self.inv_speed_boxes[name] = cb
            self.inv_speeds_grid.addWidget(cb, i // per_row, i % per_row)
        if not names:
            self.inv_speeds_grid.addWidget(QLabel("(none on the rate sheet)"), 0, 0)
        self._place_output_cols()  # more or fewer speeds: the panels may fit in a row now, or no longer

    def _invoice_speeds_changed(self, _=None):
        """The speeds ticked are saved straight away. Speeds of other rate sheets stay as they were. With one
        speed ticked the invoice bills that speed alone; with none, the speed chosen under Order."""
        from ..rates import speed_key
        here = {speed_key(n) for n in self.inv_speed_boxes}
        keep = [x for x in self.s.invoice_speeds if speed_key(x) not in here]
        self._set_opt("invoice_speeds", keep + [n for n, cb in self.inv_speed_boxes.items() if cb.isChecked()])
        self._refresh_outputs()

    def _open_run_sheets_folder(self):
        """Opens the run sheets folder, making it first if needed (it is made with the first run sheet)."""
        folder = runsheets_folder(self.s)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Could not open the folder", str(e))
            return
        open_path(folder)

    def lock_pdfs(self):
        """File → Lock finished PDFs: a copy of each PDF picked, with its fields flattened so the values can no
        longer be changed ('<name> (locked).pdf' next to it). Only PDFs this app made that still have fields
        are locked."""
        from ..fill import has_fields, is_generated, lock_pdf
        start = self.s.output_dir or str(Path.home() / "Documents")
        files, _ = QFileDialog.getOpenFileNames(self, "Lock finished PDFs", start, "PDF files (*.pdf)")
        if not files:
            return
        made, skipped, no_fields, failed = [], [], [], []
        for f in map(Path, files):
            if not is_generated(f):
                skipped.append(f.name)
                continue
            try:
                if not has_fields(f):  # flattened when saved, or a locked copy: nothing to lock
                    no_fields.append(f.name)
                    continue
                made.append(lock_pdf(f))
            except Exception as e:
                failed.append(f"{f.name}: {e}")
        copies = "1 locked copy" if len(made) == 1 else f"{len(made)} locked copies"
        lines = [f"Saved {copies} next to the originals."] if made else []
        if skipped:
            lines.append("Not made by this app, so left alone:\n  " + "\n  ".join(skipped))
        if no_fields:
            lines.append("Already locked or flattened (no fields), so left alone:\n  " + "\n  ".join(no_fields))
        if failed:
            lines.append("Could not be locked (is it open in a PDF viewer?):\n  " + "\n  ".join(failed))
        QMessageBox.information(self, "Lock finished PDFs", "\n\n".join(lines))

    def _invoice_detail_changed(self, on: bool):
        """"Show granular detail" was clicked: it applies to this job's invoice (and the other days on it) only;
        a new job starts with it off."""
        for job in self._invoice_group():
            job.invoice_detail = on
        self._refresh_outputs()

    def _invoice_parties_changed(self, n: int):
        """A number typed here stays; set back to the number of ticked attorneys, it follows them again. It
        applies to the other days on the same invoice too."""
        from ..batch import group_attorneys
        group = self._invoice_group()
        ticked = max(1, len(group_attorneys(group)) if len(group) > 1 else len(self.cur.ticked_keys()))
        for job in group:
            job.parties = 0 if n == ticked else n
        self._refresh_outputs()

    def open_records(self):
        """Shows the Records window (Ctrl+R), made once and reloaded each time it is opened."""
        from .records_window import RecordsWindow
        win = getattr(self, "_records_win", None)
        if win is None:
            win = self._records_win = RecordsWindow(self.s, self)
        win.reload()
        win.show()
        win.raise_()
        win.activateWindow()

    def _records_changed(self):
        """Files were made: an open Records window shows them."""
        win = getattr(self, "_records_win", None)
        if win is not None and win.isVisible():
            win.reload()

    def _save_invoice_template(self):
        """Saves a copy of the invoice spreadsheet template that ships with the app where the user picks."""
        from ..invoice import TEMPLATE
        if not TEMPLATE.is_file():
            QMessageBox.warning(self, "Not found", "The invoice spreadsheet template is missing from this install.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save the invoice spreadsheet template",
                                              str(Path.home() / "Documents" / TEMPLATE.name),
                                              "Excel workbook (*.xlsx)")
        if dest:
            import shutil
            try:
                shutil.copyfile(TEMPLATE, dest)
            except OSError as e:
                QMessageBox.warning(self, "Could not save", str(e))
                return
            open_path(Path(dest).parent)

    def closeEvent(self, e):
        """Asks first while a batch is being made; remembers the window's size and place."""
        if self.filling and QMessageBox.question(
                self, "Still working", "The files of the batch are still being made. Close anyway?\n\n"
                "(The file being written may be left incomplete.)") != QMessageBox.Yes:
            e.ignore()
            return
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.s.save()
        super().closeEvent(e)
