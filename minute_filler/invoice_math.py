"""The math of the invoices just made, spelled out (Settings -> Options: "Show the math of each invoice"), or of
those about to be made (the main window's "Who pays what" opens it before there are numbers).

Most invoices show only each speed's amount; when someone later asks how an amount was reached, this is the
answer. explain() gives, for each invoice, each speed's charges line by line (pages x rate, times the copies),
their total and how it was split between the parties, as HTML for the window that shows it
(gui.preview.MathDialog) and as plain text for the clipboard. to_pdf() saves the same HTML as a PDF.
A firm's own share of pages ordered together is worked out exactly (invoice_calc.quote_shares) and spelled
out stretch by stretch: the pages it ordered alone at the full price, and the pages ordered with other firms
divided between them ("50 pages × $4.30 = $215.00 ÷ 2 firms = $107.50"). A line can come to part of a cent: it
is shown as it is ("$8.175") and the lines add up to "This firm's charges together", which is then rounded up
to the cent ("This firm pays"). When several firms are billed for the same work, a last section, "All the firms
together", adds up what they pay against their exact shares added up, which shows what rounding each one up to
the cent added. who_pays() is the short version the main window keeps up to date as things are ticked: a row
per firm, its pages, how it ordered them and what it pays at each speed; page_rates() goes under it.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
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


def money_exact(d: Decimal) -> str:
    """'$8.18' for whole cents; a firm's share of split pages can come to part of a cent, shown as it is
    ('$8.175', '$1.4333…'), so the lines add up to the total shown under them."""
    return fmt(d) if d == d.quantize(CENT) else "$" + _exact(d, 4, 2)


def _pages(n) -> str:
    """'1 page', '120 pages', '32.5 pages', '3.33… pages' (a third of 10)."""
    text = _exact(Decimal(n), 2) if isinstance(n, Decimal) else f"{n:,}"
    return f"{text} page" if n == 1 else f"{text} pages"


INDENT = "      "  # a line that spells out part of the line above it


def _firms(n: int) -> str:
    """'1 firm', '2 firms'."""
    return "1 firm" if n == 1 else f"{n} firms"


def _line(l: QuoteLine, q: Quote) -> list[str]:
    """'Copy: 2 × 120 pages × $1.00 = $240.00'. On a firm's own share, the pages split between the firms
    are spelled out: 'Original: 60 pages × $4.30 = $258.00 ÷ 2 firms = $129.00', or for pages ordered alone
    and with others, a line for each stretch ('40 pages ordered by this firm alone: ...', '50 pages ordered by
    2 firms: 50 × $4.30 = $215.00 ÷ 2 = $107.50') under the line's own total. A charge each firm pays in full
    says so when some of its pages were ordered with other firms."""
    n = f"{l.qty} × " if l.qty > 1 else ""
    if not q.share or not l.parts:
        return [f"{l.label}: {n}{_pages(l.pages or q.pages)} × {fmt(l.rate)} = {money_exact(l.amount)}"]
    pages = sum(p for p, _ in l.parts)
    if not l.shared:
        own = " (each firm pays for its own)" if any(k > 1 for _, k in l.parts) else ""
        return [f"{l.label}: {_pages(pages)} × {fmt(l.rate)} = {money_exact(l.amount)}{own}"]
    if len(l.parts) == 1:  # every page it ordered, ordered by the same firms
        p, k = l.parts[0]
        whole = l.rate * p
        return [f"{l.label}: {_pages(p)} × {fmt(l.rate)} = {money_exact(whole)} ÷ {_firms(k)} = "
                f"{money_exact(l.amount)}"]
    out = [f"{l.label} ({fmt(l.rate)} a page, divided between the firms that ordered each page): "
           f"{money_exact(l.amount)}"]
    for p, k in l.parts:
        whole = l.rate * p
        if k == 1:
            out.append(f"{INDENT}{_pages(p)} ordered by this firm alone: {p:,} × {fmt(l.rate)} = "
                       f"{money_exact(whole)}")
        else:
            out.append(f"{INDENT}{_pages(p)} ordered by {_firms(k)}: {p:,} × {fmt(l.rate)} = {money_exact(whole)} "
                       f"÷ {k} = {money_exact(whole / k)}")
    return out


def _sum(q: Quote) -> list[str]:
    """The total and what each party owes: 'Total: $319.30', '÷ 3 parties = $106.44 each (rounded up to the
    cent)' (that last only when it does not split evenly); a firm's own
    share: 'This firm pays: $219.25', after 'This firm's charges together: $219.245' when the lines come to part of
    a cent."""
    if q.share:
        out = []
        if q.total != q.per_party:  # (worked out exactly: $4.30 split three ways is $1.4333…, not whole cents)
            out.append(f"This firm's charges together: {money_exact(q.total)}")
        out.append(f"This firm pays: {fmt(q.per_party)}" + (" (rounded up to the cent)" if out else ""))
        return out
    out = [f"Total: {fmt(q.total)}"]
    if q.parties > 1:
        each = f"÷ {q.parties} parties = {fmt(q.per_party)} each"
        if q.per_party * q.parties != q.total:
            each += " (rounded up to the cent)"
        out.append(each)
    return out


def sections(made: list[tuple[FirmInvoice, str]]) -> list[tuple[str, list[tuple[str, list[str], list[str]]]]]:
    """For each invoice: its heading ('Invoice 2026-0001 · Bill to Dana Smith (Counsel & Counsel) · 120
    pages'; just 'Invoice' while it has no number yet), and for each speed (its name, the charge lines, the
    sum lines)."""
    out = []
    for f, number in made:
        who = ""
        if f.atty is not None:
            name, firm = (f.atty.name or "").strip(), (f.atty.firm or "").strip()
            who = name + (f" ({firm})" if firm and name else firm)
        head = (f"Invoice {number}" if number else "Invoice") + (f" · Bill to {who}" if who else "")
        head += f" · {_pages(f.opts.pages)}"
        if f.opts.excerpt:
            head += f" ({f.opts.excerpt})"
        if f.opts.parties > 1 and not (f.quotes and f.quotes[0].share):
            head += f", {f.opts.parties} parties"
        out.append((head, [(q.speed, [x for l in q.lines for x in _line(l, q)], _sum(q)) for q in f.quotes]))
    return out


def together(made: list[tuple[FirmInvoice, str]]) -> list[str]:
    """For the firms billed for the same work (the invoices of one set: one case, one reporter, see
    FirmInvoice.group), what they pay together at each speed against their exact shares added up: 'Regular:
    the 2 firms together pay $618.00 for work that costs $618.00' (+ what rounding each one up to the cent
    added; another reporter's set says whose: 'Regular (DS invoices): ...'). [] when no set has two firms."""
    groups: dict[int, list[FirmInvoice]] = {}
    for f, _ in made:
        if f.quotes and f.quotes[0].share:
            groups.setdefault(f.group, []).append(f)
    out = []
    for firms in groups.values():
        if len(firms) < 2:
            continue
        for i, q0 in enumerate(firms[0].quotes):
            quotes = [f.quotes[i] for f in firms if i < len(f.quotes) and f.quotes[i].speed == q0.speed]
            if len(quotes) != len(firms):
                continue
            paid = sum((q.per_party for q in quotes), Decimal("0.00"))
            # the exact shares added up first: a third of a cent three times is a whole cent, not 0.99…
            exact = sum((q.due for q in quotes), Fraction(0))
            cost = Decimal(exact.numerator) / Decimal(exact.denominator)
            whose = firms[0].opts.reporter  # (another reporter's invoices are added up apart from the user's)
            line = (f"{q0.speed}{f' ({whose.upper()} invoices)' if whose else ''}: the {len(firms)} firms "
                    f"together pay {fmt(paid)} for work that costs {money_exact(cost)}")
            extra = paid - cost
            if extra > 0:
                line += f" (rounding each one up to the cent adds {money_exact(extra)})"
            out.append(line)
    return out


