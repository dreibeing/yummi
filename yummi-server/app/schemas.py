from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

DEFAULT_CANDIDATE_POOL_LIMIT = 40
MAX_CANDIDATE_POOL_LIMIT = 200


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


class ShoppingListIngredientProductPayload(BaseModel):
    product_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("product_id", "productId"),
    )
    package_quantity: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("package_quantity", "packageQuantity"),
    )
    name: Optional[str] = None
    detail_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("detail_url", "detailUrl"),
    )
    sale_price: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("sale_price", "salePrice"),
    )
    ingredient_line: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ingredient_line", "ingredientLine"),
    )

    model_config = ConfigDict(extra="allow")


class ShoppingListMealIngredientPayload(BaseModel):
    id: Optional[str] = None
    core_item_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("core_item_name", "coreItemName"),
    )
    name: Optional[str] = None
    product_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("product_name", "productName"),
    )
    product_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("product_id", "productId"),
    )
    catalog_ref_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("catalog_ref_id", "catalogRefId"),
    )
    quantity: Optional[str] = None
    unit: Optional[str] = None
    text: Optional[str] = None
    preparation: Optional[str] = None
    ingredient_line: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ingredient_line", "ingredientLine"),
    )
    package_quantity: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("package_quantity", "packageQuantity"),
    )
    sale_price: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("sale_price", "salePrice"),
    )
    detail_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("detail_url", "detailUrl"),
    )
    selected_product: Optional[ShoppingListIngredientProductPayload] = Field(
        default=None,
        validation_alias=AliasChoices("selected_product", "selectedProduct"),
    )

    model_config = ConfigDict(extra="allow")


class ShoppingListMealPayload(BaseModel):
    meal_id: str = Field(validation_alias=AliasChoices("meal_id", "mealId"))
    name: Optional[str] = None
    servings: Optional[str] = None
    ingredients: List[ShoppingListMealIngredientPayload] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ShoppingListBuildRequest(BaseModel):
    meals: List[ShoppingListMealPayload] = Field(default_factory=list, min_length=1)
    triggerRecommendationLearning: Optional[bool] = Field(default=False)


class ShoppingListProductSelection(BaseModel):
    productId: Optional[str] = None
    catalogRefId: Optional[str] = None
    name: Optional[str] = None
    detailUrl: Optional[str] = None
    salePrice: Optional[float] = None
    packages: Optional[float] = None
    imageUrl: Optional[str] = None


class ShoppingListResultItem(BaseModel):
    id: str
    groupKey: str
    text: str
    productName: Optional[str] = None
    classification: Literal["pickup", "pantry"]
    requiredQuantity: float = Field(default=0, ge=0)
    defaultQuantity: float = Field(default=0, ge=0)
    notes: Optional[str] = None
    linkedProducts: List[ShoppingListProductSelection] = Field(default_factory=list)
    unitPrice: Optional[float] = None
    unitPriceMinor: Optional[int] = None
    needsManualProductSelection: bool = False


class ShoppingListBuildResponse(BaseModel):
    status: Literal["completed"]
    generatedAt: datetime
    items: List[ShoppingListResultItem] = Field(default_factory=list)


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


class MealSkuSnapshot(BaseModel):
    productId: Optional[str] = None
    name: Optional[str] = None
    salePrice: Optional[float] = None
    detailUrl: Optional[str] = None


class IngredientSummary(BaseModel):
    name: Optional[str] = None
    quantity: Optional[str] = None
    productName: Optional[str] = None


class CandidateMealSummary(BaseModel):
    mealId: str
    archetypeId: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    heatLevel: Optional[str] = None
    prepTimeMinutes: Optional[int] = None
    prepTimeTags: List[str] = Field(default_factory=list)
    complexity: Optional[str] = None
    skuSnapshot: List[MealSkuSnapshot] = Field(default_factory=list)


class HardConstraintOverrides(BaseModel):
    diets: List[str] = Field(default_factory=list)
    allergens: List[str] = Field(default_factory=list)
    ethics: List[str] = Field(default_factory=list)
    excludeHeatLevels: List[str] = Field(default_factory=list)
    maxPrepTimeMinutes: Optional[int] = Field(default=None, gt=0, le=240)


class CandidateFilterRequest(BaseModel):
    mealVersion: Optional[str] = None
    hardConstraints: Optional[HardConstraintOverrides] = None
    declinedMealIds: List[str] = Field(default_factory=list)
    limit: int = Field(
        default=DEFAULT_CANDIDATE_POOL_LIMIT,
        ge=1,
        le=MAX_CANDIDATE_POOL_LIMIT,
    )


