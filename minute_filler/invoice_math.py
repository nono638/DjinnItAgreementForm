"""The math of the invoices just made, spelled out ("Show the math").

Most invoices show only each speed's amount; when someone later asks how an amount was reached, this is the
answer. explain() gives, for each invoice, each speed's charges line by line (pages x rate, times the copies),
their total and how it was split between the parties, as HTML for the window that shows it
(gui.preview.MathDialog) and as plain text for the clipboard. to_pdf() saves the same HTML as a PDF.
A firm's own share of pages ordered together is worked out exactly (invoice_calc.quote_shares), so a line can
come to part of a cent: it is shown as it is ("1.5 pages × $5.45 = $8.175") and the lines add up to the
"Together" amount, which is then rounded up to the cent.
"""
from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from .invoice import FirmInvoice
from .invoice_calc import Quote, QuoteLine, fmt

CSS = """
body {font-family: sans-serif; font-size: 10pt; color: #222;}
h2 {font-size: 13pt; margin: 14px 0 2px 0;}
h3 {font-size: 11pt; margin: 10px 0 2px 0;}
p {margin: 2px 0;}
.muted {color: #666;}
.sum {font-weight: bold;}
"""


CENT = Decimal("0.01")


def _exact(d: Decimal, places: int, least: int = 0) -> str:
    """A number as worked out, not rounded away: at least `least` decimals, at most `places`, then "…" when
    there is more (8.175 -> '8.175'; a third of 4.30 -> '1.4333…'; 1000 with least=2 -> '1,000.00')."""
    shown = d.quantize(Decimal(1).scaleb(-places))
    more = "…" if shown != d else ""
    text = f"{shown.normalize():,f}"
    whole, _, frac = text.partition(".")
    frac = frac.ljust(least, "0")
    return (f"{whole}.{frac}" if frac else whole) + more


def _money(d: Decimal) -> str:
    """'$8.18' for whole cents; a firm's share of split pages can come to part of a cent, shown as it is
    ('$8.175', '$1.4333…'), so the lines add up to the total shown under them."""
    return fmt(d) if d == d.quantize(CENT) else "$" + _exact(d, 4, 2)


def _pages(n) -> str:
    """'1 page', '120 pages', '32.5 pages', '3.33… pages' (a third of 10)."""
    text = _exact(Decimal(n), 2) if isinstance(n, Decimal) else f"{n:,}"
    return f"{text} page" if n == 1 else f"{text} pages"


def _line(l: QuoteLine, q: Quote) -> str:
    """'Copy: 2 × 120 pages × $1.00 = $240.00' (a firm's shared pages: the note that they are split)."""
    n = f"{l.qty} × " if l.qty > 1 else ""
    split = " (pages ordered together are split between the firms)" if l.shared else ""
    pages = l.pages or q.pages
    if q.share and l.rate and l.qty == 1:  # the pages exactly (QuoteLine.pages is rounded to 2 places)
        pages = l.amount / l.rate
    return f"{l.label}: {n}{_pages(pages)} × {fmt(l.rate)}{split} = {_money(l.amount)}"


def _sum(q: Quote) -> list[str]:
    """The total and what each party owes: '÷ 2 parties = $378.00 each (rounded up to the cent)'; a firm's own
    share: 'Your share: $219.25', after 'Together: $219.245' when the lines come to part of a cent."""
    if q.share:
        out = []
        if q.total != q.per_party:  # (worked out exactly: a page split three ways is a third of a cent)
            out.append(f"Together: {_money(q.total)}")
        out.append(f"Your share: {fmt(q.per_party)}" + (" (rounded up to the cent)" if out else ""))
        return out
    out = [f"Total: {fmt(q.total)}"]
    if q.parties > 1:
        each = f"÷ {q.parties} parties = {fmt(q.per_party)} each"
        if q.per_party * q.parties != q.total:
            each += " (rounded up to the cent)"
        out.append(each)
    return out


def sections(made: list[tuple[FirmInvoice, str]]) -> list[tuple[str, list[tuple[str, list[str], list[str]]]]]:
    """For each invoice: its heading, and for each speed (its name, the charge lines, the sum lines)."""
    out = []
    for f, number in made:
        who = ""
        if f.atty is not None:
            name, firm = (f.atty.name or "").strip(), (f.atty.firm or "").strip()
            who = name + (f" ({firm})" if firm and name else firm)
        head = f"Invoice {number}" + (f" · Bill to {who}" if who else "")
        head += f" · {_pages(f.opts.pages)}"
        if f.opts.parties > 1 and not (f.quotes and f.quotes[0].share):
            head += f", {f.opts.parties} parties"
        out.append((head, [(q.speed, [_line(l, q) for l in q.lines], _sum(q)) for q in f.quotes]))
    return out


def explain(made: list[tuple[FirmInvoice, str]]) -> tuple[str, str]:
    """(html, plain text) of the math of each invoice made: (FirmInvoice, its number) pairs."""
    parts, plain = [], []
    for head, speeds in sections(made):
        parts.append(f"<h2>{html.escape(head)}</h2>")
        plain.append(head)
        for speed, lines, sums in speeds:
            parts.append(f"<h3>{html.escape(speed)}</h3>")
            parts += [f"<p>{html.escape(x)}</p>" for x in lines]
            parts += [f"<p class='sum'>{html.escape(x)}</p>" for x in sums]
            plain.append(f"  {speed}")
            plain += [f"    {x}" for x in lines + sums]
        plain.append("")
    return "\n".join(parts), "\n".join(plain).rstrip() + "\n"


def to_pdf(html_text: str, out: Path) -> Path:
    """Saves the math as a PDF of letter pages (as many as it takes) at out, replacing a file there (the Save
    box asked); returns the path."""
    import io

    import pymupdf

    from .fill import mark
    page = pymupdf.paper_rect("letter")
    box = page + (54, 54, -54, -54)  # 3/4 inch margins
    story = pymupdf.Story(html=f"<h1 style='font-size:15pt'>The math</h1>{html_text}", user_css=CSS)
    data = io.BytesIO()
    writer = pymupdf.DocumentWriter(data)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(box)
        story.draw(dev)
        writer.end_page()
    writer.close()
    with pymupdf.open("pdf", data.getvalue()) as doc:
        mark(doc, "math")
        doc.set_metadata({**doc.metadata, "title": "The math"})
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        doc.save(out, garbage=3, deflate=True)
    return Path(out)
