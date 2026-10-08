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
            self.assertEqual(text.count("Total 500"), 1)
            self.assertEqual(text.count("Total 50"), 1)

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

    def test_original_pdf_is_not_modified(self):
        before = Path(self.source).read_bytes()
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        self.assertEqual(Path(self.source).read_bytes(), before)

    def test_replaced_text_is_not_extractable(self):
        replace_text(self.source, self.output, [
            TextReplacement(0, "Invoice 1234", "Invoice 12")
        ])
        with fitz.open(self.output) as doc:
            self.assertEqual(doc[0].search_for("Invoice 1234"), [])
            self.assertTrue(doc[0].search_for("Invoice 12"))

    def test_reject_out_of_range_page_without_output(self):
        with self.assertRaisesRegex(PdfEditError, "Page out of range"):
            replace_text(self.source, self.output, [
                TextReplacement(3, "Invoice 1234", "Invoice 12")
            ])
        self.assertFalse(Path(self.output).exists())

    def test_reject_overwriting_original(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.source, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])


if __name__ == "__main__":
    unittest.main()
