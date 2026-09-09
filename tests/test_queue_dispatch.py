from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import papermint_job_queue as queue_module


class QueueDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="papermint-queue-tests-"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_remaining_tools_reach_their_engines(self):
        cases = [
            ("pdf-excel", "pdf_to_excel", {}),
            ("pdf-jpg", "pdf_to_jpg_zip", {}),
            ("ppt-pdf", "office_to_pdf", {}),
            ("excel-pdf", "office_to_pdf", {}),
            ("jpg-pdf", "images_to_pdf", {}),
            ("scan-pdf", "images_to_pdf", {}),
            ("html-pdf", "html_to_pdf", {}),
            ("pdfa", "pdf_to_pdfa", {}),
            ("repair", "repair_pdf", {}),
            ("ocr", "ocr_pdf", {}),
            ("compare", "compare_pdfs", {}),
            ("redact", "redact_pdf", {"redaction_text": "secret"}),
            ("crop", "crop_pdf", {"crop_margin": 7.0}),
        ]
        for tool, function_name, arguments in cases:
            with self.subTest(tool=tool):
                source_count = 2 if tool == "compare" else 1
                sources = []
                for index in range(source_count):
                    source = self.temp_dir / f"{tool}-{index}.input"
                    source.write_bytes(b"input")
                    sources.append(str(source))
                output = self.temp_dir / f"{tool}.output"
                with (
                    patch.object(queue_module, function_name, return_value={"tested": True}) as engine,
                    patch.object(queue_module, "_schedule_result_cleanup"),
                ):
                    result = queue_module.process_tool_job(
                        tool=tool,
                        sources=sources,
                        output=str(output),
                        **arguments,
                    )
                engine.assert_called_once()
                self.assertTrue(result["ok"])
                self.assertEqual(result["tool"], tool)
                self.assertEqual(result["report"], {"tested": True})


if __name__ == "__main__":
    unittest.main()
