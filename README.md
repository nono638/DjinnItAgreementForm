# DjinnItAgreementForm

A Windows app for New York court reporters. It fills in the UCS **Court Reporter Minute Agreement Form** (Private Party Transactions) and the **Minute Order Form/Receipt (MOFR)** for you, makes **invoices** from transcripts, keeps a trial's **run sheet** (which reporter wrote which pages), and keeps a record of everything it made, with a dashboard of what you billed and what was paid.

Drop in a transcript, invoice, or photo of a court document, or paste an e-mail. The app finds the court, part, judge, case name, index number, dates, proceeding type and attorneys, shows everything for review, and saves a filled PDF.

**[Website](https://nono638.github.io/DjinnItAgreementForm/)  ·  [Download the installer](../../releases/latest)**

![DjinnItAgreementForm](docs/screenshot.png)

- **Works offline, and nothing leaves your computer.** Photos are read by the text recognition built into Windows. An AI helper is optional and runs locally too.
- **Asks when unsure.** Guesses are highlighted. When there are several candidates (two dates, several attorneys), you pick from a list. It asks about anything important that's missing before filling.
- **Remembers you.** Your name, address, phone and email are filled in on every form.
- **"Per email" option.** It can write "per email" on the attorney signature line, since minutes are often ordered by email.
- **Your signature.** Choose a photo or scan of your signature once (Settings → My info) and it is placed on the court reporter line of every form. The paper background is removed, so the form's line shows through.
- **Fewer keystrokes.** Default rate sheet, speed and number of copies are set once in Settings → Defaults. Buttons under the estimated delivery date fill in today, tomorrow, 1–3 weeks or 1 month from today.
- **Rate sheets.** Pick your price list (private, city, …) and delivery speed from dropdowns. The rate, delivery checkbox and estimated delivery date follow automatically.
- **One form per attorney.** Tick several attorneys to get a separate PDF for each.
- **Batches.** Drop many documents, or a whole folder, at once. Documents about the same case and date are combined, so each order gets one form.
- **Choose what to make:** the **Outputs** box at the bottom of the window has a column for each of *Minute agreement*, *Invoice*, *MOFR* and *Run sheet*. Tick the ones to make; each column holds that output's own options (the form and "per email" for the agreement, the speeds offered for the invoice, and so on). Your choices are remembered.
- **Fix it afterwards.** The PDFs stay fillable: every value the app typed in, invoice amounts included, is a field you can still change in a PDF viewer, and a blank one can be typed into. When it's final, use **File → Lock finished PDFs** to save a locked copy (it works in any PDF viewer), or tick *Flatten the PDF* in Settings → Options to lock every PDF as it is made.
- **Invoices from transcripts.** Drop a transcript PDF and the invoice is priced from its page count and your rate sheet: one original, a copy (and e-mailed copy) for each ordering party, and an index for long transcripts, split between the parties. It lists the speeds you tick (Regular and Expedited by default) so the attorney can choose, with your turnaround times and payment instructions (Settings → Invoice). Like most reporters' invoices it shows only each speed's amount; tick *Show granular detail* to add the page count, the pages of each day, the price per page and the charges that make up each amount. Several days of one case get **one joint invoice** with the grand total. Invoices need a transcript, because the page count is what's billed.
- **Run sheets for shared trials.** When reporters take turns, each puts their initials at the foot of the pages they write. The app reads them and keeps the trial's run sheet in Excel: a row per take with the date, reporter, pages written, start and end page and the witness who took the stand. Filter by reporter for their total pages; the *By Reporter* sheet adds them up.
- **Records and dashboard.** Every file made is logged. **Records** (Ctrl+R) shows your invoices with totals billed, paid and outstanding, filtered by year, month, firm or status, broken down by firm and by month. Tick **Paid** when an invoice is paid, and say which speed they chose. Export a report (HTML), an Excel workbook or CSV files at any time.
- **Three agreement forms to choose from:** the court's own fillable UCS form (the default), a clean, re-typeset version of it (with room for a long case name, signature/date fields, fax and email), or the original 1999 scan.

## Install

1. Download `DjinnItAgreementForm-Setup-x.y.z.exe` from the [Releases](../../releases) page.
2. Run it. **No administrator rights are needed**; it installs just for you.
   - The installer isn't code-signed yet, so Windows may show "Windows protected your PC". Click **More info → Run anyway**.
3. On first launch, enter your details (name, address, phone, email). You can change them later in **Settings**.

**Requirements:** 64-bit Windows 10 (version 1809 or newer) or Windows 11, and about 200 MB of disk space. Nothing else needs installing: Python and all libraries are included, and no internet connection is needed. Reading photos and scanned PDFs uses Windows' text recognition, which needs the English language pack (normally already there). The optional AI helper needs [Ollama](https://ollama.com).

## Use

1. **Add the order:**
   - Drop a PDF (transcript, invoice, scanned document) or a photo onto the drop zone.
   - Or paste an e-mail into the text box and click **Extract from text**.
   - Or paste a screenshot with **Ctrl+V**.

   You can add several inputs for one job, and their results are combined.
2. **Check the fields.**
   - Each field has a badge showing where its value came from: **regex** (found in the document), **AI**, **default** (your settings), **derived** (calculated), or **you**.
   - Amber fields are guesses. A ▾ button lists other candidates.
3. **Pick the rate sheet and speed**, then tick the attorney(s) who ordered.
4. **Tick what to make** in the Outputs box (minute agreement, invoice, MOFR, run sheet) and check each one's options, then **Generate** (Ctrl+Enter). The PDFs are saved next to your document, or to the folder chosen in Settings, and open automatically; the run sheet is kept in its own folder (see Run sheets).

## Invoices and records

An invoice can be made when one of the job's inputs is a **transcript PDF**: its pages are billed (you can change the number in *Est. number of pages*). The word index printed after a transcript (Min-U-Script) is not counted. Several transcripts in one job, such as the days of a trial, are added up. A title that runs over two pages ("Title continues on next page") is read to its end, so the attorneys listed on the second page are found too. One invoice is made for each ticked attorney, numbered 2026-0001, 2026-0002, … (the format is in Settings → Invoice). The *Invoice* panel of the Outputs box shows the prices before you generate. There you tick the speeds to offer (tick one to bill that speed alone; with none ticked, the speed chosen under Order is billed), change the number of ordering parties, and choose *Show granular detail*. Two buttons change this job's invoice only (your defaults for every invoice are in Settings → Invoice):
- **Extras…**: the e-mailed copy for each party (some attorneys skip it to save money), and the index: automatic, always (for a short transcript too) or never.
- **Customize…**: what granular detail shows: the page count, the pages of each day, the price per page, the charges in each amount and the split between the parties.

**Several days of one case.** When you generate the days of a case together (*Generate all*), they share **one invoice** for each ordering attorney: every date is listed, and the amount is the grand total. The agreements and MOFRs are still made one per day. The *Prices* line of the Invoice panel says when *Generate all* will put the day shown on such an invoice (only ticked days are). A day once invoiced is not billed again by *Generate all*, even when you tick it again; new documents for it make it billable again. Select a single day and *Generate this job* to bill that day alone, or choose *An invoice for each day* in Settings → Invoice. The index follows a rule you can change there too: by default, once any day reaches the threshold (50 pages), every day gets an index; it can also be each day on its own, or the days' pages together.

Without granular detail the invoice lists each speed with its turnaround and the amount due, and nothing else. With it, the invoice also shows the number of pages, the price per page, the charges that make up each amount (as in the table below) and how it is split between the parties. Either way the amounts, the invoice number and date, *Bill to* and the case details are fields, so you can correct them in a PDF viewer.

The price of each speed, per page of the transcript, is:

| Line | Charged |
|---|---|
| Original | Original rate, once (shared between the parties) |
| Copy | Copy rate, for each party |
| E-mailed copy | Email rate, for each party (optional) |
| Index | Index rate, for each party, from 50 pages (optional; the threshold and the rule for several days are in Settings → Invoice) |
| Judge's index | Index rate, once, with the indexes |

Each party pays the total divided by the number of parties.

**Records** (Ctrl+R, or File → Records) has two tabs:
- **Invoices**: totals (billed, paid, outstanding), filters by year, month, firm and status, and per-firm and per-month breakdowns. Tick **Paid** to record a payment; you're asked which speed they paid for and the amount and date. Right-click an invoice to open it, void it or add a note.
- **Everything made**: every minute agreement, MOFR and invoice, and every run sheet takes were added to, with the case, attorney and file. Double-click to open the file.

The records live in `%APPDATA%\DjinnItAgreementForm\records.db`, and are copied to `invoices.csv` and `activity.csv` in *Documents\DjinnIt Records* after every change, so you can always open them in Excel. Buttons export an HTML report, an Excel workbook (with *By firm* and *By month* sheets) or CSV files.

Prefer a spreadsheet? **File → Save the invoice spreadsheet template** gives you an Excel/Google Sheets workbook that prices invoices the same way (fill in *Setup* once, then *Job* for each invoice, and print the *Invoice* tab).

## MOFR

The Minute Order Form/Receipt gets the reporter's parts only: county, Civil/Criminal (*Case* in the MOFR column of the Outputs box), title, your name and location, index number, part, judge, dates, total copies, the type of order and the page count. The judge's, counsel's, clerk's and auditor's sections are left blank. On a civil form the printed "PEOPLE V" is covered, so the full caption fits.

The djinn in the drop zone shows what's happening: working while documents are read, smiling when the form is ready, and stumped when something required is missing. If you'd rather not see him, turn him off in Settings → Options.

## Run sheets

When several reporters share a trial, each one writes their initials at the foot (or top) of every page they write, and the run sheet keeps track of who wrote what, for billing. Tick **Run sheet** and Generate: the transcript's pages are grouped into takes by those initials, one row per take, in the columns of a reporters' run sheet:

| Date | Weekday | Reporter | Day's Take | Running Take | Pages written | Starting Page No. | Ending Page No. | Witness Start | Witness End | Note |
|---|---|---|---|---|---|---|---|---|---|---|

- **Reporter:** the initials become a name. Put your own initials in Settings → My info, and other reporters' in Settings → Run sheet ("ds = Dana"). Without that, the reporters named on the title page ("DANA SMITH, Senior Court Reporter") are matched to the initials; failing that, the initials are written.
- **Witness Start / End:** a witness sworn in, or a new witness in the running head, starts; "the witness stepped down" ends.
- **Title pages alone** show as half a take in *Day's Take* (Note: *title page only*), as on a hand-kept run sheet. The *By Reporter* totals don't count them as a take, but do count their pages.
- The weekday, take counts and page numbers are formulas (the *Don't write here* columns; the first page of a transcript is written as a number), so rows you type in or correct are counted too. Filter the Reporter column to see one reporter's rows; the total of the pages shown is at the top. The **By Reporter** sheet totals each reporter's takes and pages.

**One run sheet per trial.** Before adding, the app looks for the case's run sheet in the run sheets folder (*Documents\DjinnIt Run Sheets*, Settings → Run sheet) and next to the transcript. It's the case's when it has the same **index number**, or the same **case name**: a trial can have several index numbers that are billed together. It then asks whether to add the takes to it or start a new one (or, if you prefer, always adds or always starts a new one: *If one exists* in the Run sheet column of the Outputs box). Takes already on the run sheet are not added twice, and an unchanged run sheet isn't saved again. New takes go in date and page order; rows already there keep their place, and what you typed on them (notes, formulas, extra columns) is kept. If the run sheet is open in Excel, nothing is made until you close it, so trying again never makes a second invoice.

A run sheet made elsewhere, such as one downloaded from Google Sheets as .xlsx, can be added to as well. It needs Date, Reporter and Pages columns. New rows go at its end (or on a row you typed in ahead for that day) with its own formulas copied down, and a copy of the file is kept first ("… (before DjinnIt).xlsx").

## Batches

Drop any number of documents at once, or a folder (**File → Open a folder of documents**). The app reads them all and sorts them into **jobs**, one per case and date:

- Documents belong together when they have the same index number and share a date of proceedings. A transcript and the invoice for it make one form, not two.
- A document without an index number is matched by its caption, so "Smith v Jones" in an e-mail finds "John Smith v. Jones Trucking Corp.".
- Two days of the same case make two forms. To get one form listing all the days, turn on Settings → Options → *Batches: put all days of the same case on one form*.
- A document that names no date joins its case if there is only one job for it.
- A document that names no case gets a job of its own, left unticked.

The jobs appear in a list on the left. Click one to check or correct it in the usual editor; right-click a document to move it to a job of its own. ✓ marks the jobs already saved, ⚠ the ones that would have blanks on the form: something required is missing, or attorneys were found but none is ticked (a transcript lists everyone who appeared, not who ordered).

**Generate all** (Ctrl+Shift+Enter) makes the ticked outputs for every ticked job. A job without a transcript gets its forms but no invoice or run sheet, and the summary says which. The days of one trial in a batch go on one run sheet, and you're asked about it once. Documents dropped later are added to the job they belong to, and files that are already loaded are skipped.

Batches are read with the built-in rules and Windows' text recognition only; the AI helper is not used, as it would take most of a minute per document.

The same can be done without the window:
`DjinnItAgreementForm.exe --batch <output folder> [--outputs agreement,mofr,invoice,runsheet] <files or folders…>` makes the outputs (those ticked in the window, unless `--outputs` says otherwise) and writes a `batch.json` report. With nobody to ask, takes are added to a run sheet only when it has the same index number (or, with Settings → Run sheet set to always add, the same case name).

Files this app made (agreements, MOFRs, invoices) are recognised and skipped when a folder is read again, so its output can live next to your documents.

## Rate sheets

Rates come from small CSV files you can edit in Excel. Open their folder with **File → Open rate sheets folder**; it's `%APPDATA%\DjinnItAgreementForm\Rate Sheets`, or any folder you choose in Settings.

To add one (for example city rates):
1. Copy `Rate Sheet TEMPLATE.csv`.
2. Rename it, e.g. `City Rates.csv`.
3. Fill in the prices.
4. Click ⟳ next to the *Rate sheet* dropdown.

```
Rate,Original,Copy,Email,Index,Days
Immediate,$7.60,$1.45,$1.45,$1.45,0
Daily,$6.50,$1.30,$1.30,$1.30,1
Expedite,$5.40,$1.10,$1.10,$1.10,7
Regular,$4.30,$1.00,$1.00,$1.00,21
Rates Last Updated:,5/2/2024
```

- Each row is a delivery speed.
- **Original** is the per-page rate written on the form. The other price columns are shown for reference.
- **Days** (optional) is the turnaround used for the estimated delivery date. Without it, Settings → Defaults → *Turnaround days* is used.
- Files with "template" in the name are ignored.
- Speeds the form has no box for (e.g. *Immediate*) are marked under "Other".

## Optional AI helper (Ollama)

The app doesn't need AI. The built-in rules handle transcripts, invoices, photos and ordinary e-mails. The optional local model helps most with very informal e-mails.

1. Install **Ollama for Windows** from <https://ollama.com/download>.
2. In the app: **Help → Set up the AI helper → Download gemma4:e2b**. The download is about 7 GB. Or run `ollama pull gemma4:e2b` in PowerShell.
3. **Settings → AI → Test connection.**

On a typical business laptop with no dedicated graphics card, the model runs on the processor and takes about 30–60 seconds per document. Results appear as soon as the rules finish, and the AI suggestions (purple **AI** badge) are added when they arrive. Everything the model says is checked against the original text, so invented values are dropped.

If photos come out blank, Windows' OCR language pack is missing. Install it from an admin PowerShell:
`Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"`

## If something goes wrong

The program keeps a small log at `%APPDATA%\DjinnItAgreementForm\logs\app.log` (about 3 MB at most). It records the program and Windows versions, which kind of file was read or failed, each form filled, and any crash with where it happened.

**It never records what the documents say.** File names, e-mail addresses, index numbers, phone numbers and case names are left out, and nothing is sent anywhere.

- **Help → Copy details for a problem report** puts the versions, a few settings and the recent log on the clipboard, ready to paste into an e-mail or a [GitHub issue](../../issues).
- **Help → Open the log folder** opens the folder, if you would rather attach the file.

## Building from source

Requires Python 3.12 on Windows.

```bat
setup_env.bat           :: create .venv and install requirements
run.bat                 :: run from source
.venv\Scripts\python -m pytest
build_exe.bat           :: dist\DjinnItAgreementForm\DjinnItAgreementForm.exe
build_installer.bat     :: tests + exe + installer (needs Inno Setup 6) -> dist\installer\
```

**Release checklist:**
1. Run `build_installer.bat`. It runs the tests, then raises the version by one patch step (1.0.1 → 1.0.2) if that version was already released on GitHub (or, without the `gh` tool, already built). The number lives only in `minute_filler/__init__.py`.
   - For a bigger step, run `python tools/bump_version.py minor` (or `major`, or an exact number such as `1.4.0`) first. `python tools/bump_version.py` alone shows the current version.
2. Upload `dist\installer\DjinnItAgreementForm-Setup-x.y.z.exe` to a GitHub release.

With Claude Code, `/update-the-app` runs the whole cycle: a bug sweep, a docstrings review, commit and push, the installer, the GitHub release, the website and these notes (`.claude/skills/update-the-app/`).

Never change the `AppId` in `installer/DjinnItAgreementForm.iss`; Windows uses it to recognise upgrades.

**Testing with real documents:** put them in `samples_internal/`. That folder is git-ignored and never published. Every file there is run through extraction and form filling by `tests/test_private_samples.py`, and expected values can be listed in `samples_internal/expected.json`. The public tests use the fictional documents in `tests/samples/`.

### How it works

| Step | Module |
|---|---|
| Read input: PDF text layer (PyMuPDF), Windows OCR for photos and scans, e-mail/text | `ingest.py` |
| Rule-based extraction: index no., part, judge, caption, dates, appearances, e-mail signatures… | `extract_regex.py` |
| Optional local AI pass, validated against the source text | `extract_llm.py` |
| Merge candidates, apply defaults and rate sheet, derive dates and pages | `merge.py`, `rates.py` |
| Sort documents into jobs by case and date, fill a whole batch, one invoice for the days of a case | `batch.py` |
| Fill the chosen form, one PDF per attorney; fields for typed values, locked copies | `fill.py`, `forms/` |
| Fill the MOFR | `mofr.py`, `forms/mofr_map.py` |
| Price and draw invoices (values as fields) | `invoice_calc.py`, `invoice.py` |
| Read who wrote which pages, keep the run sheet | `takes.py`, `runsheet.py` |
| Make the chosen outputs and record them | `deliver.py`, `records.py` |
| PySide6 interface | `gui/` |

The invoice spreadsheet template is built by `python tools/make_invoice_template.py`. The clean form is generated by `python tools/make_clean_form.py`. The original scan's randomly named fields are mapped in `forms/original_map.py`.

## Contact

Created by **Noah Collin**, Senior Court Reporter.
Questions, bugs or ideas: [noahcollincourtreporter@gmail.com](mailto:noahcollincourtreporter@gmail.com), or open an issue.

If this saves you time, you can [buy me a coffee ☕](https://buymeacoffee.com/noahcollin).

## License

[GNU AGPL-3.0](LICENSE). You're free to use, share and modify this program. If you distribute a modified version, you must share its source under the same license.

Built with Qt for Python (LGPL-3.0), PyMuPDF (AGPL-3.0), Pillow, PyWinRT and ollama-python. The Minute Agreement Form itself is a New York State Unified Court System form. The djinn artwork was generated with Google Gemini.
