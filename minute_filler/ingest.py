"""Turns any dropped input into plain text (plus images for the AI model).

- PDF with a text layer: text of the first pages, page count and first page number.
- Scanned PDF / photo: Windows' built-in OCR engine (fast, offline). Line
  positions are used to split the page into blocks, so appearance columns stay
  together.
- E-mail: pasted text (the usual way) or a saved .eml; also .txt and .docx.
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

try:  # pillow-heif is optional
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif"}
TEXT_EXT = {".txt", ".text", ".md", ".csv", ".htm", ".html"}
PDF_TEXT_PAGES = 3


@dataclass
class Ingested:
    name: str
    kind: str                      # pdf / image / email / text
    text: str = ""
    images: list[bytes] = field(default_factory=list)   # JPEG bytes for the AI model
    page_count: int = 0
    first_page_no: int | None = None
    ocr_used: bool = False
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- OCR

def _winrt_ocr(png: bytes):
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
    img.thumbnail((OcrEngine.max_image_dimension,) * 2)
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
    try:
        from winrt.windows.media.ocr import OcrEngine
        return OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        return False


def _jpeg(img: Image.Image, max_side: int = 1400) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


# ------------------------------------------------------------------ inputs

def ingest_image(path: Path) -> Ingested:
    return ingest_pil(ImageOps.exif_transpose(Image.open(path)), path.name)


def ingest_pil(img: Image.Image, name: str = "Pasted image") -> Ingested:
    ing = Ingested(name, "image", images=[_jpeg(img)])
    try:
        ing.text = ocr_image(img)
        ing.ocr_used = True
    except Exception as e:  # pragma: no cover - depends on Windows install
        ing.warnings.append(f"Windows OCR failed ({e}); only the AI model can read this image.")
    return ing


def ingest_pdf(path: Path) -> Ingested:
    doc = pymupdf.open(path)
    ing = Ingested(path.name, "pdf", page_count=doc.page_count)
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
    return ing


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    import html as h
    return h.unescape(text)


def ingest_eml(path: Path) -> Ingested:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    header = "\n".join(f"{k}: {msg[k]}" for k in ("From", "To", "Subject", "Date") if msg[k])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = _html_to_text(text)
    return Ingested(path.name, "email", text=f"{header}\n\n{text}")


def ingest_docx(path: Path) -> Ingested:
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
    path = Path(path)
    ext = path.suffix.lower()
    try:
        return _ingest_file(path, ext)
    except Exception as e:
        if type(e) is ValueError or isinstance(e, (FileNotFoundError, PermissionError)):
            raise  # already says what is wrong
        # PyMuPDF, Pillow and zipfile report damaged files in their own words
        if path.stat().st_size == 0:
            raise ValueError("the file is empty") from e
        why = str(e).replace(repr(str(path)), path.name).replace(str(path), path.name)
        raise ValueError(f"this doesn't look like a valid {ext or 'text'} file ({why[:120]})") from e


def _ingest_file(path: Path, ext: str) -> Ingested:
    if ext == ".pdf":
        ing = ingest_pdf(path)
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
    kind = "email" if re.search(r"(?im)^(from|subject|sent|to):", text) else "text"
    return Ingested(name, kind, text=text)
