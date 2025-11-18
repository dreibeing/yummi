from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import UserPreferenceProfile
from ..services.meals import get_meal_manifest

logger = logging.getLogger(__name__)


ALLOWED_RESPONSE_VALUES = {"like", "dislike"}
NEUTRAL_VALUES = {"neutral", "skip", "unset", ""}


@dataclass
class TagManifest:
    tags_version: str | None
    tag_to_category: Dict[str, str]
    tag_to_value: Dict[str, str]


@lru_cache
def load_tag_manifest() -> TagManifest:
    """Load the defined tags manifest once per process."""
    path_setting = get_settings().tags_manifest_path
    if not path_setting:
        logger.warning("tags_manifest_path not configured; tag validation disabled")
        return TagManifest(tags_version=None, tag_to_category={}, tag_to_value={})

    manifest_path = Path(path_setting)
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        logger.warning(
            "Tag manifest file %s not found; tag validation disabled", manifest_path
        )
        return TagManifest(tags_version=None, tag_to_category={}, tag_to_value={})
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Unable to read tag manifest %s: %s", manifest_path, exc)
        return TagManifest(tags_version=None, tag_to_category={}, tag_to_value={})

    lookup: Dict[str, str] = {}
    value_lookup: Dict[str, str] = {}
    for entry in payload.get("defined_tags", []):
        tag_id = entry.get("tag_id")
        category = entry.get("category")
        value = entry.get("value")
        if tag_id and category:
            lookup[tag_id] = category
        if tag_id and value:
            value_lookup[tag_id] = str(value)
    tags_version = payload.get("tags_version")
    return TagManifest(tags_version=tags_version, tag_to_category=lookup, tag_to_value=value_lookup)


