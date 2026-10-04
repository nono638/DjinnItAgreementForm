"""The app's dialogs: Settings, the "please clarify" questions before filling, the run sheet choice, a job's
invoice Extras, granular detail, excerpts (who ordered which pages) and whose pages of a transcript of several
reporters to bill, the Ollama setup help (with a model download), About and the How to use guide (GUIDE).
The preview before saving and the first-run welcome are in preview.py."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import __version__
from ..models import Attorney, FIELD_LABELS
from ..settings import (DETAIL_ITEMS, INDEX_RULES, INDEX_SHARED, OUTPUT_FOLDERS, OUTPUTS, SPEEDS, TEXT_PLACEHOLDERS,
                        TEXT_PLACES, TEXT_WHEN, Settings, default_invoice_texts, reporter_key)
from .widgets import check_row, open_path, open_url, refill_combo, rounded
from .zoom import z


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
    def __init__(self, settings: Settings, parent=None):
        """settings: the Settings object to edit in place."""
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(z(560))
        lay = QVBoxLayout(self)
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
        self.p_initials.setToolTip("Run sheets and invoices: the pages with these initials are yours (an invoice "
                                   "of a transcript several reporters wrote bills only yours).\n"
                                   "Blank = the first letters of your first and last name.")
        for label, wid in (("Name", self.p_name), ("Title", self.p_title), ("Address", self.p_addr1),
                           ("", self.p_addr2), ("Telephone", self.p_phone), ("Fax", self.p_fax),
                           ("Email", self.p_email), ("Website", self.p_web), ("Initials", self.p_initials)):
            f.addRow(label, wid)
        # Signature picture: shown as it will appear on the form; stored on Save
        self._sig_new: str | None = None  # None = unchanged, "" = removed, else a prepared file
        self.p_sig = QLabel()
        self.p_sig.setObjectName("muted")
        self.p_sig.setMinimumHeight(z(56))
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
        self.o_preview = QCheckBox("Show a preview before saving (Generate)")
        self.o_preview.setToolTip("Generate first shows the files as pictures; nothing is saved until you say so.\n"
                                  "Generate all (a batch) saves without a preview.")
        self.o_tc = QCheckBox("Convert ALL-CAPS names to Title Case")
        self.o_djinn = QCheckBox("Show the Djinn (working / done / stumped pictures)")
        for cb, val in ((self.o_per_email, settings.per_email),
                        (self.o_today, settings.agreement_today), (self.o_flat, settings.flatten),
                        (self.o_open, settings.open_after), (self.o_preview, settings.preview_before_saving),
                        (self.o_tc, settings.title_case_names),
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
        self._folders(f, settings)
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
        from .zoom import MAX, MIN, zoom
        self.o_zoom = QSpinBox()
        self.o_zoom.setRange(round(MIN * 100), round(MAX * 100))
        self.o_zoom.setSingleStep(10)
        self.o_zoom.setSuffix(" %")
        self.o_zoom.setValue(round(zoom() * 100))
        self.o_zoom.setToolTip("How big everything in the window is drawn, on top of Windows' own display scaling.\n"
                               "Also Ctrl + and Ctrl − in the window, or Ctrl and the mouse wheel;\n"
                               "Ctrl 0 goes back to 100 %.")
        f.addRow("Zoom", self.o_zoom)
        self.o_fuzzy_numbers = QCheckBox("Fuzzy search matches numbers loosely too")
        self.o_fuzzy_numbers.setToolTip("Records → the search box's Fuzzy box. Ticked: numbers are matched like words, "
                                        "so \"2026-0001\" also finds 2026-0002.\nUnticked: a word with a digit in "
                                        "it (an invoice or index number, a date) must be found as typed;\n"
                                        "names and other words still forgive a typo.")
        self.o_fuzzy_numbers.setChecked(settings.fuzzy_numbers)
        f.addRow("Records search", self.o_fuzzy_numbers)
        self.o_recaps = QCheckBox("Show a recap of last month (and last year) when Records is first opened")
        self.o_recaps.setToolTip("\"Last month you made $870.00 with 243 pages\": once a month, and once a year for "
                                 "the year before.")
        self.o_recaps.setChecked(settings.recaps)
        f.addRow("Recaps", self.o_recaps)
        self.o_updates = QCheckBox("Look for a newer version once a day")
        self.o_updates.setToolTip("The only time the app goes online: it asks GitHub for the number of the latest "
                                  "version\nand shows a link when there is a newer one. Nothing about you, your "
                                  "computer or your cases is sent,\nand nothing is installed by itself.")
        self.o_updates.setChecked(settings.check_updates)
        f.addRow("New versions", self.o_updates)
        scroll = QScrollArea()  # a long tab: scrolls on small screens
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(w)
        self.tabs.addTab(scroll, "Options")

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
        self.i_threshold.setFixedWidth(z(110))
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
        self.i_texts = InvoiceTextEditor(settings.invoice_texts, self._preview_invoice)
        f.addRow("Invoice text", self.i_texts)
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
        where = QLabel("The run sheets' folder is under Options → Folders.")
        where.setObjectName("muted")
        f.addRow("Folder", where)
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
        self.r_names.setFixedHeight(z(110))
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
            self.p_sig.setPixmap(pix.scaled(z(230), z(56), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.p_sig_remove.setEnabled(not pix.isNull())

    def _pick_signature(self):
        """Asks for a photo or scan of the signature and prepares it (transparent background, cropped).

        The result waits in signature_new.png until Save. Choosing a signature also ticks the box to sign
        every form. A picture that cannot be used (for instance a blank one) gives a message instead.
        """
        from PySide6.QtWidgets import QMessageBox
        from ..signature import prepare, signature_path
        src, _ = QFileDialog.getOpenFileName(self, "Picture of your signature", "",
                                             "Pictures (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.heic *.heif)")
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

    def _preview_invoice(self) -> None:
        """Preview…: a made-up invoice (two parties, the speeds offered) with the text as it is in the editor now,
        before Save, shown as a picture."""
        import copy
        import tempfile
        from pathlib import Path
        from ..invoice import sample_invoice
        s = copy.deepcopy(self.s)
        s.invoice_texts = self.i_texts.rows()
        s.flatten = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                png = sample_invoice(s, Path(tmp))
                from PySide6.QtGui import QPixmap
                pix = QPixmap(str(png))
        except Exception as e:
            QMessageBox.warning(self, "Preview", f"Could not make the preview: {e}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Invoice preview (made-up case)")
        lay = QVBoxLayout(dlg)
        pic = QLabel()
        pic.setPixmap(pix)
        scroll = QScrollArea()
        scroll.setWidget(pic)
        lay.addWidget(scroll)
        ok = QDialogButtonBox(QDialogButtonBox.Close)
        ok.rejected.connect(dlg.reject)
        lay.addWidget(ok)
        dlg.resize(min(pix.width() + z(40), z(900)), z(820))
        dlg.exec()

    def _folders(self, f: QFormLayout, settings: Settings) -> None:
        """The Folders rows of the Options tab: "Save to" (every output), then a row for each output (a key of
        OUTPUTS, so an output added later gets one too) to give it a folder of its own."""
        head = QLabel("Folders")
        head.setObjectName("section")
        f.addRow(head)
        self.o_dir = QLineEdit(settings.output_dir)
        self.o_dir.setPlaceholderText("The folder of the dropped file")
        f.addRow("Save to", self._folder_row(self.o_dir, "Save the outputs to", None))
        self.o_dirs: dict[str, QLineEdit] = {}
        for key, label in OUTPUTS.items():
            e = QLineEdit(settings.output_dirs.get(key, ""))
            builtin = OUTPUT_FOLDERS.get(key)
            e.setPlaceholderText(str(Settings().folder_for(key)) if builtin else "Same as Save to")
            e.setToolTip(f"Where {label.lower()}s are saved. Blank = "
                         + (f"{Settings().folder_for(key)}" if builtin else "the Save to folder above") + ".")
            self.o_dirs[key] = e
            f.addRow(f"{label}s", self._folder_row(e, f"Folder for {label.lower()}s", key))
        hint = QLabel("Give an output a folder of its own, or leave it blank to save it with the others.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow("", hint)

    def _folder_row(self, edit: QLineEdit, title: str, key: str | None) -> QHBoxLayout:
        """[folder] [Browse...] [Open] for one of the Folders rows."""
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._pick_into(edit, title))
        row.addWidget(browse)
        opener = QPushButton("Open")
        opener.setToolTip("Opens the folder (made first when it isn't there yet)")
        opener.clicked.connect(lambda: self._open_folder(edit, key))
        row.addWidget(opener)
        return row

    def _open_folder(self, edit: QLineEdit, key: str | None) -> None:
        """Opens the folder of a Folders row as typed (before Save): its own, else where its files go now."""
        from pathlib import Path
        typed = edit.text().strip()
        if typed:
            folder = Path(typed)
        elif key in OUTPUT_FOLDERS:
            folder = Settings().folder_for(key)
        elif self.o_dir.text().strip():
            folder = Path(self.o_dir.text().strip())
        else:
            QMessageBox.information(self, "Folders", "Without a Save to folder, the files are saved next to the "
                                    "first document dropped (or in Documents\\Minute Agreements).")
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)  # not made until the first file is saved there
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
        s.zoom = self.o_zoom.value() / 100
        s.fuzzy_numbers = self.o_fuzzy_numbers.isChecked()
        s.preview_before_saving = self.o_preview.isChecked()
        s.recaps, s.check_updates = self.o_recaps.isChecked(), self.o_updates.isChecked()
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
        s.invoice_texts = self.i_texts.rows()
        s.invoice_number_format = self.i_number.text().strip() or s.invoice_number_format
        s.invoice_filename_pattern = self.i_pattern.text().strip() or s.invoice_filename_pattern
        s.records_dir = self.i_records.text().strip()
        s.output_dirs = {k: e.text().strip() for k, e in self.o_dirs.items() if e.text().strip()}
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


class InvoiceTextEditor(QWidget):
    """Settings -> Invoice -> Invoice text: the invoice's own text as rows. Each row says where it goes
    (TEXT_PLACES), when it is shown (TEXT_WHEN, e.g. only when more than one party ordered) and what it says,
    with {placeholders}; the row selected is edited in the box under the list. rows() gives them as
    Settings.invoice_texts keeps them."""

    def __init__(self, rows: list[dict], on_preview=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QListWidget()
        self.table.setMinimumHeight(z(130))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # long rows are cut short with "…"
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.currentRowChanged.connect(self._show_row)
        lay.addWidget(self.table)
        pick = QHBoxLayout()
        self.where, self.when = QComboBox(), QComboBox()
        for combo in (self.where, self.when):  # as narrow as the Settings window needs, not as the longest choice
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        for key, label in TEXT_PLACES.items():
            self.where.addItem(label, key)
        for key, label in TEXT_WHEN.items():
            self.when.addItem(label, key)
        self.where.currentIndexChanged.connect(self._edited)
        self.when.currentIndexChanged.connect(self._edited)
        pick.addWidget(QLabel("Where:"))
        pick.addWidget(self.where, 1)
        pick.addWidget(QLabel("Show when:"))
        pick.addWidget(self.when, 1)
        lay.addLayout(pick)
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(z(84))
        self.text.setPlaceholderText("What the invoice says, e.g. The {transcript} {is} delivered once every party "
                                     "has paid.")
        self.text.textChanged.connect(self._edited)
        lay.addWidget(self.text)
        buttons = QHBoxLayout()
        for label, slot, tip in (("Add", self._add, "A new row of text"),
                                 ("Remove", self._remove, "Removes the row selected"),
                                 ("Up", lambda: self._move(-1), "Moves the row up (rows in one place are shown in "
                                                                "this order)"),
                                 ("Down", lambda: self._move(1), "Moves the row down"),
                                 ("Restore defaults", self._defaults, "The text the invoice had at first"),
                                 ("Preview…", on_preview, "A made-up invoice with this text")):
            if slot is None:
                continue
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addWidget(_tip(f"Placeholders: {TEXT_PLACEHOLDERS}. A row is shown only when its condition holds: "
                           "\"More than one party ordered\" for a transcript shared by several attorneys, say."))
        self._rows = [dict(r) for r in rows]
        self._loading = False
        self._refresh(0)

    def rows(self) -> list[dict]:
        """The rows as Settings.invoice_texts keeps them (blank ones left out)."""
        return [dict(r) for r in self._rows if r["text"].strip()]

    def _label(self, r: dict) -> str:
        """'Under the amounts · More than one party ordered: The {transcript} {is} delivered…'."""
        first = r["text"].strip().splitlines()[0] if r["text"].strip() else "(empty)"
        return f"{TEXT_PLACES.get(r['where'], r['where'])} · {TEXT_WHEN.get(r['when'], r['when'])}:  {first}"

    def _refresh(self, select: int) -> None:
        """Lists the rows again and selects one."""
        self.table.blockSignals(True)
        self.table.clear()
        for r in self._rows:
            self.table.addItem(self._label(r))
        self.table.blockSignals(False)
        if self._rows:
            self.table.setCurrentRow(max(0, min(select, len(self._rows) - 1)))
        self._show_row(self.table.currentRow())

    def _show_row(self, i: int) -> None:
        """Puts the row selected in the boxes under the list (empty and greyed out when there is none)."""
        has = 0 <= i < len(self._rows)
        for w in (self.where, self.when, self.text):
            w.setEnabled(has)
        self._loading = True
        if has:
            r = self._rows[i]
            self.where.setCurrentIndex(max(0, self.where.findData(r["where"])))
            self.when.setCurrentIndex(max(0, self.when.findData(r["when"])))
            self.text.setPlainText(r["text"])
        else:
            self.text.setPlainText("")
        self._loading = False

    def _edited(self, _=None) -> None:
        """A box under the list was changed: the row selected follows it."""
        i = self.table.currentRow()
        if self._loading or not 0 <= i < len(self._rows):
            return
        self._rows[i] = {"where": self.where.currentData(), "when": self.when.currentData(),
                         "text": self.text.toPlainText()}
        self.table.item(i).setText(self._label(self._rows[i]))

    def _add(self) -> None:
        i = self.table.currentRow() + 1 if self._rows else 0
        self._rows.insert(i, {"where": "amounts", "when": "always", "text": ""})
        self._refresh(i)
        self.text.setFocus()

    def _remove(self) -> None:
        i = self.table.currentRow()
        if 0 <= i < len(self._rows):
            del self._rows[i]
            self._refresh(i)

    def _move(self, step: int) -> None:
        i, j = self.table.currentRow(), self.table.currentRow() + step
        if 0 <= i < len(self._rows) and 0 <= j < len(self._rows):
            self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
            self._refresh(j)

    def _defaults(self) -> None:
        """Back to the text the invoice had at first (the payment and footer rows too, after asking)."""
        if QMessageBox.question(self, "Invoice text", "Put back the text the invoice had at first? Your own rows, "
                                "including your payment details, are replaced.") == QMessageBox.Yes:
            self._rows = default_invoice_texts()
            self._refresh(0)


class ClarifyDialog(QDialog):
    """Asks about blank or conflicting fields, and which attorney(s) ordered."""

    def __init__(self, questions: list[tuple[str, str, list[str]]], attorneys: list[Attorney] | None, parent=None):
        """questions: (field key, current value, other candidates) for each field to confirm.
        attorneys: shown as a checklist of who ordered, or None to skip that question.
        """
        super().__init__(parent)
        self.setWindowTitle("A few details before filling")
        self.setMinimumWidth(z(560))
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
            self.att_list.setMaximumHeight(z(min(200, 30 * len(attorneys) + 10)))
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
        super().__init__(parent)
        self.setWindowTitle("Invoice extras")
        self.setMinimumWidth(z(460))
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
        self.setMinimumWidth(z(420))
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


def excerpt_rows(pages: int, everyone: list[str], who: str, first: int, last: int) -> list[tuple[int, list[str]]]:
    """The Excerpts... rows for an excerpt: `who` ordered pages first to last, everyone else the whole day.
    excerpt_rows(100, ["a", "b"], "b", 20, 40) -> [(19, ["a"]), (40, ["a", "b"]), (100, ["a"])]."""
    first, last = max(1, min(first, pages)), max(1, min(last, pages))
    first, last = min(first, last), max(first, last)
    others = [k for k in everyone if k != who]
    rows = [(first - 1, others)] if first > 1 else []
    rows.append((last, [k for k in everyone if k in others or k == who]))
    if last < pages:
        rows.append((pages, others))
    return rows


class PortionsDialog(QDialog):
    """Excerpts…: which attorney ordered which pages of one day. One attorney ordering the whole
    transcript and another an excerpt is set in one go at the top (excerpt_rows); otherwise each row is a
    stretch of pages, from the page after the row above up to the page chosen (the last row ends at the day's
    pages), with a box per attorney. values() gives the rows as Job.portions keeps them, or None when every
    attorney ticked on the day ordered every page."""

    def __init__(self, attorneys: list[Attorney], pages: int, rows: list | None, ticked: list[str], parent=None,
                 printed: list | None = None):
        """attorneys: the columns (the attorneys ticked on the day); pages: the day's pages; rows: the job's
        Job.portions (None = everyone ordered every page; ticks of attorneys not among the columns are left
        out); ticked: the Attorney.key()s ticked on the day (the default row); printed: the number printed on
        each of the pages (Job.printed_pages), shown next to each row ([] = not known)."""
        super().__init__(parent)
        self.setWindowTitle("Excerpts: who ordered which pages")
        self.setMinimumWidth(z(560))
        self.pages = max(1, pages)
        self.printed = list(printed or [])
        self.keys = list(dict.fromkeys(a.key() for a in attorneys))
        self.default = [k for k in self.keys if k in ticked] or list(self.keys)  # nobody ticked: everyone
        self.result_rows: list | None = None
        self.lines: list[tuple[QLabel, QSpinBox, QLabel, list[QCheckBox]]] = []
        lay = QVBoxLayout(self)
        intro = QLabel(f"This day has {self.pages} pages. Did an attorney order only some of them (an excerpt)? "
                       "Set it here.")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        named = {}  # one column per attorney (the same one written two ways is one)
        for a in attorneys:
            named.setdefault(a.key(), a)
        # the usual case in one go: one attorney's excerpt, the others the whole transcript
        quick = QHBoxLayout()
        quick.setSpacing(6)
        self.ex_who = QComboBox()
        for k, a in named.items():
            self.ex_who.addItem(a.name or a.firm, k)
        self.ex_from, self.ex_to = QSpinBox(), QSpinBox()
        for spin, value in ((self.ex_from, 1), (self.ex_to, self.pages)):
            spin.setRange(1, self.pages)
            spin.setValue(value)
        self.ex_printed = QLabel("")  # the numbers printed on those pages, when known: "(pp. 358–377)"
        self.ex_printed.setObjectName("muted")
        self.ex_printed.setToolTip("The page numbers printed on these pages")
        for spin in (self.ex_from, self.ex_to):
            spin.valueChanged.connect(self._update_quick)
            spin.installEventFilter(self)  # Enter sets the excerpt (see eventFilter)
        self.ex_who.installEventFilter(self)
        set_btn = QPushButton("Set excerpt")
        set_btn.setToolTip("Fills in the rows below: this attorney ordered these pages, the others every page")
        set_btn.setAutoDefault(False)
        set_btn.clicked.connect(self._excerpt)
        for w in (QLabel("Excerpt:"), self.ex_who, QLabel("ordered pages"), self.ex_from, QLabel("to"), self.ex_to,
                  self.ex_printed, set_btn):
            quick.addWidget(w)
        quick.addStretch(1)
        self._update_quick()
        lay.addLayout(quick)
        hint = QLabel("…and the others ordered the whole transcript. For anything else, split the pages into rows "
                      "below and tick who ordered each.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        line = QFrame()
        line.setObjectName("outputRule")
        line.setFixedHeight(1)
        lay.addWidget(line)
        box = QWidget()
        self.grid = QGridLayout(box)
        self.grid.setContentsMargins(0, 6, 0, 6)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(6)
        for c, a in enumerate(named.values()):
            name = a.name or a.firm
            head = QLabel(name if len(name) <= 24 else name[:23] + "…")
            head.setToolTip(" - ".join(x for x in (a.name, a.firm) if x))
            self.grid.addWidget(head, 0, 3 + c, Qt.AlignHCenter | Qt.AlignBottom)
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
        for label, spin, printed, boxes in self.lines:
            for w in [label, spin, printed] + boxes:
                self.grid.removeWidget(w)
                w.hide()  # at once: until it is deleted it would be drawn over the rows, at the top left
                w.deleteLater()
        self.lines = []
        for r, (last, keys) in enumerate(rows, 1):
            label = QLabel("")
            spin = QSpinBox()
            spin.setRange(1, self.pages)
            spin.setValue(min(max(1, int(last)), self.pages))
            spin.setToolTip("The last page of this stretch")
            spin.valueChanged.connect(self._update_starts)
            printed = QLabel("")
            printed.setObjectName("muted")
            printed.setToolTip("The page numbers printed on these pages")
            boxes = []
            for c, k in enumerate(self.keys):
                cb = QCheckBox()
                cb.setChecked(k in keys)
                boxes.append(cb)
                self.grid.addWidget(cb, r, 3 + c, Qt.AlignHCenter)
            self.grid.addWidget(label, r, 0, Qt.AlignRight)
            self.grid.addWidget(spin, r, 1)
            self.grid.addWidget(printed, r, 2)
            self.lines.append((label, spin, printed, boxes))
        last_spin = self.lines[-1][1]
        last_spin.setValue(self.pages)
        last_spin.setEnabled(False)  # the last row always runs to the day's last page
        last_spin.setToolTip("The last row runs to the day's last page")
        self._update_starts()

    def _update_starts(self, _=None) -> None:
        """Each row's label says where its pages start: the page after the row above ends; and, when known, the
        numbers printed on its pages ("pp. 358–397")."""
        start = 1
        for label, spin, printed, _ in self.lines:
            label.setText(f"Pages {start} to")
            printed.setText(self._printed(start, spin.value()))
            start = spin.value() + 1

    def _printed(self, first: int, last: int) -> str:
        """'pp. 358–397': the printed numbers of pages first to last of the day ("" when not known)."""
        if len(self.printed) != self.pages or first > last:
            return ""
        a, b = self.printed[first - 1], self.printed[last - 1]
        if a is None or b is None:
            return ""
        return f"p. {a}" if first == last else f"pp. {a}–{b}"

    def _update_quick(self, _=None) -> None:
        """The excerpt row's printed page numbers, for the pages chosen in it ("" when not known)."""
        text = self._printed(self.ex_from.value(), self.ex_to.value())
        self.ex_printed.setText(f"({text})" if text else "")

    def eventFilter(self, obj, event) -> bool:
        """Enter in the excerpt row sets the excerpt, rather than pressing OK (which closed the window without
        it)."""
        if (event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and obj in (self.ex_who, self.ex_from, self.ex_to)):
            for spin in (self.ex_from, self.ex_to):
                spin.interpretText()  # a number being typed counts
            self._excerpt()
            return True
        return super().eventFilter(obj, event)

    def _excerpt(self) -> None:
        """Set excerpt: the attorney chosen ordered the pages chosen, the others every page (see excerpt_rows)."""
        who = self.ex_who.currentData()
        self._show_rows(excerpt_rows(self.pages, self.keys, who, self.ex_from.value(), self.ex_to.value()))

    def rows(self) -> list[tuple[int, list[str]]]:
        """The rows as shown: (last page, keys of the attorneys ticked) each."""
        return [(spin.value(), [k for k, cb in zip(self.keys, boxes) if cb.isChecked()])
                for _, spin, _, boxes in self.lines]

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


