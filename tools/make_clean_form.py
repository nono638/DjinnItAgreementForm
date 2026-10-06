"""Generates the clean, vector version of the UCS Court Reporter Minute Agreement Form.

Same wording and order as the 1999 scanned form, but with named AcroForm fields,
a three-line case name, checkboxes, and fields for the signature lines, date of
agreement, fax and email. Run this file directly to regenerate the PDF:

    python tools/make_clean_form.py
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "minute_filler" / "forms" / "minute_agreement_clean.pdf"

W, H = 612, 792
LEFT, RIGHT = 60, 552
FONT, BOLD, ITAL = "tiro", "tibo", "tiit"
SIZE = 11
INK = (0, 0, 0)
FIELD_H = 13


class Builder:
    """Draws the form on one letter page: text, rules and fillable fields."""

    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=W, height=H)

    def text(self, x, y, s, font=FONT, size=SIZE, align="left"):
        """Writes s on baseline y; x is its left, centre or right edge (align). Returns where the text ends."""
        if align != "left":
            w = pymupdf.get_text_length(s, fontname=font, fontsize=size)
            x = x - w / 2 if align == "center" else x - w
        self.page.insert_text((x, y), s, fontname=font, fontsize=size, color=INK)
        return x + pymupdf.get_text_length(s, fontname=font, fontsize=size)

    def line(self, x0, x1, y, width=0.6):
        """A rule just under baseline y, to write on."""
        self.page.draw_line((x0, y + 2), (x1, y + 2), color=INK, width=width)

    def field(self, name, x0, x1, y, underline=True):
        """Text field sitting on a baseline at y (the label baseline)."""
        if underline:
            self.line(x0, x1, y)
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = pymupdf.Rect(x0 + 1, y - FIELD_H + 3, x1 - 1, y + 2)
        w.text_font = "Helv"
        w.text_fontsize = 0
        w.border_width = 0
        w.fill_color = None
        self.page.add_widget(w)

    def labeled(self, x, y, label, name, x1, font=FONT):
        """A label, then the field `name` from after it to x1; returns where the field starts."""
        x0 = self.text(x, y, label, font=font) + 4
        self.field(name, x0, x1, y)
        return x0

    def checkbox(self, name, x, y, label):
        """A drawn box with a checkbox field on it and the label after it; returns where the label ends."""
        size = 9
        rect = pymupdf.Rect(x, y - size + 1, x + size, y + 1)
        self.page.draw_rect(rect, color=INK, width=0.7)
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = name
        w.rect = rect
        w.border_width = 0
        w.field_value = False
        self.page.add_widget(w)
        return self.text(x + size + 4, y, label)


def build(out: Path = OUT) -> Path:
    """Draws the whole form and saves it at out (the app's forms folder); returns out."""
    b = Builder()
    t = b.text

    t(RIGHT, 40, "(UCS-Revised 1999)", size=9, align="right")
    t(W / 2, 62, "COURT REPORTER MINUTE AGREEMENT FORM", font=BOLD, size=13, align="center")
    t(W / 2, 76, "(Private Party Transactions)", size=10, align="center")
    t(LEFT, 100, "Please Type or Print Clearly", font=ITAL, size=10)

    # 1. Court / county / part / judge
    y = 124
    t(LEFT, y, "1.")
    b.field("court", LEFT + 16, 290, y)
    t(293, y, "Court,")
    b.field("county", 328, 512, y)
    t(515, y, "County.")
    y = 146
    b.labeled(LEFT + 16, y, "Part No.", "part", 200)
    b.labeled(215, y, "Name of Judge/Justice", "judge", RIGHT)

    # 2. Case name, three lines
    y = 172
    x0 = b.labeled(LEFT, y, "2.  Name of Case", "case_name_1", RIGHT)
    b.field("case_name_2", x0, RIGHT, y + 16)
    b.field("case_name_3", x0, RIGHT, y + 32)

    # 3 / 4. Index and dates
    y = 232
    b.labeled(LEFT, y, "3.  Court Docket/File/Index Number", "index_no", 318)
    b.labeled(326, y, "4.  Date(s) of Minutes Requested", "dates", RIGHT)
    b.field("dates_2", 326, RIGHT, y + 16)

    # 5. Type of proceeding
    y = 272
    t(LEFT, y, "5.  Type of Proceeding (check one or more):")
    y = 292
    x = LEFT + 16
    for label in ("Arraignment", "Application", "Hearing", "Plea", "Trial", "Sentence"):
        x = b.checkbox(f"proc_{label.lower()}", x, y, label) + 16
    b.checkbox("proc_other_check", LEFT + 16, y + 20, "")
    b.labeled(LEFT + 29, y + 20, "Other (specify):", "proc_other", 360)

    # 6. Rate table (text from the UCS form)
    y = 342
    t(LEFT, y, "6.  Pursuant to Section 108 of the Rules of the Chief Administrative Judge, the rates per page for")
    t(LEFT + 16, y + 13, "transcripts of proceedings reported in New York State courts shall be as follows:")
    rows = [("Regular delivery:", "$3.30 - $4.30 (original)", "$1.00 (each copy)"),
            ("Expedited delivery:", "$4.40 - $5.40 (original)", "$1.10 (each copy)"),
            ("Daily delivery:", "$5.50 - $6.50 (original)", "$1.25 (each copy)")]
    y += 36
    for label, orig, copy in rows:
        t(170, y, label)
        t(330, y, orig)
        t(330, y + 12, copy)
        y += 30

    # 7. Rate charged
    y = 482
    b.labeled(LEFT, y, "7.  Rate to be Charged Per Page:  $", "rate", 290)
    y = 502
    x = LEFT + 16
    for label in ("Regular", "Expedited", "Daily"):
        x = b.checkbox(f"delivery_{label.lower()}", x, y, label) + 18
    x = b.checkbox("delivery_other_check", x, y, "Other") + 4
    b.field("delivery_other", x, 450, y)
    b.labeled(LEFT + 16, 522, "No. of Copies Ordered", "copies", 230)

    # 8 / 9
    y = 546
    b.labeled(LEFT, y, "8.  Estimated Number of Pages:", "est_pages", 290)
    b.labeled(305, y, "9.  Estimated Delivery Date:", "delivery_date", RIGHT)

    # 10. Signatures
    t(LEFT, 572, "10.  Agreed to:")
    y = 604
    b.field("sig_reporter", LEFT, 215, y)
    b.field("sig_attorney", 235, 405, y)
    b.field("agreement_date", 425, RIGHT, y)
    t((LEFT + 215) / 2, y + 14, "Court Reporter (signature)", size=10, align="center")
    t((235 + 405) / 2, y + 14, "Attorney/Party (signature)", size=10, align="center")
    t((425 + RIGHT) / 2, y + 14, "Date of Agreement", size=10, align="center")

    # Reporter / attorney blocks
    L, R, MID = LEFT, 316, 296
    y = 644
    b.labeled(L, y, "Name of Court Reporter", "rep_name", MID)
    b.labeled(R, y, "Name of Attorney/Party", "atty_name", RIGHT)
    y += 15
    b.labeled(L, y, "Address", "rep_address_1", MID)
    b.labeled(R, y, "Firm/Address", "atty_firm", RIGHT)
    y += 15
    b.field("rep_address_2", L, MID, y)
    b.field("atty_address_1", R, RIGHT, y)
    y += 15
    b.field("rep_address_3", L, MID, y)
    b.field("atty_address_2", R, RIGHT, y)
    y += 15
    b.labeled(L, y, "Telephone Number", "rep_phone", MID)
    b.labeled(R, y, "Telephone Number", "atty_phone", RIGHT)
    y += 15
    b.labeled(L, y, "Fax Number", "rep_fax", MID)
    b.labeled(R, y, "Fax Number", "atty_fax", RIGHT)
    y += 15
    b.labeled(L, y, "Email", "rep_email", MID)
    b.labeled(R, y, "Email", "atty_email", RIGHT)

    t(LEFT, 758, "A copy of this agreement must be filed by the court reporter with his/her supervisor as designated by the",
      size=10)
    t(LEFT, 770, "Administrative Judge within 7 calendar days following the date of agreement.", size=10)

    b.doc.set_metadata({"title": "Court Reporter Minute Agreement Form", "subject": "UCS (Revised 1999), clean fillable"})
    b.doc.save(out, garbage=3, deflate=True)
    return out


if __name__ == "__main__":
    print(build())
