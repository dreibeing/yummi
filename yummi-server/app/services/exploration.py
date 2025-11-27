from __future__ import annotations

import json
import logging
import asyncio
from collections import deque, defaultdict
from datetime import datetime, timezone
from time import perf_counter
from textwrap import dedent
from typing import Any, Dict, List, Callable
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
    MAX_CANDIDATE_POOL_LIMIT,
)
from .filtering import CandidateMealDetail, generate_candidate_pool_with_details
from .meals import get_meal_manifest
from .meal_representation import extract_key_ingredients, extract_sku_snapshot, format_json
from .openai_responses import call_openai_responses
from .exploration_tracker import register_background_run
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
    meal_target = _resolve_meal_target(settings.exploration_meal_count, request.mealCount)
    logger.info(
        "Exploration run starting user=%s model=%s candidate_limit=%s meal_target=%s",
        user_id,
        settings.openai_exploration_model,
        candidate_limit,
        meal_target,
    )
    filter_request = CandidateFilterRequest(limit=MAX_CANDIDATE_POOL_LIMIT)
    filter_response, detail_records = generate_candidate_pool_with_details(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=filter_request,
        user_id=user_id,
    )
    logger.info(
        "Exploration candidate pool built user=%s total_candidates=%s returned=%s",
        user_id,
        filter_response.totalCandidates,
        filter_response.returnedCount,
    )
    if filter_response.returnedCount == 0 or not detail_records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No meals available for the selected preferences. Please adjust your constraints.",
        )

    profile_payload = serialize_preference_profile(profile, tag_manifest)
    archetype_batches = _build_archetype_batches(detail_records, candidate_limit)
    if not archetype_batches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible archetype meal batches were found for exploration.",
        )

    streamed_meal_ids: Dict[str, List[str]] = defaultdict(list)

    def handle_streamed_meal(archetype_uid: str, meal_id: str) -> None:
        if not archetype_uid or not meal_id:
            return
        streamed_meal_ids[archetype_uid].append(meal_id)
        logger.info(
            "Exploration stream update user=%s archetype=%s meal_id=%s",
            user_id,
            archetype_uid,
            meal_id,
        )

    timeout_seconds = settings.exploration_stream_timeout_seconds
    archetype_payloads, pending_tasks = await _score_archetype_batches(
        user_id=user_id,
        profile_payload=profile_payload,
        archetype_batches=archetype_batches,
        settings=settings,
        on_stream_meal=handle_streamed_meal,
        timeout_seconds=timeout_seconds,
        get_streamed_ids=lambda uid: list(streamed_meal_ids.get(uid, [])),
    )

    archetype_meal_map: Dict[str, List[ExplorationMeal]] = {}
    raw_payload: Dict[str, Any] = {
        "archetypeResponses": {},
        "streamedMealIds": _snapshot_streamed_meal_ids(streamed_meal_ids),
    }
    for archetype_uid, parsed_payload, batch_details in archetype_payloads:
        raw_payload["archetypeResponses"][archetype_uid] = parsed_payload
        selections = parsed_payload.get("explorationSet") or []
        meals = _materialize_meals(selections, batch_details, None)
        if meals:
            archetype_meal_map[archetype_uid] = meals

    exploration_meals = _balance_archetype_meals(archetype_meal_map, meal_target)
    if not exploration_meals:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Exploration model returned no meals.",
        )
    if len(exploration_meals) < meal_target:
        logger.warning(
            "Exploration run returned %s meals (target %s)",
            len(exploration_meals),
            meal_target,
        )
    random.shuffle(exploration_meals)

    record = await _persist_session(
        user_id=user_id,
        manifest=manifest,
        filter_metadata={
            "candidatePoolId": filter_response.candidatePoolId,
            "totalCandidates": filter_response.totalCandidates,
            "returnedCandidates": filter_response.returnedCount,
        },
        meals=exploration_meals,
        raw_payload=raw_payload,
        notes=[],
    )

    if pending_tasks:
        async def _persist_stream_snapshot(stream_snapshot: Dict[str, List[str]]) -> None:
            await _persist_streamed_ids(record.id, stream_snapshot)

        register_background_run(
            session_id=str(record.id),
            streamed_ids=streamed_meal_ids,
            pending_tasks=pending_tasks,
            persist_callback=_persist_stream_snapshot,
        )

    return ExplorationRunResponse(
        sessionId=str(record.id),
        status=record.status,
        meals=exploration_meals,
        infoNotes=[],
    )


