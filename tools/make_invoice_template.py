"""Builds minute_filler/templates/Invoice Template.xlsx: a spreadsheet for making transcript invoices by
hand, with the same arithmetic as the app (minute_filler/invoice_calc.py). All its data is fictional.

    .venv/Scripts/python.exe tools/make_invoice_template.py

Tabs:
  Setup        your details, payment methods, turnaround wording, which speeds are offered
  Rates        per-page rates (same layout as the rate sheets)
  Job          what changes per invoice: invoice no., pages, parties, case, bill to
  Calculation  the price of every speed, one row each
  Invoice      the page to print or save as PDF
  Exhibit Log  exhibits and witnesses during a trial
  Page Log     pages written per day

Only functions that work in both Excel and Google Sheets are used. The rates, turnaround wording, payment
lines and footer are the app's own defaults (the bundled Sample Rates sheet and settings.py), so the two agree.
"""
from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from minute_filler.rates import BUNDLED_DIR, load_sheet, parse_money, speed_key  # noqa: E402
from minute_filler.settings import INVOICE_FOOTER, INVOICE_PAYMENT_TEXT, INVOICE_TURNAROUND  # noqa: E402

SHEET = load_sheet(BUNDLED_DIR / "Sample Rates.csv")
PAY_LINES = (INVOICE_PAYMENT_TEXT.splitlines() + [""] * 5)[:5]

OUT = Path(__file__).resolve().parent.parent / "minute_filler" / "templates" / "Invoice Template.xlsx"

ACCENT = "4B3F8F"
MONEY = '"$"#,##0.00'
INPUT = PatternFill("solid", fgColor="FFF7D6")   # cells to type in
HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor=ACCENT)
BOLD = Font(bold=True)
MUTED = Font(color="6B6880")
THIN = Side(style="thin", color="D9D6E4")
SPEEDS = [sp.name for sp in reversed(SHEET.speeds)]  # the sheet's spelling, fastest first


def name(wb: Workbook, label: str, ref: str) -> None:
    """A workbook-wide name, so formulas read like =Pages*OriginalRate."""
    wb.defined_names[label] = DefinedName(label, attr_text=ref)


def labels(ws, rows: list[tuple], col_width=(26, 46)) -> None:
    """rows: (label, value, defined name or None, input?) from row 1 down, in columns A:B."""
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = col_width
    for r, (label, value, _, is_input) in enumerate(rows, 1):
        ws.cell(r, 1, label).font = BOLD if value is None else MUTED
        if value is not None:
            c = ws.cell(r, 2, value)
            if is_input:
                c.fill = INPUT
            c.alignment = Alignment(wrap_text=True, vertical="top")


