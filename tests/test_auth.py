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

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        user = self.store.register("reset@example.com", "strong-password")
        session = self.store.create_session(user.id)
        token = self.store.create_password_reset("reset@example.com")
        self.assertIsNotNone(token)

        self.store.reset_password(token, "new-strong-password")
        self.assertEqual(
            self.store.authenticate("reset@example.com", "new-strong-password").id,
            user.id,
        )
        self.assertIsNone(self.store.user_for_session(session))
        with self.assertRaises(AuthError):
            self.store.reset_password(token, "another-password")

    def test_email_verification_is_single_use(self):
        user = self.store.register("verify@example.com", "strong-password")
        self.assertFalse(self.store.email_is_verified(user.id))

        token = self.store.create_email_verification(user.id)
        verified = self.store.verify_email(token)
        self.assertEqual(verified.email, user.email)
        self.assertTrue(self.store.email_is_verified(user.id))

        with self.assertRaises(AuthError):
            self.store.verify_email(token)

    def test_subscription_and_ai_usage_limits(self):
        user = self.store.register("pro@example.com", "strong-password")
        self.assertEqual(self.store.plan_for_user(user), "free")

        self.store.set_subscription(user.id, "monthly", current_period_end=4_102_444_800)
        self.assertEqual(self.store.plan_for_user(user), "pro")

        usage = self.store.consume_ai_usage(
            user.id,
            documents=1,
            document_limit=1,
            question_limit=3,
        )
        self.assertEqual((usage.documents, usage.questions), (1, 0))

        with self.assertRaises(AuthError):
            self.store.consume_ai_usage(
                user.id,
                documents=1,
                document_limit=1,
                question_limit=3,
            )

        refunded = self.store.refund_ai_usage(user.id, documents=1)
        self.assertEqual(refunded.documents, 0)

    def test_stripe_customer_mapping(self):
        user = self.store.register("billing@example.com", "strong-password")
        self.store.set_billing_customer(
            user.id,
            customer_id="cus_test_123",
            subscription_id="sub_test_123",
        )

        billing = self.store.billing_for_user(user.id)
        self.assertIsNotNone(billing)
        self.assertEqual(billing.stripe_customer_id, "cus_test_123")
        self.assertEqual(billing.stripe_subscription_id, "sub_test_123")
        self.assertEqual(
            self.store.user_id_for_billing(customer_id="cus_test_123"),
            user.id,
        )
        self.assertEqual(
            self.store.user_id_for_billing(subscription_id="sub_test_123"),
            user.id,
        )


if __name__ == "__main__":
    unittest.main()
