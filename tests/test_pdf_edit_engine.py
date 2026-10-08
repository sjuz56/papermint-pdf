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

    def test_reject_overwriting_original(self):
        with self.assertRaises(PdfEditError):
            replace_text(self.source, self.source, [
                TextReplacement(0, "Invoice 1234", "Invoice 12")
            ])


if __name__ == "__main__":
    unittest.main()
