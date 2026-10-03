"""Zoom: everything in the windows bigger or smaller (Ctrl + / Ctrl − / Ctrl 0, or Ctrl and the mouse
wheel), on top of the Windows display scaling.

Qt already follows the Windows scale setting (125 %, 150 %...) by itself; this is for a user who wants the
text bigger or smaller than that. Qt can only change its own scale factor before the app starts, so the zoom
scales what the app sets itself: the style sheet's font sizes, paddings and corner radii (theme.apply_theme),
and the fixed sizes of widgets. A widget size goes through sized() (or z() when it is worked out each time),
so that a new zoom can set it again (reapply)."""
from __future__ import annotations

import re
import weakref

MIN, MAX, STEP = 0.7, 1.6, 0.1
_zoom = 1.0
_sized: list[tuple[weakref.ref, str, tuple, int]] = []  # (widget, method, sizes at 100 %, arguments not scaled)


def zoom() -> float:
    """The zoom now (1.0 = 100 %)."""
    return _zoom


def clamp(f: float) -> float:
    """f within MIN..MAX, to two decimals (1.2999 -> 1.3); a value that isn't a number is 1.0."""
    try:
        f = float(f)
    except (TypeError, ValueError):
        return 1.0
    if f != f:  # NaN
        return 1.0
    return round(min(MAX, max(MIN, f)), 2)


def set_zoom(f: float) -> float:
    """Sets the zoom (clamped) and returns it. The style sheet and the widget sizes follow once
    theme.apply_theme and reapply() are called (MainWindow.set_zoom does both)."""
    global _zoom
    _zoom = clamp(f)
    return _zoom


def z(px: float) -> int:
    """A size in pixels at 100 % as it is at the current zoom (at least 1 when px is)."""
    return max(1, round(px * _zoom)) if px else 0


def sized(widget, method: str, *px, keep: int = 0):
    """Calls widget.<method>(*px) with the sizes zoomed, and remembers it so reapply() can do it again at
    another zoom. keep: how many leading arguments are not sizes (setColumnWidth(column, width): keep=1).
    Returns the widget."""
    args = tuple(px[:keep]) + tuple(z(p) for p in px[keep:])
    getattr(widget, method)(*args)
    _sized.append((weakref.ref(widget), method, tuple(px), keep))
    return widget


def reapply() -> None:
    """Sets every size given to sized() again at the current zoom (widgets deleted since are forgotten)."""
    alive = []
    for ref, method, px, keep in _sized:
        w = ref()
        if w is None:
            continue
        try:
            getattr(w, method)(*(tuple(px[:keep]) + tuple(z(p) for p in px[keep:])))
        except RuntimeError:  # the Qt object is gone while Python still holds it
            continue
        alive.append((ref, method, px, keep))
    _sized[:] = alive


_SIZE = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)(pt|px)\b")


def scale_qss(qss: str, f: float) -> str:
    """The style sheet with every "10pt" and "6px" multiplied by f (a 1px line stays 1px: borders and rules
    should stay thin). Colors such as #1d4ed8 are not sizes and are left alone."""
    if f == 1:
        return qss

    def one(m: re.Match) -> str:
        n, unit = float(m.group(1)), m.group(2)
        if unit == "px" and n <= 1:
            return m.group(0)
        v = n * f
        if unit == "px":
            return f"{max(1, round(v))}px"
        return f"{round(v, 1):g}pt"

    return _SIZE.sub(one, qss)
