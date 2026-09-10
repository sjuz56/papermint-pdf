from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from papermint_auth import AuthError, AuthStore, verify_password


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="papermint-auth-tests-"))
        self.store = AuthStore(sqlite_path=self.temp_dir / "auth.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_login_and_session_lifecycle(self):
        user = self.store.register(" Person@Example.com ", "strong-password")
        self.assertEqual(user.email, "person@example.com")

        authenticated = self.store.authenticate("person@example.com", "strong-password")
        self.assertEqual(authenticated.id, user.id)

        token = self.store.create_session(user.id)
        session_user = self.store.user_for_session(token)
        self.assertIsNotNone(session_user)
        self.assertEqual(session_user.email, user.email)

        self.store.delete_session(token)
        self.assertIsNone(self.store.user_for_session(token))

    def test_duplicate_email_is_rejected_case_insensitively(self):
        self.store.register("person@example.com", "strong-password")
        with self.assertRaises(AuthError):
            self.store.register("PERSON@example.com", "another-password")

    def test_bad_credentials_are_rejected(self):
        self.store.register("person@example.com", "strong-password")
        with self.assertRaises(AuthError):
            self.store.authenticate("person@example.com", "wrong-password")

    def test_password_hash_does_not_contain_password(self):
        from papermint_auth import hash_password

        encoded = hash_password("strong-password")
        self.assertNotIn("strong-password", encoded)
        self.assertTrue(verify_password("strong-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))


if __name__ == "__main__":
    unittest.main()