def _normalize_state(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized or normalized in NEUTRAL_VALUES:
        return None
    if normalized not in ALLOWED_RESPONSE_VALUES:
        raise ValueError(f"Unsupported preference state '{value}'")
    return normalized


def _normalize_responses(raw: Dict[str, Dict[str, str]] | None) -> Dict[str, Dict[str, str]]:
    if not raw:
        return {}
    normalized: Dict[str, Dict[str, str]] = {}
    for category_id, tags in raw.items():
        if not isinstance(tags, dict):
            raise ValueError(f"Category '{category_id}' must contain an object of tag states")
        category_key = str(category_id)
        filtered: Dict[str, str] = {}
        for tag_id, state in tags.items():
            normalized_state = _normalize_state(state)
            if normalized_state:
                filtered[str(tag_id)] = normalized_state
        if filtered:
            normalized[category_key] = filtered
    return normalized


def _derive_tag_sets(
    responses: Dict[str, Dict[str, str]], manifest: TagManifest
) -> Tuple[Dict[str, list[str]], Dict[str, list[str]]]:
    likes: Dict[str, list[str]] = {}
    dislikes: Dict[str, list[str]] = {}
    validate_tags = bool(manifest.tag_to_category)
    for category_key, tags in responses.items():
        for tag_id, state in tags.items():
            if validate_tags and tag_id not in manifest.tag_to_category:
                raise ValueError(f"Unknown tag_id '{tag_id}' not present in defined_tags manifest")
            target = likes if state == "like" else dislikes
            resolved_category = manifest.tag_to_category.get(tag_id, category_key)
            bucket = target.setdefault(resolved_category, [])
            if tag_id not in bucket:
                bucket.append(tag_id)
    return likes, dislikes


def _coerce_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_user_preference_profile(
    session: AsyncSession,
    user_id: str,
) -> UserPreferenceProfile | None:
    return await session.get(UserPreferenceProfile, user_id)


async def upsert_user_preference_profile(
    session: AsyncSession,
    *,
    user_id: str,
    tags_version: str,
    responses: Dict[str, Dict[str, str]] | None,
    completion_stage: str | None = None,
    completed_at: datetime | None = None,
) -> Tuple[UserPreferenceProfile, TagManifest]:
    manifest = load_tag_manifest()
    normalized_responses = _normalize_responses(responses)
    likes, dislikes = _derive_tag_sets(normalized_responses, manifest)
    completion = completion_stage or ("complete" if completed_at else "in_progress")
    completed_ts = _coerce_datetime(completed_at)
    profile = await get_user_preference_profile(session, user_id)
    if not profile:
        profile = UserPreferenceProfile(user_id=user_id)
        session.add(profile)
    profile.tags_version = tags_version
    profile.responses = normalized_responses
    profile.selected_tags = likes
    profile.disliked_tags = dislikes
    profile.completion_stage = completion
    profile.completed_at = completed_ts
    profile.last_synced_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(profile)
    return profile, manifest


def serialize_preference_profile(
    profile: UserPreferenceProfile | None,
    manifest: TagManifest,
    *,
    include_latest_recommendation_details: bool = False,
) -> Dict[str, object]:
    payload = {
        "tagsVersion": profile.tags_version if profile else None,
        "manifestTagsVersion": manifest.tags_version,
        "responses": profile.responses if profile else {},
        "selectedTags": profile.selected_tags if profile else {},
        "dislikedTags": profile.disliked_tags if profile else {},
        "completionStage": profile.completion_stage if profile else None,
        "completedAt": profile.completed_at,
        "lastSyncedAt": profile.last_synced_at if profile else None,
        "updatedAt": profile.updated_at if profile else None,
        "latestRecommendations": profile.latest_recommendation_meal_ids if profile else None,
        "latestRecommendationsGeneratedAt": profile.latest_recommendation_generated_at if profile else None,
        "latestRecommendationsManifestId": profile.latest_recommendation_manifest_id if profile else None,
    }
    if include_latest_recommendation_details and profile and profile.latest_recommendation_meal_ids:
        payload["latestRecommendationMeals"] = _materialize_latest_recommendation_meals(
            profile.latest_recommendation_meal_ids
        )
    else:
        payload["latestRecommendationMeals"] = []
    return payload


async def update_latest_recommendations(
    session: AsyncSession,
    *,
    user_id: str,
    meal_ids: list[str],
    manifest_id: str | None,
    generated_at: datetime | None,
) -> None:
    profile = await get_user_preference_profile(session, user_id)
    if not profile:
        raise ValueError(f"User preference profile '{user_id}' not found")
    profile.latest_recommendation_meal_ids = meal_ids
    profile.latest_recommendation_manifest_id = manifest_id
    profile.latest_recommendation_generated_at = _coerce_datetime(generated_at)
    await session.commit()


def _materialize_latest_recommendation_meals(meal_ids: list[str]) -> list[dict[str, object]]:
    if not meal_ids:
        return []
    manifest = get_meal_manifest()
    lookup: Dict[str, dict[str, object]] = {}
    for archetype in manifest.get("archetypes", []):
        archetype_id = archetype.get("uid")
        for meal in archetype.get("meals", []):
            meal_id = str(meal.get("meal_id") or meal.get("mealId") or "")
            if not meal_id:
                continue
            final_ingredients = meal.get("final_ingredients") or meal.get("ingredients") or []
            ingredient_names = []
            for entry in final_ingredients:
                if isinstance(entry, dict) and entry.get("core_item_name"):
                    ingredient_names.append(str(entry["core_item_name"]))
                elif isinstance(entry, dict) and entry.get("name"):
                    ingredient_names.append(str(entry["name"]))
                elif isinstance(entry, str):
                    ingredient_names.append(entry)
            lookup[meal_id] = {
                "mealId": meal_id,
                "name": meal.get("name"),
                "description": meal.get("description"),
                "tags": meal.get("meal_tags") or {},
                "keyIngredients": ingredient_names,
                "prepSteps": meal.get("prep_steps") or [],
                "cookSteps": meal.get("cook_steps") or meal.get("instructions") or [],
                "ingredients": _format_final_ingredients(final_ingredients),
                "archetypeId": archetype_id,
            }
    ordered: list[dict[str, object]] = []
    for meal_id in meal_ids:
        detail = lookup.get(meal_id)
        if detail:
            ordered.append(detail)
    return ordered


def _format_final_ingredients(entries: list[Any]) -> list[dict[str, object]]:
    formatted: list[dict[str, object]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            formatted.append({"name": entry})
            continue
        if not isinstance(entry, dict):
            continue
        product = entry.get("selected_product") or {}
        formatted.append(
            {
                "name": entry.get("core_item_name")
                or entry.get("name")
                or entry.get("ingredient"),
                "quantity": entry.get("quantity"),
                "preparation": entry.get("preparation"),
                "productName": product.get("name"),
                "productId": product.get("product_id"),
                "detailUrl": product.get("detail_url"),
                "salePrice": product.get("sale_price"),
                "packageQuantity": product.get("package_quantity"),
            }
        )
    return formatted
