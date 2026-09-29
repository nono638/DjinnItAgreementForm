"""Settings/profile dialog and the 'please clarify' dialog shown before filling."""
from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ..models import Attorney, FIELD_LABELS
from ..settings import Settings


def _form(parent: QWidget) -> QFormLayout:
    f = QFormLayout(parent)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    f.setHorizontalSpacing(14)
    f.setVerticalSpacing(9)
    f.setContentsMargins(18, 18, 18, 18)
    return f


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        if first_run:
            hello = QLabel("Welcome! Enter your details once - they are written on every minute agreement.")
            hello.setWordWrap(True)
            hello.setObjectName("subtitle")
            lay.addWidget(hello)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # --- My info
        w = QWidget()
        f = _form(w)
        p = settings.profile
        self.p_name = QLineEdit(p.name)
        self.p_title = QLineEdit(p.title)
        self.p_addr1 = QLineEdit(p.address1)
        self.p_addr1.setPlaceholderText("e.g. Supreme Court, 123 Courthouse Plaza, Room 100")
        self.p_addr2 = QLineEdit(p.address2)
        self.p_addr2.setPlaceholderText("e.g. Anytown, NY 10000")
        self.p_phone = QLineEdit(p.phone)
        self.p_fax = QLineEdit(p.fax)
        self.p_email = QLineEdit(p.email)
        for label, wid in (("Name", self.p_name), ("Title", self.p_title), ("Address", self.p_addr1),
                           ("", self.p_addr2), ("Telephone", self.p_phone), ("Fax", self.p_fax),
                           ("Email", self.p_email)):
            f.addRow(label, wid)
        self.tabs.addTab(w, "My info")

        # --- Defaults
        w = QWidget()
        f = _form(w)
        self.d_court = QLineEdit(settings.default_court)
        self.d_county = QLineEdit(settings.default_county)
        self.d_copies = QLineEdit(settings.default_copies)
        f.addRow("Court", self.d_court)
        f.addRow("County", self.d_county)
        f.addRow("Copies", self.d_copies)

        # Rate sheets
        self.d_sheet = QComboBox()
        self.d_delivery = QComboBox()
        self._sheet_dir = settings.rate_sheets_dir
        self._load_sheet_names()
        self.d_sheet.currentIndexChanged.connect(lambda _: self._load_speeds())
        f.addRow("Rate sheet", self.d_sheet)
        f.addRow("Default speed", self.d_delivery)
        row = QHBoxLayout()
        self.d_dir = QLineEdit(settings.rate_sheets_dir)
        from ..rates import default_dir
        self.d_dir.setPlaceholderText(str(default_dir()))
        pick = QPushButton("Browse...")
        pick.clicked.connect(self._pick_sheet_dir)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(self._open_sheet_dir)
        row.addWidget(self.d_dir, 1)
        row.addWidget(pick)
        row.addWidget(open_btn)
        f.addRow("Sheets folder", row)
        help_lbl = QLabel("Rate sheets are CSV files (open them in Excel). To add one, e.g. city rates, copy "
                          "\"Rate Sheet TEMPLATE.csv\" in that folder, rename it, and fill in the prices. "
                          "An optional Days column sets each speed's turnaround.")
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("muted")
        f.addRow("", help_lbl)

        self.days = {}
        row = QHBoxLayout()
        for label, attr in (("Immediate", "days_immediate"), ("Daily", "days_daily"),
                            ("Expedited", "days_expedited"), ("Regular", "days_regular")):
            spin = QSpinBox()
            spin.setRange(0, 120)
            spin.setValue(getattr(settings, attr))
            self.days[attr] = spin
            row.addWidget(QLabel(label))
            row.addWidget(spin)
            row.addSpacing(8)
        row.addStretch(1)
        f.addRow("Turnaround days", row)
        note = QLabel("Used when a rate sheet has no Days column.")
        note.setObjectName("muted")
        f.addRow("", note)
        self.o_deldate = QCheckBox("Fill 'Estimated Delivery Date' from the turnaround days")
        self.o_deldate.setChecked(settings.fill_delivery_date)
        f.addRow("", self.o_deldate)
        self.tabs.addTab(w, "Defaults")

        # --- Options
        w = QWidget()
        f = _form(w)
        self.o_per_email = QCheckBox("Write \"per email\" in the attorney signature spot")
        self.o_sign = QCheckBox("Type my name on the court reporter signature line")
        self.o_today = QCheckBox("Use today as the date of agreement")
        self.o_flat = QCheckBox("Flatten the PDF (fields no longer editable)")
        self.o_open = QCheckBox("Open the PDF after saving")
        self.o_tc = QCheckBox("Convert ALL-CAPS names to Title Case")
        self.o_djinn = QCheckBox("Show the Djinn (working / done / stumped pictures)")
        for cb, val in ((self.o_per_email, settings.per_email), (self.o_sign, settings.sign_reporter),
                        (self.o_today, settings.agreement_today), (self.o_flat, settings.flatten),
                        (self.o_open, settings.open_after), (self.o_tc, settings.title_case_names),
                        (self.o_djinn, settings.show_djinn)):
            cb.setChecked(val)
            f.addRow("", cb)
        from ..fill import FORMS
        self.o_form = QComboBox()
        for key, (_, label) in FORMS.items():
            self.o_form.addItem(label, key)
        self.o_form.setCurrentIndex(max(0, self.o_form.findData(settings.form_choice)))
        f.addRow("Form", self.o_form)
        self.o_instr = QCheckBox("Include the instructions page (UCS form page 2)")
        self.o_instr.setChecked(settings.include_instructions)
        f.addRow("", self.o_instr)
        self.o_combine = QCheckBox("Batches: put all days of the same case on one form")
        self.o_combine.setToolTip("Off: documents about the same case make one form per day of proceedings.")
        self.o_combine.setChecked(settings.batch_combine_dates)
        f.addRow("", self.o_combine)
        row = QHBoxLayout()
        self.o_dir = QLineEdit(settings.output_dir)
        self.o_dir.setPlaceholderText("Same folder as the dropped file")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._pick_dir)
        row.addWidget(self.o_dir)
        row.addWidget(browse)
        f.addRow("Save to", row)
        self.o_pattern = QLineEdit(settings.filename_pattern)
        self.o_pattern.setToolTip("Placeholders: {case} {index} {attorney} {date}")
        f.addRow("File name", self.o_pattern)
        self.o_theme = QComboBox()
        self.o_theme.addItems(["system", "light", "dark"])
        self.o_theme.setCurrentText(settings.theme)
        f.addRow("Theme", self.o_theme)
        self.tabs.addTab(w, "Options")

        # --- AI
        w = QWidget()
        f = _form(w)
        self.a_use = QCheckBox("Use a local AI model (Ollama) to help read documents")
        self.a_use.setChecked(settings.use_ai)
        self.a_text = QCheckBox("Also ask the AI about e-mails and pasted text (slower, better for informal text)")
        self.a_text.setChecked(settings.ai_for_text)
        self.a_model = QComboBox()
        self.a_model.setEditable(True)
        self.a_model.addItem(settings.ollama_model)
        self.a_host = QLineEdit(settings.ollama_host)
        self.a_timeout = QSpinBox()
        self.a_timeout.setRange(10, 900)
        self.a_timeout.setValue(settings.ai_timeout)
        self.a_timeout.setSuffix(" s")
        test = QPushButton("Test connection")
        self.a_status = QLabel("")
        self.a_status.setObjectName("muted")
        self.a_status.setWordWrap(True)
        test.clicked.connect(self._test_ai)
        f.addRow("", self.a_use)
        f.addRow("", self.a_text)
        f.addRow("Model", self.a_model)
        f.addRow("Ollama URL", self.a_host)
        f.addRow("Timeout", self.a_timeout)
        f.addRow(test, self.a_status)
        guide = QPushButton("How to install Ollama + gemma…")
        guide.clicked.connect(lambda: OllamaHelpDialog(self.a_model.currentText().strip() or "gemma4:e2b",
                                                       self.a_host.text().strip(), self).exec())
        f.addRow("", guide)
        self.tabs.addTab(w, "AI")
        self._load_models()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # --- rate sheet helpers
    def _load_sheet_names(self):
        from ..rates import list_sheets, pick
        sheets, _ = list_sheets(self._sheet_dir)
        self._sheets = {s.name: s for s in sheets}
        current = pick(sheets, self.d_sheet.currentData() or self.s.rate_sheet)
        self.d_sheet.blockSignals(True)
        self.d_sheet.clear()
        for s in sheets or [current]:
            self.d_sheet.addItem(s.name, s.name)
        self.d_sheet.setCurrentIndex(max(0, self.d_sheet.findData(current.name)))
        self.d_sheet.blockSignals(False)
        self._load_speeds()

    def _load_speeds(self):
        from ..rates import FALLBACK
        sheet = self._sheets.get(self.d_sheet.currentData(), FALLBACK)
        keep = self.d_delivery.currentData() or self.s.default_delivery
        self.d_delivery.clear()
        for sp in sheet.speeds:
            self.d_delivery.addItem(sp.label(), sp.name)
        found = sheet.find(keep)
        self.d_delivery.setCurrentIndex(max(0, self.d_delivery.findData(found.name if found else "")))

    def _pick_sheet_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Rate sheets folder", self.d_dir.text() or self.d_dir.placeholderText())
        if d:
            self.d_dir.setText(d)
            self._sheet_dir = d
            self._load_sheet_names()

    def _open_sheet_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..rates import sheets_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(sheets_dir(self.d_dir.text().strip()))))

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save filled forms to", self.o_dir.text())
        if d:
            self.o_dir.setText(d)

    def _load_models(self):
        try:
            import ollama
            names = [m.model for m in ollama.Client(host=self.s.ollama_host, timeout=2).list().models]
        except Exception:
            return
        current = self.a_model.currentText()
        self.a_model.clear()
        self.a_model.addItems(sorted(set(names) | {current}))
        self.a_model.setCurrentText(current)

    def _test_ai(self):
        from ..extract_llm import OllamaExtractor
        tmp = Settings()
        tmp.ollama_model, tmp.ollama_host = self.a_model.currentText().strip(), self.a_host.text().strip()
        ok, msg = OllamaExtractor(tmp).status()
        self.a_status.setText(("✓ " if ok else "✗ ") + msg)

    def accept(self):
        s, p = self.s, self.s.profile
        p.name, p.title = self.p_name.text().strip(), self.p_title.text().strip()
        p.address1, p.address2 = self.p_addr1.text().strip(), self.p_addr2.text().strip()
        p.phone, p.fax, p.email = self.p_phone.text().strip(), self.p_fax.text().strip(), self.p_email.text().strip()
        s.default_court, s.default_county = self.d_court.text().strip(), self.d_county.text().strip()
        s.default_delivery = self.d_delivery.currentData() or s.default_delivery
        s.default_copies = self.d_copies.text().strip()
        s.rate_sheet = self.d_sheet.currentData() or s.rate_sheet
        s.rate_sheets_dir = self.d_dir.text().strip()
        for attr, spin in self.days.items():
            setattr(s, attr, spin.value())
        s.fill_delivery_date = self.o_deldate.isChecked()
        s.per_email, s.sign_reporter = self.o_per_email.isChecked(), self.o_sign.isChecked()
        s.agreement_today, s.flatten = self.o_today.isChecked(), self.o_flat.isChecked()
        s.open_after, s.title_case_names = self.o_open.isChecked(), self.o_tc.isChecked()
        s.form_choice = self.o_form.currentData()
        s.include_instructions = self.o_instr.isChecked()
        s.batch_combine_dates = self.o_combine.isChecked()
        s.output_dir, s.filename_pattern = self.o_dir.text().strip(), self.o_pattern.text().strip() or s.filename_pattern
        s.theme = self.o_theme.currentText()
        s.show_djinn = self.o_djinn.isChecked()
        s.use_ai, s.ai_for_text = self.a_use.isChecked(), self.a_text.isChecked()
        s.ollama_model, s.ollama_host = self.a_model.currentText().strip(), self.a_host.text().strip()
        s.ai_timeout = self.a_timeout.value()
        s.save()
        super().accept()


