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


def replace_text(pdf_path: str, output_path: str, changes: list[TextReplacement]) -> dict:
    """Replace whole selectable spans; fail safely on overflow.

    This prototype does not support replacing arbitrary substrings or scanned PDFs.
    """
    if not changes:
        raise PdfEditError("No changes supplied")
    if Path(pdf_path).resolve() == Path(output_path).resolve():
        raise PdfEditError("Output must differ from input")
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass:
            raise PdfEditError("Password-protected PDF is unsupported")
        operations = []
        # Inspect only pages being edited; large PDFs may contain many unrelated pages.
        page_spans = {}
        for change in changes:
            if type(change.page) is not int or type(change.occurrence) is not int:
                raise PdfEditError("Page and occurrence must be integers")
            if not 0 <= change.page < len(doc):
                raise PdfEditError("Page out of range")
            if not isinstance(change.old_text, str) or not isinstance(change.new_text, str):
                raise PdfEditError("Replacement text must be strings")
            if not change.old_text or not change.new_text.strip():
                raise PdfEditError("Both old and new text must be nonempty")
            if change.page not in page_spans:
                page_spans[change.page] = inspect_page(doc[change.page])
            matches = [s for s in page_spans[change.page]
                       if s["text"] == change.old_text]
            if change.occurrence < 0 or change.occurrence >= len(matches):
                raise PdfEditError("Exact selectable text span not found")
            span = matches[change.occurrence]
            rect = fitz.Rect(span["bbox"])
            operations.append((change.page, rect, change.new_text, span, change.font_file))
        # Reject duplicate/overlapping edits before checking neighbouring spans.
        # An edited span may otherwise be mistaken for an unrelated neighbour.
        for i, (p, rect, _, _, _) in enumerate(operations):
            if any(p == p2 and rect.intersects(rect2)
                   for p2, rect2, _, _, _ in operations[:i]):
                raise PdfEditError("Overlapping edits")
        # PyMuPDF redaction can remove neighbouring glyphs when boxes intersect.
        for p, rect, _, span, _ in operations:
            for neighbour in page_spans[p]:
                if neighbour is span:
                    continue
                if fitz.Rect(neighbour["bbox"]).intersects(rect):
                    raise PdfEditError("Edit box overlaps neighbouring text")
        # Preflight every edit before redacting any content. A supplied TTF/OTF
        # supports Unicode including Czech characters; the default Helvetica
        # remains suitable only for its supported WinAnsi character set.
        for p, rect, new_text, span, font_file in operations:
            if "\n" in new_text or "\r" in new_text:
                raise PdfEditError("Multiline edits are not supported yet")
            if any(ord(char) < 32 or ord(char) == 127 for char in new_text):
                raise PdfEditError("Control characters are not supported in replacement text")
            if font_file:
                selected_font_path = Path(font_file)
                if not selected_font_path.is_file() or selected_font_path.suffix.lower() not in {".ttf", ".otf"}:
                    raise PdfEditError("Provide an existing TTF or OTF font file")
                font = fitz.Font(fontfile=str(selected_font_path))
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
                import hashlib
                fontname = "editfont_" + hashlib.sha256(str(Path(font_file).resolve()).encode()).hexdigest()[:12]
                doc[p].insert_font(fontname=fontname, fontfile=font_file)
            else:
                fontname = "helv"
            doc[p].insert_text(baseline, new_text, fontname=fontname,
                               fontsize=span["size"], color=rgb, overlay=True)
        # Validate the generated document before returning it to the user.
        # Do not publish an output with missing replacement text or surviving
        # original text in a replaced span.
        for p, rect, new_text, span, _ in operations:
            # Verify the specific edited area, not merely the whole page:
            # another occurrence elsewhere must not mask a failed insertion.
            nearby = fitz.Rect(rect.x0 - 2, rect.y0 - 3,
                               rect.x1 + 2, rect.y1 + 3)
            extracted = doc[p].get_textbox(nearby)
            if new_text not in extracted:
                raise PdfEditError("Replacement could not be verified in the output PDF")
            if span["text"] != new_text and span["text"] in extracted:
                raise PdfEditError("Original text is still present in the edited area")
        doc.save(output_path, garbage=4, deflate=True)
        # Validate the bytes that will actually be returned, not just the
        # mutable in-memory document. A corrupt or incomplete save must fail.
        try:
            with fitz.open(output_path) as saved:
                for p, rect, new_text, span, _ in operations:
                    nearby = fitz.Rect(rect.x0 - 2, rect.y0 - 3,
                                       rect.x1 + 2, rect.y1 + 3)
                    saved_text = saved[p].get_textbox(nearby)
                    if new_text not in saved_text:
                        raise PdfEditError("Saved PDF does not contain the replacement")
                    if span["text"] != new_text and span["text"] in saved_text:
                        raise PdfEditError("Saved PDF still contains original text in the edited area")
        except Exception:
            Path(output_path).unlink(missing_ok=True)
            raise
        return {"replacements": len(operations), "output": output_path}


def inspect_page(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans.extend(span for span in line["spans"] if span["text"].strip())
    return spans
