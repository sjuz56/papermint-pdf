# ============================================================
# PAPERMINT PAGEGRID V2
# Editable PDF -> DOCX reconstruction
# ============================================================

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import fitz


def _set_cell_margins(cell, top=20, start=35, bottom=20, end=35):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()

    tcMar = tcPr.first_child_found_in("w:tcMar")

    if tcMar is None:
        tcMar = OxmlElement("w:tcMar")
        tcPr.append(tcMar)

    for margin, value in {
        "top": top,
        "start": start,
        "bottom": bottom,
        "end": end,
    }.items():

        node = tcMar.find(qn(f"w:{margin}"))

        if node is None:
            node = OxmlElement(f"w:{margin}")
            tcMar.append(node)

        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _remove_table_borders(table):
    tbl = table._tbl
    tblPr = tbl.tblPr

    borders = tblPr.first_child_found_in("w:tblBorders")

    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tblPr.append(borders)

    for edge in (
        "top",
        "left",
        "bottom",
        "right",
        "insideH",
        "insideV",
    ):
        tag = f"w:{edge}"

        element = borders.find(qn(tag))

        if element is None:
            element = OxmlElement(tag)
            borders.append(element)

        element.set(qn("w:val"), "nil")


def _group_words_into_rows(words, tolerance=2.5):
    """
    PyMuPDF word tuple:
    x0, y0, x1, y1, text, block, line, word
    """

    if not words:
        return []

    words = sorted(
        words,
        key=lambda w: (w[1], w[0]),
    )

    rows = []

    for word in words:

        y = word[1]

        target = None

        for row in reversed(rows[-4:]):

            if abs(row["y"] - y) <= tolerance:
                target = row
                break

        if target is None:
            target = {
                "y": y,
                "words": [],
            }

            rows.append(target)

        target["words"].append(word)

    for row in rows:
        row["words"].sort(key=lambda w: w[0])

    return rows


def _detect_columns(rows, page_width):
    """
    Creates a stable page grid instead of letting Word freely
    position every PDF text fragment.
    """

    xs = []

    for row in rows:
        for word in row["words"]:
            xs.append(word[0])

    if not xs:
        return [0, page_width]

    # Fixed normalized grid works much more reliably for statements,
    # invoices and table-heavy PDFs than floating text boxes.
    fractions = [
        0.00,
        0.18,
        0.36,
        0.56,
        0.76,
        1.00,
    ]

    return [
        page_width * f
        for f in fractions
    ]


def _column_for_x(x, boundaries):
    for i in range(len(boundaries) - 1):
        if boundaries[i] <= x < boundaries[i + 1]:
            return i

    return len(boundaries) - 2


def _clean_pdf_words(words):
    cleaned = []

    for word in words:

        text = str(word[4]).strip()

        if not text:
            continue

        # Hidden/artefact text found in the test bank statement.
        if text.upper() == "SIGN":
            continue

        cleaned.append(word)

    return cleaned


def _add_page_to_docx(doc, page):
    page_width_pt = page.rect.width
    page_height_pt = page.rect.height

    section = doc.sections[-1]

    section.page_width = Inches(
        page_width_pt / 72
    )

    section.page_height = Inches(
        page_height_pt / 72
    )

    # Small margins give the reconstructed grid enough room.
    section.top_margin = Inches(0.22)
    section.bottom_margin = Inches(0.22)
    section.left_margin = Inches(0.22)
    section.right_margin = Inches(0.22)

    words = page.get_text("words")

    words = _clean_pdf_words(words)

    rows = _group_words_into_rows(
        words,
        tolerance=2.8,
    )

    boundaries = _detect_columns(
        rows,
        page_width_pt,
    )

    column_count = len(boundaries) - 1

    table = doc.add_table(
        rows=0,
        cols=column_count,
    )

    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    _remove_table_borders(table)

    usable_width = (
        section.page_width
        - section.left_margin
        - section.right_margin
    )

    widths = []

    for i in range(column_count):

        ratio = (
            boundaries[i + 1]
            - boundaries[i]
        ) / page_width_pt

        widths.append(
            int(usable_width * ratio)
        )

    previous_y = None

    for source_row in rows:

        y = source_row["y"]

        # Preserve larger vertical gaps without generating
        # dozens of empty Word paragraphs.
        if previous_y is not None:

            gap = y - previous_y

            if gap > 24:

                spacer = table.add_row()

                for cell in spacer.cells:
                    cell.text = ""

                approx_height = min(
                    max(gap * 0.45, 4),
                    18,
                )

                spacer.height = Pt(
                    approx_height
                )

        previous_y = y

        row = table.add_row()

        values = [
            []
            for _ in range(column_count)
        ]

        for word in source_row["words"]:

            col = _column_for_x(
                word[0],
                boundaries,
            )

            values[col].append(word)

        for col_index, cell in enumerate(
            row.cells
        ):

            cell.width = widths[col_index]

            cell.vertical_alignment = (
                WD_CELL_VERTICAL_ALIGNMENT.CENTER
            )

            _set_cell_margins(cell)

            paragraph = cell.paragraphs[0]

            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1

            source_words = values[col_index]

            if not source_words:
                continue

            # Preserve spacing inside a PDF line according to X positions.
            pieces = []

            last_x1 = None

            for word in source_words:

                x0, _, x1, _, text = word[:5]

                if last_x1 is not None:

                    gap = x0 - last_x1

                    if gap > 12:
                        pieces.append("   ")
                    elif gap > 4:
                        pieces.append(" ")

                pieces.append(text)

                last_x1 = x1

            text = "".join(pieces)

            run = paragraph.add_run(text)

            # Estimate font size from PDF word height.
            heights = [
                max(1, w[3] - w[1])
                for w in source_words
            ]

            avg_height = (
                sum(heights)
                / len(heights)
            )

            font_size = min(
                max(avg_height * 0.72, 6.5),
                11,
            )

            run.font.size = Pt(font_size)

            # Right align the final columns, which usually contain
            # amounts/dates in statements and invoices.
            if col_index >= column_count - 2:
                paragraph.alignment = (
                    WD_ALIGN_PARAGRAPH.RIGHT
                )

    return table


def pdf_to_word_editable(src: Path) -> Path:
    """
    PaperMint PageGrid V2.

    Reconstructs every PDF page independently using an editable
    Word table grid. This avoids pdf2docx floating objects and
    prevents content from drifting outside the page.

    One source PDF page = one Word page.
    """

    print(
        "PAGEGRID V2 STARTED",
        flush=True,
    )

    pdf = fitz.open(src)

    out = outpath(".docx")

    doc = Document()

    # Remove the default empty paragraph.
    first_p = doc.paragraphs[0]

    p_element = first_p._element
    p_element.getparent().remove(p_element)

    for page_index, page in enumerate(pdf):

        print(
            f"PAGEGRID PAGE "
            f"{page_index + 1}/{len(pdf)}",
            flush=True,
        )

        _add_page_to_docx(
            doc,
            page,
        )

        # Explicit page break only BETWEEN source pages.
        if page_index < len(pdf) - 1:

            paragraph = doc.add_paragraph()

            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)

            paragraph.add_run().add_break()

    doc.save(out)

    pdf.close()

    print(
        "PAGEGRID V2 FINISHED",
        flush=True,
    )

    return out
