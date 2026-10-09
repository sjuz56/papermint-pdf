"""Regression tests for the experimental direct text-edit engine."""
import tempfile
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

    def test_unicode_requires_embedded_font(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Příjem 12")
            ])
        self.assertFalse(Path(self.output).exists())

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

    def test_reject_multiline_edit(self):
        with self.assertRaisesRegex(PdfEditError, "Multiline"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "Invoice 1234", "Line\nnext")
            ])
        self.assertFalse(Path(self.output).exists())

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

    def test_reject_longer_text_even_if_it_contains_original(self):
        with fitz.open(self.source) as doc:
            doc[0].insert_text((72, 200), "iii", fontname="cour", fontsize=12)
            doc.save(self.source + ".tmp")
        Path(self.source + ".tmp").replace(self.source)
        with self.assertRaisesRegex(PdfEditError, "wider than original"):
            replace_text(self.source, self.output, [
                TextReplacement(0, "iii", "iiiiiiiiiiiiiiiiiiii")
            ])
        self.assertFalse(Path(self.output).exists())

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

    def test_reject_overwriting_original(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.source, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])


if __name__ == "__main__":
    unittest.main()