class PagesOwnerDialog(QDialog):
    """Whose pages…: for each transcript several reporters wrote (their initials alternate at the foot of its
    pages), which pages to bill: the user's own (the default), another reporter's, or the whole transcript;
    and, when it begins with pages nobody's initials are on, whose those are. values() gives Job.page_basis and
    Job.front_owner for these transcripts."""

    def __init__(self, transcripts: list[tuple[str, str, list[str]]], own: set[str], basis: dict, front: dict,
                 reason: str = "", parent=None):
        """transcripts: (Doc.key(), file name, Doc.owners()) of each transcript to ask about; own: the user's
        initials (empty when not known); basis, front: the job's Job.page_basis and Job.front_owner; reason:
        why the dialog opened by itself (shown at the top)."""
        super().__init__(parent)
        self.setWindowTitle("Whose pages to bill")
        self.setMinimumWidth(z(520))
        self.own = set(own)
        self.items: list[dict] = []
        lay = QVBoxLayout(self)
        intro = QLabel("These transcripts have pages by more than one reporter: the initials at the foot of the pages "
                       "change. An invoice bills your own pages unless you choose otherwise.")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        if reason:
            why = QLabel("⚠ " + reason[0].upper() + reason[1:])
            why.setObjectName("problem")
            why.setWordWrap(True)
            lay.addWidget(why)
        for key, name, owners in transcripts:
            found = list(dict.fromkeys(o for o in owners if o))
            leading = next((i for i, o in enumerate(owners) if o), 0)
            frame = QFrame()
            frame.setObjectName("outputPanel")
            fl = QVBoxLayout(frame)
            title = QLabel(name)
            title.setObjectName("section")
            fl.addWidget(title)
            item = {"key": key, "owners": owners, "found": found, "radios": {}, "front": None, "count": QLabel("")}
            item["count"].setObjectName("muted")
            item["count"].setWordWrap(True)
            fl.addWidget(item["count"])
            if len(found) > 1 and leading:
                row = QHBoxLayout()
                pages = "page has" if leading == 1 else f"{leading} pages have"
                row.addWidget(QLabel(f"The first {pages} no reporter's initials. They count for:"))
                combo = QComboBox()
                combo.addItem("Choose…", None)
                for x in found:
                    combo.addItem(x.upper() + (" (you)" if x in self.own else ""), x)
                combo.addItem("Nobody (not billed)", "none")
                current = front.get(key)
                if current is not None:
                    combo.setCurrentIndex(max(0, combo.findData(current)))
                combo.currentIndexChanged.connect(self._update)
                row.addWidget(combo)
                row.addStretch(1)
                fl.addLayout(row)
                item["front"] = combo
            group = QButtonGroup(frame)
            choices = [("me", "My pages")] + [(x, f"{x.upper()}'s pages") for x in found if x not in self.own] + [
                ("*", "The whole transcript")]
            row = QHBoxLayout()
            row.addWidget(QLabel("Bill:"))
            for value, label in choices:
                rb = QRadioButton(label)
                if value == "me" and not self.own:
                    rb.setEnabled(False)
                    rb.setToolTip("Your initials aren't known: enter them under Settings → My info")
                group.addButton(rb)
                item["radios"][value] = rb
                rb.toggled.connect(self._update)
                row.addWidget(rb)
            row.addStretch(1)
            fl.addLayout(row)
            chosen = basis.get(key, "me")
            if chosen not in item["radios"] or not item["radios"][chosen].isEnabled():
                chosen = "*" if not self.own else "me"
            item["radios"][chosen].setChecked(True)
            item["labels"] = dict(choices)
            lay.addWidget(frame)
            self.items.append(item)
        self.error = QLabel("")
        self.error.setObjectName("problem")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        lay.addWidget(self.error)
        ok = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok.accepted.connect(self.accept)
        ok.rejected.connect(self.reject)
        lay.addWidget(ok)
        self._update()

    @staticmethod
    def _count(owners: list[str], targets: set[str], front: str | None) -> int:
        """How many pages are the targets' (pages without initials at the start count for `front`)."""
        return sum(1 for o in owners if (o or front or "") in targets)

    def _update(self, _=None) -> None:
        """Each transcript's line of who wrote what, and the page count on each choice, as chosen now."""
        for item in self.items:
            owners, found = item["owners"], item["found"]
            if item["front"] is not None:
                front = item["front"].currentData()
            else:
                front = found[0] if len(found) == 1 else None
            parts = [f"{x.upper()}{' (you)' if x in self.own else ''}: {self._count(owners, {x}, front)} pp."
                     for x in found]
            unknown = sum(1 for o in owners if not o and front in (None, "none"))  # (Nobody: still no initials)
            if unknown:
                parts.append(f"no initials: {unknown} pp.")
            item["count"].setText(f"{len(owners)} pages · " + " · ".join(parts))
            for value, rb in item["radios"].items():
                n = len(owners) if value == "*" else self._count(owners, self.own if value == "me" else {value}, front)
                rb.setText(f"{item['labels'][value]} ({n})")

    def problem(self) -> str:
        """What must still be chosen ("" when nothing): whose the first pages are, when the pages billed depend
        on it."""
        for item in self.items:
            combo = item["front"]
            if combo is not None and combo.currentData() is None and not item["radios"]["*"].isChecked():
                return "Choose whose the first pages are (or bill the whole transcript)."
        return ""

    def accept(self):
        """Closes with OK once nothing is left to choose (else says what)."""
        why = self.problem()
        self.error.setText(why)
        self.error.setVisible(bool(why))
        if not why:
            super().accept()

    def values(self) -> tuple[dict, dict]:
        """(Job.page_basis, Job.front_owner) for the transcripts shown: by Doc.key(), "me", initials or "*";
        and the initials (or "none") the first pages count for."""
        basis, front = {}, {}
        for item in self.items:
            basis[item["key"]] = next(v for v, rb in item["radios"].items() if rb.isChecked())
            if item["front"] is not None and item["front"].currentData() is not None:
                front[item["key"]] = item["front"].currentData()
        return basis, front


