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
            operations.append((change.page, rect, change.new_text, span))
        # Disallow overlapping edits before mutating any page.
        for i, (p, rect, _, _) in enumerate(operations):
            if any(p == p2 and rect.intersects(rect2)
                   for p2, rect2, _, _ in operations[:i]):
                raise PdfEditError("Overlapping edits")
        # Preflight text fit using the PDF built-in Helvetica font.
        for p, rect, new_text, span in operations:
            if any(ord(ch) > 255 for ch in new_text):
                raise PdfEditError("Unicode font embedding is required for this replacement")
            if fitz.get_text_length(new_text, fontname="helv", fontsize=span["size"]) > rect.width + 0.5:
                raise PdfEditError("Replacement is wider than original text box")
        for p, rect, _, _ in operations:
            doc[p].add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
        for p in {op[0] for op in operations}:
            doc[p].apply_redactions(images=0, graphics=0, text=0)
        for p, rect, new_text, span in operations:
            c = span["color"]
            rgb = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
            # Baseline inferred from original span geometry; use insert_text rather
            # than textbox to preserve its approximate original placement.
            baseline = fitz.Point(rect.x0, rect.y1 - max(0.5, span["size"] * 0.18))
            doc[p].insert_text(baseline, new_text, fontname="helv",
                               fontsize=span["size"], color=rgb, overlay=True)
        doc.save(output_path, garbage=4, deflate=True)
        return {"replacements": len(operations), "output": output_path}


def inspect_page(page) -> list[dict]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans.extend(line["spans"])
    return spans
