from __future__ import annotations

import unittest
from unittest import mock

from app.payments.payfast import build_signature, build_checkout_params
from app.config import Settings


class PayFastHelpersTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            payfast_merchant_id="10000100",
            payfast_merchant_key="46f0cd694581a",
            payfast_passphrase="passphrase",
            payfast_notify_url="https://example.com/itn",
            payfast_return_url="https://example.com/return",
            payfast_cancel_url="https://example.com/cancel",
        )

    def test_build_signature_matches_reference(self):
        params = {
            "merchant_id": "10000100",
            "merchant_key": "46f0cd694581a",
            "amount": "100.00",
            "item_name": "Test Item",
        }
        signature = build_signature(params, "passphrase")
        self.assertEqual(signature, "b47f5cacb00a66ced2e6ea62d490aa17")

    def test_build_checkout_params_uses_settings(self):
        with mock.patch("app.payments.payfast.get_settings", return_value=self.settings):
            host, params = build_checkout_params(
                amount_minor=1000,
                currency="ZAR",
                item_name="Wallet Top-up",
                item_description=None,
                user_email="user@example.com",
                user_reference="user-123",
            )
        self.assertIn("signature", params)
        self.assertEqual(host, "https://sandbox.payfast.co.za/eng/process")
        self.assertEqual(params["amount"], "10.00")
        self.assertEqual(params["custom_str1"], "user-123")


if __name__ == "__main__":
    unittest.main()