async def fetch_exploration_session(user_id: str, session_id: uuid.UUID) -> ExplorationRunResponse:
    async with get_session() as session:
        record = await session.get(MealExplorationSession, session_id)
    if not record or record.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exploration session not found")

    meals_payload = (record.exploration_results or {}).get("meals") or []
    meals = [ExplorationMeal(**meal) for meal in meals_payload]
    return ExplorationRunResponse(
        sessionId=str(record.id),
        status=record.status,
        meals=meals,
        infoNotes=[],
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


def _build_archetype_batches(
    details: List[CandidateMealDetail],
    per_archetype_limit: int,
) -> List[tuple[str, List[CandidateMealDetail]]]:
    if per_archetype_limit <= 0:
        return []
    buckets: Dict[str, List[CandidateMealDetail]] = {}
    for detail in details:
        archetype_uid = detail.archetype_uid or "unassigned"
        buckets.setdefault(archetype_uid, []).append(detail)
    batches: List[tuple[str, List[CandidateMealDetail]]] = []
    for archetype_uid, records in buckets.items():
        if not records:
            continue
        random.shuffle(records)
        take = min(len(records), per_archetype_limit)
        batches.append((archetype_uid, records[:take]))
    return batches


async def _score_archetype_batches(
    *,
    user_id: str,
    profile_payload: Dict[str, Any],
    archetype_batches: List[tuple[str, List[CandidateMealDetail]]],
    settings,
    on_stream_meal: Callable[[str, str], None] | None = None,
    timeout_seconds: int | None = None,
    get_streamed_ids: Callable[[str], List[str]] | None = None,
) -> tuple[List[tuple[str, Dict[str, Any], List[CandidateMealDetail]]], List[asyncio.Task]]:
    tasks = []
    for archetype_uid, batch_details in archetype_batches:
        task = asyncio.create_task(
            _score_single_archetype_batch(
                user_id=user_id,
                profile_payload=profile_payload,
                archetype_uid=archetype_uid,
                batch_details=batch_details,
                settings=settings,
                on_stream_meal=on_stream_meal,
            )
        )
        tasks.append((task, archetype_uid, batch_details))

    task_objects = [task for task, _, _ in tasks]
    if timeout_seconds and timeout_seconds > 0:
        done, _pending = await asyncio.wait(task_objects, timeout=timeout_seconds)
    else:
        done, _pending = await asyncio.wait(task_objects)

    results: List[tuple[str, Dict[str, Any], List[CandidateMealDetail]]] = []
    pending_tasks: List[asyncio.Task] = []
    for task, archetype_uid, batch_details in tasks:
        if task in done:
            try:
                results.append(task.result())
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Exploration archetype run failed user=%s archetype=%s error=%s",
                    user_id,
                    archetype_uid,
                    exc,
                )
                fallback_payload = _build_stream_fallback_payload(
                    archetype_uid,
                    get_streamed_ids,
                )
                results.append((archetype_uid, fallback_payload, batch_details))
        else:
            logger.warning(
                "Exploration archetype run timed out user=%s archetype=%s timeout=%ss",
                user_id,
                archetype_uid,
                timeout_seconds,
            )
            fallback_payload = _build_stream_fallback_payload(
                archetype_uid,
                get_streamed_ids,
            )
            results.append((archetype_uid, fallback_payload, batch_details))
            pending_tasks.append(task)
    return results, pending_tasks


async def _score_single_archetype_batch(
    *,
    user_id: str,
    profile_payload: Dict[str, Any],
    archetype_uid: str,
    batch_details: List[CandidateMealDetail],
    settings,
    on_stream_meal: Callable[[str, str], None] | None,
) -> tuple[str, Dict[str, Any], List[CandidateMealDetail]]:
    stream_accumulator = _StreamingMealAccumulator(archetype_uid, on_stream_meal)
    candidates = _prepare_llm_candidates(batch_details, len(batch_details))
    system_prompt, user_prompt = _build_prompts(profile_payload, candidates, None)
    llm_start = perf_counter()
    llm_text = await asyncio.to_thread(
        call_openai_responses,
        model=settings.openai_exploration_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=settings.openai_exploration_max_output_tokens,
        top_p=settings.openai_exploration_top_p,
        reasoning_effort=settings.openai_exploration_reasoning_effort,
        stream=True,
        on_stream_delta=stream_accumulator.handle_delta,
    )
    duration = perf_counter() - llm_start
    logger.info(
        "Exploration archetype run finished user=%s archetype=%s duration=%.2fs candidates=%s",
        user_id,
        archetype_uid,
        duration,
        len(batch_details),
    )
    final_text = llm_text or stream_accumulator.buffer
    parsed = _parse_llm_payload(final_text)
    return archetype_uid, parsed, batch_details


