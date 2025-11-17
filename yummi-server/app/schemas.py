from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
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


class MealIngredientProduct(BaseModel):
    product_id: Optional[str] = None
    package_quantity: Optional[float] = None
    name: Optional[str] = None
    detail_url: Optional[str] = None
    sale_price: Optional[float] = None


class MealIngredient(BaseModel):
    core_item_name: Optional[str] = None
    quantity: Optional[str] = None
    preparation: Optional[str] = None
    ingredient_line: Optional[str] = None
    selected_product: Optional[MealIngredientProduct] = None


class MealProductMatch(BaseModel):
    core_item_name: Optional[str] = None
    selected_product_id: Optional[str] = None
    package_quantity: Optional[float] = None
    package_notes: Optional[str] = None
    ingredient_line: Optional[str] = None


class MealRecord(BaseModel):
    meal_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    servings: Optional[str] = None
    meal_tags: Dict[str, List[str]] = Field(default_factory=dict)
    prep_steps: List[str] = Field(default_factory=list)
    cook_steps: List[str] = Field(default_factory=list)
    instructions: List[str] = Field(default_factory=list)
    ingredients: List[MealIngredient] = Field(default_factory=list)
    final_ingredients: List[MealIngredient] = Field(default_factory=list)
    product_matches: List[MealProductMatch] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class MealArchetype(BaseModel):
    uid: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    core_tags: Dict[str, List[str]] = Field(default_factory=dict)
    diet_profile: Dict[str, Any] = Field(default_factory=dict)
    allergen_flags: Dict[str, Any] = Field(default_factory=dict)
    heat_band: Optional[str] = None
    prep_time_minutes_range: Optional[List[int]] = None
    complexity: Optional[str] = None
    audience_context: Optional[str] = None
    cuisine_openness: Optional[str] = None
    refresh_version: Optional[str] = None
    rationale: Optional[str] = None
    meals: List[MealRecord] = Field(default_factory=list)


class MealManifest(BaseModel):
    schema_version: Optional[str] = None
    manifest_id: Optional[str] = None
    generated_at: Optional[str] = None
    tags_version: Optional[str] = None
    required_categories: List[str] = Field(default_factory=list)
    source: Dict[str, Any] = Field(default_factory=dict)
    stats: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    archetypes: List[MealArchetype] = Field(default_factory=list)


class PreferenceSaveRequest(BaseModel):
    tagsVersion: str = Field(min_length=1)
    responses: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    completionStage: Optional[str] = Field(default=None, min_length=1)
    completedAt: Optional[datetime] = Field(default=None)


class PreferenceProfileResponse(BaseModel):
    tagsVersion: Optional[str] = None
    manifestTagsVersion: Optional[str] = None
    responses: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    selectedTags: Dict[str, List[str]] = Field(default_factory=dict)
    dislikedTags: Dict[str, List[str]] = Field(default_factory=dict)
    completionStage: Optional[str] = None
    completedAt: Optional[datetime] = None
    lastSyncedAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
