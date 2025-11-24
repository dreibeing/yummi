from __future__ import annotations

import logging
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

DIET_RESTRICTION_TAG_IDS = {
    "diet_vegan",
    "diet_veg",
    "diet_pesc",
    "diet_poultry",
    "diet_lowcarb",
    "diet_keto",
    "diet_glutenaware",
}

ETHICS_RESTRICTION_TAG_IDS = {
    "ethics_halal",
    "ethics_kosher",
    "ethics_jain",
    "ethics_sussea",
    "ethics_animal",
}

HEAT_RESTRICTION_TAG_IDS = {
    "heat_none",
    "heat_mild",
}

HEAT_LEVEL_ORDER = {
    "NoHeat": 0,
    "Mild": 1,
    "Medium": 2,
    "Hot": 3,
    "ExtraHot": 4,
}

PREP_TIME_BUCKET_TO_MINUTES = {
    "Under15": 15,
    "15to30": 30,
    "30to45": 45,
    "45Plus": 60,
}

SKU_SNAPSHOT_LIMIT = 4


@dataclass
class ConstraintContext:
    allowed_audience: set[str]
    required_diets: set[str]
    required_ethics: set[str]
    disallowed_allergens: set[str]
    disallowed_heat: set[str]
    max_heat_rank: int | None
    max_prep_minutes: int | None
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
    disliked = dict(profile.disliked_tags or {}) if profile else {}

    allowed_audience = _resolve_allowed_audience(selected, tag_manifest)
    required_diets = _resolve_required_diets(selected, tag_manifest, overrides)
    required_ethics = _resolve_required_ethics(selected, tag_manifest, overrides)

    disallowed_allergens = set(_tag_ids_to_values(disliked.get("Allergens"), tag_manifest))
    disallowed_allergens.update(_normalize_value_list(overrides.allergens))

    disallowed_heat = set(_tag_ids_to_values(disliked.get("HeatSpice"), tag_manifest))
    disallowed_heat.update(_normalize_value_list(overrides.excludeHeatLevels))

    max_heat_rank = _resolve_heat_limit(selected, tag_manifest)

    max_prep_minutes = overrides.maxPrepTimeMinutes
    declined_meal_ids = {mid for mid in (declined_ids or []) if mid}

    return ConstraintContext(
        allowed_audience=allowed_audience,
        required_diets=required_diets,
        required_ethics=required_ethics,
        disallowed_allergens=disallowed_allergens,
        disallowed_heat=disallowed_heat,
        max_heat_rank=max_heat_rank,
        max_prep_minutes=max_prep_minutes,
        declined_meal_ids=declined_meal_ids,
    )


def _resolve_allowed_audience(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
) -> set[str]:
    tag_ids = selected.get("Audience") or []
    if not tag_ids:
        return set()
    return set(_tag_ids_to_values(tag_ids, tag_manifest))


def _resolve_required_diets(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
    overrides: HardConstraintOverrides,
) -> set[str]:
    strict_ids = [
        tag_id for tag_id in selected.get("Diet", []) if tag_id in DIET_RESTRICTION_TAG_IDS
    ]
    values = set(_tag_ids_to_values(strict_ids, tag_manifest))
    values.update(_normalize_value_list(overrides.diets))
    return values


def _resolve_required_ethics(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
    overrides: HardConstraintOverrides,
) -> set[str]:
    tag_ids = [
        tag_id
        for tag_id in selected.get("EthicsReligious", [])
        if tag_id in ETHICS_RESTRICTION_TAG_IDS
    ]
    values = set(_tag_ids_to_values(tag_ids, tag_manifest))
    values.update(_normalize_value_list(overrides.ethics))
    return values


def _resolve_heat_limit(
    selected: Dict[str, List[str]],
    tag_manifest: TagManifest,
) -> int | None:
    tag_ids = [
        tag_id
        for tag_id in selected.get("HeatSpice", [])
        if tag_id in HEAT_RESTRICTION_TAG_IDS
    ]
    heat_values = _tag_ids_to_values(tag_ids, tag_manifest)
    ranks = [
        HEAT_LEVEL_ORDER[value]
        for value in heat_values
        if value in HEAT_LEVEL_ORDER
    ]
    if not ranks:
        return None
    return min(ranks)


