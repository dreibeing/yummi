from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class Product(BaseModel):
    productId: Optional[str] = None
    catalogRefId: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    qty: Optional[int] = Field(default=1, ge=1)


class OrderItem(BaseModel):
    productId: Optional[str] = None
    catalogRefId: Optional[str] = None
    url: Optional[str] = None
    qty: int = Field(default=1, ge=1)
    title: Optional[str] = None


class CreateOrderRequest(BaseModel):
    retailer: str = Field(pattern=r"^[a-z0-9_\-]+$")
    items: List[OrderItem] = Field(min_length=1)
    notes: Optional[str] = None


class CreateOrderResponse(BaseModel):
    order_id: str
    status: str


class OrderStatusResponse(BaseModel):
    order_id: str
    status: str
    items: List[OrderItem]
    retailer: str
    events: List[dict] = []


class PayFastInitiateRequest(BaseModel):
    amountMinor: int = Field(gt=0)
    currency: str = Field(default="ZAR", min_length=3, max_length=3)
    itemName: str = Field(default="Wallet Top-up", max_length=255)
    itemDescription: Optional[str] = Field(default=None, max_length=255)


class PayFastInitiateResponse(BaseModel):
    url: str
    params: dict
    reference: str


class PayFastStatusResponse(BaseModel):
    reference: str
    status: str
    message: Optional[str] = None
    pfStatus: Optional[str] = Field(default=None, alias="pf_status")
    providerPaymentId: Optional[str] = Field(default=None, alias="provider_payment_id")
    amountMinor: Optional[int] = Field(default=None, alias="amount_minor")
    currency: Optional[str] = None
    walletCredited: bool = Field(default=False, alias="wallet_credited")
    updatedAt: Optional[str] = Field(default=None, alias="updated_at")

    model_config = ConfigDict(populate_by_name=True)


class WalletTransactionSchema(BaseModel):
    id: str
    amountMinor: int
    currency: str
    entryType: str
    transactionType: str
    note: Optional[str] = None
    createdAt: str
    paymentId: Optional[str] = None
    externalReference: Optional[str] = None
    initiatedBy: Optional[str] = None
    context: Optional[dict] = None


class WalletSummary(BaseModel):
    userId: str
    balanceMinor: int
    currency: str
    spendableMinor: int
    spendBlocked: bool = False
    lockReason: Optional[str] = None
    lockNote: Optional[str] = None
    transactions: List[WalletTransactionSchema] = Field(default_factory=list)


class WalletRefundRequest(BaseModel):
    amountMinor: int = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=255)


class WalletRefundResponse(BaseModel):
    refundId: str
    status: str
    debitedMinor: int
    balanceMinor: int
    spendBlocked: bool
    lockReason: Optional[str] = None
    lockNote: Optional[str] = None


class AdminChargebackRequest(BaseModel):
    reference: str = Field(min_length=1)
    amountMinor: Optional[int] = Field(default=None, gt=0)
    note: Optional[str] = Field(default=None, max_length=255)
    externalReference: Optional[str] = Field(default=None, max_length=128)


class AdminChargebackResponse(BaseModel):
    paymentReference: str
    debitTransactionId: Optional[str] = None
    balanceMinor: int
    spendBlocked: bool
    lockReason: Optional[str] = None
    lockNote: Optional[str] = None


class WalletRefundAdminActionRequest(BaseModel):
    status: str = Field(pattern=r"^(approved|denied|paid)$")
    note: Optional[str] = Field(default=None, max_length=255)