class RunSheetDialog(QDialog):
    """Asks whether a transcript's takes go on a run sheet that seems to be this case's, or on a new one."""

    def __init__(self, found: list, title: str, parent=None):
        """found: runsheet.Found entries, the likeliest first. title: the job, as the window names it."""
        super().__init__(parent)
        self.setWindowTitle("Run sheet")
        self.setMinimumWidth(z(520))
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
        self.resize(z(640), z(640))
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
WEBSITE_URL = "https://nono638.github.io/DjinnItAgreementForm/"
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
    ("RapidFuzz (the Records search's Fuzzy box)", "MIT", "https://github.com/rapidfuzz/RapidFuzz"),
    ("regex (the Records search's Regex box)", "Apache-2.0 and CNRI-Python", "https://github.com/mrabarnett/mrab-regex"),
    ("pillow-heif (iPhone HEIC photos)", "BSD-3-Clause", "https://github.com/bigcat88/pillow_heif"),
    ("libheif and libde265 (used by pillow-heif)", "LGPL-3.0", "https://github.com/strukturag/libheif"),
    ("x265 (used by pillow-heif)", "GPL-2.0-or-later", "https://www.videolan.org/developers/x265.html"),
    ("et_xmlfile (used by openpyxl)", "MIT", "https://foss.heptapod.net/openpyxl/et_xmlfile"),
    ("HTTPX (used by ollama-python)", "BSD-3-Clause", "https://www.python-httpx.org"),
    ("OpenSSL (the look for a newer version)", "Apache-2.0", "https://www.openssl.org"),
    ("SQLite (the records)", "public domain", "https://www.sqlite.org"),
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
        self.setMinimumWidth(z(560))
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
            "photos and e-mails.<br>"
            f'Website, with a guide and the latest version: <a href="{WEBSITE_URL}">'
            f'{WEBSITE_URL.split("//")[1].rstrip("/")}</a><br><br>'
            "Created by <b>Noah Collin</b>, Senior Court Reporter.<br>"
            f'Questions, bugs or ideas: <a href="mailto:{CONTACT_EMAIL}?subject=DjinnItAgreementForm">'
            f"{CONTACT_EMAIL}</a><br><br>"
            "Free software under the GNU AGPL-3.0 license."
            + (f' Source code: <a href="{SOURCE_URL}">{SOURCE_URL}</a>' if SOURCE_URL else "")
            + f'<br><br>If this saves you time, you can <a href="{COFFEE_URL}">buy me a coffee</a> ☕')
        body.setWordWrap(True)
        body.setMinimumWidth(z(520))  # so its height is measured at the width it's shown at (else the end is cut)
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
        box.resize(z(520), z(360))
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


