"""The PDF toolkit every output writes with (pdfout.py), moved out of fill.py in Release A of MIGRATION.md: the old
imports from fill still give the same objects (never copies that could drift apart), and pdfout imports nothing
else of the package when it is loaded, so any output, and later a courthouse profile, can use it without an
import cycle."""
import subprocess
import sys

from minute_filler import fill, pdfout

MOVED = ("FIELD_FONT", "MAX_FS", "MIN_FS", "LOCK_BUTTON", "MARK", "OLD_MARKS", "text_width", "fit_size", "wrap",
         "wrap_fit", "form_text", "set_text", "set_check", "add_text_field", "has_fields", "lock_pdf",
         "safe_filename", "short_caption", "output_name", "mark", "save_output", "is_generated", "unique_path")


def test_fill_still_gives_the_toolkit():
    assert [n for n in MOVED if getattr(fill, n) is not getattr(pdfout, n)] == []


def test_pdfout_loads_alone():
    code = ("import sys, minute_filler.pdfout; "
            "print(sorted(m for m in sys.modules if m.startswith('minute_filler')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.strip() == "['minute_filler', 'minute_filler.pdfout']"