class ClarifyDialog(QDialog):
    """Asks about blank or conflicting fields, and which attorney(s) ordered."""

    def __init__(self, questions: list[tuple[str, str, list[str]]], attorneys: list[Attorney] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("A few details before filling")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        intro = QLabel("Please confirm or fill in these items. Anything left blank stays blank on the form.")
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        lay.addWidget(intro)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.combos: dict[str, QComboBox] = {}
        for key, current, options in questions:
            cb = QComboBox()
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            opts = list(dict.fromkeys([current] + options)) if current else list(dict.fromkeys(options))
            cb.addItems(opts)
            cb.setCurrentText(current)
            cb.lineEdit().setPlaceholderText("(leave blank)")
            cb.setMinimumContentsLength(40)
            self.combos[key] = cb
            form.addRow(FIELD_LABELS.get(key, key), cb)
        lay.addLayout(form)

        self.att_list = None
        if attorneys:
            lbl = QLabel("Which attorney(s) ordered these minutes?  One form is made for each one checked.")
            lbl.setWordWrap(True)
            lbl.setObjectName("section")
            lay.addWidget(lbl)
            self.att_list = QListWidget()
            for i, a in enumerate(attorneys):
                text = " - ".join(x for x in (a.name, a.firm, a.party) if x)
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if a.checked else Qt.Unchecked)
                item.setData(Qt.UserRole, i)
                self.att_list.addItem(item)
            self.att_list.setMaximumHeight(min(200, 30 * len(attorneys) + 10))
            lay.addWidget(self.att_list)

        buttons = QDialogButtonBox()
        buttons.addButton("Fill form", QDialogButtonBox.AcceptRole)
        buttons.addButton("Go back", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def answers(self) -> dict[str, str]:
        return {k: cb.currentText().strip() for k, cb in self.combos.items()}

    def checked_attorneys(self) -> list[int] | None:
        if self.att_list is None:
            return None
        return [self.att_list.item(i).data(Qt.UserRole) for i in range(self.att_list.count())
                if self.att_list.item(i).checkState() == Qt.Checked]


OLLAMA_GUIDE = """
<h3>Optional: local AI helper (Ollama + gemma)</h3>
<p>The app works without AI. Photos and scanned PDFs are read by <b>Windows' built-in text recognition (OCR)</b>,
which needs no setup and takes about 2 seconds. The AI model is an optional second pass that <i>organizes</i>
the text. It is most useful for informal e-mails, where it can pick out names, firms and dates that the
built-in rules miss. Everything runs on this computer; nothing is sent to the internet.</p>

<h4>1. Install Ollama</h4>
<ol>
<li>Go to <a href="https://ollama.com/download">https://ollama.com/download</a> and download <b>Ollama for Windows</b>.</li>
<li>Run <b>OllamaSetup.exe</b>. Ollama starts by itself and shows a llama icon in the system tray.
It starts automatically with Windows from then on.</li>
</ol>

<h4>2. Download the model</h4>
<p>Click <b>Download {model}</b> below, <i>or</i> open PowerShell and run:</p>
<pre>   ollama pull {model}</pre>
<p>The download is about 7 GB, so allow time on a slow connection. <code>ollama list</code> shows the installed models.</p>

<h4>3. Check it</h4>
<p>Close this window and click <b>Test connection</b>. The main window shows "● AI ready" at the bottom left.</p>

<h4>What to expect</h4>
<p>On a laptop without a dedicated graphics card, the model runs on the processor, so each document takes about
<b>30–60 seconds</b>. The app shows the rule-based results right away and adds the AI suggestions when they
arrive, and you can keep editing in the meantime. Suggestions from the AI have a purple <b>AI</b> badge.
A computer with an NVIDIA graphics card is several times faster.</p>

<h4>If photos come out blank (Windows OCR missing)</h4>
<p>English Windows normally includes OCR. If it is missing, open PowerShell <b>as administrator</b> and run:</p>
<pre>   Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"</pre>
"""


class _PullThread(QThread):
    progress = Signal(str, int)   # status text, percent (-1 = unknown)
    finished_ok = Signal(str)

    def __init__(self, model: str, host: str):
        super().__init__()
        self.model, self.host = model, host

    def run(self):
        try:
            import ollama
            client = ollama.Client(host=self.host or None, timeout=None)
            for part in client.pull(self.model, stream=True):
                total, done = getattr(part, "total", None), getattr(part, "completed", None)
                pct = int(done * 100 / total) if total and done else -1
                self.progress.emit(getattr(part, "status", "") or "", pct)
            self.finished_ok.emit(f"✓ {self.model} is installed.")
        except Exception as e:
            msg = str(e)
            if "connect" in msg.lower() or "refused" in msg.lower():
                msg = "Ollama is not running. Install it (step 1) and try again."
            self.finished_ok.emit(f"✗ {msg}")


class OllamaHelpDialog(QDialog):
    def __init__(self, model: str, host: str, parent=None):
        super().__init__(parent)
        self.model, self.host = model, host
        self.setWindowTitle("Setting up the AI helper")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(OLLAMA_GUIDE.replace("{model}", model))
        lay.addWidget(text, 1)
        row = QHBoxLayout()
        self.pull_btn = QPushButton(f"Download {model}")
        self.pull_btn.clicked.connect(self._pull)
        site = QPushButton("Open ollama.com/download")
        site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ollama.com/download")))
        row.addWidget(site)
        row.addWidget(self.pull_btn)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.bar.setTextVisible(False)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addLayout(row)
        self.thread = None

    def _pull(self):
        self.pull_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText("Starting download…")
        self.thread = _PullThread(self.model, self.host)
        self.thread.progress.connect(self._progress)
        self.thread.finished_ok.connect(self._done)
        self.thread.start()

    def _progress(self, status: str, pct: int):
        if pct >= 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self.status.setText(f"{status}  {pct}%")
        else:
            self.bar.setRange(0, 0)
            self.status.setText(status)

    def _done(self, msg: str):
        self.bar.setVisible(False)
        self.status.setText(msg)
        self.pull_btn.setEnabled(True)

    def reject(self):
        self.accept()

    def accept(self):
        if self.thread and self.thread.isRunning():
            _BACKGROUND.append(self.thread)  # keep the download alive after the dialog closes
        super().accept()


_BACKGROUND: list[QThread] = []


CONTACT_EMAIL = "noahcollincourtreporter@gmail.com"
SOURCE_URL = "https://github.com/nono638/DjinnItAgreementForm"
COFFEE_URL = "https://buymeacoffee.com/noahcollin"

COMPONENTS = [
    ("Qt / PySide6", "LGPL-3.0", "https://www.qt.io/qt-for-python"),
    ("PyMuPDF", "AGPL-3.0 (or Artifex commercial license)", "https://pymupdf.readthedocs.io"),
    ("Pillow", "MIT-CMU", "https://python-pillow.org"),
    ("ollama-python", "MIT", "https://github.com/ollama/ollama-python"),
    ("PyWinRT (Windows OCR bindings)", "MIT", "https://github.com/pywinrt/pywinrt"),
    ("Python", "PSF License", "https://www.python.org"),
]


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from pathlib import Path
        from .. import __version__
        from ..settings import settings_dir
        self.setWindowTitle("About DjinnItAgreementForm")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        pic = Path(__file__).resolve().parent.parent / "assets" / "djinn_done.jpg"
        if pic.exists():
            from .main_window import _rounded
            img = QLabel()
            img.setPixmap(_rounded(pic, 420, 12))
            img.setAlignment(Qt.AlignCenter)
            lay.addWidget(img)
        title = QLabel("DjinnItAgreementForm")
        title.setObjectName("title")
        lay.addWidget(title)
        ver = QLabel(f"Version {__version__}")
        ver.setObjectName("muted")
        lay.addWidget(ver)
        body = QLabel(
            "Fills in New York UCS Court Reporter Minute Agreement Forms from transcripts, invoices, "
            "photos and e-mails.<br><br>"
            "Created by <b>Noah Collin</b>, Senior Court Reporter.<br>"
            f'Questions, bugs or ideas: <a href="mailto:{CONTACT_EMAIL}?subject=DjinnItAgreementForm">'
            f"{CONTACT_EMAIL}</a><br><br>"
            "Free software under the GNU AGPL-3.0 license."
            + (f' Source code: <a href="{SOURCE_URL}">{SOURCE_URL}</a>' if SOURCE_URL else "")
            + f'<br><br>If this saves you time, you can <a href="{COFFEE_URL}">buy me a coffee</a> ☕')
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setOpenExternalLinks(True)
        lay.addWidget(body)
        where = QLabel(f"Settings and rate sheets: {settings_dir()}")
        where.setObjectName("muted")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(where)
        row = QHBoxLayout()
        credits = QPushButton("Open-source components")
        credits.clicked.connect(self._credits)
        mail = QPushButton("Email Noah")
        mail.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"mailto:{CONTACT_EMAIL}?subject=DjinnItAgreementForm%20{__version__}")))
        coffee = QPushButton("☕  Buy me a coffee")
        coffee.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(COFFEE_URL)))
        ok = QPushButton("Close")
        ok.clicked.connect(self.accept)
        row.addWidget(credits)
        row.addWidget(mail)
        row.addWidget(coffee)
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)

    def _credits(self):
        box = QDialog(self)
        box.setWindowTitle("Open-source components")
        box.resize(520, 360)
        lay = QVBoxLayout(box)
        t = QTextBrowser()
        t.setOpenExternalLinks(True)
        rows = "".join(f'<tr><td><a href="{u}">{n}</a></td><td>&nbsp;&nbsp;{l}</td></tr>' for n, l, u in COMPONENTS)
        t.setHtml(f"<p>This program is built with these open-source components:</p><table>{rows}</table>"
                  "<p>Optional AI features use <a href='https://ollama.com'>Ollama</a> (MIT) and Google's "
                  "Gemma models (Gemma Terms of Use), installed separately by the user. Text recognition uses "
                  "the OCR engine built into Windows.</p>")
        lay.addWidget(t)
        b = QPushButton("Close")
        b.clicked.connect(box.accept)
        lay.addWidget(b, 0, Qt.AlignRight)
        box.exec()
