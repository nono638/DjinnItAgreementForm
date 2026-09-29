"""Prepares the bundled UCS form (minute_agreement_ucs.pdf) from the court's fillable PDF.

Source: "Private_Minute_Agreement_Form1999.pdf", the fillable Word/Acrobat version of the UCS
Court Reporter Minute Agreement Form (page 1 = form, page 2 = instructions).

The copy shipped with the app is the same form with:
- the orange Adobe "SIGN" tags above the signature lines removed,
- document metadata (author, producer, dates, XMP) cleared.

    python -m minute_filler.forms.prepare_ucs_form "path\\to\\Private_Minute_Agreement_Form1999.pdf"
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

OUT = Path(__file__).with_name("minute_agreement_ucs.pdf")


def prepare(src: Path, out: Path = OUT) -> Path:
    doc = pymupdf.open(src)
    page = doc[0]
    # The orange "SIGN" tags are the appearance of two Adobe digital-signature fields.
    # The app prints "per email" / the reporter's name at those spots itself (see ucs_map.OVERLAYS),
    # so the signature fields are removed.
    for w in list(page.widgets()):
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
            page.delete_widget(w)

    # Clear metadata everywhere it hides: document info, XMP streams, Word's private PieceInfo,
    # and the Word document-info record referenced from the structure tree
    doc.set_metadata({k: "" for k in ("title", "author", "subject", "keywords", "creator", "producer",
                                      "creationDate", "modDate", "trapped")})
    doc.del_xml_metadata()
    for xref in range(1, doc.xref_length()):
        for key in ("Metadata", "PieceInfo", "Info"):
            try:
                if doc.xref_get_key(xref, key)[0] != "null":
                    doc.xref_set_key(xref, key, "null")
            except Exception:
                pass
    doc.set_metadata({"title": "Court Reporter Minute Agreement Form (Private Party Transactions)"})
    doc.save(out, garbage=4, deflate=True)
    return out


if __name__ == "__main__":
    print(prepare(Path(sys.argv[1])))
