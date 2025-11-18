from __future__ import annotations

import json
import logging
from textwrap import dedent
from typing import Any, Dict, Iterable, List, Sequence

from fastapi import HTTPException, status

from ..config import get_settings
from ..db import get_session
from ..models import MealExplorationSession
from ..schemas import (
    CandidateFilterRequest,
    MealReaction,
    MealSkuSnapshot,
    RecommendationMeal,
    RecommendationRunRequest,
    RecommendationRunResponse,
)
from .filtering import CandidateMealDetail, generate_candidate_pool_with_details
from .meal_representation import extract_key_ingredients, extract_sku_snapshot, format_json
from .meals import get_meal_manifest
from .openai_responses import call_openai_responses
from .preferences import (
    get_user_preference_profile,
    load_tag_manifest,
    serialize_preference_profile,
    update_latest_recommendations,
)

logger = logging.getLogger(__name__)


async def run_recommendation_workflow(
    *,
    user_id: str,
    request: RecommendationRunRequest,
) -> RecommendationRunResponse:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI not configured",
        )
    manifest = get_meal_manifest()
    manifest_version = manifest.get("manifest_id")
    if request.mealVersion and manifest_version and request.mealVersion != manifest_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Requested mealVersion does not match the latest manifest",
        )
    tag_manifest = load_tag_manifest()

    async with get_session() as session:
        profile = await get_user_preference_profile(session, user_id)
        exploration_session = None
        if request.explorationSessionId:
            exploration_session = await session.get(MealExplorationSession, request.explorationSessionId)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preferences must be saved before running recommendations",
        )
    if request.explorationSessionId and (not exploration_session or exploration_session.user_id != user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exploration session not found",
        )

    candidate_limit = request.candidateLimit or settings.recommendation_candidate_limit
    meal_target = request.mealCount or settings.recommendation_meal_count
    declined_ids = _merge_declined_ids(request.declinedMealIds, request.reactions)
    filter_request = CandidateFilterRequest(
        mealVersion=request.mealVersion,
        hardConstraints=request.hardConstraints,
        declinedMealIds=list(declined_ids),
        limit=candidate_limit,
    )
    filter_response, detail_records = generate_candidate_pool_with_details(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=filter_request,
        user_id=user_id,
    )
    if filter_response.returnedCount == 0 or not detail_records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No meals available for the selected preferences. Please adjust your constraints.",
        )

    profile_payload = serialize_preference_profile(profile, tag_manifest)
    feedback_payload = _build_feedback_payload(
        reactions=request.reactions,
        manifest=manifest,
        exploration_session=exploration_session,
        declined_ids=declined_ids,
    )
    candidate_payload = _prepare_candidate_payload(detail_records, candidate_limit)
    system_prompt, user_prompt = _build_prompts(
        profile_payload=profile_payload,
        feedback_payload=feedback_payload,
        candidates=candidate_payload,
        meal_target=meal_target,
    )
    llm_text = call_openai_responses(
        model=settings.openai_recommendation_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=settings.openai_recommendation_max_output_tokens,
        top_p=settings.openai_recommendation_top_p,
        reasoning_effort=settings.openai_recommendation_reasoning_effort,
    )
    parsed = _parse_llm_response(llm_text)
    meals = _materialize_recommendations(
        selections=parsed.get("recommendations") or [],
        detail_records=detail_records,
        meal_target=meal_target,
    )
    meal_ids = [meal.mealId for meal in meals]
    async with get_session() as session:
        await update_latest_recommendations(
            session,
            user_id=user_id,
            meal_ids=meal_ids,
            manifest_id=filter_response.manifestId,
            generated_at=filter_response.generatedAt,
        )

    return RecommendationRunResponse(
        generatedAt=filter_response.generatedAt,
        manifestId=filter_response.manifestId,
        tagsVersion=filter_response.tagsVersion,
        notes=parsed.get("notes") or parsed.get("variety_notes") or [],
        meals=meals,
    )


def _merge_declined_ids(
    declined_ids: Sequence[str] | None,
    reactions: Sequence[MealReaction],
) -> set[str]:
    merged = {mid for mid in (declined_ids or []) if mid}
    for reaction in reactions or []:
        if reaction.reaction == "dislike":
            merged.add(reaction.mealId)
    return merged


def _build_feedback_payload(
    *,
    reactions: Sequence[MealReaction],
    manifest: Dict[str, Any],
    exploration_session: MealExplorationSession | None,
    declined_ids: set[str],
) -> Dict[str, Any]:
    likes: List[str] = []
    dislikes: List[str] = []
    for reaction in reactions or []:
        target = likes if reaction.reaction == "like" else dislikes
        if reaction.mealId not in target:
            target.append(reaction.mealId)
    return {
        "explorationSessionId": str(exploration_session.id) if exploration_session else None,
        "likedMeals": _materialize_feedback_entries(likes, manifest, exploration_session),
        "dislikedMeals": _materialize_feedback_entries(dislikes, manifest, exploration_session),
        "declinedMealIds": sorted(declined_ids),
        "counts": {"likes": len(likes), "dislikes": len(dislikes)},
    }