class CandidateFilterResponse(BaseModel):
    candidatePoolId: str
    mealVersion: Optional[str] = None
    manifestId: Optional[str] = None
    tagsVersion: Optional[str] = None
    generatedAt: datetime
    totalCandidates: int
    returnedCount: int
    candidateMeals: List[CandidateMealSummary] = Field(default_factory=list)


class ExplorationMeal(BaseModel):
    mealId: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    keyIngredients: List[IngredientSummary] = Field(default_factory=list)
    prepSteps: List[str] = Field(default_factory=list)
    cookSteps: List[str] = Field(default_factory=list)
    ingredients: List[LatestRecommendationIngredient] = Field(default_factory=list)
    rationale: Optional[str] = None
    expectedReaction: Optional[str] = None
    diversityAxes: List[str] = Field(default_factory=list)
    skuSnapshot: List[MealSkuSnapshot] = Field(default_factory=list)


class ExplorationRunRequest(BaseModel):
    candidateLimit: Optional[int] = Field(default=None, ge=1, le=MAX_CANDIDATE_POOL_LIMIT)
    mealCount: Optional[int] = Field(default=None, ge=1, le=20)


class ExplorationRunResponse(BaseModel):
    sessionId: str
    status: str
    meals: List[ExplorationMeal] = Field(default_factory=list)
    infoNotes: List[str] = Field(default_factory=list)


class MealReaction(BaseModel):
    mealId: str = Field(min_length=1)
    reaction: Literal["like", "neutral", "dislike"]


class RecommendationRunRequest(BaseModel):
    mealVersion: Optional[str] = None
    candidateLimit: Optional[int] = Field(default=None, ge=1, le=MAX_CANDIDATE_POOL_LIMIT)
    mealCount: Optional[int] = Field(default=None, ge=1, le=20)
    hardConstraints: Optional[HardConstraintOverrides] = None
    declinedMealIds: List[str] = Field(default_factory=list)
    explorationSessionId: Optional[UUID] = None
    reactions: List[MealReaction] = Field(default_factory=list)


class RecommendationMeal(BaseModel):
    mealId: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    rank: int = Field(ge=1)
    rationale: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    diversityAxes: List[str] = Field(default_factory=list)
    skuSnapshot: List[MealSkuSnapshot] = Field(default_factory=list)
    archetypeId: Optional[str] = None


class RecommendationRunResponse(BaseModel):
    generatedAt: datetime
    manifestId: Optional[str] = None
    tagsVersion: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    meals: List[RecommendationMeal] = Field(default_factory=list)
    latestRecommendationMeals: List[LatestRecommendationMeal] = Field(default_factory=list)


RecommendationLearningTrigger = Literal["shopping_list_build", "woolworths_cart_add"]
RecommendationLearningStatus = Literal["pending", "completed", "failed", "skipped"]


class RecommendationLearningRunRecord(BaseModel):
    runId: str
    userId: str
    trigger: RecommendationLearningTrigger
    status: RecommendationLearningStatus
    model: Optional[str] = None
    createdAt: datetime
    completedAt: Optional[datetime] = None
    eventContext: Dict[str, Any] = Field(default_factory=dict)
    usageSnapshot: Dict[str, Any] = Field(default_factory=dict)
    responsePayload: Dict[str, Any] = Field(default_factory=dict)
    errorMessage: Optional[str] = None


class RecommendationLearningTriggerRequest(BaseModel):
    trigger: RecommendationLearningTrigger
    context: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MealFeedbackSubmitRequest(BaseModel):
    mealId: str
    reaction: Literal["like", "dislike", "neutral"]
    source: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MealFeedbackRecordResponse(BaseModel):
    mealId: str
    reaction: Optional[str] = None
    source: Optional[str] = None
    occurredAt: Optional[datetime] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class MealFeedbackSummaryResponse(BaseModel):
    likedMealIds: List[str] = Field(default_factory=list)
    dislikedMealIds: List[str] = Field(default_factory=list)


class LatestRecommendationIngredient(BaseModel):
    name: Optional[str] = None
    quantity: Optional[str] = None
    preparation: Optional[str] = None
    productName: Optional[str] = None
    productId: Optional[str] = None
    detailUrl: Optional[str] = None
    salePrice: Optional[float] = None
    packageQuantity: Optional[float] = None


class LatestRecommendationMeal(BaseModel):
    mealId: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    keyIngredients: List[str] = Field(default_factory=list)
    archetypeId: Optional[str] = None
    prepSteps: List[str] = Field(default_factory=list)
    cookSteps: List[str] = Field(default_factory=list)
    ingredients: List[LatestRecommendationIngredient] = Field(default_factory=list)


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
    latestRecommendations: Optional[List[str]] = None
    latestRecommendationsGeneratedAt: Optional[datetime] = None
    latestRecommendationsManifestId: Optional[str] = None
    latestRecommendationMeals: List[LatestRecommendationMeal] = Field(default_factory=list)
