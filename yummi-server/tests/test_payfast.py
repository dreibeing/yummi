from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from unittest import IsolatedAsyncioTestCase, mock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.payments.payfast import build_signature, build_checkout_params, validate_itn_payload


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
        self.assertEqual(signature, "d27584081daae607abe666dc236fb2cb")

    def test_build_signature_matches_payfast_notification_sample(self):
        payload = {
            "m_payment_id": "000000020",
            "pf_payment_id": "1579137",
            "payment_status": "COMPLETE",
            "item_name": "Order #000000020",
            "item_description": "",
            "amount_gross": "15.00",
            "amount_fee": "-2.30",
            "amount_net": "12.70",
            "custom_str1": "",
            "custom_str2": "",
            "custom_str3": "",
            "custom_str4": "",
            "custom_str5": "",
            "custom_int1": "",
            "custom_int2": "",
            "custom_int3": "",
            "custom_int4": "",
            "custom_int5": "",
            "name_first": "Tom",
            "name_last": "Tom",
            "email_address": "lindley+user1@appinlet.com",
            "merchant_id": "10027938",
        }
        expected_signature = "4078bca2c8987e0e0c4e7230f2f46323"
        self.assertEqual(
            build_signature(payload, preserve_order=True, include_empty=True),
            expected_signature,
        )

    def test_build_checkout_params_uses_settings(self):
        with mock.patch("app.payments.payfast.get_settings", return_value=self.settings):
            host, params, signature_payload = build_checkout_params(
                amount_minor=1000,
                currency="ZAR",
                item_name="Wallet Top-up",
                item_description=None,
                user_email="user@example.com",
                user_reference="user-123",
            )
        self.assertIn("signature", params)
        self.assertIn("merchant_id=10000100", signature_payload)
        self.assertEqual(host, "https://sandbox.payfast.co.za/eng/process")
        self.assertEqual(params["amount"], "10.00")
        self.assertEqual(params["custom_str1"], "user-123")
        self.assertNotEqual(params["custom_str2"], "user-123")
        self.assertTrue(params["custom_str2"].startswith("yummi-"))


class PayFastValidationTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_settings = Settings(
            environment="test",
            payfast_mode="sandbox",
        )

    async def test_validate_itn_payload_hits_remote_service(self):
        payload = {"merchant_id": "10000100"}
        response = mock.Mock()
        response.text = "VALID"
        response.raise_for_status = mock.Mock()

        client_instance = mock.AsyncMock()
        client_instance.post = mock.AsyncMock(return_value=response)
        client_manager = mock.AsyncMock()
        client_manager.__aenter__.return_value = client_instance

        with mock.patch("app.payments.payfast.get_settings", return_value=self.test_settings), mock.patch(
            "app.payments.payfast.httpx.AsyncClient", return_value=client_manager
        ):
            is_valid = await validate_itn_payload(payload)

        self.assertTrue(is_valid)
        client_instance.post.assert_awaited_once_with(
            "https://sandbox.payfast.co.za/eng/query/validate", data=payload
        )

    async def test_validate_itn_payload_skips_remote_when_flag_enabled(self):
        dev_settings = Settings(
            environment="dev", payfast_mode="sandbox", payfast_skip_remote_validation=True
        )
        payload = {"merchant_id": "10000100"}
        with mock.patch("app.payments.payfast.get_settings", return_value=dev_settings), mock.patch(
            "app.payments.payfast.httpx.AsyncClient"
        ) as client_cls:
            is_valid = await validate_itn_payload(payload)
        self.assertTrue(is_valid)
        client_cls.assert_not_called()


class PayFastITNRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _multipart(self, payload: dict[str, str]):
        return {k: (None, v) for k, v in payload.items()}

    def test_itn_accepts_signed_multipart_payload(self):
        passphrase = "test-passphrase"
        unsigned_payload = {
            "m_payment_id": "order-001",
            "pf_payment_id": "PF123",
            "payment_status": "COMPLETE",
            "amount_gross": "100.00",
            "amount_fee": "-2.30",
            "amount_net": "97.70",
            "custom_str2": "user-ref-1",
            "merchant_id": "10000100",
        }
        signature = build_signature(
            unsigned_payload.copy(), passphrase, preserve_order=True, include_empty=True
        )
        signed_payload = dict(unsigned_payload)
        signed_payload["signature"] = signature

        settings = Settings(payfast_passphrase=passphrase, environment="staging")
        validate_mock = mock.AsyncMock(return_value=True)
        update_mock = mock.AsyncMock()

        @asynccontextmanager
        async def stub_session():
            yield mock.AsyncMock()

        with (
            mock.patch("app.routes.payfast.get_settings", return_value=settings),
            mock.patch("app.routes.payfast.validate_itn_payload", new=validate_mock),
            mock.patch("app.routes.payfast.update_payfast_payment_from_itn", new=update_mock),
            mock.patch("app.routes.payfast.get_session", new=stub_session),
        ):
            response = self.client.post(
                "/v1/payments/payfast/itn",
                files=self._multipart(signed_payload),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "OK")
        validate_mock.assert_awaited_once()
        update_mock.assert_awaited_once()

    def test_itn_rejects_invalid_signature_without_remote_call(self):
        passphrase = "another-passphrase"
        payload = {
            "m_payment_id": "order-001",
            "payment_status": "COMPLETE",
            "custom_str2": "user-ref-1",
            "signature": "not-a-real-signature",
        }
        settings = Settings(payfast_passphrase=passphrase, environment="staging")
        validate_mock = mock.AsyncMock(return_value=True)
        update_mock = mock.AsyncMock()

        @asynccontextmanager
        async def stub_session():
            yield mock.AsyncMock()

        with (
            mock.patch("app.routes.payfast.get_settings", return_value=settings),
            mock.patch("app.routes.payfast.validate_itn_payload", new=validate_mock),
            mock.patch("app.routes.payfast.update_payfast_payment_from_itn", new=update_mock),
            mock.patch("app.routes.payfast.get_session", new=stub_session),
        ):
            response = self.client.post(
                "/v1/payments/payfast/itn",
                files=self._multipart(payload),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.text, "INVALID")
        validate_mock.assert_not_awaited()
        update_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
