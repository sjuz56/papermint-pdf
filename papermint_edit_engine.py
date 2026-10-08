"""Experimental direct PDF text editing engine.

Edits a selected text span using its exact PDF bounding box. Existing content is
redacted (removed, not just painted over) before replacement. Complex layouts,
embedded/subset fonts and line wrapping require explicit review.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fitz


@dataclass(frozen=True)
class TextReplacement:
    page: int
    old_text: str
    new_text: str
    occurrence: int = 0
    font_file: str | None = None


class PdfEditError(ValueError):
    pass


def inspect_text(pdf_path: str, page_number: int) -> list[dict]:
    """Return selectable spans with coordinates and approximate styling."""
    with fitz.open(pdf_path) as doc:
        if not 0 <= page_number < len(doc):
            raise PdfEditError("Page out of range")
        spans = []
        for block in doc[page_number].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        spans.append({
                            "text": span["text"],
                            "bbox": list(span["bbox"]),
                            "font": span["font"],
                            "size": span["size"],
                            "color": span["color"],
                        })
        return spans


def replace_text(pdf_path: str, output_path: str, changes: list[TextReplacement], font_path: str | None = None) -> dict:
    """Replace whole selectable spans; fail safely on overflow.

    This prototype does not support replacing arbitrary substrings or scanned PDFs.
    """
    if not changes:
        raise PdfEditError("No changes supplied")
    if Path(pdf_path).resolve() == Path(output_path).resolve():
        raise PdfEditError("Output must differ from input")
    if font_path is not None and not Path(font_path).is_file():
        raise PdfEditError("Font file not found")
    if font_path is None and any(ord(ch) > 127 for change in changes for ch in change.new_text):
        raise PdfEditError("Non-ASCII replacement requires a Unicode font_path")
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass:
            raise PdfEditError("Password-protected PDF is unsupported")
        operations = []
        for change in changes:
            if not 0 <= change.page < len(doc):
                raise PdfEditError("Page out of range")
            if not change.old_text or not change.new_text.strip():
                raise PdfEditError("Both old and new text must be nonempty")
            matches = [s for s in inspect_page(doc[change.page])
                       if s["text"] == change.old_text]
            if change.occurrence < 0 or change.occurrence >= len(matches):
                raise PdfEditError("Exact selectable text span not found")
            span = matches[change.occurrence]
            rect = fitz.Rect(span["bbox"])
            operations.append((change.page, rect, change.new_text, span, change.font_file))
        # Disallow overlapping edits before mutating any page.
        for i, (p, rect, _, _, _) in enumerate(operations):
            if any(p == p2 and rect.intersects(rect2)
                   for p2, rect2, _, _, _ in operations[:i]):
                raise PdfEditError("Overlapping edits")
        # Preflight every edit before redacting any content. A supplied TTF/OTF
        # supports Unicode including Czech characters; the default Helvetica
        # remains suitable only for its supported WinAnsi character set.
        for p, rect, new_text, span, font_file in operations:
            if "\\n" in new_text or "\\r" in new_text:
                raise PdfEditError("Multiline edits are not supported yet")
            if font_file:
                font_path = Path(font_file)
                if not font_path.is_file() or font_path.suffix.lower() not in {".ttf", ".otf"}:
                    raise PdfEditError("Provide an existing TTF or OTF font file")
                font = fitz.Font(fontfile=str(font_path))
            else:
                try:
                    new_text.encode("cp1252")
                except UnicodeEncodeError as exc:
                    raise PdfEditError("Supply a Unicode font_file for this text") from exc
                font = fitz.Font("helv")
            if font.text_length(new_text, fontsize=span["size"]) > rect.width + 0.5:
                raise PdfEditError("Replacement is wider than original text box")
        for p, rect, _, _, _ in operations:
            doc[p].add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
        for p in {op[0] for op in operations}:
            doc[p].apply_redactions(images=0, graphics=0, text=0)
        for p, rect, new_text, span, font_file in operations:
            c = span["color"]
            rgb = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
            baseline = fitz.Point(rect.x0, rect.y1 - max(0.5, span["size"] * 0.18))
            if font_file:
                # Register embedded font for this page. This does not guarantee
                # an exact match with the original PDF's subset font.
                fontname = "editfont_" + str(abs(hash(str(Path(font_file).resolve()))))
                doc[p].insert_font(fontname=fontname, fontfile=font_file)
            else:
                fontname = "helv"
            doc[p].insert_text(baseline, new_text, fontname=fontname,
                               fontsize=span["size"], color=rgb, overlay=True)
        doc.save(output_path, garbage=4, deflate=True)
        return {"replacements": len(operations), "output": output_path}


def inspect_page(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans.extend(line["spans"])
    return spans
