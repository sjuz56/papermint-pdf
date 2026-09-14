from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from starlette.requests import Request

import app as app_module
import papermint_limits as limits


class FakeRedis:
    def __init__(self):
        self.values: dict[str, int] = {}

    def eval(self, script, key_count, key, *arguments):
        del key_count
        current = self.values.get(key, 0)
        if "INCR" in script:
            limit = int(arguments[0])
            if current >= limit:
                return -1
            self.values[key] = current + 1
            return self.values[key]
        if current <= 1:
            self.values.pop(key, None)
            return 0
        self.values[key] = current - 1
        return self.values[key]


def request_for(ip: str = "203.0.113.10") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/convert",
        "headers": [(b"x-forwarded-for", ip.encode("ascii"))],
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "server": ("testserver", 80),
        "query_string": b"",
    })


def convert_arguments(content: bytes = b"pdf") -> dict:
    return {
        "request": request_for(),
        "tool": "compress",
        "files": [UploadFile(filename="input.pdf", file=BytesIO(content))],
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
    }


class FreeLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="papermint-limit-tests-"))
        self.original_tmp = app_module.TMP
        app_module.TMP = self.temp_dir

    def tearDown(self):
        app_module.TMP = self.original_tmp
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_anonymous_identifier_is_not_stored_in_key(self):
        key = limits.anonymous_quota_key("203.0.113.10")
        self.assertTrue(key.startswith("anon:"))
        self.assertNotIn("203.0.113.10", key)

    def test_client_cannot_choose_identity_with_forwarded_prefix(self):
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/api/convert",
            "headers": [(b"x-forwarded-for", b"spoofed, 203.0.113.10")],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        })
        self.assertEqual(app_module._request_network_identifier(request), "203.0.113.10")

    def test_counter_allows_two_tasks_and_release_refunds_one(self):
        redis = FakeRedis()
        timestamp = 1_700_000_000
        first = limits.reserve_free_task("anon:test", connection=redis, timestamp=timestamp)
        second = limits.reserve_free_task("anon:test", connection=redis, timestamp=timestamp)
        self.assertEqual((first["remaining"], second["remaining"]), (1, 0))
        with self.assertRaises(limits.FreeLimitReached):
            limits.reserve_free_task("anon:test", connection=redis, timestamp=timestamp)
        limits.release_free_task("anon:test", connection=redis, timestamp=timestamp)
        retried = limits.reserve_free_task("anon:test", connection=redis, timestamp=timestamp)
        self.assertEqual(retried["used"], 2)

    def test_conversion_returns_429_after_free_quota_is_used(self):
        with (
            patch.object(
                app_module,
                "_conversion_access",
                return_value=("anon:test", limits.FREE_UPLOAD_BYTES, limits.FREE_UPLOAD_BYTES),
            ),
            patch.object(
                app_module,
                "reserve_free_task",
                side_effect=limits.FreeLimitReached("The Free plan includes 2 PDF tasks per day."),
            ),
            patch.object(app_module, "enqueue_tool_job") as enqueue,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(app_module.convert_tool(**convert_arguments()))
        self.assertEqual(raised.exception.status_code, 429)
        enqueue.assert_not_called()

    def test_free_upload_over_10_mb_is_rejected_before_reservation(self):
        content = b"x" * (limits.FREE_UPLOAD_BYTES + 1)
        with (
            patch.object(
                app_module,
                "_conversion_access",
                return_value=("anon:test", limits.FREE_UPLOAD_BYTES, limits.FREE_UPLOAD_BYTES),
            ),
            patch.object(app_module, "reserve_free_task") as reserve,
            patch.object(app_module, "enqueue_tool_job") as enqueue,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(app_module.convert_tool(**convert_arguments(content)))
        self.assertEqual(raised.exception.status_code, 413)
        reserve.assert_not_called()
        enqueue.assert_not_called()

    def test_failed_enqueue_refunds_reserved_task(self):
        usage = {"used": 1, "limit": 2, "remaining": 1, "key": "anon:test"}
        with (
            patch.object(
                app_module,
                "_conversion_access",
                return_value=("anon:test", limits.FREE_UPLOAD_BYTES, limits.FREE_UPLOAD_BYTES),
            ),
            patch.object(app_module, "reserve_free_task", return_value=usage),
            patch.object(
                app_module,
                "enqueue_tool_job",
                side_effect=app_module.QueueUnavailable("queue unavailable"),
            ),
            patch.object(app_module, "release_free_task") as release,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(app_module.convert_tool(**convert_arguments()))
        self.assertEqual(raised.exception.status_code, 503)
        release.assert_called_once_with("anon:test")

    def test_pro_access_bypasses_free_quota(self):
        with (
            patch.object(app_module.AUTH_STORE, "user_for_session", return_value=object()),
            patch.object(app_module.AUTH_STORE, "plan_for_user", return_value="pro"),
        ):
            access = app_module._conversion_access(request_for())
        self.assertEqual(
            access,
            (None, app_module.MAX_UPLOAD_BYTES, app_module.MAX_REQUEST_BYTES),
        )

    def test_pdf_word_pipeline_reserves_free_task(self):
        usage = {"used": 1, "limit": 2, "remaining": 1, "key": "anon:test"}
        job_id = "pdf-word-limit-test"
        app_module.PDF_WORD_META.pop(job_id, None)
        with (
            patch.object(app_module, "cleanup_pdf_word_jobs"),
            patch.object(
                app_module,
                "_conversion_access",
                return_value=("anon:test", limits.FREE_UPLOAD_BYTES, limits.FREE_UPLOAD_BYTES),
            ),
            patch.object(app_module, "reserve_free_task", return_value=usage) as reserve,
            patch.object(
                app_module.PDF_WORD_MANAGER,
                "submit",
                return_value={"job_id": job_id, "queue_depth": 1, "preflight": {"pages": 1}},
            ),
        ):
            response = asyncio.run(
                app_module.pdf_word_start(
                    request_for(),
                    UploadFile(filename="input.pdf", file=BytesIO(b"pdf")),
                )
            )
        try:
            self.assertEqual(response["free_usage"]["remaining"], 1)
            reserve.assert_called_once_with("anon:test")
            self.assertEqual(
                app_module.PDF_WORD_META[job_id]["free_quota_key"],
                "anon:test",
            )
        finally:
            app_module.PDF_WORD_META.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
