"""Invoices for transcripts: a one-page PDF per ordering attorney.

It lists the price of each speed ticked in Settings.invoice_speeds (a "choice" invoice, as on the reporter's
spreadsheet). With one speed ticked it bills that speed alone; with none, the job's own speed. An invoice can
cover several days of one case (a joint invoice, see batch.joint_invoice): it is then priced on the pages of
every day.

By default only each speed's turnaround and amount are shown. "Show granular detail" adds what the job's
Customize... choice (else Settings.invoice_detail_items) lists: the page count, the pages of each day, the
price per page, the charges in each amount and the split between parties (settings.DETAIL_ITEMS).

The page is laid out as HTML and drawn with PyMuPDF (Story); no other library is needed. Its values (number,
Bill To, case details, amounts) are text fields, so they can be corrected in a PDF viewer, and the PDF gets
the "Lock fields" button unless it is flattened (see fill.save_output). Every invoice is numbered and entered
in the records (records.Ledger).
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pymupdf

from .dates import us_date
from .fill import add_text_field, output_name, save_output
from .invoice_calc import Quote, fmt, quotes_for
from .models import Attorney, CaseInfo
from .records import NUMBER_LOCK, Invoice, Ledger
from .settings import Settings

PAGE = pymupdf.paper_rect("letter")
TEMPLATE = Path(__file__).resolve().with_name("templates") / "Invoice Template.xlsx"  # for manual use
MARGIN = 48
INK = (0.114, 0.106, 0.149)  # #1d1b26, the colour of the page's text

CSS = """
* { font-family: sans-serif; color: #1d1b26; }
body { font-size: 10pt; line-height: 1.35; }
.top { width: 100%; }
.me b { font-size: 13pt; }
.title { font-size: 24pt; font-weight: bold; color: #4b3f8f; text-align: right; letter-spacing: 2px; }
.meta { text-align: right; }
table.meta { width: 100%; }
.lbl { color: #6b6880; text-align: right; }
.muted { color: #6b6880; }
h3 { font-size: 9pt; color: #6b6880; margin: 14px 0 3px 0; letter-spacing: 1px; }
table.case td { padding: 1px 10px 1px 0; vertical-align: top; }
table.case td.day { padding-left: 12px; }
table.opts { width: 100%; border-collapse: collapse; margin-top: 4px; }
table.opts th { white-space: nowrap; background-color: #4b3f8f; color: white; text-align: left; padding: 5px 6px; font-size: 9pt; }
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
    """Choices made for one job's invoices, or for the days of a case on a joint invoice (the Invoice panel of
    the Outputs box, and its Extras... and Customize... windows). None = as Settings say."""
    pages: int                   # the pages billed (all the days together)
    parties: int = 1             # ordering parties: each pays an equal share
    detail: bool | None = None   # show granular detail (Settings.invoice_detail)
    days: list[tuple[str, int]] = field(default_factory=list)  # (date, pages) of each day; empty = one day
    email: bool | None = None    # an e-mailed copy for each party (Settings.invoice_include_email)
    index: str | None = None     # "auto", "on" or "off" (see invoice_calc.index_days)
    show: list[str] | None = None  # what granular detail shows: keys of settings.DETAIL_ITEMS

    def day_pages(self) -> list[int]:
        """The pages of each day: [120, 80]; one day of `pages` when the days aren't known."""
        return [p for _, p in self.days] or [self.pages]


@dataclass
class Field:
    """A value on the invoice that stays editable: drawn as a PDF text field over the place it takes."""
    name: str                    # the field's name in the PDF (no dots: they would nest fields)
    value: str
    fs: float = 10               # font size before the page is shrunk to fit
    multiline: bool = False
    right: bool = False


def _e(s: str) -> str:
    """Text made safe for the invoice's HTML, its line breaks kept."""
    return html.escape(s or "").replace("\n", "<br>")


def _field_name(name: str, taken: set[str]) -> str:
    """A PDF field name made from a date or speed: without dots (a dot would make it part of another field:
    "Sept. 3" or "Exp.") and not one already taken, as two fields of one name share their value
    ("pages" twice -> "pages 2")."""
    base = " ".join(name.replace(".", " ").split()) or "field"
    out, n = base, 2
    while out in taken:
        out, n = f"{base} {n}", n + 1
    return out


def _detail(q: Quote) -> str:
    """'Original: 30 pp. × $4.30 + Copy: 2 × 30 pp. × $1.00 = $189.00, split 2 ways' (the arithmetic,
    small print)."""
    bits = []
    for l in q.lines:
        n = f"{l.qty} × " if l.qty > 1 else ""
        bits.append(f"{l.label}: {n}{l.pages or q.pages} pp. × {fmt(l.rate)}")
    out = " + ".join(bits) + f" = {fmt(q.total)}"
    if q.parties > 1:
        out += f", split {q.parties} ways"
    return out


def invoice_layout(case: CaseInfo, atty: Attorney | None, s: Settings, quotes: list[Quote], number: str,
                   when: date | None = None, opts: InvoiceOpts | None = None) -> tuple[str, dict[str, Field]]:
    """The invoice page as HTML (see CSS), and its editable values by the id of the element holding each.
    atty: who is billed (None: a blank Bill To); quotes: a row per speed offered; number: the invoice number;
    when: the invoice's date (today); opts: the job's choices. Without granular detail, as on most reporters'
    invoices, only each speed's turnaround and amount are shown; with it, what opts.show (else
    Settings.invoice_detail_items) lists: the page count, the pages of each day, the price per page, the
    charges in each amount and the split between parties."""
    when = when or date.today()
    opts = opts or InvoiceOpts(quotes[0].pages if quotes else 0)
    detail = s.invoice_detail if opts.detail is None else opts.detail
    shown = set(s.invoice_detail_items if opts.show is None else opts.show) if detail else set()
    days = opts.days if len(opts.days) > 1 else []
    p = s.profile
    me = "<br>".join(_e(x) for x in (p.title, p.address1, p.address2, p.phone and f"Tel. {p.phone}", p.email,
                                     p.website) if x)
    g = case.get
    fields: dict[str, Field] = {}

    def value(fid: str, field: Field, tag: str = "td", attrs: str = "", lines: int = 1) -> str:
        """The element holding one editable value. It keeps room for `lines` lines, so a blank can be
        typed into later."""
        fields[fid] = replace(field, name=_field_name(field.name, {f.name for f in fields.values()}))
        shown = _e(field.value) or "&nbsp;"
        shown += "<br>&nbsp;" * (lines - 1 - field.value.count("\n"))
        return f"<{tag} id='{fid}'{attrs}>{shown}</{tag}>"

    bill = []
    if atty is not None:
        bill = [x.strip() for x in (atty.name, atty.firm, atty.address, atty.email) if x and x.strip()]
    case_rows = [("case", "Case", g("case_name")), ("index_no", "Index No.", g("index_no")),
                 ("court", "Court", ", ".join(x for x in (g("court") and f"{g('court')} Court", g("county") and
                                                         f"{g('county')} County", g("part") and f"Part {g('part')}")
                                              if x)),
                 ("judge", "Judge", g("judge")), ("dates", "Date(s)", g("dates"))]
    if "pages" in shown:
        case_rows.append(("pages", "Pages", str(quotes[0].pages) if quotes else ""))
    case_html = "".join(
        f"<tr><td class='muted'>{label}</td>{value('f_' + key, Field(key, v or '', multiline=key == 'case'))}</tr>"
        for key, label, v in case_rows)
    if days and "days" in shown:  # the pages of each day, under the case: "6/3/2026   30"
        case_html += "<tr><td class='muted'>Pages by day</td><td>&nbsp;</td></tr>" + "".join(
            f"<tr><td class='muted day'>{_e(day)}</td>{value(f'f_day{i}', Field(f'pages {day}', str(n)))}</tr>"
            for i, (day, n) in enumerate(days))
    parties = quotes[0].parties if quotes else 1
    rows = []
    for i, q in enumerate(quotes):
        turn = _e(s.turnaround(q.speed))
        if "charges" in shown:
            turn += f"<br><span class='detail'>{_e(_detail(q))}</span>"
        cells = [f"<td><span class='big'>{_e(q.speed)}</span></td>", f"<td>{turn}</td>"]
        if "per_page" in shown:
            cells.append(value(f"f_rate{i}", Field(f"per page {q.speed}", fmt(q.per_page), right=True),
                               attrs=" class='amt'"))
        cells.append(value(f"f_amount{i}", Field(f"amount {q.speed}", fmt(q.per_party), fs=12, right=True), attrs=" class='amt big'"))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = "Delivery" if len(quotes) == 1 else "Choose one"
    notes = []
    if len(quotes) > 1:
        notes.append("Please choose one delivery option and pay the amount shown for it.")
    the = "The transcripts are" if days else "The transcript is"
    if "split" in shown and parties > 1:
        notes.append(f"Amounts are per party ({parties} parties ordered). {the} delivered once every party "
                     "has paid.")
    else:
        notes.append(f"{the} sent after payment is received.")
    rate_head = '<th class="amt" width="11%">Per page</th>' if "per_page" in shown else ""
    number_cell = value("f_number", Field("invoice_no", number, right=True), attrs=' width="45%"')
    date_cell = value("f_date", Field("date", us_date(when), right=True))
    meta = (f"<table class='meta'><tr><td class='lbl'>No.</td>{number_cell}</tr>"
            f"<tr><td class='lbl'>Date</td>{date_cell}</tr></table>")
    html_text = f"""
<table class="top"><tr>
<td class="me"><b>{_e(p.name) or "Court Reporter"}</b><br><span class="muted">{me}</span></td>
<td class="meta"><div class="title">INVOICE</div>{meta}</td></tr></table>
<h3>BILL TO</h3>
{value("f_bill", Field("bill_to", chr(10).join(bill), multiline=True), tag="div", lines=3)}
<h3>TRANSCRIPT</h3>
<table class="case">{case_html}</table>
<h3>{head.upper()}</h3>
<table class="opts"><tr><th width="16%">Speed</th><th>Turnaround</th>{rate_head}<th class="amt" width="18%">Amount due</th></tr>
{"".join(rows)}</table>
<div class="note">{"<br>".join(_e(n) for n in notes)}</div>
<h3>PAYMENT</h3>
<div class="pay">{_e(s.invoice_payment_text)}</div>
<div class="footer">{_e(s.invoice_footer)}</div>
"""
    return html_text, fields


def render(case: CaseInfo, atty: Attorney | None, s: Settings, quotes: list[Quote], number: str, out: Path,
           opts: InvoiceOpts | None = None) -> Path:
    """Draws the invoice on one letter page and saves it at out (or "out (2)" when taken); returns the path.
    The page is laid out like Page.insert_htmlbox does it (shrunk to fit when it runs long), then each
    value (number, Bill To, case details, amounts) is turned into a text field at the same place, so it can
    be corrected in a PDF viewer. With Settings.flatten the fields are flattened again."""
    html_text, fields = invoice_layout(case, atty, s, quotes, number, opts=opts)
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE.width, height=PAGE.height)
    box = pymupdf.Rect(MARGIN, MARGIN, PAGE.width - MARGIN, PAGE.height - MARGIN)
    story = pymupdf.Story(html=html_text, user_css="body {margin:1px;}" + CSS)
    fit = story.fit_scale(pymupdf.Rect(0, 0, box.width, box.height), scale_min=1,
                          flags=pymupdf.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW)
    scale = 1 / fit.parameter  # < 1 when the page had to be shrunk to fit
    where: dict[str, pymupdf.Rect] = {}

    def seen(pos) -> None:
        """Notes where each value's element was placed, in page coordinates."""
        if pos.id in fields and pos.open_close & 1 and pos.id not in where:
            r = pymupdf.Rect(pos.rect)
            where[pos.id] = pymupdf.Rect(box.x0 + r.x0 * scale, box.y0 + r.y0 * scale,
                                         box.x0 + r.x1 * scale, box.y0 + r.y1 * scale)

    laid_out = story.write_with_links(lambda *_: (fit.rect, fit.rect, None), positionfn=seen)
    page.show_pdf_page(box, laid_out, 0)  # same shape as fit.rect, so it is only scaled
    for r in where.values():  # the drawn values make way for the fields
        page.add_redact_annot(r)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for fid, f in fields.items():
        if fid in where:
            add_text_field(page, f.name, where[fid], f.value, f.fs * scale, color=INK,
                           multiline=f.multiline, right=f.right)  # no bold: fields can't show it
    doc.set_metadata({**doc.metadata, "title": f"Invoice {number}", "author": s.profile.name})
    return save_output(doc, "invoice", out, s.flatten)


def job_quotes(case: CaseInfo, s: Settings, opts: InvoiceOpts) -> list[Quote]:
    """The prices an invoice for this job lists: one per speed of Settings.invoice_speeds on the rate sheet,
    or the job's own speed (else the default one) when none of them is. ValueError without a page count."""
    if opts.pages <= 0:
        raise ValueError("an invoice needs the transcript's page count")
    return quotes_for(opts.day_pages(), opts.parties, s.sheet(), s, case.get("delivery") or s.default_delivery,
                      opts.email, opts.index)


def make_invoice(case: CaseInfo, atty: Attorney | None, s: Settings, out_dir: Path, opts: InvoiceOpts,
                 ledger: Ledger, dated: bool = False, quotes: list[Quote] | None = None) -> tuple[Path, str]:
    """Numbers, draws and records one invoice; returns (file, invoice number). quotes: the job's prices,
    when already worked out for its other invoices."""
    quotes = quotes or job_quotes(case, s, opts)
    with NUMBER_LOCK:  # no other thread may take the same number before this invoice is in the records
        number, year, seq = ledger.next_invoice_no(s.invoice_number_format)
        name = output_name(case, atty, s, dated, pattern=s.invoice_filename_pattern,
                           fallback=f"Invoice {number}", number=number)
        out = render(case, atty, s, quotes, number, Path(out_dir) / name, opts)
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