GUIDE = """
<h2>How to use DjinnItAgreementForm</h2>
<p>The app reads the documents of an order (a transcript, an invoice, a photo of a court paper, an e-mail),
finds the court, part, judge, case, index number, dates and attorneys, shows them for you to check, and makes
the paperwork: the minute agreement, the invoice, the MOFR and the run sheet. Everything stays on this
computer (once a day the app asks GitHub whether there is a newer version, and sends nothing about you or
your cases; Settings → Options turns that off).</p>

<h3>What you can put in</h3>
<ul>
<li><b>Transcript PDFs.</b> The best input: the case details come from the title page, and the page count
(the pages with <i>your</i> initials, when several reporters wrote it) is what the invoice bills.</li>
<li><b>Other PDFs:</b> an invoice, a scanned order, a printed e-mail. Scans are read by Windows' text
recognition.</li>
<li><b>Photos and screenshots</b> (JPG, PNG, iPhone HEIC, TIFF, BMP, WebP), or a screenshot pasted with
<b>Ctrl+V</b>.</li>
<li><b>E-mails:</b> paste the text into the box and click <b>Extract from text</b>, or drop a saved
<code>.eml</code>. Word (<code>.docx</code>), <code>.txt</code> and web pages work too. Outlook
<code>.msg</code> files don't: copy the e-mail's text instead.</li>
<li><b>A whole folder</b> (File → Open a folder of documents). Documents about the same case and date are
put together, one job per day.</li>
</ul>

<h3>What it makes</h3>
<p>Tick them in the <b>Outputs</b> box at the bottom of the window; each column holds that output's
options.</p>
<ul>
<li><b>Minute agreement</b>: the UCS Court Reporter Minute Agreement Form, one PDF for each ticked
attorney.</li>
<li><b>Invoice</b>: priced from your rate sheet and the transcript's pages, one for each ticked attorney.
It needs a transcript. Several days of one case made together with <b>Generate all</b> get one joint invoice
per attorney (unless Settings → Invoice says an invoice for each day), and an attorney who ordered an excerpt
pays only for those pages (<b>Who ordered what</b> shows who pays for what).</li>
<li><b>MOFR</b>: the Minute Order Form/Receipt, the reporter's parts filled in.</li>
<li><b>Run sheet</b>: an Excel sheet of a shared trial's takes (who wrote which pages), read from the
initials at the foot of the pages.</li>
</ul>
<p>The PDFs are saved next to your document, or in the folders chosen in Settings → Options → Folders, and
they stay fillable (unless <i>Flatten the PDF</i> is ticked in Settings → Options): you can still correct any
value in a PDF viewer. Every file made is listed in
<b>Records</b> (Ctrl+R).</p>

<h3>Step by step</h3>
<ol>
<li><b>Add the order:</b> drop the documents on the drop zone (or click <b>Browse...</b>, or Ctrl+O).</li>
<li><b>Check the fields.</b> Amber fields are guesses; a ▾ button lists the other candidates. The badge
on each field says where its value came from (found in the document, AI, your defaults, calculated or
typed by you).</li>
<li><b>Tick the attorneys who ordered.</b> A transcript lists everyone who appeared, not who ordered.</li>
<li><b>Pick the rate sheet and speed</b> under Order.</li>
<li><b>Tick the outputs</b> and click <b>Generate</b> (Ctrl+Enter), or <b>Generate all</b>
(Ctrl+Shift+Enter) for every ticked job in the list on the left.</li>
<li><b>Look at the preview</b> and click <b>Save</b>, or <b>Go back</b> to change something: nothing is saved
until then. (Generate all saves without a preview. <i>Don't show previews anymore</i> turns it off; Settings →
Options turns it back on.) The box that says what was saved can <b>print</b> it.</li>
</ol>

<h3>Tips</h3>
<ul>
<li>The first time, fill in <b>Settings → My info</b> (your name, contact details, initials and signature)
and <b>Settings → Defaults</b> (rate sheet, speed, copies) once, and they are used on every form.</li>
<li>Several documents for one order can be added together; what they say is combined.</li>
<li>In the job list, ✓ marks a job already made and ⚠ one that failed or has something missing. Right-click a
document to
move it to a job of its own.</li>
<li>When one attorney ordered the whole transcript and another only some pages, set it with
<b>Excerpts…</b> in the Invoice panel of the Outputs box, and check the <b>Who ordered what</b> card before generating.</li>
<li>Your own invoice text (shown always, or only when it applies) is in Settings → Invoice → <i>Invoice
text</i>; <b>Preview…</b> shows a made-up invoice with it.</li>
<li>In <b>Records</b>, type a firm, case or index number to find its invoices, tick <b>Paid</b> when they
pay, and choose the columns with <b>Columns…</b>. Deleted records stay in the trash for 30 days; the PDFs
are never deleted.</li>
<li>Also in <b>Records</b>: right-click a record to <b>open its job again</b> (for a corrected invoice, or
another day of the case) or to print it; <b>Undo</b> (Ctrl+Z) takes back the last change; <b>Summary…</b> sums
up a month or a year; <b>Backups…</b> lists the copies of your records made each day, to go back to one.</li>
<li><b>File → Open recent</b> lists the documents and folders you opened lately.</li>
<li>A new computer? <b>File → Export settings</b> saves your details, options and rate sheets as one file
(it asks whether to leave your personal details out, for a file you give to a colleague),
and <b>File → Import settings</b> reads it there.</li>
<li>When an invoice is final, <b>File → Lock finished PDFs</b> saves a copy nobody can change.</li>
<li>Text too small or too big? <b>Ctrl +</b> and <b>Ctrl −</b> (or Ctrl and the mouse wheel) zoom,
<b>Ctrl 0</b> goes back to 100%.</li>
<li>Prices are in rate sheets you can edit in Excel: <b>File → Open rate sheets folder</b>.</li>
</ul>

<h3>Keyboard shortcuts</h3>
<table cellpadding="2">
<tr><td><b>F1</b></td><td>This guide</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Open documents</td></tr>
<tr><td><b>Ctrl+V</b></td><td>Paste a screenshot or e-mail text</td></tr>
<tr><td><b>Ctrl+N</b></td><td>New job</td></tr>
<tr><td><b>Ctrl+Enter</b></td><td>Generate (this job)</td></tr>
<tr><td><b>Ctrl+Shift+Enter</b></td><td>Generate all</td></tr>
<tr><td><b>Ctrl+R</b></td><td>Records</td></tr>
<tr><td><b>Ctrl+Z</b></td><td>In Records: undo the last change</td></tr>
<tr><td><b>Ctrl + / Ctrl − / Ctrl 0</b></td><td>Zoom in, out, back to 100%</td></tr>
</table>

<h3>More help</h3>
<p>The <a href="{website}">website</a> shows the app with pictures and explains each feature, and has the
latest version to download. Something not working? <b>Help → Copy details for a problem report</b> and
e-mail it with <b>Help → Send feedback</b>; the log never records what your documents say.</p>
"""


class GuideDialog(QDialog):
    """Help → How to use: what the app takes in, what it makes, the steps, tips and shortcuts (GUIDE)."""

    def __init__(self, parent=None):
        """Builds the window: the guide in a scrolling text box, a Website button and Close."""
        super().__init__(parent)
        self.setWindowTitle("How to use DjinnItAgreementForm")
        self.resize(z(680), z(720))
        lay = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setHtml(GUIDE.replace("{website}", WEBSITE_URL))
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        site = QPushButton("Open the website")
        site.setToolTip(WEBSITE_URL)
        site.clicked.connect(lambda: open_url(WEBSITE_URL))
        row.addWidget(site)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