def _materialize_feedback_entries(
    meal_ids: Iterable[str],
    manifest: Dict[str, Any],
    exploration_session: MealExplorationSession | None,
) -> List[Dict[str, Any]]:
    session_lookup = _session_meal_lookup(exploration_session)
    entries: List[Dict[str, Any]] = []
    for meal_id in meal_ids:
        snapshot = session_lookup.get(meal_id)
        if snapshot:
            entries.append(
                {
                    "meal_id": meal_id,
                    "name": snapshot.get("name"),
                    "tags": snapshot.get("tags") or snapshot.get("meal_tags") or {},
                    "key_ingredients": snapshot.get("keyIngredients") or snapshot.get("key_ingredients") or [],
                }
            )
            continue
        manifest_entry = _find_manifest_meal(manifest, meal_id)
        if manifest_entry:
            entries.append(
                {
                    "meal_id": meal_id,
                    "name": manifest_entry.get("name"),
                    "tags": manifest_entry.get("meal_tags") or {},
                    "key_ingredients": extract_key_ingredients(manifest_entry),
                }
            )
        else:
            entries.append({"meal_id": meal_id})
    return entries


def _session_meal_lookup(session: MealExplorationSession | None) -> Dict[str, Dict[str, Any]]:
    if not session or not session.exploration_results:
        return {}
    lookup: Dict[str, Dict[str, Any]] = {}
    for entry in session.exploration_results.get("meals") or []:
        meal_id = entry.get("mealId") or entry.get("meal_id")
        if not meal_id:
            continue
        lookup[str(meal_id)] = entry
    return lookup


def _find_manifest_meal(manifest: Dict[str, Any], meal_id: str) -> Dict[str, Any] | None:
    for archetype in manifest.get("archetypes") or []:
        for meal in archetype.get("meals") or []:
            if str(meal.get("meal_id")) == str(meal_id):
                return meal
    return None


def _prepare_candidate_payload(
    details: List[CandidateMealDetail],
    limit: int,
) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for detail in details[:limit]:
        meal = detail.meal
        tags = meal.get("meal_tags") or {}
        metadata = meal.get("metadata") or {}
        payload.append(
            {
                "meal_id": meal.get("meal_id"),
                "archetype_id": detail.archetype_uid,
                "name": meal.get("name"),
                "description": meal.get("description"),
                "tags": tags,
                "heat_level": tags.get("HeatSpice"),
                "prep_time_minutes": metadata.get("prep_time_minutes"),
                "prep_time_tags": tags.get("PrepTime") or [],
                "complexity": tags.get("Complexity"),
                "key_ingredients": extract_key_ingredients(meal),
                "sku_snapshot": extract_sku_snapshot(meal),
            }
        )
    return payload


def _build_prompts(
    *,
    profile_payload: Dict[str, Any],
    feedback_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    meal_target: int,
) -> tuple[str, str]:
    system_prompt = (
        "You are Yummi's weekly meal curator. Select meals the user will love while ensuring variety if they cooked them back-to-back. "
        "Respect every preference, never invent meals, and keep output JSON valid."
    )
    instructions = dedent(
        f"""
        USER_PROFILE:
        {format_json(profile_payload)}

        FEEDBACK_SUMMARY:
        {format_json(feedback_payload)}

        CANDIDATE_MEALS:
        {format_json(candidates)}

        Requirements:
        1. Choose exactly {meal_target} meals from the candidate list. Return them in rank order from best match to exploratory picks.
        2. Do not repeat meals the user disliked or marked as declined. Reinforce liked themes but vary cuisines, proteins, and prep times.
        3. Respond in JSON: {{"recommendations":["meal_uid_1","meal_uid_2","meal_uid_3"], "notes":["short variety notes"]}}. Only include the meal IDs in the `recommendations` array; the API will hydrate the rest of the fields.
        4. Use only information provided here. Never hallucinate new tags, meals, or SKUs.
        """
    ).strip()
    return system_prompt, instructions


def _parse_llm_response(raw_text: str) -> Dict[str, Any]:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Recommendation model returned invalid JSON: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recommendation model returned invalid JSON",
        )


def _materialize_recommendations(
    *,
    selections: List[Dict[str, Any]],
    detail_records: List[CandidateMealDetail],
    meal_target: int,
) -> List[RecommendationMeal]:
    lookup = {str(detail.meal.get("meal_id")): detail for detail in detail_records}
    hydrated: List[RecommendationMeal] = []
    normalized_selections = _normalize_selection_payload(selections)
    for index, meal_id in enumerate(normalized_selections, start=1):
        if not meal_id:
            continue
        detail = lookup.get(str(meal_id))
        if not detail:
            continue
        meal = detail.meal
        hydrated.append(
            RecommendationMeal(
                mealId=str(meal.get("meal_id")),
                name=meal.get("name"),
                description=meal.get("description"),
                tags=meal.get("meal_tags") or {},
                rank=index,
                rationale=None,
                confidence=None,
                diversityAxes=[],
                skuSnapshot=[MealSkuSnapshot(**snapshot) for snapshot in extract_sku_snapshot(meal)],
                archetypeId=detail.archetype_uid,
            )
        )
        if len(hydrated) >= meal_target:
            break

    return hydrated


def _normalize_selection_payload(selections: List[Any]) -> List[str]:
    ordered: List[str] = []
    for entry in selections or []:
        if isinstance(entry, str):
            ordered.append(entry)
            continue
        if isinstance(entry, dict):
            meal_id = entry.get("meal_id") or entry.get("mealId")
            if meal_id:
                ordered.append(meal_id)
    return ordered