def _filter_manifest(
    *,
    manifest: Dict[str, Any],
    constraints: ConstraintContext,
    limit: int,
) -> tuple[int, List[CandidateMealSummary], List[CandidateMealDetail]]:
    archetypes = manifest.get("archetypes") or []
    retained: List[CandidateMealSummary] = []
    details: List[CandidateMealDetail] = []
    total_matches = 0

    for archetype in archetypes:
        archetype_uid = archetype.get("uid")
        for meal in archetype.get("meals") or []:
            meal_id = meal.get("meal_id")
            if not meal_id or meal_id in constraints.declined_meal_ids:
                continue
            tags = meal.get("meal_tags") or {}
            if not _passes_audience(tags, constraints):
                continue
            if not _passes_diet(tags, constraints):
                continue
            if not _passes_ethics(tags, constraints):
                continue
            if not _passes_allergens(tags, constraints):
                continue
            if not _passes_heat(tags, constraints):
                continue
            if not _passes_prep_time(meal, tags, constraints):
                continue
            total_matches += 1
            if len(retained) < limit:
                retained.append(_build_candidate_summary(meal, archetype_uid))
                details.append(CandidateMealDetail(archetype_uid=archetype_uid, meal=meal))
    return total_matches, retained, details


def _passes_audience(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    if not constraints.allowed_audience:
        return True
    meal_audience = set(tags.get("Audience") or [])
    return bool(meal_audience & constraints.allowed_audience)


def _passes_diet(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    if not constraints.required_diets:
        return True
    meal_diets = set(tags.get("Diet") or [])
    return constraints.required_diets.issubset(meal_diets)


def _passes_ethics(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    if not constraints.required_ethics:
        return True
    meal_ethics = set(tags.get("EthicsReligious") or [])
    return constraints.required_ethics.issubset(meal_ethics)


def _passes_allergens(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    if not constraints.disallowed_allergens:
        return True
    meal_allergens = set(tags.get("Allergens") or [])
    return not bool(meal_allergens & constraints.disallowed_allergens)


def _passes_heat(tags: Dict[str, List[str]], constraints: ConstraintContext) -> bool:
    meal_heat_tags = set(tags.get("HeatSpice") or [])
    if constraints.disallowed_heat and meal_heat_tags & constraints.disallowed_heat:
        return False
    if constraints.max_heat_rank is None:
        return True
    meal_rank = _heat_rank(meal_heat_tags)
    if meal_rank is None:
        return True
    return meal_rank <= constraints.max_heat_rank


def _passes_prep_time(
    meal: Dict[str, Any],
    tags: Dict[str, List[str]],
    constraints: ConstraintContext,
) -> bool:
    if constraints.max_prep_minutes is None:
        return True
    minutes = _estimate_prep_minutes(meal, tags)
    if minutes is None:
        return True
    return minutes <= constraints.max_prep_minutes


def _estimate_prep_minutes(meal: Dict[str, Any], tags: Dict[str, List[str]]) -> int | None:
    metadata = meal.get("metadata") or {}
    raw_minutes = metadata.get("prep_time_minutes")
    if isinstance(raw_minutes, (int, float)):
        return int(raw_minutes)
    for bucket in tags.get("PrepTime") or []:
        approx = PREP_TIME_BUCKET_TO_MINUTES.get(bucket)
        if approx is not None:
            return approx
    return None


def _build_candidate_summary(meal: Dict[str, Any], archetype_uid: str | None) -> CandidateMealSummary:
    tags = meal.get("meal_tags") or {}
    return CandidateMealSummary(
        mealId=str(meal.get("meal_id")),
        archetypeId=archetype_uid,
        name=meal.get("name"),
        description=meal.get("description"),
        tags=tags,
        heatLevel=_extract_single_value(tags.get("HeatSpice")),
        prepTimeMinutes=_estimate_prep_minutes(meal, tags),
        prepTimeTags=tags.get("PrepTime") or [],
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
def _heat_rank(values: Iterable[str] | None) -> int | None:
    if not values:
        return None
    ranks = [HEAT_LEVEL_ORDER.get(value) for value in values if value in HEAT_LEVEL_ORDER]
    ranks = [rank for rank in ranks if rank is not None]
    if not ranks:
        return None
    return max(ranks)
