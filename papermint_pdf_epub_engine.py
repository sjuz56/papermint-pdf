"""PDF to EPUB conversion for PDFaspect."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
import uuid
import zipfile

import fitz


class PdfEpubError(RuntimeError):
    """Raised when a PDF cannot be converted to EPUB."""


def _paragraphs(page: fitz.Page) -> list[str]:
    blocks = page.get_text("blocks", sort=True)
    out: list[str] = []
    for block in blocks:
        if len(block) < 5:
            continue
        text = str(block[4] or "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n\s*", " ", text).strip()
        if text:
            out.append(text)
    return out


def pdf_to_epub(source: str | Path, output: str | Path) -> dict:
    source = Path(source)
    output = Path(output)

    if not source.is_file():
        raise PdfEpubError("The uploaded PDF could not be found.")

    try:
        doc = fitz.open(source)
    except Exception as exc:
        raise PdfEpubError("The PDF could not be opened.") from exc

    try:
        if doc.needs_pass:
            raise PdfEpubError("Password-protected PDFs must be unlocked before conversion.")
        if doc.page_count < 1:
            raise PdfEpubError("The PDF has no pages.")

        chapters: list[tuple[str, str]] = []
        extracted_chars = 0

        for index, page in enumerate(doc):
            paras = _paragraphs(page)
            extracted_chars += sum(len(p) for p in paras)
            body = "\n".join(f"<p>{escape(p)}</p>" for p in paras)
            if not body:
                body = "<p></p>"
            title = f"Page {index + 1}"
            chapter = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<!DOCTYPE html>'
                '<html xmlns="http://www.w3.org/1999/xhtml" lang="en">'
                '<head><meta charset="utf-8"/><title>' + title + '</title>'
                '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
                '<body><section><h2>' + title + '</h2>' + body + '</section></body></html>'
            )
            chapters.append((f"page-{index + 1}.xhtml", chapter))

        if extracted_chars < 20:
            raise PdfEpubError(
                "This PDF contains little or no selectable text. Run OCR first, then convert the text-based PDF to EPUB."
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        book_id = f"urn:uuid:{uuid.uuid4()}"

        nav_items = "".join(
            f'<li><a href="{name}">Page {i + 1}</a></li>'
            for i, (name, _) in enumerate(chapters)
        )
        nav = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE html>'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" lang="en">'
            '<head><meta charset="utf-8"/><title>Contents</title></head>'
            '<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
            + nav_items +
            '</ol></nav></body></html>'
        )

        manifest = "".join(
            f'<item id="p{i + 1}" href="{name}" media-type="application/xhtml+xml"/>'
            for i, (name, _) in enumerate(chapters)
        )
        spine = "".join(f'<itemref idref="p{i + 1}"/>' for i in range(len(chapters)))
        opf = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="book-id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="book-id">{book_id}</dc:identifier>'
            f'<dc:title>{escape(source.stem or "Converted PDF")}</dc:title>'
            '<dc:language>en</dc:language>'
            '</metadata><manifest>'
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            '<item id="css" href="style.css" media-type="text/css"/>'
            + manifest +
            '</manifest><spine>' + spine + '</spine></package>'
        )

        container = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        )
        css = (
            "body{font-family:serif;line-height:1.5;margin:5%;}"
            "h1,h2{page-break-after:avoid;}p{margin:0 0 1em 0;}"
        )

        with zipfile.ZipFile(output, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/style.css", css, compress_type=zipfile.ZIP_DEFLATED)
            for name, chapter in chapters:
                zf.writestr(f"OEBPS/{name}", chapter, compress_type=zipfile.ZIP_DEFLATED)

        if not output.is_file() or output.stat().st_size < 500:
            raise PdfEpubError("The EPUB could not be created.")

        return {
            "pages": doc.page_count,
            "characters": extracted_chars,
            "format": "EPUB 3",
        }
    except PdfEpubError:
        output.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise PdfEpubError("The PDF could not be converted to EPUB.") from exc
    finally:
        doc.close()
