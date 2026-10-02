"""The app's dialogs: Settings, the "please clarify" questions before filling, the run sheet choice, a job's
invoice Extras, granular detail and who ordered which pages, the Ollama setup help (with a model download) and
About."""
from __future__ import annotations

import re

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import __version__
from ..models import Attorney, FIELD_LABELS
from ..runsheet import runsheets_folder
from ..settings import DETAIL_ITEMS, INDEX_RULES, INDEX_SHARED, OUTPUTS, SPEEDS, Settings, reporter_key
from .widgets import check_row, open_path, open_url, refill_combo, rounded


def _form(parent: QWidget) -> QFormLayout:
    """A form layout with the label alignment, spacing and margins used by every settings tab."""
    f = QFormLayout(parent)
    f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    f.setHorizontalSpacing(14)
    f.setVerticalSpacing(9)
    f.setContentsMargins(18, 18, 18, 18)
    return f


class SettingsDialog(QDialog):
    """The Settings window: tabs for My info, Defaults, Options, Invoice, Run sheet and AI.

    Nothing is stored until Save (accept), which copies every control back into the Settings object
    and saves it to disk. Cancel leaves the settings, and a picked but unsaved signature image,
    untouched.
    """
    def __init__(self, settings: Settings, parent=None, first_run: bool = False):
        """settings: the Settings object to edit in place.
        first_run: show a welcome line above the tabs (first launch, before a name is entered).
        """
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
        self.p_web = QLineEdit(p.website)
        self.p_web.setPlaceholderText("optional, shown on invoices")
        self.p_initials = QLineEdit(p.initials)
        self.p_initials.setPlaceholderText("as you put them at the foot of your transcript pages, e.g. pr")
        self.p_initials.setToolTip("Run sheets: the pages with these initials are yours.\n"
                                   "Blank = the first letters of your first and last name.")
        for label, wid in (("Name", self.p_name), ("Title", self.p_title), ("Address", self.p_addr1),
                           ("", self.p_addr2), ("Telephone", self.p_phone), ("Fax", self.p_fax),
                           ("Email", self.p_email), ("Website", self.p_web), ("Initials", self.p_initials)):
            f.addRow(label, wid)
        # Signature picture: shown as it will appear on the form; stored on Save
        self._sig_new: str | None = None  # None = unchanged, "" = removed, else a prepared file
        self.p_sig = QLabel()
        self.p_sig.setObjectName("muted")
        self.p_sig.setMinimumHeight(56)
        self.p_sig.setWordWrap(True)
        pick = QPushButton("Choose image...")
        pick.clicked.connect(self._pick_signature)
        self.p_sig_remove = QPushButton("Remove")
        self.p_sig_remove.clicked.connect(self._remove_signature)
        row = QHBoxLayout()
        row.addWidget(self.p_sig, 1)
        row.addWidget(pick)
        row.addWidget(self.p_sig_remove)
        f.addRow("Signature", row)
        self.p_sign = QCheckBox("Sign the court reporter line on every form")
        self.p_sign.setToolTip("With your signature picture if you chose one, otherwise your name is typed.")
        self.p_sign.setChecked(settings.sign_reporter)
        f.addRow("", self.p_sign)
        self._show_signature(settings.signature_image)
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
        for label in reversed(SPEEDS):  # fastest first
            attr = f"days_{label.lower()}"
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
        self.o_today = QCheckBox("Use today as the date of agreement")
        self.o_flat = QCheckBox("Flatten the PDF (fields no longer editable)")
        self.o_flat.setToolTip("Applies to minute agreements, MOFRs and invoices: they are locked as they are made.\n"
                               "Unticked, they stay fillable; File → Lock finished PDFs locks a copy later.")
        self.o_open = QCheckBox("Open the PDF after saving")
        self.o_tc = QCheckBox("Convert ALL-CAPS names to Title Case")
        self.o_djinn = QCheckBox("Show the Djinn (working / done / stumped pictures)")
        for cb, val in ((self.o_per_email, settings.per_email),
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
        make, self.o_outputs = check_row(OUTPUTS, settings.outputs)
        make.addStretch(1)
        f.addRow("Make by default", make)
        self.o_division = QComboBox()
        self.o_division.addItem("Civil", "civil")
        self.o_division.addItem("Criminal", "criminal")
        self.o_division.setCurrentIndex(max(0, self.o_division.findData(settings.mofr_division)))
        f.addRow("MOFR case", self.o_division)
        self.o_instr = QCheckBox("Include the instructions page (UCS form page 2)")
        self.o_instr.setToolTip("Unticked: the saved PDF has the form page only.")
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
        browse.clicked.connect(lambda: self._pick_into(self.o_dir, "Save filled forms to"))
        row.addWidget(self.o_dir)
        row.addWidget(browse)
        f.addRow("Save to", row)
        self.o_pattern = QLineEdit(settings.filename_pattern)
        self.o_pattern.setToolTip("Placeholders:\n{case}  {index}  {attorney}\n"
                                  "{date} - date of the minutes\n{today} - the day the form is filled")
        f.addRow("File name", self.o_pattern)
        self.o_mofr_pattern = QLineEdit(settings.mofr_filename_pattern)
        self.o_mofr_pattern.setToolTip(self.o_pattern.toolTip())
        f.addRow("MOFR file name", self.o_mofr_pattern)
        self.o_theme = QComboBox()
        self.o_theme.addItems(["system", "light", "dark"])
        self.o_theme.setCurrentText(settings.theme)
        f.addRow("Theme", self.o_theme)
        self.tabs.addTab(w, "Options")

        # --- Invoice
        w = QWidget()
        f = _form(w)
        intro = QLabel("Invoices are made from transcripts (they need the page count), priced from the rate sheet.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        f.addRow(intro)
        from ..rates import speed_key
        offered = {speed_key(x) for x in settings.invoice_speeds}
        speeds, self.i_speeds = check_row({n: n for n in SPEEDS}, [n for n in SPEEDS if speed_key(n) in offered],
                                          spacing=12)
        speeds.addStretch(1)
        f.addRow("Speeds offered", speeds)
        hint = QLabel("The attorney chooses one. With one ticked the invoice bills that speed alone; with none, "
                      "the speed chosen under Order.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow("", hint)
        shows = QGridLayout()
        shows.setHorizontalSpacing(16)
        shows.setVerticalSpacing(6)
        self.i_items: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(DETAIL_ITEMS.items()):
            cb = QCheckBox(label)
            cb.setChecked(key in settings.invoice_detail_items)
            self.i_items[key] = cb
            shows.addWidget(cb, i // 2, i % 2)
        f.addRow("Granular detail shows", shows)
        self.i_email = QCheckBox("Each party also gets an e-mailed copy (Email column of the rate sheet)")
        self.i_email.setChecked(settings.invoice_include_email)
        f.addRow("", self.i_email)
        idx = QHBoxLayout()
        self.i_index = QCheckBox("Add an index (and one for the judge) from")
        self.i_index.setChecked(settings.invoice_include_index)
        self.i_threshold = QSpinBox()
        self.i_threshold.setRange(1, 10000)
        self.i_threshold.setValue(settings.invoice_index_threshold)
        self.i_threshold.setSuffix(" pages")
        self.i_threshold.setFixedWidth(110)
        idx.addWidget(self.i_index)
        idx.addWidget(self.i_threshold)
        idx.addStretch(1)
        f.addRow("", idx)
        self.i_rule = QComboBox()
        for key, label in INDEX_RULES.items():
            self.i_rule.addItem(label, key)
        self.i_rule.setCurrentIndex(max(0, self.i_rule.findData(settings.invoice_index_rule)))
        self.i_rule.setToolTip("For an invoice covering several days. A job's Extras… can still turn the index\n"
                               "on or off for that job.")
        f.addRow("Index on several days", self.i_rule)
        self.i_shared = QComboBox()
        for key, label in INDEX_SHARED.items():
            self.i_shared.addItem(label, key)
        self.i_shared.setCurrentIndex(max(0, self.i_shared.findData(settings.invoice_index_shared)))
        self.i_shared.setToolTip("When several attorneys ordered the same pages: one index, its price split between\n"
                                 "them like the original's, or an index for each of them. The judge's index is\n"
                                 "always split.")
        f.addRow("Index on shared pages", self.i_shared)
        self.i_joint = QComboBox()
        self.i_joint.addItem("One joint invoice for all the days of a case", True)
        self.i_joint.addItem("An invoice for each day", False)
        self.i_joint.setCurrentIndex(0 if settings.invoice_joint else 1)
        self.i_joint.setToolTip("When several days of one case are generated together (Generate all).\n"
                                "A single day selected and generated on its own is always billed alone.")
        f.addRow("Days of one case", self.i_joint)
        self.i_turn: dict[str, QLineEdit] = {}
        for name in SPEEDS:
            e = QLineEdit(settings.turnaround(name))
            self.i_turn[name] = e
            f.addRow(f"{name} turnaround", e)
        self.i_pay = QPlainTextEdit(settings.invoice_payment_text)
        self.i_pay.setPlaceholderText("How to pay you: Zelle, check payable to..., mailing address")
        self.i_pay.setFixedHeight(96)
        f.addRow("Payment", self.i_pay)
        self.i_footer = QPlainTextEdit(settings.invoice_footer)
        self.i_footer.setFixedHeight(56)
        f.addRow("Footer note", self.i_footer)
        self.i_number = QLineEdit(settings.invoice_number_format)
        self.i_number.setToolTip("{year} and {seq} (the count this year, {seq:04} = 0007); {yy} = 26")
        f.addRow("Invoice numbers", self.i_number)
        self.i_pattern = QLineEdit(settings.invoice_filename_pattern)
        self.i_pattern.setToolTip("Placeholders: {number} {case} {index} {attorney} {date} {today}")
        f.addRow("File name", self.i_pattern)
        row = QHBoxLayout()
        self.i_records = QLineEdit(settings.records_dir)
        self.i_records.setPlaceholderText(str(settings.records_folder()))
        rb = QPushButton("Browse...")
        rb.clicked.connect(lambda: self._pick_into(self.i_records, "Folder for the records (CSV copies)"))
        row.addWidget(self.i_records)
        row.addWidget(rb)
        f.addRow("Records folder", row)
        scroll = QScrollArea()  # a long tab: scrolls on small screens
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        self.tabs.addTab(scroll, "Invoice")

        # --- Run sheet
        w = QWidget()
        f = _form(w)
        intro = QLabel("A run sheet lists who wrote which pages of a trial, take by take, from the initials at the "
                       "foot of each transcript page. There is one per case; a transcript's takes are added to it.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        f.addRow(intro)
        row = QHBoxLayout()
        self.r_dir = QLineEdit(settings.runsheet_dir)
        self.r_dir.setPlaceholderText(str(runsheets_folder(Settings())))
        rb = QPushButton("Browse...")
        rb.clicked.connect(lambda: self._pick_into(self.r_dir, "Folder for the run sheets"))
        ob = QPushButton("Open folder")
        ob.clicked.connect(self._open_run_sheets_folder)
        row.addWidget(self.r_dir, 1)
        row.addWidget(rb)
        row.addWidget(ob)
        f.addRow("Run sheets folder", row)
        self.r_existing = QComboBox()
        for key, label in (("ask", "Ask whether to add to it or start a new one"),
                           ("add", "Add to it without asking"), ("new", "Always start a new run sheet")):
            self.r_existing.addItem(label, key)
        self.r_existing.setCurrentIndex(max(0, self.r_existing.findData(settings.runsheet_existing)))
        self.r_existing.setToolTip("A run sheet is the case's when it has the same index number or the same case "
                                   "name\n(a trial with several index numbers is billed together).")
        f.addRow("When the case has one", self.r_existing)
        self.r_pattern = QLineEdit(settings.runsheet_filename_pattern)
        self.r_pattern.setToolTip("Placeholders: {month} {year} (of the first day)  {case}  {index}  {date}")
        f.addRow("File name", self.r_pattern)
        self.r_names = QPlainTextEdit("\n".join(f"{k} = {v}" for k, v in sorted(settings.reporters.items())))
        self.r_names.setPlaceholderText("ds = Dana\nkl = Kim")
        self.r_names.setFixedHeight(110)
        f.addRow("Reporters", self.r_names)
        help_lbl = QLabel("Initials = the name for the Reporter column, one reporter per line. Without a line, the "
                          "first name of the reporter listed on the transcript's title page is used (and yours "
                          "for your own initials, under My info); failing that, the initials.")
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("muted")
        f.addRow("", help_lbl)
        self.tabs.addTab(w, "Run sheet")

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
        guide.clicked.connect(lambda: OllamaHelpDialog(self.a_model.currentText().strip() or Settings.ollama_model,
                                                       self.a_host.text().strip(), self).exec())
        f.addRow("", guide)
        self.tabs.addTab(w, "AI")
        self._load_models()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # --- signature picture
    def _show_signature(self, path: str):
        """Shows the signature picture at `path` scaled to the row, or a hint if there is none, and enables Remove accordingly."""
        from pathlib import Path
        from PySide6.QtGui import QPixmap
        pix = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if pix.isNull():
            self.p_sig.setPixmap(QPixmap())
            self.p_sig.setText("None - your name is typed instead.\nA photo or scan of your signature on white paper works.")
        else:
            self.p_sig.setText("")
            self.p_sig.setPixmap(pix.scaled(230, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.p_sig_remove.setEnabled(not pix.isNull())

    def _pick_signature(self):
        """Asks for a photo or scan of the signature and prepares it (transparent background, cropped).

        The result waits in signature_new.png until Save. Choosing a signature also ticks the box to sign
        every form. A picture that cannot be used (for instance a blank one) gives a message instead.
        """
        from PySide6.QtWidgets import QMessageBox
        from ..signature import prepare, signature_path
        src, _ = QFileDialog.getOpenFileName(self, "Picture of your signature", "",
                                             "Pictures (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.heic)")
        if not src:
            return
        try:
            new = prepare(src, signature_path().with_name("signature_new.png"))
        except Exception as e:
            QMessageBox.warning(self, "Could not use that picture", str(e))
            return
        self._sig_new = str(new)
        self._show_signature(self._sig_new)
        self.p_sign.setChecked(True)  # choosing a signature means wanting it on the forms

    def _remove_signature(self):
        """Marks the signature for removal on Save and shows the empty state."""
        self._sig_new = ""
        self._show_signature("")

    def _store_signature(self):
        """Applies a pick or removal made in this dialog: moves the prepared picture to signature.png (or
        deletes it) and records it in the settings. Does nothing if the signature was not touched, and leaves
        the settings as they were if the file cannot be moved or deleted.
        """
        from pathlib import Path
        from ..signature import signature_path
        if self._sig_new is None:
            return
        final = signature_path()
        try:
            if self._sig_new:
                Path(self._sig_new).replace(final)  # in one step: the old picture stays if this fails
            else:
                final.unlink(missing_ok=True)
        except OSError:
            return
        self.s.signature_image = str(final) if self._sig_new else ""

    # --- rate sheet helpers
    def _load_sheet_names(self):
        """Lists the rate sheets found in the sheets folder, keeps the current choice if it still exists, then reloads the speeds."""
        from ..rates import list_sheets, pick
        sheets, _ = list_sheets(self._sheet_dir)
        self._sheets = {s.name: s for s in sheets}
        current = pick(sheets, self.d_sheet.currentData() or self.s.rate_sheet)
        refill_combo(self.d_sheet, [(s.name, s.name) for s in sheets or [current]], current.name)
        self._load_speeds()

    def _load_speeds(self):
        """Fills the default-speed box with the chosen rate sheet's speeds, keeping the current speed if the sheet has it."""
        from ..rates import FALLBACK
        sheet = self._sheets.get(self.d_sheet.currentData(), FALLBACK)
        keep = self.d_delivery.currentData() or self.s.default_delivery
        found = sheet.find(keep)
        refill_combo(self.d_delivery, [(sp.label(), sp.name) for sp in sheet.speeds], found.name if found else None)

    def _pick_sheet_dir(self):
        """Lets the user choose another rate sheets folder and reloads the sheet list from it."""
        d = QFileDialog.getExistingDirectory(self, "Rate sheets folder", self.d_dir.text() or self.d_dir.placeholderText())
        if d:
            self.d_dir.setText(d)
            self._sheet_dir = d
            self._load_sheet_names()

    def _open_sheet_dir(self):
        """Opens the rate sheets folder (the one typed in, or the default) in Explorer."""
        from ..rates import sheets_dir
        open_path(sheets_dir(self.d_dir.text().strip()))

    def _load_models(self):
        """Fills the model box with the models Ollama has installed. Silent if Ollama is not running (waits at most 2 seconds)."""
        from ..extract_llm import OllamaExtractor
        try:
            names = OllamaExtractor(self.s).installed_models(timeout=2)
        except Exception:
            return
        current = self.a_model.currentText()
        self.a_model.clear()
        self.a_model.addItems(sorted(set(names) | {current}))
        self.a_model.setCurrentText(current)

    def _test_ai(self):
        """Checks the typed model and URL with a throwaway Settings object, so nothing is saved, and shows the result."""
        from ..extract_llm import OllamaExtractor
        tmp = Settings()
        tmp.ollama_model, tmp.ollama_host = self.a_model.currentText().strip(), self.a_host.text().strip()
        ok, msg = OllamaExtractor(tmp).status()
        self.a_status.setText(("✓ " if ok else "✗ ") + msg)

    def _run_sheet_settings(self) -> Settings:
        """A throwaway Settings with the run sheets folder typed in (for Open folder before Save)."""
        tmp = Settings()
        tmp.runsheet_dir = self.r_dir.text().strip()
        return tmp

    def _open_run_sheets_folder(self):
        """Opens the run sheets folder typed in (or the default), making it first if needed."""
        folder = runsheets_folder(self._run_sheet_settings())
        try:
            folder.mkdir(parents=True, exist_ok=True)  # not made until the first run sheet is
        except OSError as e:
            QMessageBox.warning(self, "Could not open the folder", str(e))
            return
        open_path(folder)

    @staticmethod
    def _reporter_names(text: str) -> tuple[dict[str, str], list[str]]:
        """'ds = Dana' lines -> {'ds': 'Dana'} (also 'D.S.: Dana', 'ds - Dana', a tab, or 'ds Dana'), and the
        lines that can't be read that way. The initials are written as in My info: 'D. S.' is 'ds'."""
        out, bad = {}, []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"(.+?)\s*(?:=|:|\t|\s-\s)\s*(.+)", line)
            if not m or not re.fullmatch(r"[a-z]{2,4}", reporter_key(m.group(1))):
                m = re.match(r"((?:[A-Za-z]\.?\s?){2,3})\s+(.+)", line)  # "ds Dana", "D.S. Dana"
            if m and re.fullmatch(r"[a-z]{2,4}", reporter_key(m.group(1))) and m.group(2).strip():
                out[reporter_key(m.group(1))] = m.group(2).strip()
            else:
                bad.append(line)
        return out, bad

    def _pick_into(self, edit: QLineEdit, title: str):
        """Asks for a folder, starting at the one in `edit`, and puts the choice into `edit`."""
        d =QFileDialog.getExistingDirectory(self, title, edit.text() or edit.placeholderText())
        if d:
            edit.setText(d)

    def accept(self):
        """Copies every control into the Settings object, saves it to disk and closes. A blank file name
        pattern or invoice number format keeps the old one. Reporter lines that cannot be read are left out,
        with a warning that lists them."""
        s, p = self.s, self.s.profile
        p.name, p.title = self.p_name.text().strip(), self.p_title.text().strip()
        p.address1, p.address2 = self.p_addr1.text().strip(), self.p_addr2.text().strip()
        p.phone, p.fax, p.email = self.p_phone.text().strip(), self.p_fax.text().strip(), self.p_email.text().strip()
        p.website = self.p_web.text().strip()
        p.initials = self.p_initials.text().strip().lower().replace(".", "").replace(" ", "")
        s.default_court, s.default_county = self.d_court.text().strip(), self.d_county.text().strip()
        s.default_delivery = self.d_delivery.currentData() or s.default_delivery
        s.default_copies = self.d_copies.text().strip()
        s.rate_sheet = self.d_sheet.currentData() or s.rate_sheet
        s.rate_sheets_dir = self.d_dir.text().strip()
        for attr, spin in self.days.items():
            setattr(s, attr, spin.value())
        s.fill_delivery_date = self.o_deldate.isChecked()
        s.per_email, s.sign_reporter = self.o_per_email.isChecked(), self.p_sign.isChecked()
        self._store_signature()
        s.agreement_today, s.flatten = self.o_today.isChecked(), self.o_flat.isChecked()
        s.open_after, s.title_case_names = self.o_open.isChecked(), self.o_tc.isChecked()
        s.form_choice = self.o_form.currentData()
        s.include_instructions = self.o_instr.isChecked()
        s.batch_combine_dates = self.o_combine.isChecked()
        s.output_dir, s.filename_pattern = self.o_dir.text().strip(), self.o_pattern.text().strip() or s.filename_pattern
        s.theme = self.o_theme.currentText()
        s.outputs = [k for k, cb in self.o_outputs.items() if cb.isChecked()]
        s.mofr_division = self.o_division.currentData()
        s.mofr_filename_pattern = self.o_mofr_pattern.text().strip() or s.mofr_filename_pattern
        # speeds named otherwise on a rate sheet (ticked in the Outputs box) have no box here: they stay
        from ..rates import speed_key
        keep = [x for x in s.invoice_speeds if speed_key(x) not in {speed_key(n) for n in SPEEDS}]
        s.invoice_speeds = keep + [k for k, cb in self.i_speeds.items() if cb.isChecked()]
        s.invoice_include_email, s.invoice_include_index = self.i_email.isChecked(), self.i_index.isChecked()
        s.invoice_index_threshold = self.i_threshold.value()
        s.invoice_index_rule = self.i_rule.currentData()
        s.invoice_index_shared = self.i_shared.currentData()
        s.invoice_joint = bool(self.i_joint.currentData())
        s.invoice_detail_items = [k for k, cb in self.i_items.items() if cb.isChecked()]
        s.invoice_turnaround = {k: e.text().strip() for k, e in self.i_turn.items()}
        s.invoice_payment_text = self.i_pay.toPlainText().strip()
        s.invoice_footer = self.i_footer.toPlainText().strip()
        s.invoice_number_format = self.i_number.text().strip() or s.invoice_number_format
        s.invoice_filename_pattern = self.i_pattern.text().strip() or s.invoice_filename_pattern
        s.records_dir = self.i_records.text().strip()
        s.runsheet_dir = self.r_dir.text().strip()
        s.runsheet_existing = self.r_existing.currentData()
        s.runsheet_filename_pattern = self.r_pattern.text().strip() or s.runsheet_filename_pattern
        s.reporters, bad = self._reporter_names(self.r_names.toPlainText())
        if bad:
            QMessageBox.warning(self, "Reporters", "These lines under Run sheet → Reporters were left out (write "
                                "them as initials = name, e.g. ds = Dana):\n\n" + "\n".join(bad[:10]))
        s.show_djinn = self.o_djinn.isChecked()
        s.use_ai, s.ai_for_text = self.a_use.isChecked(), self.a_text.isChecked()
        s.ollama_model, s.ollama_host = self.a_model.currentText().strip(), self.a_host.text().strip()
        s.ai_timeout = self.a_timeout.value()
        s.save()
        super().accept()


class ClarifyDialog(QDialog):
    """Asks about blank or conflicting fields, and which attorney(s) ordered."""

    def __init__(self, questions: list[tuple[str, str, list[str]]], attorneys: list[Attorney] | None, parent=None):
        """questions: (field key, current value, other candidates) for each field to confirm.
        attorneys: shown as a checklist of who ordered, or None to skip that question.
        """
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
        """The text of each question's box, by field key (blank means leave the field empty)."""
        return {k: cb.currentText().strip() for k, cb in self.combos.items()}

    def checked_attorneys(self) -> list[int] | None:
        """Indexes of the ticked attorneys, or None if the attorney question was not asked."""
        if self.att_list is None:
            return None
        return [self.att_list.item(i).data(Qt.UserRole) for i in range(self.att_list.count())
                if self.att_list.item(i).checkState() == Qt.Checked]


DEFAULTS_TIP = "Your defaults for every invoice are in Settings → Invoice."


def _tip(text: str) -> QLabel:
    """A small grey note."""
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _invoice_buttons(dlg: QDialog, on_defaults) -> QDialogButtonBox:
    """OK, Cancel and "Use my defaults" (which calls on_defaults and closes the window with OK)."""
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    reset = buttons.addButton("Use my defaults", QDialogButtonBox.ResetRole)
    reset.clicked.connect(on_defaults)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    return buttons


class InvoiceExtrasDialog(QDialog):
    """What one job's invoice includes besides the original and the copies: an e-mailed copy for each party,
    and the index (automatic, always or never). values() gives (email, index), None where it is as Settings
    say."""

    def __init__(self, email: bool | None, index: str | None, s: Settings, days: int = 1, parent=None):
        """email, index: the job's choices (None = Settings); days: how many days the invoice covers."""
        from PySide6.QtWidgets import QButtonGroup, QRadioButton
        super().__init__(parent)
        self.setWindowTitle("Invoice extras")
        self.setMinimumWidth(460)
        self.s = s
        self.default_email = s.invoice_include_email
        self.default_index = "auto" if s.invoice_include_index else "off"
        lay = QVBoxLayout(self)
        intro = QLabel("For this job's invoice" + (f" (all {days} days of the case)" if days > 1 else "") + ":")
        intro.setObjectName("subtitle")
        lay.addWidget(intro)
        self.email = QCheckBox("An e-mailed copy for each party (Email column of the rate sheet)")
        self.email.setChecked(self.default_email if email is None else email)
        lay.addWidget(self.email)
        lay.addSpacing(6)
        lay.addWidget(QLabel("Index (and one for the judge):"))
        rule = INDEX_RULES.get(s.invoice_index_rule, "").lower()
        self.index = QButtonGroup(self)
        for key, label in (("auto", f"Automatic: from {s.invoice_index_threshold} pages ({rule})"),
                           ("on", "Always, however short the transcript"), ("off", "Never")):
            rb = QRadioButton(label)
            rb.setProperty("key", key)
            rb.setChecked(key == (self.default_index if index is None else index))
            self.index.addButton(rb)
            lay.addWidget(rb)
        lay.addSpacing(6)
        lay.addWidget(_tip(DEFAULTS_TIP))
        lay.addWidget(_invoice_buttons(self, self._defaults))

    def _defaults(self):
        """Use my defaults: the boxes as Settings say, and the window closes with OK (values() gives (None, None))."""
        self.email.setChecked(self.default_email)
        for b in self.index.buttons():
            b.setChecked(b.property("key") == self.default_index)
        self.accept()

    def values(self) -> tuple[bool | None, str | None]:
        """(email, index) for the job; None where the choice is the same as Settings, so it follows them."""
        email = self.email.isChecked()
        b = self.index.checkedButton()
        index = b.property("key") if b else self.default_index
        return (None if email == self.default_email else email), (None if index == self.default_index else index)


class InvoiceShowDialog(QDialog):
    """What "Show granular detail" adds to one job's invoice (keys of DETAIL_ITEMS). values() gives the list,
    or None when it is the same as Settings."""

    def __init__(self, items: list | None, s: Settings, parent=None):
        """items: the job's choice (None = Settings.invoice_detail_items)."""
        super().__init__(parent)
        self.setWindowTitle("Granular detail")
        self.setMinimumWidth(420)
        self.default = list(s.invoice_detail_items)
        lay = QVBoxLayout(self)
        intro = QLabel("With \"Show granular detail\" ticked, this job's invoice also shows:")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        chosen = self.default if items is None else items
        self.boxes: dict[str, QCheckBox] = {}
        for key, label in DETAIL_ITEMS.items():
            cb = QCheckBox(label)
            cb.setChecked(key in chosen)
            self.boxes[key] = cb
            lay.addWidget(cb)
        lay.addSpacing(6)
        lay.addWidget(_tip("\"Pages for each day\" is shown when the invoice covers several days. " + DEFAULTS_TIP))
        lay.addWidget(_invoice_buttons(self, self._defaults))

    def _defaults(self):
        """Use my defaults: the boxes as Settings say, and the window closes with OK (values() gives None)."""
        for key, cb in self.boxes.items():
            cb.setChecked(key in self.default)
        self.accept()

    def values(self) -> list | None:
        """The items ticked; None when they are the same as Settings, so the job follows them."""
        items = [k for k, cb in self.boxes.items() if cb.isChecked()]
        return None if set(items) == set(self.default) else items


class PortionsDialog(QDialog):
    """Who ordered…: which attorney ordered which pages of one day. Each row is a stretch of pages, from the
    page after the row above up to the page chosen (the last row ends at the day's pages), with a box per
    attorney. values() gives the rows as Job.portions keeps them, or None when every attorney ticked on the
    day ordered every page."""

    def __init__(self, attorneys: list[Attorney], pages: int, rows: list | None, ticked: list[str], parent=None):
        """attorneys: the columns (the attorneys ticked on the day); pages: the day's pages; rows: the job's
        Job.portions (None = everyone ordered every page; ticks of attorneys not among the columns are left
        out); ticked: the Attorney.key()s ticked on the day (the default row)."""
        super().__init__(parent)
        self.setWindowTitle("Who ordered which pages")
        self.setMinimumWidth(460)
        self.pages = max(1, pages)
        self.keys = list(dict.fromkeys(a.key() for a in attorneys))
        self.default = [k for k in self.keys if k in ticked] or list(self.keys)  # nobody ticked: everyone
        self.result_rows: list | None = None
        self.lines: list[tuple[QLabel, QSpinBox, list[QCheckBox]]] = []
        lay = QVBoxLayout(self)
        intro = QLabel(f"This day has {self.pages} pages. Each row is a stretch of them: tick who ordered it.")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        box = QWidget()
        self.grid = QGridLayout(box)
        self.grid.setContentsMargins(0, 6, 0, 6)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(6)
        named = {}  # one column per attorney (the same one written two ways is one)
        for a in attorneys:
            named.setdefault(a.key(), a)
        for c, a in enumerate(named.values()):
            name = a.name or a.firm
            head = QLabel(name if len(name) <= 24 else name[:23] + "…")
            head.setToolTip(" - ".join(x for x in (a.name, a.firm) if x))
            self.grid.addWidget(head, 0, 2 + c, Qt.AlignHCenter | Qt.AlignBottom)
        lay.addWidget(box)
        buttons = QHBoxLayout()
        for text, slot, tip in (("Add row", self._add_row, "Splits the last row in two"),
                                ("Remove row", self._remove_row, "The row above then runs to the last page"),
                                ("Everyone ordered every page", self._everyone,
                                 "Back to the default: every attorney ticked on the day ordered all of it")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addWidget(_tip("Pages ordered by several attorneys share the original and the judge's index; each "
                           "attorney pays for their own copy (Settings → Invoice says who pays the index). The "
                           "Parties number doesn't apply to a day split here."))
        self.error = QLabel("")
        self.error.setObjectName("problem")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        lay.addWidget(self.error)
        ok = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok.accepted.connect(self.accept)
        ok.rejected.connect(self.reject)
        lay.addWidget(ok)
        self._show_rows(rows or [(self.pages, self.default)])

    def _show_rows(self, rows: list) -> None:
        """Puts these rows ((last page, keys) each) in the grid, in place of the ones shown."""
        for label, spin, boxes in self.lines:
            for w in [label, spin] + boxes:
                self.grid.removeWidget(w)
                w.deleteLater()
        self.lines = []
        for r, (last, keys) in enumerate(rows, 1):
            label = QLabel("")
            spin = QSpinBox()
            spin.setRange(1, self.pages)
            spin.setValue(min(max(1, int(last)), self.pages))
            spin.setToolTip("The last page of this stretch")
            spin.valueChanged.connect(self._update_starts)
            boxes = []
            for c, k in enumerate(self.keys):
                cb = QCheckBox()
                cb.setChecked(k in keys)
                boxes.append(cb)
                self.grid.addWidget(cb, r, 2 + c, Qt.AlignHCenter)
            self.grid.addWidget(label, r, 0, Qt.AlignRight)
            self.grid.addWidget(spin, r, 1)
            self.lines.append((label, spin, boxes))
        last_spin = self.lines[-1][1]
        last_spin.setValue(self.pages)
        last_spin.setEnabled(False)  # the last row always runs to the day's last page
        last_spin.setToolTip("The last row runs to the day's last page")
        self._update_starts()

    def _update_starts(self, _=None) -> None:
        """Each row's label says where its pages start: the page after the row above ends."""
        start = 1
        for label, spin, _ in self.lines:
            label.setText(f"Pages {start} to")
            start = spin.value() + 1

    def rows(self) -> list[tuple[int, list[str]]]:
        """The rows as shown: (last page, keys of the attorneys ticked) each."""
        return [(spin.value(), [k for k, cb in zip(self.keys, boxes) if cb.isChecked()])
                for _, spin, boxes in self.lines]

    def _add_row(self) -> None:
        """Splits the last row in two halves with the same attorneys ticked (no row of a single page is split)."""
        rows = self.rows()
        start = rows[-2][0] + 1 if len(rows) > 1 else 1
        if start >= self.pages:
            return
        mid = min(self.pages - 1, max(start, start - 1 + (self.pages - start + 1) // 2))
        rows[-1] = (mid, rows[-1][1])
        rows.append((self.pages, list(rows[-1][1])))
        self._show_rows(rows)

    def _remove_row(self) -> None:
        """Removes the last row; the row above then runs to the last page."""
        rows = self.rows()
        if len(rows) > 1:
            rows.pop()
            rows[-1] = (self.pages, rows[-1][1])
            self._show_rows(rows)

    def _everyone(self) -> None:
        """Everyone ordered every page: the job goes back to the default, and the window closes with OK."""
        self.result_rows = None
        super().accept()

    def problem(self) -> str:
        """What is wrong with the rows ("" when nothing is): a row nobody ordered, or a row that doesn't end
        after the one above."""
        prev = 0
        for last, keys in self.rows():
            if not keys:
                return f"Pages {prev + 1} to {last}: tick who ordered them."
            if last <= prev:
                return f"The row after page {prev} must end on a later page than the row above."
            prev = last
        if prev != self.pages:
            return f"The last row must end on page {self.pages}."
        return ""

    def accept(self):
        """Closes with OK when the rows make sense (else says what is wrong). Rows next to each other with the
        same attorneys are joined; one row of everyone ticked on the day is the default (None)."""
        why = self.problem()
        self.error.setText(why)
        self.error.setVisible(bool(why))
        if why:
            return
        rows: list[tuple[int, list[str]]] = []
        for last, keys in self.rows():
            if rows and set(rows[-1][1]) == set(keys):
                rows[-1] = (last, rows[-1][1])
            else:
                rows.append((last, keys))
        default = len(rows) == 1 and set(rows[0][1]) == set(self.default)
        self.result_rows = None if default else rows
        super().accept()

    def values(self) -> list | None:
        """The rows for Job.portions ((last page, keys) each), or None for everyone ordering every page."""
        return self.result_rows


class RunSheetDialog(QDialog):
    """Asks whether a transcript's takes go on a run sheet that seems to be this case's, or on a new one."""

    def __init__(self, found: list, title: str, parent=None):
        """found: runsheet.Found entries, the likeliest first. title: the job, as the window names it."""
        from PySide6.QtWidgets import QButtonGroup, QRadioButton
        super().__init__(parent)
        self.setWindowTitle("Run sheet")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel(f"There may already be a run sheet for {title}. Add this transcript's takes to it, or start "
                       "a new run sheet?")
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        lay.addWidget(intro)
        if any(f.why == "name" for f in found):
            note = QLabel("A run sheet with the same case name but another index number may be the same trial: "
                          "several index numbers are often billed together.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            lay.addWidget(note)
        self.group = QButtonGroup(self)
        for i, f in enumerate(found):
            rb = QRadioButton(f"Add to  {f.path.name}\n({f.reason()}{'' if f.ours else ', made elsewhere'})")
            rb.setToolTip(str(f.path))
            rb.setProperty("path", str(f.path))
            rb.setChecked(i == 0)
            self.group.addButton(rb)
            lay.addWidget(rb)
        rb = QRadioButton("Start a new run sheet")
        rb.setProperty("path", "")
        self.group.addButton(rb)
        lay.addWidget(rb)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def choice(self) -> str:
        """The run sheet to add to, or "" for a new one."""
        b = self.group.checkedButton()
        return b.property("path") if b else ""


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
    """Downloads an Ollama model in the background (the model is several GB).

    progress carries the status text and a percentage (-1 when unknown); finished_ok carries the
    final message, a tick or a cross, whichever way it ended.
    """
    progress = Signal(str, int)   # status text, percent (-1 = unknown)
    finished_ok = Signal(str)

    def __init__(self, model: str, host: str):
        """model: the Ollama model name; host: the Ollama URL (empty for the default)."""
        super().__init__()
        self.model, self.host = model, host

    def run(self):
        """Streams the download and reports progress. Any error, including Ollama not running, is turned into a message."""
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
    """Step-by-step help for installing Ollama, with a button that downloads the model.

    The download keeps going if the dialog is closed (see accept).
    """
    def __init__(self, model: str, host: str, parent=None):
        """model: the model the guide and download button refer to; host: the Ollama URL."""
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
        site.clicked.connect(lambda: open_url("https://ollama.com/download"))
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
        """Starts the model download on a background thread and shows the progress bar."""
        self.pull_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText("Starting download…")
        self.thread = _PullThread(self.model, self.host)
        self.thread.progress.connect(self._progress)
        self.thread.finished_ok.connect(self._done)
        self.thread.start()

    def _progress(self, status: str, pct: int):
        """Shows download progress: a percentage bar when the total is known, a busy bar otherwise."""
        if pct >= 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self.status.setText(f"{status}  {pct}%")
        else:
            self.bar.setRange(0, 0)
            self.status.setText(status)

    def _done(self, msg: str):
        """Hides the progress bar, shows the final message and allows a new download."""
        self.bar.setVisible(False)
        self.status.setText(msg)
        self.pull_btn.setEnabled(True)

    def reject(self):
        """Escape or the window's close button behaves like Close."""
        self.accept()

    def accept(self):
        """Closes the dialog. A download still running is handed to _BACKGROUND so it is not cut off."""
        if self.thread and self.thread.isRunning():
            _BACKGROUND.append(self.thread)  # keep the download alive after the dialog closes
        super().accept()


# Downloads that outlive their dialog. Qt aborts the program if a QThread is destroyed while it still runs,
# so a reference is kept here for as long as the app is open.
_BACKGROUND: list[QThread] = []


CONTACT_EMAIL = "noahcollincourtreporter@gmail.com"
SOURCE_URL = "https://github.com/nono638/DjinnItAgreementForm"
FEEDBACK_URL = f"mailto:{CONTACT_EMAIL}?subject=DjinnItAgreementForm%20{__version__}"
COFFEE_URL = "https://buymeacoffee.com/noahcollin"

COMPONENTS = [
    ("Qt / PySide6", "LGPL-3.0", "https://www.qt.io/qt-for-python"),
    ("PyMuPDF", "AGPL-3.0 (or Artifex commercial license)", "https://pymupdf.readthedocs.io"),
    ("Pillow", "MIT-CMU", "https://python-pillow.org"),
    ("ollama-python", "MIT", "https://github.com/ollama/ollama-python"),
    ("PyWinRT (Windows OCR bindings)", "MIT", "https://github.com/pywinrt/pywinrt"),
    ("openpyxl (run sheets)", "MIT", "https://openpyxl.readthedocs.io"),
    ("et_xmlfile (used by openpyxl)", "MIT", "https://foss.heptapod.net/openpyxl/et_xmlfile"),
    ("HTTPX (used by ollama-python)", "BSD-3-Clause", "https://www.python-httpx.org"),
    ("Python", "PSF License", "https://www.python.org"),
]


class AboutDialog(QDialog):
    """The About window: version, author, licence, links, where settings are kept and the open-source credits."""
    def __init__(self, parent=None):
        """Builds the window; the version and settings folder shown come from the running program."""
        super().__init__(parent)
        from pathlib import Path
        from ..settings import settings_dir
        self.setWindowTitle("About DjinnItAgreementForm")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        pic = Path(__file__).resolve().parent.parent / "assets" / "djinn_done.jpg"
        if pic.exists():
            img = QLabel()
            img.setPixmap(rounded(pic, 420, 12))
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
        mail.clicked.connect(lambda: open_url(FEEDBACK_URL))
        coffee = QPushButton("☕  Buy me a coffee")
        coffee.clicked.connect(lambda: open_url(COFFEE_URL))
        ok = QPushButton("Close")
        ok.clicked.connect(self.accept)
        row.addWidget(credits)
        row.addWidget(mail)
        row.addWidget(coffee)
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)

    def _credits(self):
        """Shows the list of open-source components (COMPONENTS) and how the optional AI features are supplied."""
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
