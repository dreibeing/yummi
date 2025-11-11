from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, PaymentStatus
from app.services.payments import (
    create_payfast_payment,
    get_payfast_status_details,
    update_payfast_payment_from_itn,
)


class PayFastEndToEndTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_itn_updates_wallet_and_status(self):
        async with self.Session() as session:
            payment = await create_payfast_payment(
                session,
                reference="ref-abc123",
                user_id="user-1",
                user_email="user@example.com",
                amount_minor=1999,
                currency="zar",
                item_name="Wallet Top-up",
                item_description=None,
                checkout_payload={"mock": True},
            )

            payload = {
                "custom_str2": payment.provider_reference,
                "pf_payment_id": "PF123456",
                "payment_status": "COMPLETE",
            }
            await update_payfast_payment_from_itn(session, payload)

            status_payload = await get_payfast_status_details(
                session, payment.provider_reference, expected_user_id="user-1"
            )
            self.assertIsNotNone(status_payload)
            assert status_payload is not None  # for mypy/type checking
            self.assertEqual(status_payload["status"], PaymentStatus.COMPLETE)
            self.assertEqual(status_payload["pf_status"], "COMPLETE")
            self.assertTrue(status_payload["wallet_credited"])
            self.assertEqual(status_payload["amount_minor"], 1999)
            self.assertEqual(status_payload["currency"], "ZAR")
            self.assertIsNotNone(status_payload["updated_at"])

    async def test_status_missing_reference(self):
        async with self.Session() as session:
            status_payload = await get_payfast_status_details(session, "missing-ref")
            self.assertIsNone(status_payload)

    async def test_status_requires_owner(self):
        async with self.Session() as session:
            payment = await create_payfast_payment(
                session,
                reference="ref-owner-check",
                user_id="owner-1",
                user_email="owner@example.com",
                amount_minor=5000,
                currency="zar",
                item_name="Wallet Top-up",
                item_description=None,
                checkout_payload={"mock": True},
            )

            with self.assertRaises(PermissionError):
                await get_payfast_status_details(
                    session, payment.provider_reference, expected_user_id="other-user"
                )
