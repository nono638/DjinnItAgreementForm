"""Date helpers shared by the forms, invoices, records and window: M/D/YYYY text and weekday arithmetic."""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def us_date(d: date | str | None = None) -> str:
    """date(2026, 9, 30) or '2026-09-30...' -> '9/30/2026'; None = today. Unreadable text comes back unchanged."""
    if d is None:
        d = date.today()
    elif isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return f"{d.month}/{d.day}/{d.year}"


def next_weekday(d: date) -> date:
    """d itself, or the Monday after when d falls on a weekend."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def add_months(d: date, months: int) -> date:
    """Same day of the month `months` later; the 31st becomes the month's last day when it has fewer."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def quick_date(days: int = 0, months: int = 0, today: date | None = None) -> str:
    """Today plus days/months as M/D/YYYY; a day on the weekend becomes the Monday after."""
    d = today or date.today()
    if months:
        d = add_months(d, months)
    return us_date(next_weekday(d + timedelta(days=days)))
