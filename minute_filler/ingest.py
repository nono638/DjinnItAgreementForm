"""Turns any dropped input into plain text (plus images for the AI model).

- PDF with a text layer: text of the first pages (separated by form feeds, "\\f"), page count,
  first page number, what each page of a transcript says about itself, and its own pages without the word
  index, read four ways (takes.read_pages, takes.count_pages).
- Scanned PDF / photo: Windows' built-in OCR engine (fast, offline). Line
  positions are used to split the page into blocks, so appearance columns stay
  together.
- E-mail: pasted text (the usual way) or a saved .eml; also .txt, .htm(l) and .docx.
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .log import describe, log
from .takes import PageCount, count_pages, counted_marks, index_heading, read_pages

try:  # iPhone photos (.heic/.heif): pillow-heif teaches Pillow to open them (in requirements and the installer)
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_OK = True
except Exception as e:  # (a source install without it): such a photo then gets a message saying so
    HEIF_OK = False
    log.warning("HEIC photos can't be read: %s", describe(e))

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif"}
HEIF_EXT = {".heic", ".heif"}
PDF_TEXT_PAGES = 3  # a transcript's title and appearances are on its first pages; the rest is dialogue


@dataclass
class Ingested:
    """One input as text, ready for the extractors."""
    name: str                      # file name, or "Pasted text" / "Pasted image"
    kind: str                      # pdf / image / email / text
    text: str = ""
    images: list[bytes] = field(default_factory=list)   # JPEG bytes for the AI model
    page_count: int = 0
    first_page_no: int | None = None  # the number printed on a transcript's first page ("358")
    ocr_used: bool = False
    warnings: list[str] = field(default_factory=list)
    # PDF: one takes.PageMark per transcript page (its number, the reporter's initials...); [] otherwise
    marks: list = field(default_factory=list)
    count: PageCount | None = None  # PDF: its own pages, read four ways and reconciled (takes.count_pages)
    index_head: str = ""            # PDF: the first lines of the word index after the transcript (may name the case)


# --------------------------------------------------------------------- OCR

def _winrt_ocr(png: bytes):
    """Runs Windows OCR over PNG bytes and returns its OcrResult. Raises when no OCR language is installed."""
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    async def run():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("No Windows OCR language pack is installed")
        return await engine.recognize_async(bitmap)

    return asyncio.run(run())


def ocr_image(img: Image.Image) -> str:
    """OCR a PIL image; returns text with blank lines between visual blocks."""
    from winrt.windows.media.ocr import OcrEngine

    img = img.convert("RGB")
    img.thumbnail((OcrEngine.max_image_dimension,) * 2)  # Windows OCR refuses larger images
    buf = io.BytesIO()
    img.save(buf, "PNG")
    result = _winrt_ocr(buf.getvalue())

    lines = []
    for ln in result.lines:
        rects = [w.bounding_rect for w in ln.words]
        if not rects:
            continue
        x = min(r.x for r in rects)
        y = min(r.y for r in rects)
        h = max(r.y + r.height for r in rects) - y
        lines.append((x, y, h, ln.text))
    if not lines:
        return ""
    heights = sorted(l[2] for l in lines)
    typical = heights[len(heights) // 2] or 10

    # A new block starts after a gap of more than a line, when the text jumps back up the page (the
    # next column), or when it starts far to the left or right of the line before.
    out, prev = [], None
    for x, y, h, text in lines:
        if prev is not None:
            px, py, ph = prev
            gap = y - (py + ph)
            if gap > typical * 1.1 or y < py - typical or abs(x - px) > typical * 4:
                out.append("")
        out.append(text)
        prev = (x, y, h)
    return "\n".join(out)


def ocr_available() -> bool:
    """True when Windows OCR works here (the winrt packages load and a language pack is installed)."""
    try:
        from winrt.windows.media.ocr import OcrEngine
        return OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        return False


def _jpeg(img: Image.Image, max_side: int = 1400) -> bytes:
    """The image as JPEG bytes, at most `max_side` pixels each way (enough for the AI model to read)."""
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


# ------------------------------------------------------------------ inputs

def ingest_image(path: Path) -> Ingested:
    """A photo or scan file, turned upright (phone photos store their rotation separately) and read."""
    with Image.open(path) as img:  # closed again, so the file can be moved or deleted while the app is open
        return ingest_pil(ImageOps.exif_transpose(img), path.name)


def ingest_pil(img: Image.Image, name: str = "Pasted image") -> Ingested:
    """An image (pasted or from a file): its OCR text, and the picture itself for the AI model. Without
    OCR the text stays blank and a warning says why."""
    ing = Ingested(name, "image", images=[_jpeg(img)])
    try:
        ing.text = ocr_image(img)
        ing.ocr_used = True
    except Exception as e:  # pragma: no cover - depends on Windows install
        ing.warnings.append(f"Windows OCR failed ({e}); only the AI model can read this image.")
    return ing


def ingest_pdf(path: Path) -> Ingested:
    """A PDF file: see _read_pdf."""
    with pymupdf.open(path) as doc:  # closed again, so the file can be moved or deleted while the app is open
        return _read_pdf(doc, path.name)


def _read_pdf(doc: pymupdf.Document, name: str) -> Ingested:
    """The text of the first PDF_TEXT_PAGES pages (OCR for scanned ones, whose pictures also go to the
    AI model), the page count, the number printed on the first page, and the page marks."""
    ing = Ingested(name, "pdf", page_count=doc.page_count)
    parts = []
    for i in range(min(PDF_TEXT_PAGES, doc.page_count)):
        page = doc[i]
        text = page.get_text()
        if len(text.strip()) < 40:  # scanned page
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            ing.images.append(_jpeg(img))
            try:
                text = ocr_image(img)
                ing.ocr_used = True
            except Exception as e:  # pragma: no cover
                ing.warnings.append(f"OCR failed on page {i + 1}: {e}")
        parts.append(text)
    ing.text = "\n\f\n".join(parts)
    # Transcript pages carry their own page number at the top (e.g. "   358").
    m = re.match(r"\s*(\d{1,5})\s*\n", parts[0] if parts else "")
    if m:
        ing.first_page_no = int(m.group(1))
    try:  # every page: who wrote it, and where the transcript ends (the word index after it isn't counted)
        facts = read_pages(doc)
        ing.count = count_pages(facts, doc.page_count)
        ing.marks = counted_marks(facts, ing.count)
        ing.index_head = index_heading(facts, ing.count)
        if ing.count.warning or ing.count.note:
            # (numbers only: no case, no file). A PDF without line numbers is seldom a transcript (a scanned
            # page sheet, an invoice): its count is only used if it turns out to be one, so no warning
            loud = log.warning if "lines" in ing.count.readings else log.info
            loud("page count: %s", ing.count.warning or ing.count.note)
    except Exception as e:  # the run sheet then has one row for the whole transcript
        log.warning("could not read the pages' initials: %s", describe(e))
    return ing


def _html_to_text(html: str) -> str:
    """Rough plain text of an HTML page or e-mail: line breaks kept, tags and scripts dropped."""
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    import html as h
    return h.unescape(text)


def ingest_eml(path: Path) -> Ingested:
    """A saved e-mail: its From/To/Subject/Date lines, then the body (plain text if it has one)."""
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    header = "\n".join(f"{k}: {msg[k]}" for k in ("From", "To", "Subject", "Date") if msg[k])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = _html_to_text(text)
    return Ingested(path.name, "email", text=f"{header}\n\n{text}")


def ingest_docx(path: Path) -> Ingested:
    """A Word file's text, read straight from its XML: one line per paragraph."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return Ingested(path.name, "text", text=re.sub(r"<[^>]+>", "", xml))


