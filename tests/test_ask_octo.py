from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import fitz

import app as app_module
from papermint_ai import AskOctoError, AskOctoSessions, ExtractedDocument, extract_pdf_text
from papermint_auth import AuthStore


class AskOctoApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="ask-octo-tests-"))
        self.store = AuthStore(sqlite_path=self.directory / "auth.sqlite3")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def sign_in(self, *, pro: bool) -> str:
        user = self.store.register(
            "pro@example.com" if pro else "free@example.com",
            "strong-password",
        )
        if pro:
            self.store.set_subscription(user.id, "monthly", current_period_end=4_102_444_800)
        token = self.store.create_session(user.id)
        self.client.cookies.set(app_module.AUTH_COOKIE, token)
        return user.id

    def test_ask_octo_is_paid_only(self):
        self.sign_in(pro=False)
        with (
            patch.object(app_module, "AUTH_STORE", self.store),
            patch.object(app_module, "extract_pdf_text") as extractor,
        ):
            response = self.client.post(
                "/api/ai/document",
                files={"file": ("document.pdf", b"fake", "application/pdf")},
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("paid plan", response.json()["detail"])
        extractor.assert_not_called()

    def test_ask_octo_requires_sign_in_before_processing(self):
        with (
            patch.object(app_module, "AUTH_STORE", self.store),
            patch.object(app_module, "extract_pdf_text") as extractor,
        ):
            response = self.client.post(
                "/api/ai/document",
                files={"file": ("document.pdf", b"fake", "application/pdf")},
            )
        self.assertEqual(response.status_code, 401)
        extractor.assert_not_called()

    def test_sessions_are_bound_to_the_account(self):
        sessions = AskOctoSessions()
        session_id = sessions.create("owner", ["private text"])
        with self.assertRaises(AskOctoError):
            sessions.get(session_id, "somebody-else")

    def test_text_pdf_is_extracted_locally(self):
        source = self.directory / "text.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "This is enough searchable text for Ask Octo extraction.")
        document.save(source)
        document.close()

        result = extract_pdf_text(source)
        self.assertIn("searchable text", result.pages[0])
        self.assertEqual(result.ocr_pages, [])

    def test_paid_user_can_summarize_and_ask_three_questions(self):
        user_id = self.sign_in(pro=True)
        sessions = AskOctoSessions()
        extracted = ExtractedDocument(
            pages=["Invoice total is EUR 120.", "Payment is due on Friday."],
            ocr_pages=[2],
        )
        with (
            patch.object(app_module, "AUTH_STORE", self.store),
            patch.object(app_module, "TMP", self.directory),
            patch.object(app_module, "AI_SESSIONS", sessions),
            patch.object(app_module, "ai_is_configured", return_value=True),
            patch.object(app_module, "extract_pdf_text", return_value=extracted),
            patch.object(
                app_module,
                "summarize_document",
                return_value="Total EUR 120 [p. 1]. Due Friday [p. 2].",
            ),
            patch.object(
                app_module,
                "answer_question",
                return_value=("EUR 120 [p. 1].", [1]),
            ),
        ):
            summary = self.client.post(
                "/api/ai/document",
                files={"file": ("document.pdf", b"fake", "application/pdf")},
                data={"ocr_language": "eng", "response_language": "en"},
            )
            self.assertEqual(summary.status_code, 200)
            data = summary.json()
            self.assertTrue(data["summary"].endswith("[p. 2]."))
            self.assertEqual(data["ocr_pages"], [2])
            self.assertEqual(data["questions_remaining"], 3)

            for remaining in (2, 1, 0):
                answer = self.client.post(
                    "/api/ai/question",
                    json={
                        "session_id": data["session_id"],
                        "question": "What is the total?",
                        "response_language": "en",
                    },
                )
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.json()["questions_remaining"], remaining)
                self.assertEqual(answer.json()["source_pages"], [1])

            blocked = self.client.post(
                "/api/ai/question",
                json={
                    "session_id": data["session_id"],
                    "question": "One more?",
                    "response_language": "en",
                },
            )
            self.assertEqual(blocked.status_code, 400)

        usage = self.store.ai_usage(user_id)
        self.assertEqual((usage.documents, usage.questions), (1, 3))


if __name__ == "__main__":
    unittest.main()
