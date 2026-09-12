from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from papermint_auth import AuthStore


class StripeBillingApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="papermint-billing-tests-"))
        self.store = AuthStore(sqlite_path=self.directory / "auth.sqlite3")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def sign_in(self):
        user = self.store.register("payer@example.com", "strong-password")
        token = self.store.create_session(user.id)
        self.client.cookies.set(app_module.AUTH_COOKIE, token)
        return user

    @contextmanager
    def stripe_config(self):
        with (
            patch.object(app_module, "AUTH_STORE", self.store),
            patch.object(app_module, "STRIPE_SECRET_KEY", "sk_test_fake"),
            patch.object(
                app_module,
                "STRIPE_PRICE_IDS",
                {"monthly": "price_monthly", "yearly": "price_yearly"},
            ),
        ):
            yield

    def test_checkout_requires_sign_in(self):
        with patch.object(app_module, "AUTH_STORE", self.store):
            response = self.client.post(
                "/api/billing/checkout",
                json={"plan": "monthly", "accepted_terms": True},
            )
        self.assertEqual(response.status_code, 401)

    def test_checkout_requires_terms_acceptance(self):
        self.sign_in()
        with self.stripe_config():
            response = self.client.post(
                "/api/billing/checkout",
                json={"plan": "monthly", "accepted_terms": False},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Terms", response.json()["detail"])

    def test_checkout_creates_monthly_subscription_session(self):
        user = self.sign_in()
        with (
            self.stripe_config(),
            patch.object(
                app_module.stripe.checkout.Session,
                "create",
                return_value={"url": "https://checkout.stripe.com/c/pay/test"},
            ) as create_session,
        ):
            response = self.client.post(
                "/api/billing/checkout",
                json={"plan": "monthly", "accepted_terms": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://checkout.stripe.com/c/pay/test")
        parameters = create_session.call_args.kwargs
        self.assertEqual(parameters["mode"], "subscription")
        self.assertEqual(parameters["line_items"][0]["price"], "price_monthly")
        self.assertEqual(parameters["metadata"]["user_id"], user.id)
        self.assertEqual(parameters["metadata"]["terms_version"], "2026-09-12")
        self.assertTrue(parameters["metadata"]["terms_accepted_at"].isdigit())
        self.assertEqual(parameters["customer_email"], user.email)

    def test_webhook_activates_and_cancels_subscription(self):
        user = self.sign_in()
        active_subscription = {
            "id": "sub_test_123",
            "customer": "cus_test_123",
            "status": "active",
            "current_period_end": 4_102_444_800,
            "metadata": {"user_id": user.id, "plan": "monthly"},
            "items": {"data": [{"price": {"id": "price_monthly"}}]},
        }
        deleted_subscription = {
            **active_subscription,
            "status": "canceled",
        }

        for event_type, subscription, expected_plan in (
            ("customer.subscription.updated", active_subscription, "pro"),
            ("customer.subscription.deleted", deleted_subscription, "free"),
        ):
            with (
                self.stripe_config(),
                patch.object(app_module, "STRIPE_WEBHOOK_SECRET", "whsec_test"),
                patch.object(
                    app_module.stripe.Webhook,
                    "construct_event",
                    return_value={
                        "type": event_type,
                        "data": {"object": subscription},
                    },
                ),
            ):
                response = self.client.post(
                    "/api/billing/webhook",
                    content=b"{}",
                    headers={"stripe-signature": "t=1,v1=test"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.store.plan_for_user(user), expected_plan)

        billing = self.store.billing_for_user(user.id)
        self.assertEqual(billing.stripe_customer_id, "cus_test_123")

    def test_customer_portal_is_bound_to_signed_in_customer(self):
        user = self.sign_in()
        self.store.set_billing_customer(
            user.id,
            customer_id="cus_test_123",
            subscription_id="sub_test_123",
        )
        with (
            self.stripe_config(),
            patch.object(
                app_module.stripe.billing_portal.Session,
                "create",
                return_value={"url": "https://billing.stripe.com/p/session/test"},
            ) as create_portal,
        ):
            response = self.client.post("/api/billing/portal")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://billing.stripe.com/p/session/test")
        self.assertEqual(create_portal.call_args.kwargs["customer"], "cus_test_123")


if __name__ == "__main__":
    unittest.main()