def how_shared(parts: list[tuple[int, int]]) -> str:
    """How a firm ordered its pages, in a few words, from a quote line's parts ((pages, firms) each):
    [(70, 1), (50, 2)] -> '70 alone + 50 shared by 2'; [(30, 1)] -> 'alone'; [(30, 3)] -> 'shared by 3'."""
    by: dict[int, int] = {}
    for p, k in parts:
        by[max(1, k)] = by.get(max(1, k), 0) + p
    if len(by) == 1:
        k = next(iter(by))
        return "alone" if k == 1 else f"shared by {k}"
    return " + ".join(f"{p:,} alone" if k == 1 else f"{p:,} shared by {k}" for k, p in sorted(by.items()))


@dataclass
class PayRow:
    """One firm's row of the window's "Who pays what": its name, the pages billed to it, how it ordered them
    ("70 alone + 50 shared by 2") and, for each speed the invoice offers, what it pays and what that comes to
    a page, everything on its invoice counted (the original, its copy, the e-mailed copy, the index)."""
    name: str
    pages: int
    how: str
    prices: list[tuple[str, Decimal, Decimal]]  # (speed, what it pays, a page)


def who_pays(firms: list[FirmInvoice]) -> list[PayRow]:
    """The rows of "Who pays what" for the invoices about to be made (invoice.firm_invoices): a row per
    invoice, in order, "(no attorney)" for a blank Bill To. Nothing is worked out again: what each pays is the
    invoice's own amount (per_party). Invoices made the old way (no shares) say "alone" or "split 3 ways"."""
    out = []
    for f in firms:
        parts = next((l.parts for q in f.quotes[:1] for l in q.lines if l.parts), [])
        n = f.opts.parties
        how = how_shared(parts) if parts else "alone" if n <= 1 else f"split {n} ways"
        name = f.atty.label() if f.atty is not None else "(no attorney)"
        out.append(PayRow(name, f.opts.pages, how, [(q.speed, q.per_party, q.per_page) for q in f.quotes]))
    return out