def decode_text(raw: bytes) -> str:
    """Text files as Notepad and Outlook save them: UTF-8, UTF-16 ("Unicode") or the Windows code page."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "ignore")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "ignore")
    if raw[1:2] == b"\x00" and raw[3:4] == b"\x00":  # UTF-16 without a byte order mark
        return raw.decode("utf-16-le", "ignore")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def ingest_file(path: str | Path) -> Ingested:
    """Reads any input file by its extension. A file that can't be read raises ValueError with a
    message for the user (or FileNotFoundError / PermissionError as they are)."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        return _ingest_file(path, ext)
    except Exception as e:
        log.warning("could not read a %s file: %s", ext or "text", describe(e))
        if type(e) is ValueError or isinstance(e, (FileNotFoundError, PermissionError)):
            raise  # already says what is wrong
        # PyMuPDF, Pillow and zipfile report damaged files in their own words
        if path.stat().st_size == 0:
            raise ValueError("the file is empty") from e
        why = str(e).replace(repr(str(path)), path.name).replace(str(path), path.name)
        raise ValueError(f"this doesn't look like a valid {ext or 'text'} file ({why[:120]})") from e


def _ingest_file(path: Path, ext: str) -> Ingested:
    """ingest_file without the error wording: any other extension is read as text."""
    if ext == ".pdf":
        ing = ingest_pdf(path)
    elif ext in HEIF_EXT and not HEIF_OK:  # else Pillow says the photo isn't valid, which isn't the problem
        raise ValueError("this computer's copy of the app can't read HEIC photos (the pillow-heif library is "
                         "missing). Open the photo, save it as JPG and drop that instead.")
    elif ext in IMAGE_EXT:
        ing = ingest_image(path)
    elif ext == ".eml":
        ing = ingest_eml(path)
    elif ext == ".msg":
        raise ValueError("Outlook .msg files aren't supported. Open the e-mail, copy its text "
                         "(Ctrl+A, Ctrl+C) and paste it into the text box instead.")
    elif ext == ".docx":
        ing = ingest_docx(path)
    else:
        text = decode_text(path.read_bytes())
        if ext in (".htm", ".html"):
            text = _html_to_text(text)
        ing = Ingested(path.name, "text", text=text)
    return ing


def ingest_text(text: str, name: str = "Pasted text") -> Ingested:
    """Pasted text; it counts as an e-mail when a line starts with From:, To:, Sent: or Subject:."""
    kind = "email" if re.search(r"(?im)^(from|subject|sent|to):", text) else "text"
    return Ingested(name, kind, text=text)
