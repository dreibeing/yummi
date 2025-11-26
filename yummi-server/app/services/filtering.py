from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from ..models import UserPreferenceProfile
from ..schemas import (
    CandidateFilterRequest,
    CandidateFilterResponse,
    CandidateMealSummary,
    HardConstraintOverrides,
    MealSkuSnapshot,
    DEFAULT_CANDIDATE_POOL_LIMIT,
    MAX_CANDIDATE_POOL_LIMIT,
)
from .preferences import TagManifest

logger = logging.getLogger(__name__)

PLACEHOLDER_ALLERGEN_VALUES = {"None", "NoAllergens"}

SKU_SNAPSHOT_LIMIT = 4


@dataclass
class ConstraintContext:
    selected_audience: str | None
    required_dietary_restrictions: set[str]
    disallowed_allergens: set[str]
    declined_meal_ids: set[str]


@dataclass
class CandidateMealDetail:
    archetype_uid: str | None
    meal: Dict[str, Any]


def generate_candidate_pool(
    *,
    manifest: Dict[str, Any],
    tag_manifest: TagManifest,
    profile: UserPreferenceProfile | None,
    request: CandidateFilterRequest,
    user_id: str,
) -> CandidateFilterResponse:
    """Build the filtered candidate pool returned to the client/AI worker."""
    response, _ = _build_candidate_pool(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=request,
        user_id=user_id,
    )
    return response


def generate_candidate_pool_with_details(
    *,
    manifest: Dict[str, Any],
    tag_manifest: TagManifest,
    profile: UserPreferenceProfile | None,
    request: CandidateFilterRequest,
    user_id: str,
) -> tuple[CandidateFilterResponse, List[CandidateMealDetail]]:
    return _build_candidate_pool(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=request,
        user_id=user_id,
    )


def _build_candidate_pool(
    *,
    manifest: Dict[str, Any],
    tag_manifest: TagManifest,
    profile: UserPreferenceProfile | None,
    request: CandidateFilterRequest,
    user_id: str,
) -> tuple[CandidateFilterResponse, List[CandidateMealDetail]]:
    limit = _normalize_limit(request.limit)
    overrides = request.hardConstraints or HardConstraintOverrides()
    constraints = _build_constraint_context(
        profile=profile,
        tag_manifest=tag_manifest,
        overrides=overrides,
        declined_ids=request.declinedMealIds,
    )
    total_candidates, summaries, details = _filter_manifest(
        manifest=manifest,
        constraints=constraints,
        limit=limit,
    )
    response = CandidateFilterResponse(
        candidatePoolId=str(uuid.uuid4()),
        mealVersion=manifest.get("manifest_id"),
        manifestId=manifest.get("manifest_id"),
        tagsVersion=manifest.get("tags_version"),
        generatedAt=datetime.now(timezone.utc),
        totalCandidates=total_candidates,
        returnedCount=len(summaries),
        candidateMeals=summaries,
    )
    return response, details


def _normalize_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_CANDIDATE_POOL_LIMIT
    return max(1, min(value, MAX_CANDIDATE_POOL_LIMIT))


def _build_constraint_context(
    *,
    profile: UserPreferenceProfile | None,
    tag_manifest: TagManifest,
    overrides: HardConstraintOverrides,
    declined_ids: Sequence[str] | None,
) -> ConstraintContext:
    selected = dict(profile.selected_tags or {}) if profile else {}

    selected_audience = _resolve_selected_audience(selected, tag_manifest)
    required_dietary_restrictions = _resolve_required_dietary_restrictions(
        selected,
        tag_manifest,
        overrides,
    )

    disallowed_allergens = set(_tag_ids_to_values(selected.get("Allergens"), tag_manifest))
    disallowed_allergens.update(_normalize_value_list(overrides.allergens))
    disallowed_allergens = _drop_placeholder_allergens(disallowed_allergens)

    declined_meal_ids = {mid for mid in (declined_ids or []) if mid}

    return ConstraintContext(
        selected_audience=selected_audience,
        required_dietary_restrictions=required_dietary_restrictions,
        disallowed_allergens=disallowed_allergens,
        declined_meal_ids=declined_meal_ids,
    )


def _resolve_selected_audience(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
) -> str | None:
    tag_ids = selected.get("Audience") or []
    if not tag_ids:
        return None
    tag_values = _tag_ids_to_values(tag_ids, tag_manifest)
    return _extract_single_value(tag_values)


