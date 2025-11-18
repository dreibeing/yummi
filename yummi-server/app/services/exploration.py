from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any, Dict, List
import uuid

from fastapi import HTTPException, status
import httpx
from openai import OpenAI

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
    llm_text = _call_openai(
        model=settings.openai_exploration_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    parsed = _parse_llm_payload(llm_text)
    exploration_meals = _materialize_meals(parsed.get("explorationSet") or [], detail_records, meal_target)
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
                "description": meal.get("description"),
                "tags": meal.get("meal_tags") or {},
                "key_ingredients": _extract_key_ingredients(meal),
                "sku_snapshot": _extract_sku_snapshot(meal),
            }
        )
    return payload


def _extract_key_ingredients(meal: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    collection: List[Dict[str, Any]] = []
    for ingredient in (meal.get("final_ingredients") or meal.get("ingredients") or []):
        label = ingredient.get("core_item_name") or ingredient.get("ingredient_line")
        if not label:
            continue
        entry = {
            "name": label,
            "quantity": ingredient.get("quantity"),
            "product": (ingredient.get("selected_product") or {}).get("name"),
        }
        collection.append(entry)
        if len(collection) >= limit:
            break
    return collection


def _extract_sku_snapshot(meal: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    snapshots: List[Dict[str, Any]] = []
    for ingredient in meal.get("final_ingredients") or []:
        product = ingredient.get("selected_product") or {}
        if not any([product.get("product_id"), product.get("name"), product.get("detail_url"), product.get("sale_price")]):
            continue
        snapshots.append(
            {
                "productId": product.get("product_id"),
                "name": product.get("name"),
                "detailUrl": product.get("detail_url"),
                "salePrice": product.get("sale_price"),
            }
        )
        if len(snapshots) >= limit:
            break
    return snapshots


def _build_prompts(
    profile_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    meal_target: int,
) -> tuple[str, str]:
    system_prompt = (
        "You are Yummi's exploration planner. Select meals from the provided candidate list. "
        "Your goal is to produce ten meals that the user is likely to enjoy while covering diverse tags "
        "to confirm their preferences. Always respect the contract and return valid JSON."
    )
    instructions = dedent(
        f"""
        USER_PROFILE:
        {_format_json(profile_payload)}

        CANDIDATE_MEALS:
        {_format_json(candidates)}

        Requirements:
        1. Choose exactly {meal_target} meals present in the candidate list.
        2. Bias toward meals the user will likely enjoy but include at least two exploratory picks that validate different cuisines or proteins.
        3. Cover every diet/ethics tag the user selected when candidates exist.
        4. Respond in JSON: {{"explorationSet":[{{"meal_id": "...", "reason_to_show": "...", "expected_reaction": "likely_like|uncertain", "diversity_axes":["Diet:Vegan","Cuisine:Thai"]}}], "information_gain_notes":["note1","note2"]}}
        5. Never invent meals or tags. Use only provided data.
        """
    ).strip()
    return system_prompt, instructions


def _call_openai(*, model: str, system_prompt: str, user_prompt: str) -> str:
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    response_payload: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_output_tokens": settings.openai_exploration_max_output_tokens,
    }
    if settings.openai_exploration_top_p is not None:
        response_payload["top_p"] = settings.openai_exploration_top_p
    if settings.openai_exploration_reasoning_effort:
        response_payload["reasoning"] = {"effort": settings.openai_exploration_reasoning_effort}

    responses_client = getattr(client, "responses", None)
    if responses_client and hasattr(responses_client, "create"):
        response = responses_client.create(**response_payload)
        if getattr(response, "status", "completed") != "completed":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            logger.error("Exploration Responses API returned incomplete status: %s", reason)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exploration model did not complete successfully")
        text = _extract_response_text(response)
        if not text:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exploration model returned empty output")
        return text

    logger.warning("OpenAI client missing Responses API; calling REST endpoint directly")
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/responses",
            json=response_payload,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
    except httpx.HTTPError as exc:
        logger.error("HTTP error calling OpenAI Responses API: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to reach OpenAI") from exc

    if resp.status_code >= 400:
        logger.error("OpenAI Responses REST API returned %s: %s", resp.status_code, resp.text)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exploration model call failed")

    payload = resp.json()
    text = _extract_response_text(payload)
    if not text:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Exploration model returned empty output")
    return text


def _extract_response_text(response: Any) -> str:
    chunks: List[str] = []
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    for block in output or []:
        block_content = getattr(block, "content", None)
        if block_content is None and isinstance(block, dict):
            block_content = block.get("content")
        for content in block_content or []:
            part_text = getattr(content, "text", None)
            if part_text is None and isinstance(content, dict):
                part_text = content.get("text")
            if part_text:
                chunks.append(part_text)
    return "".join(chunks).strip()


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
    for selection in selections:
        meal_id = selection.get("meal_id")
        detail = lookup.get(meal_id)
        if not detail:
            continue
        meal = detail.meal
        meals.append(
            ExplorationMeal(
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
                    for item in _extract_key_ingredients(meal)
                ],
                rationale=selection.get("reason_to_show"),
                expectedReaction=selection.get("expected_reaction"),
                diversityAxes=selection.get("diversity_axes") or [],
                skuSnapshot=[
                    MealSkuSnapshot(**snapshot) for snapshot in _extract_sku_snapshot(meal)
                ],
            )
        )
        if len(meals) >= meal_target:
            break
    return meals


def _format_json(payload: Any) -> str:
    def _default(value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(payload, indent=2, default=_default)
