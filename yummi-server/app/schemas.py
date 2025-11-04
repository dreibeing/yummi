from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


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


class WalletTransactionSchema(BaseModel):
    id: str
    amountMinor: int
    currency: str
    entryType: str
    note: Optional[str] = None
    createdAt: str
    paymentId: str


class WalletSummary(BaseModel):
    userId: str
    balanceMinor: int
    currency: str
    transactions: List[WalletTransactionSchema] = []
