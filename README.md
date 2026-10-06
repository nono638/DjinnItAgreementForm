# YinItAgreementForm

A Windows app for New York court reporters. It fills in the UCS **Court Reporter Minute Agreement Form** (Private Party Transactions) and the **Minute Order Form/Receipt (MOFR)** for you, makes **invoices** from transcripts, keeps a trial's **run sheet** (which reporter wrote which pages), and keeps a record of everything it made, with a dashboard of what you billed and what was paid.

Drop in a transcript, invoice, or photo of a court document, or paste an e-mail. The app finds the court, part, judge, case name, index number, dates, proceeding type and attorneys, shows everything for review, and saves a filled PDF.

**[Website](https://nono638.github.io/YinItAgreementForm/)  ·  [Download the installer](../../releases/latest)**

*Formerly DjinnItAgreementForm. Updating from it keeps your settings, rate sheets, records and run sheets.*

![YinItAgreementForm](docs/screenshot.png)

- **Works offline, and nothing leaves your computer.** Photos are read by the text recognition built into Windows. An AI helper is optional and runs locally too. The one time the app goes online is to ask GitHub, once a day, whether there is a newer version; nothing about you or your cases is sent, and Settings → Options turns it off.
- **Asks when unsure.** Guesses are highlighted. When there are several candidates (two dates, several attorneys), you pick from a list. It asks about anything important that's missing before filling.
- **Remembers you.** Your name, address, phone and email are filled in on every form.
- **"Per email" option.** It can write "per email" on the attorney signature line, since minutes are often ordered by email.
- **Your signature.** Choose a photo or scan of your signature once (Settings → My info) and it is placed on the court reporter line of every form. The paper background is removed, so the form's line shows through.
- **Fewer keystrokes.** Default rate sheet, speed and number of copies are set once in Settings → Defaults. Buttons under the estimated delivery date fill in today, tomorrow, 1–3 weeks or 1 month from today.
- **Rate sheets.** Pick your price list (private, city, …) and delivery speed from dropdowns. The rate, delivery checkbox and estimated delivery date follow automatically.
- **One form per attorney.** Tick several attorneys to get a separate PDF for each.
- **Batches.** Drop many documents, or a whole folder, at once. Documents about the same case and date are combined, so each order gets one form.
- **Choose what to make:** the **Outputs** box at the bottom of the window has a column for each of *Minute agreement*, *Invoice*, *MOFR* and *Run sheet*. Tick the ones to make; each column holds that output's own options (the form and "per email" for the agreement, the speeds offered for the invoice, and so on). Your choices are remembered, except *Show granular detail*, which is set for each job.
- **Fix it afterwards.** The PDFs stay fillable: every value the app typed in, invoice amounts included, is a field you can still change in a PDF viewer, and a blank one can be typed into. When it's final, use **File → Lock finished PDFs** to save a locked copy (it works in any PDF viewer), or tick *Flatten the PDF* in Settings → Options to lock every PDF as it is made.
- **Invoices from transcripts.** Drop a transcript PDF and the invoice is priced from its page count (your own pages, when several reporters wrote it) and your rate sheet: one original, a copy (and e-mailed copy) for each ordering party, and an index for long transcripts, split between the parties. It lists the speeds you tick (Regular and Expedited by default) so the attorney can choose, with your turnaround times and payment instructions (Settings → Invoice). Like most reporters' invoices it shows only each speed's amount; tick *Show granular detail* to add the page count, the pages of each day, the price per page and the charges that make up each amount. Several days of one case get **one joint invoice** for each attorney, covering the days (and pages) that attorney ordered, and an attorney who ordered only an **excerpt** pays for those pages. Write your own text on the invoice, shown only when it applies ("delivered once every party has paid" only when several parties ordered). Invoices need a transcript, because the page count is what's billed. After an invoice is made, **the math** is spelled out in a small window (copy it, or save it as a PDF for an attorney who asks), and a **detailed copy** of each invoice can be saved alongside it.
- **Run sheets for shared trials.** When reporters take turns, each puts their initials at the foot of the pages they write. The app reads them and keeps the trial's run sheet in Excel: a row per take with the date, reporter, pages written, start and end page and the witness who took the stand. Filter by reporter for their total pages; the *By Reporter* sheet adds them up.
- **Records and dashboard.** Every file made is logged. **Records** (Ctrl+R) shows your invoices with totals billed, paid and outstanding, filtered by year, month, firm or status, or found by typing a firm, case or index number (with optional regex or fuzzy matching), broken down by firm and by month. Tick **Paid** when an invoice is paid, and say which speed they chose. Choose the columns you want: your pages and the transcript's, the speeds offered and the one ordered, the excerpt, the e-mailed copy and more. Corrected an amount in the PDF? Right-click the invoice and choose **Change amounts…** so the totals agree. Deleted records go to a **trash** for 30 days. Export a report (HTML), an Excel workbook or CSV files at any time.
- **Fits your screen.** The window follows Windows' display scaling (125 %, 150 %…) and fits a small screen. **Ctrl +** and **Ctrl −** (or Ctrl and the mouse wheel; Ctrl 0 for 100 %, or Settings → Options → Zoom) make everything bigger or smaller; the size is remembered.
- **See it before it's saved.** Generate first shows the filled forms and invoices as pictures; nothing is saved until you click Save. The box that says what was saved can print it.
- **Go back to a past job.** Right-click a record to open its job again, for a corrected invoice or another day of the case. *File → Open recent* lists the documents you opened lately.
- **Your records are backed up** each day you open the app, and the last 10 copies are kept. Undo (Ctrl+Z) takes back the last change in Records.
- **A folder for each output.** Minute agreements, invoices, MOFRs and run sheets can each be saved to a folder of their own (Settings → Options → Folders).
- **Three agreement forms to choose from:** the court's own fillable UCS form (the default), a clean, re-typeset version of it (with room for a long case name, signature/date fields, fax and email), or the original 1999 scan.

## Install

1. Download `YinItAgreementForm-Setup-x.y.z.exe` from the [Releases](../../releases) page.
2. Run it. **No administrator rights are needed**; it installs just for you.
   - The installer isn't code-signed yet, so Windows may show "Windows protected your PC". Click **More info → Run anyway**.
3. On first launch, enter your details (name, address, phone, email). You can change them later in **Settings**.

**Requirements:** 64-bit Windows 10 (version 1809 or newer) or Windows 11, and about 200 MB of disk space. Nothing else needs installing: Python and all libraries are included, and no internet connection is needed (with one, the app tells you when there is a newer version). Reading photos and scanned PDFs uses Windows' text recognition, which needs the English language pack (normally already there). The optional AI helper needs [Ollama](https://ollama.com).

## Use

1. **Add the order:**
   - Drop a PDF (transcript, invoice, scanned document) or a photo (JPG, PNG, an iPhone HEIC…) onto the drop zone.
   - Or paste an e-mail into the text box and click **Extract from text**.
   - Or paste a screenshot with **Ctrl+V**.

   You can add several inputs for one job, and their results are combined.
2. **Check the fields.**
   - Each field has a badge showing where its value came from: **regex** (found in the document), **AI**, **default** (your settings), **derived** (calculated), or **you**.
   - Amber fields are guesses. A ▾ button lists other candidates.
3. **Pick the rate sheet and speed**, then tick the attorney(s) who ordered.
4. **Tick what to make** in the Outputs box (minute agreement, invoice, MOFR, run sheet) and check each one's options, then **Generate** (Ctrl+Enter). The PDFs are saved next to your document, or to the folder chosen in Settings → Options → Folders (a folder for all of them, and one for each kind if you like), and open automatically; the run sheet is kept in its own folder (see Run sheets).
5. **Look at the preview.** Generate first shows each PDF as it will be saved, a tab for each file (the invoice with the number it will get). **Save** saves them; **Go back** saves nothing, takes no invoice number and records nothing. The run sheet, a spreadsheet, is not shown. *Generate all* (a batch) saves without a preview. Click **Don't show previews anymore** to have Generate save at once; *Show a preview before saving* in Settings → Options turns it back on.
6. **Print** the PDFs from the box that says what was saved (they go to the printer you pick, every page, at their actual size: a form is not shrunk), or later from Records (right-click → *Print 1 invoice…*). The run sheet is a workbook: print it from Excel.

The first time the app starts it asks for your name, address, rate sheet and where to save files; all of it, and much more, is in **Settings**. **File → Open recent** lists the last 10 documents and folders you opened. **File → Export settings** saves your details, options and rate sheets as one file for another computer or a colleague (your signature picture and your records are not in it). It first asks whether to **include your personal details**: your name, address and contact details, the payment text of your invoices, other reporters' names and your folders. Include them for another computer of your own; leave them out for a colleague, who then keeps their own and gets your options, invoice wording and rate sheets, and **File → Import settings** reads it there; the settings you had are kept as *settings before import.json* in the app's settings folder.

**Help → How to use** (F1) sums this up in the app: what it reads, what it makes, the steps, tips and the keyboard shortcuts, with a link to the [website](https://nono638.github.io/YinItAgreementForm/).

## Invoices and records

An invoice can be made when one of the job's inputs is a **transcript PDF**: its pages are billed (you can change the number in *Est. number of pages*). The word index printed after a transcript (Min-U-Script) is not counted. Several transcripts in one job, such as the days of a trial, are added up. A title that runs over two pages ("Title continues on next page") is read to its end, so the attorneys listed on the second page are found too. One invoice is made for each ticked attorney who ordered pages, numbered 2026-0001, 2026-0002, … (the format is in Settings → Invoice). The *Invoice* panel of the Outputs box shows the prices before you generate. There you tick the speeds to offer (tick one to bill that speed alone; with none ticked, the speed chosen under Order is billed), change the number of ordering parties, set an excerpt (who ordered which pages), and choose *Show granular detail* for this job (it starts unticked for every new job). These buttons change this job's invoice only (your defaults for every invoice are in Settings → Invoice):
- **Extras…**: the e-mailed copy for each party (some attorneys skip it to save money), and the index: automatic, always (for a short transcript too) or never.
- **Customize…**: what granular detail shows: the page count, the pages of each day, the price per page, the charges in each amount and the split between the parties.
- **Excerpts…** (next to *Parties*): who ordered which pages of the day shown. When one attorney ordered the whole transcript and another only some pages, choose the attorney and the pages at the top (*Excerpt: Dana Smith ordered pages 20 to 40*) and click **Set excerpt**: the pages they share are split between them, and the others are the first attorney's alone. Below is a small grid, a row for each stretch of pages (1–40, 41–90, with the page numbers printed on them) and a box for each attorney, for anything else. By default every ticked attorney ordered every page. Add a row, set the page it runs to, and tick who ordered it: "the first 40 pages are Dana Smith's only, the next 50 both attorneys'". Every row needs an attorney, each row ends after the one above, and the last ends on the day's last page. It works on a job of one day, with at least two attorneys ticked on that day (the grid lists them). When the day's page count changes so that the rows no longer end on its last page, or an attorney named in them is unticked, the rows are kept but the *Prices* line says *Excerpts… needs checking*, and no invoice is made for that day (*Generate* asks; *Generate all* says so before it starts, see *A day not decided yet*) until you fix them. Renaming an attorney in the table renames them in the rows too. *Everyone ordered every page* goes back to the default. A day split this way ignores the *Parties* number.

**Who ordered what.** Whenever an invoice is to be made, the form shows a **Who ordered what** card under the attorneys. Nothing about who pays for what is left implicit: for every day the invoice covers (all the days *Generate all* bills together), there is a row for each attorney with the pages it ordered (*every page, 1–60*, or *excerpt: 20–40 of 60*), the page numbers printed on them, who else ordered the same pages, and how many of your pages that bills. An attorney ticked on another day of the case says *not ticked on this day: orders nothing*, and a day still waiting for a choice (Excerpts… to check, or whose pages to bill) says so. A day of the case with nobody ticked says in amber that no invoice is made for the case until you tick who ordered it. A row whose pages include none of yours says *0 (no invoice: none of these pages are yours)*. When you typed the Pages field, the row says that number is what's billed (*every page (Pages field typed: 40 billed)*). With nothing to bill (the Pages field says 0, say) the card says why instead of showing an empty table. **Edit excerpts…**, or a double-click on a row, opens the Excerpts window for that day; when the day waits for whose pages to bill, the button is **Whose pages…** and opens that instead.

**A day not decided yet.** When *Generate all* includes a day whose invoice waits for a choice, or a case with a day nobody is ticked on, it says which first. **Go back and decide** (the default) makes nothing and shows the first day to decide (the job list marks such days ⚠); **Go on anyway** makes everything else, and those days get no invoice for now: the other days of their case are invoiced without them, and the undecided days can be invoiced on their own once decided. A case with a day nobody is ticked on gets no invoice at all until you tick who ordered that day. The message at the end lists these days apart, as *not invoiced, as you chose*, not as files that could not be saved.

**Several days of one case.** When you generate the days of a case together (*Generate all*), each ordering attorney gets **one invoice** for all its days. An attorney is billed only for the days it is ticked on (if one day of the case has nobody ticked, the *Prices* line warns you and no invoice is made for the case until you tick who ordered that day), and on a day split with *Excerpts…* only for the pages it ordered: its invoice lists only those dates, those pages, and its own amount, and so does its row in the records. A ticked attorney who ordered no pages gets no invoice. When the attorneys' amounts differ, the *Prices* line shows each one's. The agreements and MOFRs are still made one per day. The *Prices* line of the Invoice panel says when *Generate all* will put the day shown on such an invoice (only ticked days are). A day once invoiced is not billed again by *Generate all*, even when you tick it again; new documents for it make it billable again. Select a single day and *Generate this job* to bill that day alone, or choose *An invoice for each day* in Settings → Invoice. The index follows a rule you can change there too: by default, once any day reaches the threshold (50 pages), every day gets an index; it can also be each day on its own, or the days' pages together.

**Several reporters on one transcript.** When reporters take turns on a trial, the initials at the foot of the pages change (as on the run sheet), and the invoice bills **your own pages** only: of a 150-page transcript where 65 pages carry your initials, the invoice is for 65 pages. The *Billed* line of the Invoice panel says "Your pages: 65 of 150", and the *Pages* field shows 65 (you can still type another number, which is then billed). Put your initials in Settings → My info if they aren't the first letters of your name. **Whose pages…** lets you bill another reporter's pages instead (if you bill for them) or the whole transcript. The app asks before billing when it can't tell: when none of the pages carry your initials, or when the transcript begins with pages nobody's initials are on (then you say whose they are). A transcript with no initials at all is billed in full, as before. With an excerpt, an attorney pays for your pages among those it ordered.

**Your own invoice text.** Settings → Invoice → *Invoice text* is a list of short texts, each with where it goes on the page (at the top, under the transcript details, under the amounts, payment, or the small print at the foot) and when it is shown: always, or only when more than one party ordered, one speed or several are offered, the invoice covers several days, the attorney ordered an excerpt, an e-mailed copy or an index is charged, or several reporters wrote the transcript. Texts can use placeholders such as {case}, {index}, {pages}, {parties} and {bill_to}, and {transcript} {is} for "transcript is" or "transcripts are". By default they are the notes the invoice always had: *Please choose one delivery option…* when several speeds are offered, *The transcript is delivered once every party has paid* when several parties ordered (else *sent after payment is received*), the note on how the amounts are split when granular detail shows the split (*Amounts are your share…* or *Amounts are per party…*), your payment details and the footer. **Preview…** shows a made-up invoice with your text.

Without granular detail the invoice lists each speed with its turnaround and the amount due, and your text. With it, the invoice also shows the number of pages, the price per page, the charges that make up each amount (as in the table below) and how it is split between the parties. Either way the amounts, the invoice number and date, *Bill to* and the case details are fields, so you can correct them in a PDF viewer.

**Show the math.** After **Generate** makes an invoice, a window spells out how each amount was reached, line by line: "Original: 60 pages × $4.30 = $258.00", "Copy: 2 × 60 pages × $1.00 = $120.00"…, then "Total: $618.00" and "÷ 2 parties = $309.00 each" (with "rounded up to the cent" when it doesn't split evenly). A firm's share of pages ordered together is shown to the part of a cent ("1.5 pages × $5.45 = $8.175") so the lines add up, then rounded up. Click **OK**, or **Copy all** to paste it into an e-mail, or **Save as PDF…**. **Don't show this anymore** turns it off; *Invoice math* in Settings → Options turns it back on. Generate all with several jobs doesn't show it.

**A detailed copy.** Settings → Invoice → *Also save a detailed copy of each invoice* (off unless you tick it) saves "… (detailed).pdf" next to each invoice: the same invoice with the granular detail, the same number, not recorded again in Records. It isn't opened or printed with the invoice: it's there for when someone asks. A job whose invoice already shows the granular detail gets none.

The price of each speed, per page of the transcript, is:

| Line | Charged |
|---|---|
| Original | Original rate, once (shared between the parties) |
| Copy | Copy rate, for each party |
| E-mailed copy | Email rate, for each party (optional) |
| Index | Index rate, from 50 pages (optional; the threshold and the rule for several days are in Settings → Invoice): once, shared between the parties like the original. Pages ordered by several parties are charged to each of them at the split rate: of a 100-page transcript where B ordered a 10-page excerpt, A pays 90 pages at the full rate and 10 at the split rate, B 10 at the split rate. *Index on shared pages* in Settings → Invoice can make it one for each party instead |
| Judge's index | Index rate, once, with the index (shared between the parties) |

Each party pays its share, rounded up to the cent so the shares cover the total: when every party ordered every page, that is the total divided by the number of parties ($319.30 three ways is $106.44 each). When they ordered different days or pages, each attorney's invoice adds up, for every stretch of pages it ordered with others (*n* parties in all): the original ÷ *n*, its own copy and e-mailed copy, the index ÷ *n* (or whole, with *Each firm pays its own*) and the judge's index ÷ *n*. A stretch it ordered alone it pays in full. For example, with $4.30 originals and $1.00 copies and e-mailed copies, 40 pages ordered by Dana Smith alone and 50 by both attorneys (no index) cost Dana Smith 40 × $6.30 + 50 × ($2.15 + $2.00) = $459.50 and the other attorney 50 × $4.15 = $207.50. With granular detail, the charges show the pages each line counts, shared pages as their share ("Original: 65 pp. × $4.30 (shared pages split between the firms)").

**Records** (Ctrl+R, or File → Records) has two tabs:
- **Invoices**: totals (billed, paid, outstanding), filters by year, month, firm and status, and per-firm and per-month breakdowns. Tick **Paid** to record a payment; you're asked which speed they paid for and the amount and date. Right-click an invoice to open it, void it, add a note, or **Change amounts…** when you corrected an amount in the invoice PDF (the records keep the amounts it was made with until you do).
- **Everything made**: every minute agreement, MOFR and invoice, and every run sheet takes were added to, with the case, attorney, pages and file. Double-click to open the file.

**Search.** Type in the search box of either tab to see only the records that match: a firm ("Smith Law"), an attorney, a case, an index number (712345/2021 or 712345-2021), an invoice number, a judge, a file name. Every word you type must be found somewhere in the record, in any order. Two boxes next to it, unticked unless you tick them, change how it searches: **Regex** reads what you type as a regular expression (`^Smith`, `2026-00(1|2)`, any case; the box turns amber while the expression is incomplete, or if searching with it takes more than a second, which stops it), and **Fuzzy** also finds near misses and misspellings ("Counsle" finds "Counsel & Counsel"). Fuzzy treats numbers the same way, so "2026-0001" also finds 2026-0002; untick *Records search: Fuzzy search matches numbers loosely too* in Settings → Options to have invoice numbers, index numbers and dates found only as typed (names still forgive a typo).

**Columns.** Click **Columns…** (or right-click a column heading) to choose what each table shows; the choice is remembered. Invoices can show the court, part, judge and dates, the firm and e-mail, the pages billed, **your pages and the transcript's pages** (they differ when several reporters wrote it), who wrote how many ("PR 65, DS 85"), the **excerpt** ordered ("6/3/2026 pp. 120–140", or *Whole*), the parties, the **speeds offered**, the **speed ordered** (the one paid for), whether an **e-mailed copy** and an **index** were charged, the amounts, the status and payment, notes and the file. Click a heading to sort by it. Invoices made by older versions leave the newer columns blank.

**Open a job again.** Right-click an invoice or a file under *Everything made* and choose **Open this job again**: the case comes back into the main window as you left it (the fields, who was ticked, the invoice's Extras and Excerpts), and the documents it was read from are read again when they are still where they were (when one is gone, what was read from it is kept as it was). The Records window closes so you see the job. Use it for a corrected invoice, or to add another day of the case. A joint invoice of several days opens its days from their documents. Records made before version 1.7 bring back the case, index number, dates, judge and attorney they list.

**Undo.** **Undo** (or Ctrl+Z) takes back the last change made in the Records window: paid or not paid, void, changed amounts, a note, a delete or a restore. It goes back up to 30 changes, until the window is closed with the app.

**Summary.** **Summary…** sums up this or last month, this or last year, or all time: invoices made, pages billed, billed, paid, still owed, payments received in the period, the average per page and per invoice, and the firms billed most. **Copy** puts it on the clipboard. The first time you open Records in a month, a box says what last month came to ("Last month (September 2026) you made $870.00 with 243 pages (5 invoices)."), and the first time in a year, last year too. Tick *Don't show me monthly or annual recaps anymore* in it to stop them; *Recaps* in Settings → Options turns them back on.

**Backups.** Each day you open the app, a copy of the records is saved in *Documents\YinIt Records\Backups* (the last 10 days' copies are kept, and the last 5 you made yourself or that were made before a restore). **Backups…** lists them, makes one now, and **restores** one: the records go back to what they were that day, after a copy of how they are now is made, so you can come back. Only the records are in a backup, not the PDF files. Invoice numbers given since stay taken.

**Deleting.** Select records and press **Delete** (or right-click → *Delete*): they go to the **🗑 Trash** for 30 days, where you can restore them, and are then deleted for good. Only the records are deleted, never the PDF files; an invoice's number is never given to another invoice.

The records live in `%APPDATA%\YinItAgreementForm\records.db`, and (except what's in the trash) are copied to `invoices.csv` and `activity.csv` in *Documents\YinIt Records* after every change, so you can always open them in Excel. Buttons export an HTML report, an Excel workbook (with *By firm* and *By month* sheets) or CSV files.

Prefer a spreadsheet? **File → Save the invoice spreadsheet template** gives you an Excel/Google Sheets workbook that prices invoices the same way when every party orders every page: one original, index and judge's index split between the parties, a copy and an e-mailed copy for each, each share rounded up to the cent (it has no excerpts). Fill in *Setup* once, then *Job* for each invoice, and print the *Invoice* tab.

## MOFR

The Minute Order Form/Receipt gets the reporter's parts only: county, Civil/Criminal (*Case* in the MOFR column of the Outputs box), title, your name and location, index number, part, judge, dates, total copies, the type of order and the page count. The judge's, counsel's, clerk's and auditor's sections are left blank. On a civil form the printed "PEOPLE V" is covered, so the full caption fits.

The yin-yang in the drop zone shows what's happening: it swirls while documents are read, settles when the form is ready, and cracks when something required is missing. If you'd rather not see it, turn it off in Settings → Options.

## Run sheets

When several reporters share a trial, each one writes their initials at the foot (or top) of every page they write, and the run sheet keeps track of who wrote what, for billing. Tick **Run sheet** and Generate: the transcript's pages are grouped into takes by those initials, one row per take, in the columns of a reporters' run sheet:

| Date | Weekday | Reporter | Day's Take | Running Take | Pages written | Starting Page No. | Ending Page No. | Witness Start | Witness End | Note |
|---|---|---|---|---|---|---|---|---|---|---|

- **Reporter:** the initials become a name. Put your own initials in Settings → My info, and other reporters' in Settings → Run sheet ("ds = Dana"). Without that, the reporters named on the title page ("DANA SMITH, Senior Court Reporter") are matched to the initials; failing that, the initials are written.
- **Witness Start / End:** a witness sworn in, or a new witness in the running head, starts; "the witness stepped down" ends.
- **Title pages alone** show as half a take in *Day's Take* (Note: *title page only*), as on a hand-kept run sheet. The *By Reporter* totals don't count them as a take, but do count their pages.
- The weekday, take counts and page numbers are formulas (the *Don't write here* columns; the first page of a transcript is written as a number), so rows you type in or correct are counted too. Filter the Reporter column to see one reporter's rows; the total of the pages shown is at the top. The **By Reporter** sheet totals each reporter's takes and pages.

**One run sheet per trial.** Before adding, the app looks for the case's run sheet in the run sheets folder (*Documents\YinIt Run Sheets*, Settings → Options → Folders) and next to the transcript. It's the case's when it has the same **index number**, or the same **case name**: a trial can have several index numbers that are billed together. It then asks whether to add the takes to it or start a new one (or, if you prefer, always adds or always starts a new one: *If one exists* in the Run sheet column of the Outputs box). Takes already on the run sheet are not added twice, and an unchanged run sheet isn't saved again. New takes go in date and page order; rows already there keep their place, and what you typed on them (notes, formulas, extra columns) is kept. If the run sheet is open in Excel, nothing is made until you close it, so trying again never makes a second invoice.

A run sheet made elsewhere, such as one downloaded from Google Sheets as .xlsx, can be added to as well. It needs Date, Reporter and Pages columns. New rows go at its end (or on a row you typed in ahead for that day) with its own formulas copied down, and a copy of the file is kept first ("… (before YinIt).xlsx").

## Batches

Drop any number of documents at once, or a folder (**File → Open a folder of documents**). The app reads them all and sorts them into **jobs**, one per case and date:

- Documents belong together when they have the same index number and share a date of proceedings. A transcript and the invoice for it make one form, not two.
- A document without an index number is matched by its caption, so "Smith v Jones" in an e-mail finds "John Smith v. Jones Trucking Corp.".
- Two days of the same case make two forms. To get one form listing all the days, turn on Settings → Options → *Batches: put all days of the same case on one form*.
- A document that names no date joins its case if there is only one job for it.
- A document that names no case gets a job of its own, left unticked.

The jobs appear in a list on the left. Click one to check or correct it in the usual editor; right-click a document to move it to a job of its own. ✓ marks the jobs already saved, ⚠ the ones that failed or need a look: something required is missing, attorneys were found but none is ticked (a transcript lists everyone who appeared, not who ordered), or the invoice waits for a choice (the job's tooltip says which).

**Generate all** (Ctrl+Shift+Enter) makes the ticked outputs for every ticked job. A job without a transcript gets its forms but no invoice or run sheet, and the summary says which. The days of one trial in a batch go on one run sheet, and you're asked about it once. Documents dropped later are added to the job they belong to, and files that are already loaded are skipped.

Batches are read with the built-in rules and Windows' text recognition only; the AI helper is not used, as it would take most of a minute per document.

The same can be done without the window:
`YinItAgreementForm.exe --batch <output folder> [--outputs agreement,mofr,invoice,runsheet] <files or folders…>` makes the outputs (those ticked in the window, unless `--outputs` says otherwise) into the output folder, whatever folders Settings give each kind (run sheets still go to theirs), and writes a `batch.json` report. With nobody to ask, no invoice is made for a transcript several reporters wrote whose pages to bill can't be told (pages with nobody's initials at the start, or none with yours): `batch.json` says why. Likewise, takes are added to a run sheet only when it has the same index number (or, with Settings → Run sheet set to always add, the same case name).

Files this app made (agreements, MOFRs, invoices) are recognised and skipped when a folder is read again, so its output can live next to your documents.

## Rate sheets

Rates come from small CSV files you can edit in Excel. Open their folder with **File → Open rate sheets folder**; it's `%APPDATA%\YinItAgreementForm\Rate Sheets`, or any folder you choose in Settings.

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
2. In the app: **Help → Set up the AI helper → Download gemma4:e2b**. The download is 4.6 to 7.5 GB (6.6 to 9.5 GB for the larger gemma4:e4b), depending on the version Ollama picks for your computer. Or run `ollama pull gemma4:e2b` in PowerShell.
3. **Settings → AI → Test connection.**

**Which model?** Settings → AI → *Model* offers two: **gemma4:e2b** (smaller and faster, the default) and **gemma4:e4b** (larger and more accurate, but much slower on a laptop without a graphics card). Choose one there, then *How to install Ollama + gemma…* downloads the one chosen. The box also lists any other model Ollama has installed, and you can type a name.

On a typical business laptop with no dedicated graphics card, the model runs on the processor and takes about 30–60 seconds per document. Results appear as soon as the rules finish, and the AI suggestions (purple **AI** badge) are added when they arrive. Everything the model says is checked against the original text, so invented values are dropped.

If photos come out blank, Windows' OCR language pack is missing. Install it from an admin PowerShell:
`Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"`

## If something goes wrong

The program keeps a small log at `%APPDATA%\YinItAgreementForm\logs\app.log` (about 3 MB at most). It records the program and Windows versions, which kind of file was read or failed, each form filled, and any crash with where it happened.

**It never records what the documents say.** File names, e-mail addresses, index numbers, phone numbers and case names are left out, and nothing is sent anywhere.

- **Help → Copy details for a problem report** puts the versions, a few settings and the recent log on the clipboard, ready to paste into an e-mail or a [GitHub issue](../../issues).
- **Help → Open the log folder** opens the folder, if you would rather attach the file.
- **Help → Send feedback…** starts an e-mail with the version in the subject; paste the details into it.

**Help → How to use** (F1) and the [website](https://nono638.github.io/YinItAgreementForm/) explain each feature.

## Building from source

Requires Python 3.12 on Windows.

```bat
setup_env.bat           :: create .venv and install requirements
run.bat                 :: run from source
.venv\Scripts\python -m pytest
build_exe.bat           :: dist\YinItAgreementForm\YinItAgreementForm.exe
build_installer.bat     :: tests + exe + installer (needs Inno Setup 6) -> dist\installer\
```

**Release checklist:**
1. Run `build_installer.bat`. It runs the tests, then raises the version by one patch step (1.0.1 → 1.0.2) if that version was already released on GitHub (or, without the `gh` tool, already built). The number lives only in `minute_filler/__init__.py`.
   - For a bigger step, run `python tools/bump_version.py minor` (or `major`, or an exact number such as `1.4.0`) first. `python tools/bump_version.py` alone shows the current version.
2. Upload `dist\installer\YinItAgreementForm-Setup-x.y.z.exe` to a GitHub release.

With Claude Code, `/update-the-app` runs the whole cycle: a bug sweep, a docstrings review, commit and push, the installer, the GitHub release, the website and these notes (`.claude/skills/update-the-app/`).

Never change the `AppId` in `installer/YinItAgreementForm.iss`; Windows uses it to recognise upgrades.

**Testing with real documents:** put them in `samples_internal/`. That folder is git-ignored and never published. Every file there is run through extraction and form filling by `tests/test_private_samples.py`, and expected values can be listed in `samples_internal/expected.json`. The public tests use the fictional documents in `tests/samples/`.

### How it works

| Step | Module |
|---|---|
| Read input: PDF text layer (PyMuPDF), Windows OCR for photos and scans, e-mail/text | `ingest.py` |
| Rule-based extraction: index no., part, judge, caption, dates, appearances, e-mail signatures… | `extract_regex.py` |
| Optional local AI pass, validated against the source text | `extract_llm.py` |
| Merge candidates, apply defaults and rate sheet, derive dates and pages | `merge.py`, `rates.py` |
| Sort documents into jobs by case and date, fill a whole batch, one invoice per attorney for the days of a case, who ordered which pages of a day (and the lines of the "Who ordered what" card), whose pages are billed | `batch.py` |
| Fill the chosen form, one PDF per attorney; fields for typed values, locked copies | `fill.py`, `forms/` |
| Fill the MOFR | `mofr.py`, `forms/mofr_map.py` |
| Price and draw invoices, each firm's share of the pages it ordered (values as fields); spell out their math | `invoice_calc.py`, `invoice.py`, `invoice_math.py` |
| Read who wrote which pages, keep the run sheet | `takes.py`, `runsheet.py` |
| Make the chosen outputs and record them (with where each job came from, to open it again); search the records (words, regex, fuzzy), back them up, sum up a period | `deliver.py`, `records.py` |
| Ask GitHub for the latest version (once a day, can be turned off) | `update.py` |
| PySide6 interface (zoom: `gui/zoom.py`; the preview before saving, the math of the invoices made, printing and the first-run welcome: `gui/preview.py`) | `gui/` |

The invoice spreadsheet template is built by `python tools/make_invoice_template.py`. The clean form is generated by `python tools/make_clean_form.py`. The original scan's randomly named fields are mapped in `forms/original_map.py`.

## Contact

Created by **Noah Collin**, Senior Court Reporter.
Questions, bugs or ideas: [noahcollincourtreporter@gmail.com](mailto:noahcollincourtreporter@gmail.com), or open an issue.

If this saves you time, you can [buy me a coffee ☕](https://buymeacoffee.com/noahcollin).

## License

[GNU AGPL-3.0](LICENSE). You're free to use, share and modify this program. If you distribute a modified version, you must share its source under the same license.

Built with Qt for Python (LGPL-3.0), PyMuPDF (AGPL-3.0), Pillow, PyWinRT and ollama-python. The Minute Agreement Form itself is a New York State Unified Court System form. The yin-yang artwork was generated with Google Gemini.