def _balance_archetype_meals(
    archetype_meals: Dict[str, List[ExplorationMeal]],
    desired_total: int,
) -> List[ExplorationMeal]:
    queues: Dict[str, deque[ExplorationMeal]] = {
        uid: deque(meals) for uid, meals in archetype_meals.items() if meals
    }
    if not queues:
        return []
    order = list(queues.keys())
    random.shuffle(order)
    selections: List[ExplorationMeal] = []
    while order and len(selections) < desired_total:
        next_round: List[str] = []
        for uid in order:
            queue = queues.get(uid)
            if not queue:
                continue
            selections.append(queue.popleft())
            if queue:
                next_round.append(uid)
            if len(selections) >= desired_total:
                break
        order = next_round
    return selections


def _build_preference_guide(profile_payload: Dict[str, Any]) -> Dict[str, Any]:
    selected = profile_payload.get("selectedTags") or {}
    disliked = profile_payload.get("dislikedTags") or {}
    responses = profile_payload.get("responses") or {}
    response_categories = {str(key) for key in responses.keys()}
    liked_categories = {str(key) for key in selected.keys()}
    disliked_categories = {str(key) for key in disliked.keys()}
    neutral_categories = sorted(response_categories - liked_categories - disliked_categories)
    return {
        "highConfidenceLikes": _format_tag_category_map(selected),
        "softDislikes": _format_tag_category_map(disliked),
        "neutralOrUnprovenCategories": neutral_categories,
        "notes": [
            "Likes are strong positive priors but still soft constraints.",
            "Dislikes indicate caution but remain eligible unless explicitly declined elsewhere.",
            "Neutral or unmentioned categories are ideal for exploratory picks.",
        ],
    }