def _resolve_required_dietary_restrictions(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
    overrides: HardConstraintOverrides,
) -> set[str]:
    tag_ids = selected.get("DietaryRestrictions") or []
    values = set(_tag_ids_to_values(tag_ids, tag_manifest))
    values.update(_normalize_value_list(overrides.diets))
    # Accept ethics override data for backward compatibility.
    values.update(_normalize_value_list(overrides.ethics))
    return values


def _filter_manifest(
    *,
    manifest: Dict[str, Any],
    constraints: ConstraintContext,
    limit: int,
) -> tuple[int, List[CandidateMealSummary], List[CandidateMealDetail]]:
    archetypes = manifest.get("archetypes") or []
    summaries: List[CandidateMealSummary] = []
    details: List[CandidateMealDetail] = []

    for archetype in archetypes:
        archetype_uid = archetype.get("uid")
        for meal in archetype.get("meals") or []:
            meal_id = meal.get("meal_id")
            if not meal_id or meal_id in constraints.declined_meal_ids:
                continue
            tags = meal.get("meal_tags") or {}
            if not _passes_audience(tags, constraints):
                continue
            if not _passes_dietary_restrictions(tags, constraints):
                continue
            if not _passes_allergens(tags, constraints):
                continue
            summaries.append(_build_candidate_summary(meal, archetype_uid))
            details.append(CandidateMealDetail(archetype_uid=archetype_uid, meal=meal))

    total_matches = len(summaries)
    if total_matches <= limit:
        return total_matches, summaries, details

    indices = random.sample(range(total_matches), limit)
    selected_summaries = [summaries[i] for i in indices]
    selected_details = [details[i] for i in indices]
    return total_matches, selected_summaries, selected_details


def _passes_audience(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    selected_audience = constraints.selected_audience
    if not selected_audience:
        return False
    meal_audience = tags.get("Audience") or []
    return selected_audience in meal_audience


def _passes_dietary_restrictions(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    required_tags = constraints.required_dietary_restrictions
    if not required_tags:
        return False
    meal_tags = set(tags.get("DietaryRestrictions") or [])
    return required_tags.issubset(meal_tags)


def _passes_allergens(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    if not constraints.disallowed_allergens:
        return True
    meal_allergens = set(tags.get("Allergens") or [])
    return not bool(meal_allergens & constraints.disallowed_allergens)


def _build_candidate_summary(meal: Dict[str, Any], archetype_uid: str | None) -> CandidateMealSummary:
    tags = meal.get("meal_tags") or {}
    return CandidateMealSummary(
        mealId=str(meal.get("meal_id")),
        archetypeId=archetype_uid,
        name=meal.get("name"),
        description=meal.get("description"),
        tags=tags,
        complexity=_extract_single_value(tags.get("Complexity")),
        skuSnapshot=_build_sku_snapshot(meal),
    )


def _build_sku_snapshot(meal: Dict[str, Any]) -> List[MealSkuSnapshot]:
    snapshot: List[MealSkuSnapshot] = []
    for ingredient in meal.get("final_ingredients") or []:
        product = ingredient.get("selected_product") or {}
        if not product:
            continue
        product_id = product.get("product_id")
        name = product.get("name")
        detail_url = product.get("detail_url")
        sale_price = _coerce_float(product.get("sale_price"))
        if not any([product_id, name, detail_url, sale_price is not None]):
            continue
        snapshot.append(
            MealSkuSnapshot(
                productId=str(product_id) if product_id is not None else None,
                name=name,
                detailUrl=detail_url,
                salePrice=sale_price,
            )
        )
        if len(snapshot) >= SKU_SNAPSHOT_LIMIT:
            break
    return snapshot


def _tag_ids_to_values(
    tag_ids: Iterable[str] | None,
    tag_manifest: TagManifest,
) -> List[str]:
    resolved: List[str] = []
    if not tag_ids:
        return resolved
    for tag_id in tag_ids:
        if not tag_id:
            continue
        resolved.append(tag_manifest.tag_to_value.get(tag_id, tag_id))
    return resolved


def _normalize_value_list(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    normalized = {str(value) for value in values if value}
    return {value for value in normalized if value}


def _drop_placeholder_allergens(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    return {
        value
        for value in values
        if value and value not in PLACEHOLDER_ALLERGEN_VALUES
    }


def _extract_single_value(values: Sequence[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        if value:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
