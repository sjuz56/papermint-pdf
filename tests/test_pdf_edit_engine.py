"""Regression tests for the experimental direct text-edit engine."""
import tempfile
import os
import unittest
from pathlib import Path

import fitz

from papermint_edit_engine import PdfEditError, TextReplacement, inspect_text, replace_text


class TestPdfTextEdit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = str(Path(self.temp.name) / "source.pdf")
        self.output = str(Path(self.temp.name) / "edited.pdf")
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), "Invoice 1234", fontsize=12)
            page.insert_text((72, 140), "Total 500", fontsize=12)
            doc.save(self.source)

    def test_inspect_returns_selectable_spans(self):
        spans = inspect_text(self.source, 0)
        self.assertIn("Invoice 1234", [span["text"] for span in spans])

    def test_replace_shorter_text_and_preserve_other_lines(self):
        result = replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        self.assertEqual(result["replacements"], 1)
        with fitz.open(self.output) as doc:
            text = doc[0].get_text()
            self.assertIn("Invoice 12", text)
            self.assertNotIn("Invoice 1234", text)
            self.assertIn("Total 500", text)

    def test_two_distinct_spans_can_be_edited_together(self):
        result = replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12"),
            TextReplacement(0, "Total 500", "Total 50"),
        ])
        self.assertEqual(result["replacements"], 2)
        with fitz.open(self.output) as doc:
            text = doc[0].get_text()
            self.assertIn("Invoice 12", text)
            self.assertIn("Total 50", text)
            self.assertNotIn("Invoice 1234", text)

    def test_duplicate_text_occurrence_selects_second_span(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((72, 180), "Total 500", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        replace_text(self.source, self.output, [
            TextReplacement(0, "Total 500", "Total 50", occurrence=1)
        ])
        with fitz.open(self.output) as doc:
            text = doc[0].get_text()
            lines = text.splitlines()
            self.assertEqual(lines.count("Total 500"), 1)
            self.assertEqual(lines.count("Total 50"), 1)

    def test_replacement_matches_correct_duplicate_location(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((72, 180), "Total 500", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        replace_text(self.source, self.output, [
            TextReplacement(0, "Total 500", "Total 50", occurrence=1)
        ])
        with fitz.open(self.output) as doc:
            first = doc[0].get_textbox(fitz.Rect(65, 125, 180, 150))
            second = doc[0].get_textbox(fitz.Rect(65, 165, 180, 190))
            self.assertIn("Total 500", first)
            self.assertIn("Total 50", second)

    def test_source_pdf_is_not_modified(self):
        original = Path(self.source).read_bytes()
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        self.assertEqual(Path(self.source).read_bytes(), original)

    def test_unrelated_page_remains_intact(self):
        with fitz.open(self.source) as doc:
            second = doc.new_page()
            second.insert_text((72, 100), "Do not change", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        with fitz.open(self.output) as doc:
            self.assertEqual(len(doc), 2)
            self.assertIn("Do not change", doc[1].get_text())

    def test_reject_out_of_range_page_without_writing_output(self):
        with self.assertRaisesRegex(PdfEditError, "Page out of range"):
            replace_text(self.source, self.output, [
                TextReplacement(99, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_missing_span_without_writing_output(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "not there", "replacement")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_text_overflow(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice number one two three four five six seven")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_overlapping_edits(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice 12"),
                TextReplacement(0, "Invoice 1234", "Invoice 13"),
            ])
        self.assertFalse(Path(self.output).exists())

    def test_unicode_is_embedded_without_a_client_font_path(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Příjem 12")
        ])
        with fitz.open(self.output) as doc:
            self.assertIn("Příjem 12", doc[0].get_text())
            self.assertNotIn("Invoice 1234", doc[0].get_text())

    def test_unicode_with_optional_font(self):
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ]
        font = next((p for p in candidates if p.is_file()), None)
        if font is None:
            self.skipTest("No Unicode TTF installed in test environment")
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Příjem 12", font_file=str(font))
        ])
        with fitz.open(self.output) as doc:
            self.assertIn("Příjem 12", doc[0].get_text())

    def test_reject_neighbouring_text_overlap(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((73, 100), "OVERLAP", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with self.assertRaisesRegex(PdfEditError, "neighbouring text"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_multiline_edit_preserves_neighbour_and_first_baseline(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Line\nnext")
        ])
        with fitz.open(self.output) as doc:
            self.assertIn("Line\nnext", doc[0].get_text())
            self.assertIn("Total 500", doc[0].get_text())
            spans = [s for b in doc[0].get_text("dict")["blocks"]
                     for line in b.get("lines", []) for s in line["spans"]]
            first = next(s for s in spans if s["text"] == "Line")
            self.assertAlmostEqual(first["origin"][1], 100, places=2)
            self.assertAlmostEqual(first["size"], 12, places=2)

    def test_replaced_text_is_not_extractable(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        with fitz.open(self.output) as doc:
            self.assertEqual(doc[0].search_for("Invoice 1234"), [])
            self.assertTrue(doc[0].search_for("Invoice 12"))

    def test_reject_boolean_page_number(self):
        with self.assertRaisesRegex(PdfEditError, "must be integers"):
            replace_text(self.source, self.output, [
                TextReplacement(True, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_non_string_replacement(self):
        with self.assertRaisesRegex(PdfEditError, "must be strings"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", 123)
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_negative_occurrence(self):
        with self.assertRaisesRegex(PdfEditError, "not found"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice 12", occurrence=-1)
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_control_characters_without_output(self):
        with self.assertRaisesRegex(PdfEditError, "Control characters"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice\t12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_replacement_is_searchable_after_save(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        with fitz.open(self.output) as doc:
            self.assertTrue(doc[0].search_for("Invoice 12"))
            self.assertFalse(doc[0].search_for("Invoice 1234"))

    def test_wrap_long_word_without_losing_characters(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((72, 200), "iii", fontname="cour", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        replace_text(self.source, self.output, [
            TextReplacement(0, "iii", "iiiiiiiiiiiiiiiiiiii")
        ])
        with fitz.open(self.output) as doc:
            text = doc[0].get_textbox(fitz.Rect(70, 185, 96, 350))
            self.assertEqual("".join(text.split()), "iiiiiiiiiiiiiiiiiiii")
            self.assertGreater(len(text.splitlines()), 1)

    def test_longer_sentence_wraps_in_the_original_width(self):
        result = replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice number 12")
        ])
        with fitz.open(self.output) as doc:
            text = doc[0].get_textbox(fitz.Rect(result["edit_boxes"][0]["bbox"]) + (-1, -1, 1, 1))
            self.assertEqual("".join(text.split()), "Invoicenumber12")
            self.assertGreater(len(text.splitlines()), 1)
            self.assertIn("Total 500", doc[0].get_text())

    def test_adjacent_paragraph_lines_are_one_editable_block(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((72, 220), "One long line\nAnother line", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        self.assertIn("One long line\nAnother line", [b["text"] for b in inspect_text(self.source, 0)])
        replace_text(self.source, self.output, [
            TextReplacement(0, "One long line\nAnother line", "New paragraph\nNext line")
        ])
        with fitz.open(self.output) as doc:
            self.assertNotIn("One long line", doc[0].get_text())
            text = doc[0].get_textbox(fitz.Rect(70, 200, 150, 290))
            self.assertEqual("".join(text.split()), "NewparagraphNextline")

    def test_original_coloured_background_is_preserved(self):
        with fitz.open(self.source) as doc:
            doc[0].draw_rect(fitz.Rect(65, 80, 180, 110), fill=(0.2, 0.7, 0.4), color=None, overlay=False)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with fitz.open(self.source) as doc:
            before = doc[0].get_pixmap().pixel(80, 88)
        replace_text(self.source, self.output, [TextReplacement(0, "Invoice 1234", "Invoice 12")])
        with fitz.open(self.output) as doc:
            self.assertEqual(doc[0].get_pixmap().pixel(80, 88), before)
            self.assertNotEqual(before, (255, 255, 255))

    def test_paragraph_from_separate_pdf_text_objects_is_editable_together(self):
        with fitz.open(self.source) as doc:
            for index, text in enumerate(("First paragraph line", "Second line", "Third line")):
                doc[0].insert_text((72, 240 + index * 22), text, fontsize=14)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        paragraph = "First paragraph line\nSecond line\nThird line"
        self.assertIn(paragraph, [b["text"] for b in inspect_text(self.source, 0)])
        replace_text(self.source, self.output, [TextReplacement(0, paragraph, "One new paragraph")])
        with fitz.open(self.output) as doc:
            self.assertIn("Onenewparagraph", "".join(doc[0].get_text().split()))
            self.assertNotIn("Third line", doc[0].get_text())

    def test_empty_replacement_deletes_text_and_preserves_neighbour(self):
        replace_text(self.source, self.output, [TextReplacement(0, "Invoice 1234", "")])
        with fitz.open(self.output) as doc:
            self.assertNotIn("Invoice 1234", doc[0].get_text())
            self.assertIn("Total 500", doc[0].get_text())

    def test_font_missing_glyphs_uses_disclosed_unicode_fallback(self):
        result = replace_text(self.source, self.output, [TextReplacement(0, "Invoice 1234", "漢字")])
        self.assertTrue(result["font_substitutions"])
        with fitz.open(self.output) as doc:
            self.assertIn("漢字", doc[0].get_text())

    def test_existing_redactions_are_not_applied_as_a_side_effect(self):
        with fitz.open(self.source) as doc:
            doc[0].add_redact_annot(fitz.Rect(65, 125, 180, 150))
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with self.assertRaisesRegex(PdfEditError, "existing redaction"):
            replace_text(self.source, self.output, [TextReplacement(0, "Invoice 1234", "Invoice 12")])
        self.assertFalse(Path(self.output).exists())

    def test_embedded_bold_italic_font_and_colour_are_retained(self):
        candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf")]
        if os.getenv("CODEX_PRIMARY_RUNTIME_ROOT"):
            candidates.append(Path(os.environ["CODEX_PRIMARY_RUNTIME_ROOT"]) /
                              "dependencies/native/libreoffice-headless/libreoffice/share/fonts/truetype/DejaVuSans-BoldOblique.ttf")
        font = next((p for p in candidates if p.is_file()), None)
        if font is None:
            self.skipTest("No embedded font fixture available")
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname="fixture", fontfile=str(font))
            page.insert_text((72, 100), "Original heading", fontname="fixture", fontsize=16, color=(0, 0.4, 0.2))
            doc.save(self.source)
        result = replace_text(self.source, self.output, [TextReplacement(0, "Original heading", "Příjem 12")])
        self.assertEqual(result["font_substitutions"], [])
        with fitz.open(self.output) as doc:
            span = doc[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
            self.assertEqual(span["text"], "Příjem 12")
            self.assertEqual(span["font"], "DejaVuSans-BoldOblique")
            self.assertEqual(span["flags"] & 18, 18)
            self.assertEqual(span["color"], 0x006633)
            self.assertAlmostEqual(span["size"], 16)

    def test_reject_rotated_page_without_output(self):
        with fitz.open(self.source) as doc:
            doc[0].set_rotation(90)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with self.assertRaisesRegex(PdfEditError, "rotated PDF pages"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_success_does_not_leave_temporary_files(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        self.assertTrue(Path(self.output).is_file())
        self.assertEqual(
            list(Path(self.temp.name).glob(".pdfaspect-edit-*.pdf")), []
        )

    def test_failed_edit_preserves_existing_output(self):
        Path(self.output).write_bytes(b"existing result")
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "A much longer replacement that will not fit")
            ])
        self.assertEqual(Path(self.output).read_bytes(), b"existing result")

    def test_failed_edit_preserves_existing_output(self):
        original_output = b"previous successful output"
        Path(self.output).write_bytes(original_output)
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "This text is far too long for the original box")
            ])
        self.assertEqual(Path(self.output).read_bytes(), original_output)

    def test_replacement_of_same_text_does_not_corrupt_pdf(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 1234")
        ])
        with fitz.open(self.output) as doc:
            self.assertIn("Invoice 1234", doc[0].get_text())
            self.assertIn("Total 500", doc[0].get_text())

    def test_reject_rotated_page_without_writing_output(self):
        with fitz.open(self.source) as doc:
            doc[0].set_rotation(90)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with self.assertRaisesRegex(PdfEditError, "rotated"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_overwriting_original(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.source, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])


if __name__ == "__main__":
    unittest.main()