def page_rates(firms: list[FirmInvoice]) -> str:
    """What a page of the original costs a firm, at each speed, alone and shared, for the ways these firms
    ordered their pages: 'A page of the original: Regular $4.30 alone, $2.15 shared by 2 · Expedite $5.40
    alone, $2.70 shared by 2'. "" when there is nothing to price."""
    ns: set[int] = set()
    for f in firms:
        parts = next((l.parts for q in f.quotes[:1] for l in q.lines if l.label == "Original"), [])
        ns |= {max(1, k) for _, k in parts} or {max(1, f.opts.parties)}
    if not firms or not firms[0].quotes or not ns:
        return ""
    bits = []
    for q in firms[0].quotes:
        rate = next((l.rate for l in q.lines if l.label == "Original"), None)
        if rate is None:
            continue
        each = [f"{money_exact(rate / k)} {'alone' if k == 1 else f'shared by {k}'}" for k in sorted(ns)]
        bits.append(f"{q.speed} {', '.join(each)}")
    return "A page of the original: " + " · ".join(bits) if bits else ""


def explain(made: list[tuple[FirmInvoice, str]]) -> tuple[str, str]:
    """(html, plain text) of the math of each invoice: (FirmInvoice, its number) pairs, the number "" for one
    not made yet."""
    parts, plain = [], []
    for head, speeds in sections(made):
        parts.append(f"<h2>{html.escape(head)}</h2>")
        plain.append(head)
        for speed, lines, sums in speeds:
            parts.append(f"<h3>{html.escape(speed)}</h3>")
            parts += [f"<p style='margin-left: 22px'>{html.escape(x.strip())}</p>" if x.startswith(INDENT)
                      else f"<p>{html.escape(x)}</p>" for x in lines]
            parts += [f"<p class='sum'>{html.escape(x)}</p>" for x in sums]
            plain.append(f"  {speed}")
            plain += [f"    {x}" for x in lines + sums]
        plain.append("")
    check = together(made)
    if check:
        parts.append("<h2>All the firms together</h2>")
        parts += [f"<p>{html.escape(x)}</p>" for x in check]
        plain += ["All the firms together"] + [f"  {x}" for x in check]
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
