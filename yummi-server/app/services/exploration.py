from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any, Dict, List
import uuid
import random

from fastapi import HTTPException, status

from ..config import get_settings
from ..db import get_session
from ..models import MealExplorationSession
from ..schemas import (
    CandidateFilterRequest,
    ExplorationMeal,
    ExplorationRunRequest,
    ExplorationRunResponse,
    IngredientSummary,
    MealSkuSnapshot,
)
from .filtering import CandidateMealDetail, generate_candidate_pool_with_details
from .meals import get_meal_manifest
from .meal_representation import extract_key_ingredients, extract_sku_snapshot, format_json
from .openai_responses import call_openai_responses
from .preferences import (
    get_user_preference_profile,
    load_tag_manifest,
    serialize_preference_profile,
)

logger = logging.getLogger(__name__)


async def run_exploration_workflow(
    *,
    user_id: str,
    request: ExplorationRunRequest,
) -> ExplorationRunResponse:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OpenAI not configured")

    manifest = get_meal_manifest()
    tag_manifest = load_tag_manifest()

    async with get_session() as session:
        profile = await get_user_preference_profile(session, user_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Preferences must be saved before running recommendations")

    candidate_limit = request.candidateLimit or settings.exploration_candidate_limit
    meal_target = request.mealCount or settings.exploration_meal_count
    filter_request = CandidateFilterRequest(limit=candidate_limit)
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
    llm_candidates = _prepare_llm_candidates(detail_records, candidate_limit)

    system_prompt, user_prompt = _build_prompts(profile_payload, llm_candidates, meal_target)
    llm_text = call_openai_responses(
        model=settings.openai_exploration_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=settings.openai_exploration_max_output_tokens,
        top_p=settings.openai_exploration_top_p,
        reasoning_effort=settings.openai_exploration_reasoning_effort,
    )
    parsed = _parse_llm_payload(llm_text)
    exploration_meals = _materialize_meals(parsed.get("explorationSet") or [], detail_records, meal_target)
    random.shuffle(exploration_meals)
    information_notes = parsed.get("information_gain_notes") or parsed.get("informationGainNotes") or []

    record = await _persist_session(
        user_id=user_id,
        manifest=manifest,
        filter_metadata={
            "candidatePoolId": filter_response.candidatePoolId,
            "totalCandidates": filter_response.totalCandidates,
            "returnedCandidates": filter_response.returnedCount,
        },
        meals=exploration_meals,
        raw_payload=parsed,
        notes=information_notes,
    )

    return ExplorationRunResponse(
        sessionId=str(record.id),
        status=record.status,
        meals=exploration_meals,
        infoNotes=information_notes,
    )


async def fetch_exploration_session(user_id: str, session_id: uuid.UUID) -> ExplorationRunResponse:
    async with get_session() as session:
        record = await session.get(MealExplorationSession, session_id)
    if not record or record.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exploration session not found")

    meals_payload = (record.exploration_results or {}).get("meals") or []
    meals = [ExplorationMeal(**meal) for meal in meals_payload]
    info_notes = (record.exploration_results or {}).get("informationGainNotes") or []
    return ExplorationRunResponse(
        sessionId=str(record.id),
        status=record.status,
        meals=meals,
        infoNotes=info_notes,
    )


async def _persist_session(
    *,
    user_id: str,
    manifest: Dict[str, Any],
    filter_metadata: Dict[str, Any],
    meals: List[ExplorationMeal],
    raw_payload: Dict[str, Any],
    notes: List[str],
) -> MealExplorationSession:
    settings = get_settings()
    record = MealExplorationSession(
        user_id=user_id,
        status="complete",
        model=settings.openai_exploration_model,
        manifest_id=manifest.get("manifest_id"),
        tags_version=manifest.get("tags_version"),
        prompt_context={
            "profileUserId": user_id,
            "filter": filter_metadata,
        },
        exploration_results={
            "meals": [meal.model_dump() for meal in meals],
            "informationGainNotes": notes,
            "rawResponse": raw_payload,
        },
        completed_at=datetime.now(timezone.utc),
    )
    async with get_session() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record


def _prepare_llm_candidates(
    details: List[CandidateMealDetail],
    limit: int,
) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for detail in details[:limit]:
        meal = detail.meal
        payload.append(
            {
                "meal_id": meal.get("meal_id"),
                "name": meal.get("name"),
                "tags": meal.get("meal_tags") or {},
            }
        )
    return payload


def _build_prompts(
    profile_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    meal_target: int,
) -> tuple[str, str]:
    system_prompt = (
        "You are Yummi's exploration planner. Select meals from the provided candidate list. "
        "All hard constraints have already been applied (Audience, Diet/Ethics, Avoidances/Allergens). "
        "Treat all remaining categories as preferences, not hard filters. Recommend a full set the user will likely enjoy while keeping reasonable diversity so we can learn from like/dislike feedback. "
        "Always respect the contract and return valid JSON."
    )
    instructions = dedent(
        f"""
        USER_PROFILE:
        {format_json(profile_payload)}

        CANDIDATE_MEALS:
        {format_json(candidates)}

        Requirements:
        1. Choose exactly {meal_target} meals when the candidate list has at least {meal_target} entries; only return fewer if the candidate pool itself is smaller. Never invent meal IDs.
        2. Primary objective: maximize expected enjoyment using preferences as soft signals. Secondary objective: maintain reasonable diversity so reactions provide useful learning.
        3. Treat USER_PROFILE thumbs ("selectedTags" vs. "dislikedTags") as "preferred"/"less preferred" hints—use them for gentle biasing, not strict inclusion/exclusion.
        4. NutritionFocus (and similar categories) are preferences only; treat "NoNutritionFocus" as neutral. Do not hard-filter on these.
        5. Do not apply additional hard filtering for Audience, Diet/Ethics, or Allergens—candidates already satisfy these. If the user selected no allergen avoidance, treat all candidates as acceptable.
        6. Ensure diversity across cuisine, proteins, heat levels, prep time, complexity, and equipment. Favor "expected_reaction":"likely_like"; include at most 3 boundary picks marked "expected_reaction":"uncertain" only if needed to cover unexplored areas without sacrificing overall expected enjoyment.
        7. Respond in JSON: {{"explorationSet":[{{"meal_id": "...", "reason_to_show": "...", "expected_reaction": "likely_like|uncertain", "diversity_axes":["Cuisine:Thai","Protein:Seafood"]}}], "information_gain_notes":["short hypotheses about preference patterns and what boundary picks test"]}}
        8. Never invent meals, tags, or SKUs. Use only provided data.
        """
    ).strip()
    return system_prompt, instructions


def _parse_llm_payload(raw_text: str) -> Dict[str, Any]:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Exploration model returned invalid JSON: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exploration model returned invalid JSON")


def _materialize_meals(
    selections: List[Dict[str, Any]],
    detail_records: List[CandidateMealDetail],
    meal_target: int,
) -> List[ExplorationMeal]:
    lookup = {detail.meal.get("meal_id"): detail for detail in detail_records}
    meals: List[ExplorationMeal] = []
    seen_ids: set[str] = set()
    for selection in selections:
        meal_id = selection.get("meal_id")
        detail = lookup.get(meal_id)
        if not detail:
            continue
        hydrated = _hydrate_exploration_meal(
            detail,
            rationale=selection.get("reason_to_show"),
            expected=selection.get("expected_reaction"),
            diversity_axes=selection.get("diversity_axes") or [],
        )
        meals.append(hydrated)
        seen_ids.add(hydrated.mealId)
        if len(meals) >= meal_target:
            break

    if len(meals) < meal_target:
        logger.warning(
            "Exploration model returned %s selections (target %s); auto-filling remainder",
            len(meals),
            meal_target,
        )
        for detail in detail_records:
            meal_id = str(detail.meal.get("meal_id"))
            if not meal_id or meal_id in seen_ids:
                continue
            hydrated = _hydrate_exploration_meal(
                detail,
                rationale="Auto-selected to complete lineup",
                expected="likely_like",
                diversity_axes=[],
            )
            meals.append(hydrated)
            seen_ids.add(hydrated.mealId)
            if len(meals) >= meal_target:
                break

    return meals


def _hydrate_exploration_meal(
    detail: CandidateMealDetail,
    rationale: str | None,
    expected: str | None,
    diversity_axes: List[str],
) -> ExplorationMeal:
    meal = detail.meal
    prep_steps = _coerce_step_list(meal.get("prep_steps"))
    cook_steps = _coerce_step_list(meal.get("cook_steps"))
    if not cook_steps:
        cook_steps = _coerce_step_list(meal.get("instructions"))
    final_ingredients = meal.get("final_ingredients") or meal.get("ingredients") or []
    return ExplorationMeal(
        mealId=str(meal.get("meal_id")),
        name=meal.get("name"),
        description=meal.get("description"),
        tags=meal.get("meal_tags") or {},
        keyIngredients=[
            IngredientSummary(
                name=item.get("name"),
                quantity=item.get("quantity"),
                productName=item.get("product"),
            )
            for item in extract_key_ingredients(meal)
        ],
        prepSteps=prep_steps,
        cookSteps=cook_steps,
        ingredients=_format_final_ingredients(final_ingredients),
        rationale=rationale,
        expectedReaction=expected,
        diversityAxes=diversity_axes,
        skuSnapshot=[
            MealSkuSnapshot(**snapshot) for snapshot in extract_sku_snapshot(meal)
        ],
    )


def _coerce_step_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [
            str(entry).strip()
            for entry in value
            if entry is not None and str(entry).strip()
        ]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return []


def _format_final_ingredients(entries: List[Any]) -> List[dict[str, object]]:
    formatted: List[dict[str, object]] = []
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