def _format_tag_category_map(tag_map: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for category, tags in sorted(tag_map.items()):
        normalized_tags = sorted(str(tag) for tag in tags or [] if tag)
        if not normalized_tags:
            continue
        formatted.append(
            {
                "category": str(category),
                "tags": normalized_tags,
            }
        )
    return formatted


def _build_prompts(
    profile_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    meal_target: int | None,
) -> tuple[str, str]:
    preference_guide = _build_preference_guide(profile_payload)
    system_prompt = (
        "You are Yummi's exploration planner. Select meals from the provided candidate list. "
        "All hard constraints have already been applied (Audience, Diet/Ethics, Avoidances/Allergens). "
        "Treat all remaining categories as preference-strength guides, not hard filters. Recommend a full set the user will likely enjoy while keeping reasonable diversity so we can learn from like/dislike feedback. "
        "Always respect the contract and return valid JSON."
    )
    requirement_lines: List[str] = []
    if meal_target is not None:
        requirement_lines.append(
            f"Choose exactly {meal_target} meals when the candidate list has at least {meal_target} entries; only return fewer if the candidate pool itself is smaller. Focus on removing only the meals that are clearly wrong for the user and retain every other plausible option so the follow-up recommender can make the final call. Never invent meal IDs."
        )
    else:
        requirement_lines.append(
            "Review the provided meals and return the subset that best matches the USER_PROFILE. Err on the side of keeping meals unless they are obvious mismatches so the recommendation stage has room to refine. You may return any number of meals from 1 up to the provided candidates. Never invent meal IDs."
        )
    requirement_lines.extend(
        [
            "Primary objective: remove only the meals the user would definitely dislike while keeping every meal they would definitely or possibly enjoy. Secondary objective: maintain reasonable diversity so reactions provide useful learning for the next stage.",
            "Treat USER_PROFILE thumbs (\"selectedTags\" vs. \"dislikedTags\") as probability hints: likes are strong positives, dislikes are down-weighted but still eligible unless explicitly declined, and everything else is exploratory signal.",
            "Only explicitly declined meals (if surfaced elsewhere) are true exclusions. All candidates already satisfy Audience, Diet/Ethics, and Allergen requirements.",
            "Aim for roughly 70% high-confidence fits (align with likes), 20% near-miss or neutral options, and 10% exploratory curveballs to validate preference boundaries; adjust proportions gracefully when the pool is small.",
            "NutritionFocus (and similar categories) are preferences only; treat \"NoNutritionFocus\" as neutral. Do not hard-filter on these.",
            "Ensure diversity across cuisine, proteins, heat levels, prep time, complexity, and equipment so the next stage receives informative reactions.",
            "Respond in JSON: {\"explorationSet\":[{\"meal_id\": \"...\"}]}",
            "Never invent meals, tags, or SKUs. Use only provided data.",
        ]
    )
    enumerated_requirements = "\n".join(
        f"{index}. {line}" for index, line in enumerate(requirement_lines, start=1)
    )
    instructions = dedent(
        f"""
        USER_PROFILE:
        {format_json(profile_payload)}

        PREFERENCE_GUIDE:
        {format_json(preference_guide)}

        CANDIDATE_MEALS:
        {format_json(candidates)}

        Requirements:
        {enumerated_requirements}
        """
    ).strip()
    return system_prompt, instructions


class _StreamingMealAccumulator:
    def __init__(
        self,
        archetype_uid: str,
        callback: Callable[[str, str], None] | None,
    ) -> None:
        self.archetype_uid = archetype_uid
        self.callback = callback
        self.buffer: str = ""
        self._emitted: set[str] = set()

    def handle_delta(self, delta: str | None) -> None:
        if not delta:
            return
        self.buffer += delta
        self._try_emit_ids()

    def _try_emit_ids(self) -> None:
        if not self.callback:
            return
        try:
            payload = json.loads(self.buffer)
        except json.JSONDecodeError:
            return
        selections = payload.get("explorationSet") or []
        for selection in selections:
            meal_id = selection.get("meal_id")
            if not meal_id or meal_id in self._emitted:
                continue
            self._emitted.add(meal_id)
            self.callback(self.archetype_uid, meal_id)


def _build_stream_fallback_payload(
    archetype_uid: str,
    get_streamed_ids: Callable[[str], List[str]] | None,
) -> Dict[str, Any]:
    meal_ids = get_streamed_ids(archetype_uid) if get_streamed_ids else []
    exploration_set = [{"meal_id": meal_id} for meal_id in meal_ids]
    return {"explorationSet": exploration_set}


def _resolve_meal_target(default_target: int, requested: int | None) -> int:
    if requested is None:
        return default_target
    return min(requested, default_target)


def _snapshot_streamed_meal_ids(streamed_meal_ids: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {uid: list(meals) for uid, meals in streamed_meal_ids.items()}


async def _persist_streamed_ids(session_id: uuid.UUID, streamed_meal_ids: Dict[str, List[str]]) -> None:
    snapshot = _snapshot_streamed_meal_ids(streamed_meal_ids)
    async with get_session() as session:
        record = await session.get(MealExplorationSession, session_id)
        if not record:
            return
        results = dict(record.exploration_results or {})
        raw_payload = dict(results.get("rawResponse") or {})
        raw_payload["streamedMealIds"] = snapshot
        results["rawResponse"] = raw_payload
        record.exploration_results = results
        await session.commit()


def _parse_llm_payload(raw_text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            logger.warning("Exploration model returned non-object payload; ignoring.")
            return {"explorationSet": []}
        return parsed
    except json.JSONDecodeError as exc:
        logger.warning("Exploration model returned invalid JSON (treating as empty): %s", exc)
        return {"explorationSet": []}


def _materialize_meals(
    selections: List[Dict[str, Any]],
    detail_records: List[CandidateMealDetail],
    meal_target: int | None,
) -> List[ExplorationMeal]:
    lookup = {detail.meal.get("meal_id"): detail for detail in detail_records}
    meals: List[ExplorationMeal] = []
    seen_ids: set[str] = set()
    for selection in selections:
        meal_id = selection.get("meal_id")
        detail = lookup.get(meal_id)
        if not detail:
            continue
        hydrated = _hydrate_exploration_meal(detail)
        meals.append(hydrated)
        seen_ids.add(hydrated.mealId)
        if meal_target is not None and len(meals) >= meal_target:
            break

    if meal_target is not None and len(meals) < meal_target:
        logger.warning(
            "Exploration model returned %s selections (target %s); auto-filling remainder",
            len(meals),
            meal_target,
        )
        for detail in detail_records:
            meal_id = str(detail.meal.get("meal_id"))
            if not meal_id or meal_id in seen_ids:
                continue
            hydrated = _hydrate_exploration_meal(detail)
            meals.append(hydrated)
            seen_ids.add(hydrated.mealId)
            if len(meals) >= meal_target:
                break

    return meals


def _hydrate_exploration_meal(
    detail: CandidateMealDetail,
    rationale: str | None = None,
    expected: str | None = None,
    diversity_axes: List[str] | None = None,
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
        diversityAxes=diversity_axes or [],
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
