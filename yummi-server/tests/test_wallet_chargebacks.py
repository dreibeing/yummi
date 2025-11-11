from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, PaymentStatus
from app.services import payments as payment_service
from app.services.payments import (
    create_payfast_payment,
    ensure_wallet_credit_for_payment,
    get_user_wallet_summary,
    record_chargeback,
    request_wallet_refund,
)


class WalletChargebackRefundTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create_funded_payment(self, session, amount_minor: int = 2000):
        payment = await create_payfast_payment(
            session,
            reference="ref-test",
            user_id="user-1",
            user_email="user@example.com",
            amount_minor=amount_minor,
            currency="zar",
            item_name="Wallet Top-up",
            item_description=None,
            checkout_payload={"mock": True},
        )
        payment.status = PaymentStatus.COMPLETE
        await session.commit()
        await session.refresh(payment)
        await ensure_wallet_credit_for_payment(session, payment)
        return payment

    async def test_request_refund_creates_debit(self):
        async with self.Session() as session:
            await self._create_funded_payment(session, amount_minor=2500)

            result = await request_wallet_refund(
                session,
                user_id="user-1",
                user_email="user@example.com",
                amount_minor=500,
                reason="Test refund",
                actor_email="user@example.com",
            )
            txn = result["transaction"]
            summary = result["summary"]

            self.assertEqual(txn.transaction_type, "refund")
            self.assertEqual(txn.entry_type, "debit")
            self.assertEqual(txn.amount_minor, 500)
            self.assertEqual(summary["balanceMinor"], 2000)
            self.assertTrue(summary["spendableMinor"] >= 2000)

    async def test_chargeback_blocks_after_limit(self):
        original_limit = payment_service.CHARGEBACK_LIMIT_PER_WINDOW
        payment_service.CHARGEBACK_LIMIT_PER_WINDOW = 1
        self.addAsyncCleanup(
            lambda: setattr(payment_service, "CHARGEBACK_LIMIT_PER_WINDOW", original_limit)
        )

        async with self.Session() as session:
            payment = await self._create_funded_payment(session, amount_minor=1000)
            payload = await record_chargeback(
                session,
                reference=payment.provider_reference,
                amount_minor=500,
                note="Test chargeback",
                external_reference="issuer-1",
                actor_email="ops@example.com",
            )

            summary = await get_user_wallet_summary(session, "user-1")
            self.assertTrue(payload["spendBlocked"])
            self.assertTrue(summary["spendBlocked"])
            self.assertEqual(payload["lockReason"], "review")
