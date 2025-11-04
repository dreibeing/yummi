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

