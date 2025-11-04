from __future__ import annotations

import unittest
from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, Payment, PaymentStatus
from app.services.payments import ensure_wallet_credit_for_payment


class WalletCreditServiceTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_ensure_wallet_credit_creates_single_entry(self):
        async with self.Session() as session:
            payment = Payment(
                provider="payfast",
                provider_reference="ref-123",
                status=PaymentStatus.COMPLETE,
                user_id="user-1",
                user_email="user@example.com",
                amount_minor=1050,
                currency="ZAR",
                item_name="Wallet Top-up",
                checkout_payload={"amount": "10.50"},
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)

            txn = await ensure_wallet_credit_for_payment(session, payment)
            self.assertIsNotNone(txn)
            self.assertEqual(txn.amount_minor, 1050)

            # Ensure idempotency
            txn_again = await ensure_wallet_credit_for_payment(session, payment)
            self.assertIsNotNone(txn_again)
            self.assertEqual(txn.id, txn_again.id)


if __name__ == "__main__":
    unittest.main()