def build() -> Workbook:
    """The template workbook, not yet saved (its tabs are listed in the module docstring)."""
    wb = Workbook()

    # ---------------------------------------------------------------- Setup
    ws = wb.active
    ws.title = "Setup"
    setup = [
        ("YOUR DETAILS (printed on every invoice)", None, None, False),
        ("Name", "Pat Reporter", "MyName", True),
        ("Title", "Senior Court Reporter", "MyTitle", True),
        ("Court / address", "Supreme Court, Example County", "MyAddress1", True),
        ("City, state, ZIP", "123 Example Street, Room 100, Anytown, NY 10000", "MyAddress2", True),
        ("Phone", "(555) 555-0100", "MyPhone", True),
        ("E-mail", "pat.reporter@example.com", "MyEmail", True),
        ("Website", "", "MyWebsite", True),
        ("", "", None, False),
        ("PAYMENT METHODS (one per line, blank lines are skipped)", None, None, False),
        *[(f"Payment {i}", line.strip(), f"PayLine{i}", True) for i, line in enumerate(PAY_LINES, 1)],
        ("Footer note", INVOICE_FOOTER, "Footer", True),
        ("", "", None, False),
        ("OPTIONS", None, None, False),
        ("Offer every speed (choice invoice)", True, "OfferChoice", True),
        ("Offer Expedite", True, "OfferExpedite", True),
        ("Offer Daily", True, "OfferDaily", True),
        ("E-mailed copy for each party", True, "EmailCopies", True),
        ("Index for each party + judge", True, "Indexes", True),
        ("...from this many pages", 50, "IndexFrom", True),
        ("", "", None, False),
        ("TURNAROUND WORDING", None, None, False),
        *[(sp, next(v for k, v in INVOICE_TURNAROUND.items() if speed_key(k) == speed_key(sp)), None, True)
          for sp in SPEEDS],
    ]
    labels(ws, setup, (34, 90))
    for r, (_, _, nm, _) in enumerate(setup, 1):
        if nm:
            name(wb, nm, f"Setup!$B${r}")
    name(wb, "Turnaround", "Setup!$A$27:$B$30")
    tf = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=False)
    ws.add_data_validation(tf)
    for r in range(19, 24):
        tf.add(f"B{r}")

    # ---------------------------------------------------------------- Rates
    ws = wb.create_sheet("Rates")
    ws.append(["Rate", "Original", "Copy", "Email", "Index"])
    for c in ws[1]:
        c.font, c.fill = HEAD, HEAD_FILL
    for sp in reversed(SHEET.speeds):
        rates = (sp.original, sp.copy, sp.extras["Email"], sp.extras["Index"])
        ws.append([sp.name] + [float(parse_money(x)) for x in rates])
    for r in range(2, 6):
        for c in range(2, 6):
            ws.cell(r, c).number_format = MONEY
            ws.cell(r, c).fill = INPUT
    ws["A8"], ws["B8"] = "Rates last updated:", SHEET.updated
    ws["B8"].fill = INPUT
    ws.column_dimensions["A"].width = 20
    name(wb, "RateTable", "Rates!$A$2:$E$5")

    # ---------------------------------------------------------------- Job
    ws = wb.create_sheet("Job")
    job = [
        ("THIS INVOICE", None, None, False),
        ("Invoice No.", "2026-0001", "InvoiceNo", True),
        ("Invoice date", "=TODAY()", "InvoiceDate", True),
        ("Pages", 30, "Pages", True),
        ("Ordering parties", 1, "Parties", True),
        ("Speed (when not offering every speed)", "Regular", "Speed", True),
        ("", "", None, False),
        ("CASE", None, None, False),
        ("Title", "Smith v. Jones", "CaseTitle", True),
        ("Index No.", "712222/2024", "IndexNo", True),
        ("Date(s) of proceedings", "5/22/2026", "CaseDates", True),
        ("Judge", "Hon. A. Example", "Judge", True),
        ("Part", "53", "Part", True),
        ("Court", "Supreme Court, Example County", "Court", True),
        ("", "", None, False),
        ("BILL TO", None, None, False),
        ("Attorney", "Alex Example, Esq.", "BillName", True),
        ("Firm", "Example Firm LLP", "BillFirm", True),
        ("Address", "1 Main Street", "BillAddress1", True),
        ("City, state, ZIP", "Anytown, NY 10000", "BillAddress2", True),
        ("E-mail", "billing@examplefirm.com", "BillEmail", True),
    ]
    labels(ws, job, (36, 46))
    for r, (_, _, nm, _) in enumerate(job, 1):
        if nm:
            name(wb, nm, f"Job!$B${r}")
    ws["B3"].number_format = "m/d/yyyy"
    sp = DataValidation(type="list", formula1='"' + ",".join(SPEEDS) + '"')
    parties = DataValidation(type="whole", operator="between", formula1="1", formula2="20")
    pages = DataValidation(type="whole", operator="greaterThan", formula1="0")
    for dv, cell in ((sp, "B6"), (parties, "B5"), (pages, "B4")):
        ws.add_data_validation(dv)
        dv.add(cell)

    # ---------------------------------------------------------------- Calculation
    ws = wb.create_sheet("Calculation")
    cols = ["Speed", "Original rate", "Copy rate", "Email rate", "Index rate", "Original", "Copies",
            "E-mailed copies", "Indexes", "Judge's index", "Total", "Each party pays", "Per page (each)"]
    ws.append(cols)
    for c in ws[1]:
        c.font, c.fill = HEAD, HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, speed in enumerate(SPEEDS, 2):
        ws.append([
            speed,
            f"=VLOOKUP($A{i},RateTable,2,FALSE)", f"=VLOOKUP($A{i},RateTable,3,FALSE)",
            f"=VLOOKUP($A{i},RateTable,4,FALSE)", f"=VLOOKUP($A{i},RateTable,5,FALSE)",
            f"=Pages*B{i}",                                            # one original, shared
            f"=Pages*C{i}*Parties",                                    # a copy for each party
            f"=IF(EmailCopies,Pages*D{i}*Parties,0)",
            f"=IF(AND(Indexes,Pages>=IndexFrom),Pages*E{i}*Parties,0)",
            f"=IF(I{i}>0,Pages*E{i},0)",
            f"=SUM(F{i}:J{i})",
            f"=ROUND(K{i}/Parties,2)",
            f"=ROUND(L{i}/Pages,2)",
        ])
        for c in range(2, 14):
            ws.cell(i, c).number_format = MONEY
    ws.column_dimensions["A"].width = 12
    for c in "BCDEFGHIJKLM":
        ws.column_dimensions[c].width = 13
    ws.row_dimensions[1].height = 32
    ws["A7"] = "Each party pays one share of the original plus their own copy (and e-mailed copy and index)."
    ws["A7"].font = MUTED
    name(wb, "CalcSpeeds", "Calculation!$A$2:$A$5")
    name(wb, "CalcEach", "Calculation!$L$2:$L$5")
    name(wb, "CalcPerPage", "Calculation!$M$2:$M$5")

    # ---------------------------------------------------------------- Invoice
    ws = wb.create_sheet("Invoice")
    for c, w in zip("ABCD", (17, 44, 13, 17)):
        ws.column_dimensions[c].width = w
    ws["A1"] = "=MyName"
    ws["A1"].font = Font(bold=True, size=14)
    ws["D1"] = "INVOICE"
    ws["D1"].font = Font(bold=True, size=20, color=ACCENT)
    ws["D1"].alignment = Alignment(horizontal="right")
    for r, ref in enumerate(("MyTitle", "MyAddress1", "MyAddress2", "MyPhone", "MyEmail", "MyWebsite"), 2):
        ws[f"A{r}"] = f'=IF({ref}="","",{ref})'
        ws[f"A{r}"].font = MUTED
    ws["D2"] = '="No. "&InvoiceNo'
    ws["D3"] = "=InvoiceDate"
    ws["D3"].number_format = "m/d/yyyy"
    for c in ("D2", "D3"):
        ws[c].alignment = Alignment(horizontal="right")
    ws["D2"].font = BOLD

    def section(r: int, text: str) -> None:
        ws[f"A{r}"] = text
        ws[f"A{r}"].font = Font(bold=True, size=9, color="6B6880")

    section(9, "BILL TO")
    for r, ref in enumerate(("BillName", "BillFirm", "BillAddress1", "BillAddress2", "BillEmail"), 10):
        ws[f"A{r}"] = f'=IF({ref}="","",{ref})'
    section(16, "TRANSCRIPT")
    for r, (label, ref) in enumerate((("Case", "CaseTitle"), ("Index No.", "IndexNo"), ("Court", "Court"),
                                      ("Part", "Part"), ("Judge", "Judge"), ("Date(s)", "CaseDates"),
                                      ("Pages", "Pages")), 17):
        ws[f"A{r}"] = label
        ws[f"A{r}"].font = MUTED
        ws[f"B{r}"] = f"={ref}"
        ws[f"B{r}"].alignment = Alignment(horizontal="left")
    section(25, '=IF(OfferChoice,"CHOOSE ONE","DELIVERY")')
    for c, text in zip("ABCD", ("Speed", "Turnaround", "Per page", "Amount due")):
        cell = ws[f"{c}26"]
        cell.value, cell.font, cell.fill = text, HEAD, HEAD_FILL
        if c in "CD":
            cell.alignment = Alignment(horizontal="right")
    # The rows list Regular / Expedite / Daily for a choice invoice, else only the job's speed;
    # the label always names the speed whose price is shown.
    speeds = ('IF(OfferChoice,"Regular",Speed)', 'IF(AND(OfferChoice,OfferExpedite),"Expedite","")',
              'IF(AND(OfferChoice,OfferDaily),"Daily","")')
    for r, f in enumerate(speeds, 27):
        ws[f"A{r}"] = f"={f}"
        ws[f"A{r}"].font = Font(bold=True, size=12)
        ws[f"B{r}"] = f'=IF(A{r}="","",VLOOKUP(A{r},Turnaround,2,FALSE))'
        ws[f"C{r}"] = f'=IF(A{r}="","",INDEX(CalcPerPage,MATCH(A{r},CalcSpeeds,0)))'
        ws[f"D{r}"] = f'=IF(A{r}="","",INDEX(CalcEach,MATCH(A{r},CalcSpeeds,0)))'
        ws[f"D{r}"].font = Font(bold=True, size=12)
        for c in "ABCD":
            ws[f"{c}{r}"].border = Border(bottom=THIN)
        for c in "CD":
            ws[f"{c}{r}"].number_format = MONEY
            ws[f"{c}{r}"].alignment = Alignment(horizontal="right")
    ws["A31"] = '=IF(OfferChoice,"Please choose one delivery option and pay the amount shown for it.","")'
    ws["A32"] = ('=IF(Parties>1,"Amounts are per party ("&Parties&" parties ordered). The transcript is delivered '
                 'once every party has paid.","The transcript is sent after payment is received.")')
    section(34, "PAYMENT")
    for r, ref in enumerate(("PayLine1", "PayLine2", "PayLine3", "PayLine4", "PayLine5"), 35):
        ws[f"A{r}"] = f'=IF({ref}="","",{ref})'
    ws.merge_cells("A41:D43")
    ws["A41"] = "=Footer"
    ws["A41"].font = Font(size=9, color="6B6880")
    ws["A41"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.print_area = "A1:D43"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.sheet_view.showGridLines = False

    # ---------------------------------------------------------------- Exhibit Log
    ws = wb.create_sheet("Exhibit Log")
    ws.append(["Case", "=CaseTitle", "", "Index No.", "=IndexNo", "", "Judge", "=Judge"])
    ws.append([])
    ws.append(["Plf Exhibit", "ID / Evd", "Description", "Deft Exhibit", "ID / Evd", "Description", "Date(s)",
               "Witness"])
    for c in ws[3]:
        c.font, c.fill = HEAD, HEAD_FILL
    letters = [chr(65 + i) for i in range(26)] + ["AA"]
    for i in range(60):
        ws.append([i + 1, "", "", letters[i] if i < len(letters) else "", "", "", "", ""])
    for c, w in zip("ABCDEFGH", (11, 10, 36, 12, 10, 36, 12, 26)):
        ws.column_dimensions[c].width = w

    # ---------------------------------------------------------------- Page Log
    ws = wb.create_sheet("Page Log")
    ws.append(["Date", "Weekday", "Reporter", "Pages written", "Starting page", "Ending page", "", "Total pages:",
               "=SUM(D2:D1000)"])
    for c in ws[1][:6]:
        c.font, c.fill = HEAD, HEAD_FILL
    ws["H1"].font = BOLD
    for r in range(2, 32):
        ws[f"B{r}"] = f'=IF(A{r}="","",TEXT(A{r},"dddd"))'
        ws[f"D{r}"] = f'=IF(OR(E{r}="",F{r}=""),"",F{r}-E{r}+1)'
        ws[f"A{r}"].number_format = "m/d/yyyy"
    for c, w in zip("ABCDEFGHI", (12, 12, 18, 14, 14, 14, 3, 13, 10)):
        ws.column_dimensions[c].width = w

    wb.active = wb.sheetnames.index("Job")
    return wb


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT)
    print(OUT)
