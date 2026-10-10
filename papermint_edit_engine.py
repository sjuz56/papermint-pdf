"""Direct editing of horizontal PDF text blocks, with verified atomic export."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import statistics
import tempfile

import fitz


@dataclass(frozen=True)
class TextReplacement:
    page: int
    old_text: str
    new_text: str
    occurrence: int = 0
    font_file: str | None = None
    bold: bool | None = None
    width: float | None = None
    italic: bool | None = None
    font_family: str | None = None
    font_size: float | None = None
    color: int | None = None


class PdfEditError(ValueError):
    pass


EDIT_FONT_FAMILIES = {
    "liberation-sans": ("Liberation Sans", "LiberationSans", "liberation"),
    "liberation-serif": ("Liberation Serif", "LiberationSerif", "liberation"),
    "liberation-mono": ("Liberation Mono", "LiberationMono", "liberation"),
    "dejavu-sans": ("DejaVu Sans", "DejaVuSans", "dejavu"),
    "dejavu-serif": ("DejaVu Serif", "DejaVuSerif", "dejavu-serif"),
    "dejavu-mono": ("DejaVu Sans Mono", "DejaVuSansMono", "dejavu"),
}


def _font_roots():
    roots = [Path("/usr/share/fonts/truetype/liberation2"), Path("/usr/share/fonts/truetype/liberation"),
             Path("/usr/share/fonts/truetype/dejavu")]
    runtime = os.getenv("CODEX_PRIMARY_RUNTIME_ROOT")
    if runtime:
        roots.append(Path(runtime) / "dependencies/native/libreoffice-headless/libreoffice/share/fonts/truetype")
    return roots


def editing_font_path(family: str, bold=False, italic=False) -> Path:
    """Resolve only server-owned font families, never a client filesystem path."""
    if family not in EDIT_FONT_FAMILIES:
        raise PdfEditError("Unknown font family")
    _, basename, kind = EDIT_FONT_FAMILIES[family]
    if kind == "liberation":
        variant = "BoldItalic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    else:
        slant = "Italic" if kind == "dejavu-serif" else "Oblique"
        variant = "Bold" + slant if bold and italic else "Bold" if bold else slant if italic else ""
    filename = basename + ("-" + variant if variant else "") + ".ttf"
    for root in _font_roots():
        path = root / filename
        if path.is_file():
            return path
    raise PdfEditError("This font is unavailable on the server")


def editing_fonts() -> list[dict]:
    available = []
    for family, (label, _, _) in EDIT_FONT_FAMILIES.items():
        try:
            for bold, italic in ((False, False), (True, False), (False, True), (True, True)):
                editing_font_path(family, bold, italic)
        except PdfEditError:
            continue
        available.append({"id": family, "label": label})
    return available


def _style(span):
    return (span["font"], round(span["size"], 2), span["color"], span.get("flags", 0), span.get("alpha", 255))


def _block(members, lines, direction):
    rect = fitz.Rect(members[0]["bbox"])
    for member in members[1:]:
        rect |= fitz.Rect(member["bbox"])
    return {**members[0], "text": "\n".join(lines), "bbox": tuple(rect),
            "members": members, "direction": direction}


def inspect_page(page) -> list[dict]:
    """Group adjacent left-aligned lines with one style into editable blocks.

    Mixed styles and overlapping runs stay separate to preserve their formatting.
    Inspection and export use the same grouping.
    """
    result = []
    for raw_block in page.get_text("dict")["blocks"]:
        members, texts, direction = [], [], (1, 0)
        for line in raw_block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            uniform = all(_style(s) == _style(spans[0]) for s in spans)
            ordered = all(-0.1 <= b["bbox"][0] - a["bbox"][2] <= spans[0]["size"] * 0.75
                          for a, b in zip(spans, spans[1:]))
            line_dir = tuple(line.get("dir", (1, 0)))
            joins = members and uniform and ordered and _style(members[0]) == _style(spans[0])
            if joins:
                gap = spans[0]["origin"][1] - members[-1]["origin"][1]
                joins = (direction == line_dir and abs(spans[0]["bbox"][0] - members[0]["bbox"][0]) < 2
                         and 0 < gap <= spans[0]["size"] * 2)
            if members and not joins:
                result.append(_block(members, texts, direction))
                members, texts = [], []
            if uniform and ordered:
                members.extend(spans)
                text = spans[0]["text"]
                for previous, following in zip(spans, spans[1:]):
                    if (following["bbox"][0] - previous["bbox"][2] > spans[0]["size"] * 0.2
                            and not text.endswith(" ") and not following["text"].startswith(" ")):
                        text += " "
                    text += following["text"]
                texts.append(text)
                direction = line_dir
            else:
                result.extend(_block([s], [s["text"]], line_dir) for s in spans)
        if members:
            result.append(_block(members, texts, direction))
    # Some generators emit every paragraph line as a separate PDF text object.
    # Join neighbouring objects using the same style/alignment rules.
    grouped = []
    for block in result:
        if grouped:
            previous = grouped[-1]
            gap = block["origin"][1] - previous["members"][-1]["origin"][1]
            if (_style(previous) == _style(block) and previous["direction"] == block["direction"]
                    and abs(previous["bbox"][0] - block["bbox"][0]) < 2
                    and 0 < gap <= block["size"] * 2):
                grouped[-1] = _block(previous["members"] + block["members"],
                                     [previous["text"], block["text"]], previous["direction"])
                continue
        grouped.append(block)
    return grouped


def inspect_text(pdf_path: str, page_number: int) -> list[dict]:
    with fitz.open(pdf_path) as doc:
        if not 0 <= page_number < len(doc):
            raise PdfEditError("Page out of range")
        return [{k: block[k] for k in ("text", "bbox", "font", "size", "color")}
                for block in inspect_page(doc[page_number])]


def editing_bbox(page, block, blocks, drawings=None) -> list[float]:
    """Allow horizontal growth up to the page margin, adjacent text or cell edge."""
    rect = fitz.Rect(block["bbox"])
    right = max(rect.x1, page.rect.x1 - 12)
    for neighbour in blocks:
        if neighbour is block:
            continue
        for member in neighbour["members"]:
            other = fitz.Rect(member["bbox"])
            if other.x0 >= rect.x1 - 0.1 and other.y0 < rect.y1 and other.y1 > rect.y0:
                right = min(right, max(rect.x1, other.x0 - 2))
    for drawing in page.get_drawings() if drawings is None else drawings:
        for item in drawing["items"]:
            edges = []
            if item[0] == "l" and abs(item[1].x - item[2].x) < 0.5:
                edges.append((item[1].x, min(item[1].y, item[2].y), max(item[1].y, item[2].y)))
            elif item[0] == "re":
                box = item[1]
                edges.extend((x, box.y0, box.y1) for x in (box.x0, box.x1))
            for x, top, bottom in edges:
                if x > rect.x1 + 0.1 and top < rect.y1 and bottom > rect.y0:
                    right = min(right, max(rect.x1, x - 2))
    return [rect.x0, rect.y0, right, rect.y1]


def _font_name(name):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", name).lower())


def _supports(font, text):
    return all(font.has_glyph(ord(c)) for c in set(text) if not c.isspace())


def _bold(block):
    return bool(block.get("flags", 0) & 16) or any(
        name in _font_name(block["font"]) for name in ("bold", "demi", "black"))


def _font_family(name):
    return re.sub(r"semibold|bold|italic|oblique|regular|roman|demi|black", "", _font_name(name))


def _select_font(page, block, text, font_file, requested_bold=None, requested_italic=None, family_id=None):
    original = _font_name(block["font"])
    bold = _bold(block) if requested_bold is None else requested_bold
    original_italic = bool(block.get("flags", 0) & 2) or "italic" in original or "oblique" in original
    italic = original_italic if requested_italic is None else requested_italic
    if family_id:
        font = fitz.Font(fontfile=str(editing_font_path(family_id, bold, italic)))
        if not _supports(font, text):
            raise PdfEditError("The selected font does not contain all replacement characters")
        return font, False
    if font_file:
        path = Path(font_file)
        if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf"}:
            raise PdfEditError("Provide an existing TTF or OTF font file")
        font = fitz.Font(fontfile=str(path))
        if not _supports(font, text):
            raise PdfEditError("The selected font does not contain all replacement characters")
        return font, True
    style_changed = bold != _bold(block) or italic != original_italic
    for xref, _, _, basefont, *_ in page.get_fonts(full=True):
        if (not style_changed and _font_name(basefont) != original) or (
                style_changed and _font_family(basefont) != _font_family(original)):
            continue
        try:
            buffer = page.parent.extract_font(xref)[3]
            if buffer:
                font = fitz.Font(fontbuffer=buffer)
                if (_supports(font, text) and (not style_changed or (
                        bool(font.flags["bold"]) == bold and bool(font.flags["italic"]) == italic))):
                    return font, False
        except (RuntimeError, ValueError):
            continue
    base14 = {"helvetica": "helv", "helveticabold": "hebo",
              "helveticaoblique": "heit", "helveticaboldoblique": "hebi",
              "timesroman": "tiro", "timesbold": "tibo", "timesitalic": "tiit",
              "timesbolditalic": "tibi", "courier": "cour", "courierbold": "cobo",
              "courieroblique": "coit", "courierboldoblique": "cobi"}
    if original in base14:
        name = base14[original]
        if style_changed:
            if original.startswith("helvetica"):
                name = "hebi" if bold and italic else "hebo" if bold else "heit" if italic else "helv"
            elif original.startswith("times"):
                name = "tibi" if bold and italic else "tibo" if bold else "tiit" if italic else "tiro"
            else:
                name = "cobi" if bold and italic else "cobo" if bold else "coit" if italic else "cour"
        font = fitz.Font(name)
        if _supports(font, text):
            return font, False
    flags = block.get("flags", 0)
    # Some PDFs set misleading serif flags on a subset of DejaVu Sans.
    # Known family names and an installed full version take precedence.
    if any(name in original for name in ("mono", "courier", "consolas")):
        family = "Mono"
    elif any(name in original for name in ("sans", "helvetica", "arial", "calibri", "carlito")):
        family = "Sans"
    elif any(name in original for name in ("serif", "times", "cambria", "caladea", "georgia")):
        family = "Serif"
    else:
        family = "Mono" if flags & 8 else "Serif" if flags & 4 else "Sans"
    variant = "BoldItalic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    roots = _font_roots()
    filenames = [f"Liberation{family}-{variant}.ttf"]
    dejavu_variant = "BoldOblique" if bold and italic else "Bold" if bold else "Oblique" if italic else ""
    if family == "Serif":
        dejavu_variant = dejavu_variant.replace("Oblique", "Italic")
    filenames.append("DejaVu" + ("SansMono" if family == "Mono" else family)
                     + ("-" + dejavu_variant if dejavu_variant else "") + ".ttf")
    configured = os.getenv("PAPERMINT_EDIT_UNICODE_FONT")
    original_file = re.sub(r"[^A-Za-z0-9_-]", "", re.sub(r"^[A-Z]{6}\+", "", block["font"])) + ".ttf"
    original_candidates = [root / original_file for root in roots]
    if style_changed:
        original_candidates = [root / name for root in roots for name in (
            "DejaVu" + ("SansMono" if family == "Mono" else family)
            + ("-" + dejavu_variant if dejavu_variant else "") + ".ttf",
            f"Liberation{family}-{variant}.ttf")
            if _font_family(name.removesuffix(".ttf")) == _font_family(original)]
    candidates = (([Path(configured)] if configured else []) + original_candidates
                  + [root / name for root in roots for name in filenames])
    for path in candidates:
        if path.is_file():
            font = fitz.Font(fontfile=str(path))
            if (_supports(font, text) and (requested_bold is None or bool(font.flags["bold"]) == bold)
                    and (requested_italic is None or bool(font.flags["italic"]) == italic)):
                return font, True
    font = fitz.Font("cjk")
    if (_supports(font, text) and (requested_bold is None or bool(font.flags["bold"]) == bold)
            and (requested_italic is None or bool(font.flags["italic"]) == italic)):
        return font, True
    raise PdfEditError("No available font contains all replacement characters")


def _wrap(text, font, size, width):
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for token in re.findall(r"\s+|\S+", paragraph):
            if font.text_length(current + token, fontsize=size) <= width + 0.1:
                current += token
                continue
            if current:
                lines.append(current.rstrip())
                current = ""
            token = token.lstrip()
            for char in token:
                if font.text_length(current + char, fontsize=size) > width + 0.1:
                    if not current:
                        raise PdfEditError("A character is wider than the text block")
                    lines.append(current)
                    current = ""
                current += char
        lines.append(current.rstrip())
    return lines


def _compact(text):
    return "".join(text.split())


def _verify(doc, operations):
    for op in operations:
        extracted = doc[op["page"]].get_textbox(op["area"] + (-0.5, -0.5, 0.5, 0.5))
        if _compact(extracted) != _compact(op["new_text"]):
            raise PdfEditError("Replacement could not be verified in the output PDF")


def replace_text(pdf_path: str, output_path: str, changes: list[TextReplacement]) -> dict:
    """Remove original glyphs and reflow replacements without moving neighbours.

    Text may grow horizontally in a requested width and downwards in free space.
    Fonts, size, colour and the original baseline are retained where possible.
    Missing subset-font glyphs use a disclosed fallback. Scans and rotated text
    are not editable. Preflight every operation before removing any content.
    """
    if not changes:
        raise PdfEditError("No changes supplied")
    if Path(pdf_path).resolve() == Path(output_path).resolve():
        raise PdfEditError("Output must differ from input")
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass:
            raise PdfEditError("Password-protected PDF is unsupported")
        operations, page_blocks = [], {}
        for change in changes:
            if type(change.page) is not int or type(change.occurrence) is not int:
                raise PdfEditError("Page and occurrence must be integers")
            if not 0 <= change.page < len(doc):
                raise PdfEditError("Page out of range")
            if not isinstance(change.old_text, str) or not isinstance(change.new_text, str):
                raise PdfEditError("Replacement text must be strings")
            if change.bold is not None and type(change.bold) is not bool:
                raise PdfEditError("Bold must be a boolean")
            if change.italic is not None and type(change.italic) is not bool:
                raise PdfEditError("Italic must be a boolean")
            if change.font_family is not None and (not isinstance(change.font_family, str)
                    or change.font_family not in EDIT_FONT_FAMILIES):
                raise PdfEditError("Unknown font family")
            if change.font_size is not None and (type(change.font_size) not in (int, float)
                    or not math.isfinite(change.font_size) or not 4 <= change.font_size <= 144):
                raise PdfEditError("Font size must be between 4 and 144 points")
            if change.color is not None and (type(change.color) is not int or not 0 <= change.color <= 0xFFFFFF):
                raise PdfEditError("Color must be an RGB value")
            if change.width is not None and (type(change.width) not in (int, float)
                    or not math.isfinite(change.width) or change.width <= 0):
                raise PdfEditError("Text width must be a positive finite number")
            text = change.new_text.replace("\r\n", "\n").replace("\r", "\n")
            if not change.old_text:
                raise PdfEditError("Original text must be nonempty")
            if any((ord(c) < 32 and c != "\n") or ord(c) == 127 for c in text):
                raise PdfEditError("Control characters are not supported in replacement text")
            page = doc[change.page]
            if page.rotation:
                raise PdfEditError("Editing rotated PDF pages is not supported yet")
            if any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in page.annots() or []):
                raise PdfEditError("Apply or remove existing redaction annotations before editing")
            if change.page not in page_blocks:
                page_blocks[change.page] = inspect_page(page)
            matches = [b for b in page_blocks[change.page] if b["text"] == change.old_text]
            if change.occurrence < 0 or change.occurrence >= len(matches):
                raise PdfEditError("Exact selectable text block not found")
            block = matches[change.occurrence]
            if abs(block["direction"][0] - 1) > 0.001 or abs(block["direction"][1]) > 0.001:
                raise PdfEditError("Rotated text blocks are not supported yet")
            rect = fitz.Rect(block["bbox"])
            font, substituted = _select_font(page, block, text, change.font_file, change.bold, change.italic, change.font_family)
            width = rect.width if change.width is None else change.width
            if change.width is not None:
                available = editing_bbox(page, block, page_blocks[change.page])[2] - rect.x0
                if width > available + 0.1:
                    raise PdfEditError("Text width exceeds the available space on the page")
            size = block["size"] if change.font_size is None else change.font_size
            origin = fitz.Point(block["origin"])
            baselines = sorted(set(s["origin"][1] for s in block["members"]))
            gaps = [b - a for a, b in zip(baselines, baselines[1:])]
            lineheight = max(size * 1.2, size * (font.ascender - font.descender),
                             statistics.median(gaps) * size / block["size"] if gaps else 0)
            lines = _wrap(text, font, size, width) if text.strip() else []
            area = fitz.Rect(rect)
            if lines:
                ink_right = origin.x + max(font.text_length(line, fontsize=size) for line in lines)
                area |= fitz.Rect(origin.x, origin.y - font.ascender * size, ink_right,
                                  origin.y + (len(lines) - 1) * lineheight - font.descender * size)
            if not page.rect.contains(area):
                raise PdfEditError("Replacement does not fit on the page")
            operations.append({"page": change.page, "block": block, "area": area,
                               "font": font, "lines": lines, "lineheight": lineheight,
                               "new_text": text, "substituted": substituted, "size": size,
                               "color": block["color"] if change.color is None else change.color})
        for i, op in enumerate(operations):
            if any(op["page"] == prev["page"] and op["area"].intersects(prev["area"])
                   for prev in operations[:i]):
                raise PdfEditError("Overlapping edits")
            for neighbour in page_blocks[op["page"]]:
                if neighbour is op["block"]:
                    continue
                if any(op["area"].intersects(fitz.Rect(s["bbox"])) for s in neighbour["members"]):
                    raise PdfEditError("Replacement overlaps neighbouring text; shorten the text")
        for op in operations:
            for member in op["block"]["members"]:
                doc[op["page"]].add_redact_annot(member["bbox"], fill=False, cross_out=False)
        for number in {op["page"] for op in operations}:
            doc[number].apply_redactions(images=0, graphics=0, text=0)
        for op in operations:
            if not op["lines"]:
                continue
            block, font = op["block"], op["font"]
            fontname = "edit_" + hashlib.sha256(font.buffer).hexdigest()[:12]
            page = doc[op["page"]]
            page.insert_font(fontname=fontname, fontbuffer=font.buffer)
            color = op["color"]
            rgb = tuple((color >> shift & 255) / 255 for shift in (16, 8, 0))
            x, y = block["origin"]
            for index, line in enumerate(op["lines"]):
                if line:
                    page.insert_text((x, y + index * op["lineheight"]), line,
                                     fontname=fontname, fontsize=op["size"], color=rgb,
                                     fill_opacity=block.get("alpha", 255) / 255, overlay=True)
        _verify(doc, operations)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".pdfaspect-edit-", suffix=".pdf", dir=output.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            doc.save(str(temporary), garbage=4, deflate=True)
            with fitz.open(temporary) as saved:
                _verify(saved, operations)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {"replacements": len(operations), "output": output_path,
                "edit_boxes": [{"page": op["page"], "bbox": list(op["area"])} for op in operations],
                "font_substitutions": [{"original": op["block"]["font"], "replacement": op["font"].name}
                                       for op in operations if op["substituted"] and op["lines"]]}
