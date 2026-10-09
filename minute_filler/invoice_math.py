"""The math of the invoices just made, spelled out (Settings -> Options: "Show the math of each invoice"), or of
those about to be made (the main window's "Who pays what" and the Invoice panel open it before there are numbers).

Most invoices show only each speed's amount; when someone later asks how an amount was reached, this is the
answer. explain() lays it out as tables (HTML, for the window that shows it, gui.preview.MathView, and for
to_pdf) in one of two layouts (settings.MATH_LAYOUTS): "invoice", a table for each invoice and speed with a row
for each charge (its pages, the rate, what it costs, how it is paid, what this firm pays), or "firms", a table
for each set of firms billed for the same work and each speed, with a column per firm. It also gives the same as
plain text, a line per charge, for the clipboard and an e-mail. The tables come from rows() and _foot() of each
quote, the plain text from _line() and _sum(): the same charges worded twice, so a change of wording goes in both.

How a charge is paid is said in three plain phrases: "Each firm splits this cost" (the original and the judge's
index of pages ordered together, divided between the firms that ordered each page), "Each firm pays for its
own" (its copy, its e-mailed copy and, as Settings.invoice_index_shared says by default, its index) and "This
firm alone" (pages no other firm ordered). A firm's
own share is worked out exactly (invoice_calc.quote_shares) and spelled out stretch by stretch: the pages it
ordered alone at the full price, and the pages ordered with other firms divided between them ("50 pages ×
$4.30 = $215.00 ÷ 2 firms = $107.50"). A line can come to part of a cent: it is shown as it is ("$8.175") and
the lines add up to "This firm's charges together", which is then rounded up to the cent ("This firm pays").
Under each invoice's heading (about): the pages the user wrote of the whole transcript ("You wrote 45 of 83
total pages.") and whether there is an index and why (FirmInvoice.index_note). When several firms are billed for
the same work, a last section, "All the firms together", adds up what they pay against their exact shares added
up, which shows what rounding each one up to the cent added. who_pays() is the short version the main window
keeps up to date as things are ticked: a row per firm, its pages, how it ordered them and what it pays at each
speed; page_rates() goes under it.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .invoice import FirmInvoice
from .invoice_calc import Quote, QuoteLine, fmt

# the PDF's look (to_pdf); the window's is gui.preview.MATH_CSS
CSS = """
body {font-family: sans-serif; font-size: 10pt; color: #222;}
h2 {font-size: 13pt; margin: 14px 0 2px 0;}
h3 {font-size: 11pt; margin: 10px 0 4px 0;}
p {margin: 2px 0;}
.muted {color: #666;}
.sum {font-weight: bold;}
table.math {border-collapse: collapse; font-size: 9pt; margin: 2px 0 6px 0;}
table.math th, table.math td {border: 1px solid #b9c0cc; padding: 3px 5px; vertical-align: top;}
table.math th {background-color: #eef0f4;}
"""

SPLITS = "Each firm splits this cost"
OWN = "Each firm pays for its own"
ALONE = "This firm alone"

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


def _num(n) -> str:
    """A number of pages: '120', '1,200', '32.5', '3.33…' (a third of 10)."""
    return _exact(Decimal(n), 2) if isinstance(n, Decimal) else f"{n:,}"


def _pages(n) -> str:
    """'1 page', '120 pages', '32.5 pages', '3.33… pages' (a third of 10)."""
    return f"{_num(n)} page" if n == 1 else f"{_num(n)} pages"


INDENT = "      "  # a line that spells out part of the line above it


def _firms(n: int) -> str:
    """'1 firm', '2 firms'."""
    return "1 firm" if n == 1 else f"{n} firms"


# ------------------------------------------------------------------ the rows of the tables

@dataclass
class Row:
    """One row of the math's tables: a charge of a quote or, `sub`, a stretch of its pages under it (those a firm
    ordered alone, those it ordered with 2 firms...). pages: as shown ("60", "2 × 60" for the copies of a whole
    invoice); cost: the charge before anyone splits it; how: how it is paid (SPLITS, OWN, ALONE, with "(÷ 2)");
    pays: what this firm, or each party of a whole invoice, pays for it (exact: maybe part of a cent); short: that
    in a few words, for the firms' layout ("59 pp. ÷ 4", "70 alone + 50 ÷ 2"); split: its cost is divided."""
    label: str
    pages: str
    rate: Decimal
    cost: Decimal
    how: str
    pays: Decimal
    short: str = ""
    split: bool = False
    sub: bool = False


def rows(q: Quote) -> list[Row]:
    """The rows of one speed's table. On a whole invoice (not a firm's share) each party pays an equal part of
    every charge: the copies (and, with Settings' index for each party, the index) one each (OWN), the rest split
    between the parties. On a firm's share, the lines as
    quote_shares made them: a line split between firms shows each stretch under it when the firm ordered some
    pages alone and some with others."""
    out = []
    n = max(1, q.parties)
    for l in q.lines:
        if not q.share:
            pages = l.pages or q.pages
            shown = f"{l.qty} × {_num(pages)}" if l.qty > 1 else _num(pages)
            split = l.qty == 1 and n > 1
            how = OWN if l.qty > 1 else f"{SPLITS} (÷ {n})" if split else ALONE
            out.append(Row(l.label, shown, l.rate, l.amount, how, l.amount / n,
                           f"{_num(pages)} pp." + (f" ÷ {n}" if split else ""), split))
            continue
        parts = l.parts or [(l.pages, 1)]
        pages = sum(p for p, _ in parts)
        whole = l.rate * pages  # these pages' charge, before the firms that ordered them split it
        if not l.shared:
            how = OWN if any(k > 1 for _, k in parts) else ALONE
            out.append(Row(l.label, _num(pages), l.rate, l.amount, how, l.amount, f"{_num(pages)} pp."))
            continue
        if len(parts) == 1:  # every page it ordered, ordered by the same firms
            p, k = parts[0]
            out.append(Row(l.label, _num(p), l.rate, whole, f"{SPLITS} (÷ {k})", l.amount, f"{_num(p)} pp. ÷ {k}",
                           True))
            continue
        short = " + ".join(f"{_num(p)} alone" if k == 1 else f"{_num(p)} ÷ {k}" for p, k in parts)
        out.append(Row(l.label, _num(pages), l.rate, whole, f"{SPLITS} (the pages ordered together)", l.amount,
                       short, True))
        for p, k in parts:
            label = f"{_pages(p)} ordered by this firm alone" if k == 1 else f"{_pages(p)} ordered by {_firms(k)}"
            out.append(Row(label, _num(p), l.rate, l.rate * p, ALONE if k == 1 else f"{SPLITS} (÷ {k})",
                           l.rate * p / k, sub=True))
    return out


def _foot(q: Quote) -> list[tuple[str, str]]:
    """The last rows of a speed's table, (what, amount): a firm's share 'This firm pays' (after 'This firm's
    charges together' when the lines come to part of a cent, and then '(rounded up to the cent)'); a whole
    invoice its total and what each party pays."""
    if q.share:
        out = []
        if q.total != q.per_party:
            out.append(("This firm's charges together", money_exact(q.total)))
        out.append(("This firm pays" + (" (rounded up to the cent)" if out else ""), fmt(q.per_party)))
        return out
    if q.parties <= 1:
        return [("Total", fmt(q.total))]
    up = " (rounded up to the cent)" if q.per_party * q.parties != q.total else ""
    return [(f"Total, the {q.parties} parties together", fmt(q.total)),
            (f"Each party pays ({fmt(q.total)} ÷ {q.parties}){up}", fmt(q.per_party))]


# ------------------------------------------------------------------ the plain text

def _line(l: QuoteLine, q: Quote) -> list[str]:
    """'Copy: 2 × 120 pages × $1.00 = $240.00'. On a firm's own share, the pages split between the firms
    are spelled out: 'Original: 60 pages × $4.30 = $258.00 ÷ 2 firms = $129.00 (each firm splits this cost)', or
    for pages ordered alone and with others, a line for each stretch ('40 pages ordered by this firm alone: ...',
    '50 pages ordered by 2 firms: 50 × $4.30 = $215.00 ÷ 2 = $107.50') under the line's own total. A charge each
    firm pays in full says so when some of its pages were ordered with other firms."""
    n = f"{l.qty} × " if l.qty > 1 else ""
    if not q.share or not l.parts:
        return [f"{l.label}: {n}{_pages(l.pages or q.pages)} × {fmt(l.rate)} = {money_exact(l.amount)}"]
    pages = sum(p for p, _ in l.parts)
    if not l.shared:
        own = f" ({OWN.lower()})" if any(k > 1 for _, k in l.parts) else ""
        return [f"{l.label}: {_pages(pages)} × {fmt(l.rate)} = {money_exact(l.amount)}{own}"]
    if len(l.parts) == 1:  # every page it ordered, ordered by the same firms
        p, k = l.parts[0]
        whole = l.rate * p
        return [f"{l.label}: {_pages(p)} × {fmt(l.rate)} = {money_exact(whole)} ÷ {_firms(k)} = "
                f"{money_exact(l.amount)} ({SPLITS.lower()})"]
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


def _who(f: FirmInvoice) -> str:
    """Who an invoice bills: 'Dana Smith (Smith Law)', 'Smith Law'; '' for a blank Bill To."""
    if f.atty is None:
        return ""
    name, firm = (f.atty.name or "").strip(), (f.atty.firm or "").strip()
    return name + (f" ({firm})" if firm and name else firm)


def heading(f: FirmInvoice, number: str) -> str:
    """'Invoice 2026-0001 · Bill to Dana Smith (Counsel & Counsel) · 120 pages' (just 'Invoice' while it has no
    number yet), with the excerpt it ordered and, on a whole invoice, the parties."""
    who = _who(f)
    head = (f"Invoice {number}" if number else "Invoice") + (f" · Bill to {who}" if who else "")
    head += f" · {_pages(f.opts.pages)}"
    if f.opts.excerpt:
        head += f" ({f.opts.excerpt})"
    if f.opts.parties > 1 and not (f.quotes and f.quotes[0].share):
        head += f", {f.opts.parties} parties"
    return head


def about(f: FirmInvoice) -> str:
    """What the heading doesn't say: the pages the user wrote of the whole transcript, when other reporters
    wrote some ("You wrote 45 of 83 total pages.", as the Invoice panel's Billed line says it; "DS wrote ..." on
    an invoice in another reporter's name; "The Pages field: 40 of 83 total pages." when that number was typed,
    as it need not be the pages the user wrote), and whether there is an index and why ("Index: 83 total pages,
    50 or more."). "" when there is neither."""
    o = f.opts
    mine, whole = o.my_pages or o.pages, o.total_pages
    bits = []
    if whole > mine:
        who = "The Pages field:" if o.typed else f"{o.reporter.upper()} wrote" if o.reporter else "You wrote"
        bits.append(f"{who} {mine:,} of {whole:,} total pages.")
    if f.index_note:
        bits.append(f.index_note)
    return " ".join(bits)


def _sections(made: list[tuple[FirmInvoice, str]]) -> list[tuple[str, str, list[tuple[str, list[str], list[str]]]]]:
    """For each invoice: its heading, the line under it (about) and, for each speed, (its name, the charge lines,
    the sum lines), as the plain text says them."""
    return [(heading(f, number), about(f), [(q.speed, [x for l in q.lines for x in _line(l, q)], _sum(q))
                                            for q in f.quotes]) for f, number in made]


def _plain(made: list[tuple[FirmInvoice, str]]) -> list[str]:
    """The plain text of the invoices' math: the heading, the line under it, and each speed's lines."""
    plain = []
    for head, note, speeds in _sections(made):
        plain.append(head)
        if note:
            plain.append(note)
        for speed, lines, sums in speeds:
            plain.append(f"  {speed}")
            plain += [f"    {x}" for x in lines + sums]
        plain.append("")
    return plain


# ------------------------------------------------------------------ the tables

_TABLE = ("<table class='math' border='1' cellspacing='0' cellpadding='4' "
          "style='border-collapse: collapse; border-color: #8a8f99; border-style: solid;'>")


def _e(text: str) -> str:
    return html.escape(text)


def _cells(cells: list[tuple[str, str]], tag: str = "td") -> str:
    """A table row of (text, align) cells, the text already escaped."""
    return "<tr>" + "".join(f"<{tag} align='{a}'>{t}</{tag}>" for t, a in cells) + "</tr>"


def _speed_table(q: Quote) -> str:
    """One speed of one invoice: a row per charge (and its stretches, indented), then what this firm pays."""
    heads = ["Charge", "Pages", "Rate", "Cost", "How it's paid", "This firm pays" if q.share else "Each party pays"]
    out = [_TABLE, _cells([(h, "left" if i in (0, 4) else "right") for i, h in enumerate(heads)], "th")]
    for r in rows(q):
        label = ("&nbsp;" * 6 + _e(r.label)) if r.sub else _e(r.label)
        out.append(_cells([(label, "left"), (_e(r.pages), "right"), (fmt(r.rate), "right"),
                           (money_exact(r.cost), "right"), (_e(r.how), "left"), (money_exact(r.pays), "right")]))
    for what, amount in _foot(q):
        out.append(f"<tr><td colspan='5' align='right'><b>{_e(what)}</b></td>"
                   f"<td align='right'><b>{amount}</b></td></tr>")
    out.append("</table>")
    return "".join(out)


def _by_invoice(made: list[tuple[FirmInvoice, str]]) -> list[str]:
    """The "invoice" layout: for each invoice its heading, the line under it and a table for each speed."""
    parts = []
    for f, number in made:
        parts.append(f"<h2>{_e(heading(f, number))}</h2>")
        note = about(f)
        if note:
            parts.append(f"<p class='muted'>{_e(note)}</p>")
        for q in f.quotes:
            parts.append(f"<h3>{_e(q.speed)}</h3>")
            parts.append(_speed_table(q))
    return parts


def _by_firms(made: list[tuple[FirmInvoice, str]]) -> list[str]:
    """The "firms" layout: for each set of invoices billed for the same work (FirmInvoice.group), a table for each
    speed with a column per firm: what it pays for each charge and how many pages that is, and what it pays in
    all. A charge a firm has none of shows "—"."""
    groups: dict[int, list[tuple[FirmInvoice, str]]] = {}
    for f, number in made:
        groups.setdefault(f.group, []).append((f, number))
    parts = []
    for firms in groups.values():
        whose = firms[0][0].opts.reporter
        count = f"{len(firms)} invoices side by side" if len(firms) > 1 else "The invoice"
        if whose:
            count += f" in {whose.upper()}'s name"
        parts.append(f"<h2>{_e(count)}</h2>")
        notes = [(f, about(f)) for f, _ in firms if about(f)]
        if len({n for _, n in notes}) == 1:
            parts.append(f"<p class='muted'>{_e(notes[0][1])}</p>")
        else:
            parts += [f"<p class='muted'>{_e(_who(f) or '(no attorney)')}: {_e(n)}</p>" for f, n in notes]
        for q0 in firms[0][0].quotes:
            quotes = [next((q for q in f.quotes if q.speed == q0.speed), None) for f, _ in firms]
            parts.append(f"<h3>{_e(q0.speed)}</h3>")
            parts.append(_firms_table(firms, quotes))
    return parts


def _firms_table(firms: list[tuple[FirmInvoice, str]], quotes: list[Quote | None]) -> str:
    """One speed of a set of invoices, a column per firm (see _by_firms)."""
    tables = [{r.label: r for r in rows(q) if not r.sub} if q else {} for q in quotes]
    labels = list(dict.fromkeys(label for t in tables for label in t))
    heads = [("Charge", "left"), ("Rate", "right"), ("How it's paid", "left")]
    for f, number in firms:
        bits = ([number] if number else []) + [_pages(f.opts.pages)]
        heads.append((f"{_e(_who(f) or '(no attorney)')}<br><span class='muted'>{_e(' · '.join(bits))}</span>",
                      "right"))
    out = [_TABLE, _cells(heads, "th")]
    for label in labels:
        got = [t.get(label) for t in tables]
        first = next(r for r in got if r is not None)
        how = SPLITS if any(r and r.split for r in got) else OWN if any(r and r.how == OWN for r in got) else ALONE
        cells = [(_e(label), "left"), (fmt(first.rate), "right"), (_e(how), "left")]
        cells += [(f"{money_exact(r.pays)}<br><span class='muted'>{_e(r.short)}</span>" if r else "—", "right")
                  for r in got]
        out.append(_cells(cells))
    share = any(q is not None and q.share for q in quotes)
    parts = any(q is not None and q.share and q.total != q.per_party for q in quotes)  # (part of a cent)
    if parts:
        out.append(_cells([("<b>Charges together</b>", "left"), ("", "right"), ("", "left")]
                          + [(money_exact(q.total) if q else "—", "right") for q in quotes]))
    uneven = any(q is not None and not q.share and q.per_party * q.parties != q.total for q in quotes)
    pays = ("Each firm pays" if share else "Each party pays") + (" (rounded up to the cent)" if parts or uneven else "")
    out.append(_cells([(f"<b>{_e(pays)}</b>", "left"), ("", "right"), ("", "left")]
                      + [(f"<b>{fmt(q.per_party)}</b>" if q else "—", "right") for q in quotes]))
    out.append("</table>")
    return "".join(out)


# ------------------------------------------------------------------ several firms together

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


def explain(made: list[tuple[FirmInvoice, str]], layout: str = "invoice") -> tuple[str, str]:
    """(html, plain text) of the math of each invoice: (FirmInvoice, its number) pairs, the number "" for one
    not made yet. layout: "invoice" (a table for each invoice and speed) or "firms" (a table for each set of
    firms and speed, a column per firm); the plain text is the same for both."""
    parts = _by_firms(made) if layout == "firms" else _by_invoice(made)
    plain = _plain(made)
    check = together(made)
    if check:
        parts.append("<h2>All the firms together</h2>")
        parts += [f"<p>{_e(x)}</p>" for x in check]
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
