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


if __name__ == "__main__":
    unittest.main()
