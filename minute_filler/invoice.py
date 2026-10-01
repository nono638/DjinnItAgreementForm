"""Invoices for transcripts: a one-page PDF per ordering attorney, listing the price of each speed offered
(a "choice" invoice, as on the reporter's spreadsheet) or of the job's speed alone.

The page is laid out as HTML and drawn with PyMuPDF (Page.insert_htmlbox); no other library is needed.
Every invoice is numbered and entered in the records (records.Ledger).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from .dates import us_date
from .fill import output_name, save_output
from .invoice_calc import Quote, fmt, quotes_for
from .models import Attorney, CaseInfo
from .records import Invoice, Ledger
from .settings import Settings

PAGE = pymupdf.paper_rect("letter")
TEMPLATE = Path(__file__).resolve().with_name("templates") / "Invoice Template.xlsx"  # for manual use
MARGIN = 48

CSS = """
* { font-family: sans-serif; color: #1d1b26; }
body { font-size: 10pt; line-height: 1.35; }
.top { width: 100%; }
.me b { font-size: 13pt; }
.title { font-size: 24pt; font-weight: bold; color: #4b3f8f; text-align: right; letter-spacing: 2px; }
.meta { text-align: right; }
.muted { color: #6b6880; }
h3 { font-size: 9pt; color: #6b6880; margin: 14px 0 3px 0; letter-spacing: 1px; }
table.case td { padding: 1px 10px 1px 0; vertical-align: top; }
table.opts { width: 100%; border-collapse: collapse; margin-top: 4px; }
table.opts th { background-color: #4b3f8f; color: white; text-align: left; padding: 5px 6px; font-size: 9pt; }
table.opts td { border-bottom: 1px solid #d9d6e4; padding: 6px 6px; vertical-align: top; }
table.opts td.amt, table.opts th.amt { text-align: right; }
.big { font-size: 12pt; font-weight: bold; }
.detail { font-size: 8pt; color: #6b6880; }
.note { margin-top: 8px; }
.pay { white-space: pre-wrap; }
.footer { margin-top: 16px; font-size: 8.5pt; color: #6b6880; }
"""


@dataclass
class InvoiceOpts:
    """Choices made for one job's invoices (the footer controls)."""
    pages: int
    parties: int = 1
    choice: bool | None = None   # None = Settings.invoice_choice


def _e(s: str) -> str:
    return html.escape(s or "").replace("\n", "<br>")


def _detail(q: Quote) -> str:
    """'Original 30 pp. x $4.30 + 2 copies x $1.00 ...' (the arithmetic, small print)."""
    bits = []
    for l in q.lines:
        n = f"{l.qty} × " if l.qty > 1 else ""
        bits.append(f"{l.label}: {n}{q.pages} pp. × {fmt(l.rate)}")
    out = " + ".join(bits) + f" = {fmt(q.total)}"
    if q.parties > 1:
        out += f", split {q.parties} ways"
    return out


def invoice_html(case: CaseInfo, atty: Attorney | None, s: Settings, quotes: list[Quote], number: str,
                 when: date | None = None) -> str:
    """The invoice page as HTML (see CSS)."""
    when = when or date.today()
    p = s.profile
    me = "<br>".join(_e(x) for x in (p.title, p.address1, p.address2, p.phone and f"Tel. {p.phone}", p.email,
                                     p.website) if x)
    g = case.get
    bill = []
    if atty is not None:
        bill = [x for x in (atty.name, atty.firm, atty.address, atty.email) if x and x.strip()]
    case_rows = [("Case", g("case_name")), ("Index No.", g("index_no")),
                 ("Court", ", ".join(x for x in (g("court") and f"{g('court')} Court", g("county") and
                                                 f"{g('county')} County", g("part") and f"Part {g('part')}") if x)),
                 ("Judge", g("judge")), ("Date(s)", g("dates")), ("Pages", str(quotes[0].pages) if quotes else "")]
    parties = quotes[0].parties if quotes else 1
    opts = "".join(
        f"<tr><td><span class='big'>{_e(q.speed)}</span></td><td>{_e(s.turnaround(q.speed))}<br>"
        f"<span class='detail'>{_e(_detail(q))}</span></td>"
        f"<td class='amt'>{fmt(q.per_page)}</td><td class='amt'><span class='big'>{fmt(q.per_party)}</span></td></tr>"
        for q in quotes)
    head = "Delivery" if len(quotes) == 1 else "Choose one"
    notes = []
    if len(quotes) > 1:
        notes.append("Please choose one delivery option and pay the amount shown for it.")
    if parties > 1:
        notes.append(f"Amounts are per party ({parties} parties ordered). The transcript is delivered once "
                     "every party has paid.")
    else:
        notes.append("The transcript is sent after payment is received.")
    return f"""
<table class="top"><tr>
<td class="me"><b>{_e(p.name) or "Court Reporter"}</b><br><span class="muted">{me}</span></td>
<td class="meta"><div class="title">INVOICE</div>
<b>No. {_e(number)}</b><br>{us_date(when)}</td></tr></table>
<h3>BILL TO</h3>
<div>{"<br>".join(_e(x) for x in bill) or "&nbsp;"}</div>
<h3>TRANSCRIPT</h3>
<table class="case">{"".join(f"<tr><td class='muted'>{k}</td><td>{_e(v)}</td></tr>" for k, v in case_rows if v)}</table>
<h3>{head.upper()}</h3>
<table class="opts"><tr><th width="16%">Speed</th><th>Turnaround</th><th class="amt" width="11%">Per page</th><th class="amt" width="16%">Amount due</th></tr>
{opts}</table>
<div class="note">{"<br>".join(_e(n) for n in notes)}</div>
<h3>PAYMENT</h3>
<div class="pay">{_e(s.invoice_payment_text)}</div>
<div class="footer">{_e(s.invoice_footer)}</div>
"""


def render(case: CaseInfo, atty: Attorney | None, s: Settings, quotes: list[Quote], number: str, out: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE.width, height=PAGE.height)
    box = pymupdf.Rect(MARGIN, MARGIN, PAGE.width - MARGIN, PAGE.height - MARGIN)
    page.insert_htmlbox(box, invoice_html(case, atty, s, quotes, number), css=CSS, scale_low=0)  # shrinks to fit
    doc.set_metadata({**doc.metadata, "title": f"Invoice {number}", "author": s.profile.name})
    return save_output(doc, "invoice", out)


def job_quotes(case: CaseInfo, s: Settings, opts: InvoiceOpts) -> list[Quote]:
    """The prices an invoice for this job lists."""
    if opts.pages <= 0:
        raise ValueError("an invoice needs the transcript's page count")
    return quotes_for(opts.pages, opts.parties, s.sheet(), s, case.get("delivery") or s.default_delivery,
                      opts.choice)


def make_invoice(case: CaseInfo, atty: Attorney | None, s: Settings, out_dir: Path, opts: InvoiceOpts,
                 ledger: Ledger, dated: bool = False, quotes: list[Quote] | None = None) -> tuple[Path, str]:
    """Numbers, draws and records one invoice; returns (file, invoice number)."""
    quotes = quotes or job_quotes(case, s, opts)
    number, year, seq = ledger.next_invoice_no(s.invoice_number_format)
    name = output_name(case, atty, s, dated, pattern=s.invoice_filename_pattern, fallback=f"Invoice {number}",
                       number=number)
    out = render(case, atty, s, quotes, number, Path(out_dir) / name)
    ledger.add_invoice(Invoice(
        invoice_no=number, created=date.today().isoformat(), case_name=case.get("case_name"),
        index_no=case.get("index_no"), dates=case.get("dates"), judge=case.get("judge"),
        bill_to=atty.name if atty else "", firm=atty.firm if atty else "", email=atty.email if atty else "",
        pages=opts.pages, parties=opts.parties,
        amounts={q.speed: str(q.per_party) for q in quotes}, billed_speed=quotes[0].speed if quotes else "",
        file_path=str(out)), year, seq)
    return out, number


def make_invoices(case: CaseInfo, s: Settings, out_dir: Path, opts: InvoiceOpts, ledger: Ledger | None = None,
                  dated: bool = False) -> list[Path]:
    """One invoice per ticked attorney (or one with a blank Bill To); each is numbered and recorded."""
    ledger = ledger or Ledger()
    quotes = job_quotes(case, s, opts)
    return [make_invoice(case, a, s, out_dir, opts, ledger, dated, quotes)[0] for a in case.orderers()]
