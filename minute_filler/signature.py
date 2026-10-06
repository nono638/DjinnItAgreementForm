"""The reporter's signature as an image: prepared once, then placed on the signature line of every form.

A scan or photo of a signature has a paper background that would cover the form's
signature line, so the paper is made transparent and the margins are cut off. The
result is kept with the settings (%APPDATA%\\YinItAgreementForm\\signature.png).
"""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .settings import settings_dir

MAX_WIDTH = 1200
HEIGHT_ABOVE_LINE, DEPTH_BELOW_LINE = 28, 3  # points; the space the forms leave over the line


def signature_path() -> Path:
    """Where the prepared signature is kept: signature.png next to settings.json."""
    return settings_dir() / "signature.png"


def prepare(src: str | Path, dst: str | Path) -> Path:
    """Writes `src` (photo, scan, PNG...) to `dst` as a cropped PNG with a transparent background, at most
    MAX_WIDTH pixels wide, and returns `dst`. Raises ValueError when the picture has no ink on it."""
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened).convert("RGBA")
    paper = Image.new("RGBA", img.size, "white")
    paper.alpha_composite(img)
    grey = ImageOps.autocontrast(paper.convert("L"), cutoff=1)  # grey paper in a photo becomes white
    # ink is opaque, paper is transparent, with a soft edge in between
    alpha = grey.point(lambda v: 0 if v > 200 else 255 if v < 110 else int((200 - v) * 255 / 90))
    box = alpha.point(lambda v: 255 if v > 60 else 0).getbbox()
    if box is None:
        raise ValueError("No signature was found in that image - it looks blank.")
    pad = 6
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    out = paper.convert("RGB")
    out.putalpha(alpha)
    out = out.crop(box)
    if out.width > MAX_WIDTH:
        out = out.resize((MAX_WIDTH, max(1, round(out.height * MAX_WIDTH / out.width))), Image.LANCZOS)
    dst = Path(dst)
    out.save(dst, "PNG")
    return dst


def place(page: pymupdf.Page, line: pymupdf.Rect, image: str | Path) -> None:
    """Draws the signature over the signature line `line`, as large as the space above it allows."""
    box = pymupdf.Rect(line.x0 + 2, line.y1 - HEIGHT_ABOVE_LINE, line.x1 - 2, line.y1 + DEPTH_BELOW_LINE)
    page.insert_image(box, filename=str(image), keep_proportion=True)
