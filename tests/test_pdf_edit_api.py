"""Integration tests for the feature-flagged experimental PDF editor API."""
import io
import json
import os
import unittest
from unittest.mock import patch

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient

from papermint_edit_api import router


def sample_pdf(rotated=False):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 100), "Invoice 1234", fontsize=12)
        page.insert_text((72, 140), "Total 500", fontsize=12)
        if rotated:
            page.set_rotation(90)
        return doc.tobytes()


class PdfEditApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_disabled_endpoints_return_404(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "0"}):
            response = self.client.post(
                "/api/experimental/inspect-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
            )
            self.assertEqual(response.status_code, 404)

    def test_inspect_then_edit_pdf(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            inspected = self.client.post(
                "/api/experimental/inspect-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
            )
            self.assertEqual(inspected.status_code, 200, inspected.text)
            self.assertEqual(inspected.headers.get("cache-control"), "no-store")
            pages = inspected.json()["pages"]
            self.assertEqual(len(pages), 1)
            self.assertTrue(pages[0]["image"].startswith("data:image/png;base64,"))
            self.assertIn("Invoice 1234", [s["text"] for s in pages[0]["spans"]])
            changed = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": json.dumps([
                    {"page": 0, "old_text": "Invoice 1234",
                     "new_text": "Invoice 12", "occurrence": 0}
                ])},
            )
            self.assertEqual(changed.status_code, 200, changed.text[:500])
            self.assertEqual(changed.headers.get("cache-control"), "no-store")
            with fitz.open(stream=changed.content, filetype="pdf") as doc:
                text = doc[0].get_text()
                self.assertIn("Invoice 12", text)
                self.assertIn("Total 500", text)
                self.assertNotIn("Invoice 1234", text)

    def test_longer_unicode_edit_returns_verified_pdf_and_block_geometry(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": json.dumps([
                    {"page": 0, "old_text": "Invoice 1234", "new_text": "Příjem 12\nDruhý řádek", "occurrence": 0}
                ])},
            )
            self.assertEqual(response.status_code, 200, response.text[:500])
            boxes = json.loads(response.headers["x-pdfaspect-edit-boxes"])
            self.assertEqual(len(boxes), 1)
            self.assertEqual(boxes[0]["page"], 0)
            self.assertGreater(boxes[0]["bbox"][3] - boxes[0]["bbox"][1], 25)
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                self.assertIn("Příjem12Druhýřádek", "".join(doc[0].get_text().split()))
                self.assertIn("Total 500", doc[0].get_text())

    def test_rotated_preview_rejected_early(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/inspect-pdf",
                files={"file": ("rotated.pdf", io.BytesIO(sample_pdf(rotated=True)), "application/pdf")},
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn("rotated", response.json()["detail"])

    def test_reject_malformed_edit_payload(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": "not valid json"},
            )
            self.assertEqual(response.status_code, 422)

    def test_reject_oversized_edit_instructions(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": "x" * 100001},
            )
            self.assertEqual(response.status_code, 413)

    def test_reject_client_supplied_font_path(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": json.dumps([
                    {"page": 0, "old_text": "Invoice 1234", "new_text": "Invoice 12",
                     "occurrence": 0, "font_file": "/etc/passwd"}
                ])},
            )
            self.assertEqual(response.status_code, 422)

    def test_inspected_width_and_bold_export_keep_the_sentence_on_one_line(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            inspected = self.client.post(
                "/api/experimental/inspect-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
            )
            span = next(s for s in inspected.json()["pages"][0]["spans"] if s["text"] == "Invoice 1234")
            width = span["edit_bbox"][2] - span["edit_bbox"][0]
            self.assertGreater(width, span["bbox"][2] - span["bbox"][0])
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": json.dumps([{"page": 0, "old_text": span["text"],
                    "new_text": "Příjem za celý minulý měsíc", "occurrence": 0, "bold": True, "width": width}])},
            )
            self.assertEqual(response.status_code, 200, response.text[:500])
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                first = next(s for b in doc[0].get_text("dict")["blocks"]
                             for line in b.get("lines", []) for s in line["spans"] if s["text"].startswith("Příjem"))
                self.assertEqual(first["text"], "Příjem za celý minulý měsíc")
                self.assertTrue(first["flags"] & 16)

    def test_reject_invalid_formatting_and_excessive_width(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            for options in ({"bold": "true"}, {"bold": None}, {"width": True}, {"width": "400"},
                            {"width": float("inf")}, {"width": 2000}):
                with self.subTest(options=options):
                    response = self.client.post(
                        "/api/experimental/edit-pdf",
                        files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                        data={"changes": json.dumps([{"page": 0, "old_text": "Invoice 1234",
                            "new_text": "Invoice 12", "occurrence": 0, **options}])},
                    )
                    self.assertEqual(response.status_code, 422, response.text[:500])

    def test_font_catalog_and_server_owned_font_downloads(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            catalog = self.client.get("/api/experimental/edit-pdf-fonts")
            self.assertEqual(catalog.status_code, 200)
            fonts = catalog.json()["fonts"]
            self.assertTrue(fonts)
            for family in fonts:
                for variant in ("regular", "bold", "italic", "bold-italic"):
                    with self.subTest(family=family["id"], variant=variant):
                        response = self.client.get("/api/experimental/edit-pdf-fonts/" + family["id"] + "/" + variant)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers["content-type"], "font/ttf")
                        self.assertGreater(len(response.content), 1000)
                        font = fitz.Font(fontbuffer=response.content)
                        self.assertTrue(font.has_glyph(ord("ř")))

    def test_font_endpoints_are_feature_flagged_and_reject_unknown_ids(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "0"}):
            self.assertEqual(self.client.get("/api/experimental/edit-pdf-fonts").status_code, 404)
            self.assertEqual(self.client.get("/api/experimental/edit-pdf-fonts/dejavu-sans/regular").status_code, 404)
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            self.assertEqual(self.client.get("/api/experimental/edit-pdf-fonts/not-a-font/regular").status_code, 404)
            self.assertEqual(self.client.get("/api/experimental/edit-pdf-fonts/dejavu-sans/not-a-variant").status_code, 404)

    def test_font_family_size_italic_and_color_only_export(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf",
                files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                data={"changes": json.dumps([{"page": 0, "old_text": "Invoice 1234", "new_text": "Invoice 1234",
                    "occurrence": 0, "width": 400, "font_family": "dejavu-serif", "font_size": 18,
                    "bold": True, "italic": True, "color": 0x2456A8}])},
            )
            self.assertEqual(response.status_code, 200, response.text[:500])
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                span = next(s for b in doc[0].get_text("dict")["blocks"]
                            for line in b.get("lines", []) for s in line["spans"] if s["text"] == "Invoice 1234")
                self.assertEqual(span["font"], "DejaVuSerif-BoldItalic")
                self.assertAlmostEqual(span["size"], 18)
                self.assertEqual(span["color"], 0x2456A8)

    def test_reject_untrusted_font_and_style_payloads(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            for options in ({"font_family": "../../etc/passwd"}, {"font_family": None},
                            {"italic": 1}, {"font_size": float("inf")}, {"font_size": 200},
                            {"color": "#2456A8"}, {"color": -1}):
                with self.subTest(options=options):
                    response = self.client.post(
                        "/api/experimental/edit-pdf",
                        files={"file": ("test.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
                        data={"changes": json.dumps([{"page": 0, "old_text": "Invoice 1234",
                            "new_text": "Invoice 12", "occurrence": 0, **options}])},
                    )
                    self.assertEqual(response.status_code, 422, response.text[:500])

    def test_copied_original_font_can_be_exported_from_another_block(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), "Serif source", fontsize=16, fontname="tiit")
            page.insert_text((72, 180), "Target", fontsize=12)
            source = doc.tobytes()
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            response = self.client.post(
                "/api/experimental/edit-pdf", files={"file": ("test.pdf", source, "application/pdf")},
                data={"changes": json.dumps([{"page": 0, "old_text": "Target", "new_text": "Copied title",
                    "occurrence": 0, "width": 400, "font_size": 16, "italic": True, "color": 0x2456A8,
                    "font_source": {"page": 0, "old_text": "Serif source", "occurrence": 0}}])})
            self.assertEqual(response.status_code, 200, response.text[:500])
            with fitz.open(stream=response.content, filetype="pdf") as doc:
                span = next(s for b in doc[0].get_text("dict")["blocks"]
                            for line in b.get("lines", []) for s in line["spans"] if s["text"] == "Copied title")
                self.assertIn(span["font"], {"Times-Italic", "NimbusRoman-Italic"})
                self.assertAlmostEqual(span["size"], 16)
                self.assertEqual(span["color"], 0x2456A8)

    def test_reject_untrusted_copied_font_payloads(self):
        with patch.dict(os.environ, {"PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF": "1"}):
            for source in (None, "../../etc/passwd", {"page": 0, "old_text": "Invoice 1234", "occurrence": 0,
                           "font_file": "../../etc/passwd"}, {"page": 0, "old_text": "Missing", "occurrence": 0},
                           {"page": 0, "old_text": "Invoice 1234", "occurrence": True}):
                with self.subTest(source=source):
                    response = self.client.post(
                        "/api/experimental/edit-pdf", files={"file": ("test.pdf", sample_pdf(), "application/pdf")},
                        data={"changes": json.dumps([{"page": 0, "old_text": "Total 500", "new_text": "Copied",
                            "occurrence": 0, "width": 400, "font_source": source}])})
                    self.assertEqual(response.status_code, 422, response.text[:500])


if __name__ == "__main__":
    unittest.main()
