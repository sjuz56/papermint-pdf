from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

import app as app_module


class RemainingToolsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="papermint-api-tests-"))
        self.original_tmp = app_module.TMP
        app_module.TMP = self.temp_dir

    def tearDown(self):
        app_module.TMP = self.original_tmp
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _fake_enqueue(tool, sources, output, **kwargs):
        return {"job_id": f"job-{tool}", "status": "queued", "queue_position": 1}

    def _post(self, tool, filenames, **data):
        files = [UploadFile(filename=name, file=BytesIO(b"test-content")) for name in filenames]
        arguments = {
            "tool": tool,
            "files": files,
            "pages": "",
            "rotation": 90,
            "password": "",
            "password_confirm": "",
            "text": "",
            "signature_page": 1,
            "signature_x": 30,
            "signature_y": 30,
            "page_number_start": 1,
            "page_number_position": "bottom-center",
            "page_number_format": "number",
            "page_number_skip_first": False,
            "margin": 10.0,
            "ocr_language": "eng",
            **data,
        }
        with patch.object(app_module, "enqueue_tool_job", side_effect=self._fake_enqueue) as enqueue:
            response = asyncio.run(app_module.convert_tool(**arguments))
        self.assertEqual(response["status"], "queued")
        self.assertEqual(enqueue.call_args.args[0], tool)
        return enqueue.call_args

    def test_every_remaining_tool_is_routed(self):
        cases = [
            ("pdf-excel", ["input.pdf"], {}),
            ("pdf-jpg", ["input.pdf"], {}),
            ("ppt-pdf", ["slides.pptx"], {}),
            ("excel-pdf", ["sheet.xlsx"], {}),
            ("jpg-pdf", ["one.jpg", "two.png"], {}),
            ("html-pdf", ["page.html"], {}),
            ("pdfa", ["input.pdf"], {}),
            ("repair", ["input.pdf"], {}),
            ("scan-pdf", ["one.jpg", "two.png"], {}),
            ("ocr", ["input.pdf"], {"ocr_language": "ces"}),
            ("compare", ["a.pdf", "b.pdf"], {}),
            ("redact", ["input.pdf"], {"text": "secret"}),
            ("crop", ["input.pdf"], {"margin": 8.5}),
        ]
        for tool, filenames, data in cases:
            with self.subTest(tool=tool):
                call = self._post(tool, filenames, **data)
                if tool == "redact":
                    self.assertEqual(call.kwargs["redaction_text"], "secret")
                if tool == "crop":
                    self.assertEqual(call.kwargs["crop_margin"], 8.5)
                if tool == "ocr":
                    self.assertEqual(call.kwargs["ocr_language"], "ces")

    def test_redaction_requires_text(self):
        with self.assertRaises(HTTPException) as raised:
            self._post("redact", ["input.pdf"])
        self.assertEqual(raised.exception.status_code, 400)

    def test_compare_requires_exactly_two_files(self):
        with self.assertRaises(HTTPException) as raised:
            self._post("compare", ["input.pdf"])
        self.assertEqual(raised.exception.status_code, 400)

    def test_ocr_rejects_unknown_language(self):
        with self.assertRaises(HTTPException) as raised:
            self._post("ocr", ["input.pdf"], ocr_language="unknown")
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
