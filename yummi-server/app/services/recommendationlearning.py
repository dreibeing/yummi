from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple
from uuid import UUID

from sqlalchemy import select

from ..config import get_settings
from ..db import get_session
from ..models import RecommendationLearningRun, UserPreferenceProfile
from ..schemas import (
    CandidateFilterRequest,
    MAX_CANDIDATE_POOL_LIMIT,
    MealSkuSnapshot,
    RecommendationLearningTrigger,
    RecommendationMeal,
)
from .filtering import CandidateMealDetail, generate_candidate_pool_with_details
from .meal_feedback import MealFeedbackSummary, load_feedback_summary
from .meal_representation import extract_sku_snapshot, format_json
from .meals import get_meal_manifest
from .openai_responses import call_openai_responses
from .preferences import (
    get_user_preference_profile,
    load_tag_manifest,
    serialize_preference_profile,
    update_latest_recommendations,
)
from .exploration import (
    _build_archetype_batches as exploration_build_archetype_batches,
    _score_archetype_batches as exploration_score_archetype_batches,
    _materialize_meals as exploration_materialize_meals,
    _balance_archetype_meals as exploration_balance_archetype_meals,
)
from ..models import UserPreferenceProfile

logger = logging.getLogger(__name__)

LEARNING_STATUS_PENDING = "pending"
LEARNING_STATUS_COMPLETED = "completed"
LEARNING_STATUS_FAILED = "failed"
LEARNING_STATUS_SKIPPED = "skipped"
SHOPPING_LIST_TRIGGER: RecommendationLearningTrigger = "shopping_list_build"
WOOLWORTHS_CART_TRIGGER: RecommendationLearningTrigger = "woolworths_cart_add"


@dataclass
class RecommendationLearningContext:
    """Structured event payloads that inform the learning call."""

    request: Dict[str, Any]
    response: Dict[str, Any] | None = None
    metadata: Dict[str, Any] | None = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"request": self.request or {}}
        if self.response is not None:
            payload["response"] = self.response
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


def build_learning_context(
    *,
    request_payload: Any,
    response_payload: Any | None = None,
    metadata: Any | None = None,
) -> Dict[str, Any]:
    """Normalize arbitrary payloads into a JSON-safe structure."""

    context = RecommendationLearningContext(
        request=_json_safe(request_payload) or {},
        response=_json_safe(response_payload) or None,
        metadata=_json_safe(metadata) or None,
    )
    return context.as_dict()


def schedule_recommendation_learning_run(
    *,
    user_id: str,
    trigger: RecommendationLearningTrigger,
    event_context: Dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget helper so API responses aren't blocked by the LLM call."""

    context_keys = sorted((event_context or {}).keys())
    logger.info(
        "Scheduling recommendation learning run user=%s trigger=%s context_keys=%s",
        user_id,
        trigger,
        context_keys,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "Cannot schedule recommendation learning run; no running loop (user=%s trigger=%s)",
            user_id,
            trigger,
        )
        return

    loop.create_task(
        _run_with_guard(
            user_id=user_id,
            trigger=trigger,
            event_context=event_context or {},
        )
    )


async def _run_with_guard(
    *,
    user_id: str,
    trigger: RecommendationLearningTrigger,
    event_context: Dict[str, Any],
) -> None:
    logger.info("Recommendation learning workflow dispatch user=%s trigger=%s", user_id, trigger)
    try:
        await run_recommendation_learning_workflow(
            user_id=user_id,
            trigger=trigger,
            event_context=event_context,
        )
    except Exception:
        logger.exception("Recommendation learning workflow failed (user=%s trigger=%s)", user_id, trigger)


async def run_recommendation_learning_workflow(
    *,
    user_id: str,
    trigger: RecommendationLearningTrigger,
    event_context: Dict[str, Any] | None = None,
) -> None:
    """Persist the run, build prompts, and execute the dual-stage learning flow."""

    settings = get_settings()
    manifest = get_meal_manifest()
    tag_manifest = load_tag_manifest()
    profile, usage_snapshot = await _collect_profile_snapshot(user_id, tag_manifest)
    if not profile:
        logger.info("Skipping recommendation learning (missing profile) user=%s", user_id)
        return

    feedback_summary = await load_feedback_summary(user_id)
    normalized_event_context = _json_safe(event_context) or {}
    normalized_event_context["_feedbackSnapshot"] = feedback_summary.serialize_for_prompt()

    run, skip_reason = await _create_run_record_if_allowed(
        user_id=user_id,
        trigger=trigger,
        event_context=normalized_event_context,
        usage_snapshot=usage_snapshot,
    )
    if not run:
        logger.info(
            "Skipping recommendation learning run user=%s trigger=%s reason=%s",
            user_id,
            trigger,
            skip_reason,
        )
        return

    declined_meal_ids = set(feedback_summary.declined_meal_ids)
    declined_meal_ids.update(_extract_latest_home_feed_ids(profile))
    filter_request = CandidateFilterRequest(
        limit=MAX_CANDIDATE_POOL_LIMIT,
        declinedMealIds=list(declined_meal_ids),
    )
    filter_response, candidate_details = generate_candidate_pool_with_details(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=filter_request,
        user_id=user_id,
    )
    if not candidate_details:
        await _update_run_record(
            run.id,
            status=LEARNING_STATUS_SKIPPED,
            error_message="no_candidates",
            completed_at=datetime.now(timezone.utc),
        )
        return

    logger.info(
        "Recommendation learning candidate pool ready user=%s trigger=%s candidates=%s declined=%s",
        user_id,
        trigger,
        len(candidate_details),
        len(declined_meal_ids),
    )
    prompts_payload = {
        "trigger": trigger,
        "usageSnapshot": usage_snapshot,
        "eventContext": normalized_event_context,
        "feedback": feedback_summary.serialize_for_prompt(),
        "candidatePoolId": filter_response.candidatePoolId,
    }
    await _update_run_record(run.id, prompt_payload=prompts_payload)

    if not settings.recommendation_learning_enabled or not settings.openai_api_key:
        await _update_run_record(
            run.id,
            status=LEARNING_STATUS_SKIPPED,
            error_message="learning_disabled" if not settings.recommendation_learning_enabled else "openai_not_configured",
            completed_at=datetime.now(timezone.utc),
        )
        return

    exploration_stage = await _run_shadow_exploration(
        user_id=user_id,
        settings=settings,
        usage_snapshot=usage_snapshot,
        feedback_summary=feedback_summary,
        event_context=normalized_event_context,
        candidate_details=candidate_details,
    )
    shortlisted_ids = exploration_stage.get("mealIds") or []
    logger.info(
        "Shadow exploration stage complete user=%s shortlisted_ids=%s",
        user_id,
        len(shortlisted_ids),
    )
    shortlisted_details = _restrict_candidates(candidate_details, shortlisted_ids) or list(candidate_details)
    max_reco_candidates = settings.recommendation_learning_recommendation_candidate_limit
    if max_reco_candidates and len(shortlisted_details) > max_reco_candidates:
        shortlisted_details = random.sample(shortlisted_details, max_reco_candidates)
    logger.info(
        "Recommendation learning shortlist ready user=%s candidates_for_recommendation=%s limit=%s",
        user_id,
        len(shortlisted_details),
        max_reco_candidates,
    )

    recommendation_stage = await _run_shadow_recommendation(
        settings=settings,
        usage_snapshot=usage_snapshot,
        feedback_summary=feedback_summary,
        event_context=normalized_event_context,
        candidate_details=shortlisted_details,
    )
    final_meals: List[RecommendationMeal] = recommendation_stage.get("meals") or []
    if not final_meals:
        await _update_run_record(
            run.id,
            status=LEARNING_STATUS_FAILED,
            error_message="no_recommendations",
            response_payload={
                "exploration": exploration_stage,
                "recommendations": recommendation_stage,
            },
            completed_at=datetime.now(timezone.utc),
        )
        return

    meal_ids = [meal.mealId for meal in final_meals]
    async with get_session() as session:
        await update_latest_recommendations(
            session,
            user_id=user_id,
            meal_ids=meal_ids,
            manifest_id=filter_response.manifestId,
            generated_at=filter_response.generatedAt,
        )

    await _update_run_record(
        run.id,
        status=LEARNING_STATUS_COMPLETED,
        model=settings.openai_recommendation_learning_model,
        response_payload={
            "exploration": exploration_stage,
            "recommendations": {
                "notes": recommendation_stage.get("notes"),
                "mealIds": meal_ids,
            },
        },
        completed_at=datetime.now(timezone.utc),
    )
    logger.info(
        "Recommendation learning completed user=%s trigger=%s meals=%s manifest=%s generated_at=%s",
        user_id,
        trigger,
        meal_ids,
        filter_response.manifestId,
        filter_response.generatedAt.isoformat() if filter_response.generatedAt else None,
    )


async def _collect_profile_snapshot(
    user_id: str,
    tag_manifest,
) -> tuple[UserPreferenceProfile | None, Dict[str, Any]]:
    async with get_session() as session:
        profile = await get_user_preference_profile(session, user_id)
    serialized = serialize_preference_profile(
        profile,
        tag_manifest,
        include_latest_recommendation_details=True,
    )
    return profile, _json_safe(serialized) or {}


async def _run_shadow_exploration(
    *,
    user_id: str,
    settings,
    usage_snapshot: Dict[str, Any],
    feedback_summary: MealFeedbackSummary,
    event_context: Dict[str, Any],
    candidate_details: Sequence[CandidateMealDetail],
) -> Dict[str, Any]:
    per_archetype_limit = settings.recommendation_learning_exploration_meal_count
    if not per_archetype_limit or per_archetype_limit <= 0:
        per_archetype_limit = settings.recommendation_learning_meal_count or 1
    if not candidate_details:
        return {"mealIds": [], "notes": []}
    logger.info(
        "Shadow exploration starting user=%s candidates=%s per_archetype_limit=%s",
        user_id,
        len(candidate_details),
        per_archetype_limit,
    )
    profile_payload = dict(usage_snapshot or {})
    profile_payload["learningFeedback"] = feedback_summary.serialize_for_prompt()
    profile_payload["learningEventContext"] = event_context
    archetype_batches = exploration_build_archetype_batches(list(candidate_details), per_archetype_limit)
    if not archetype_batches:
        return {"mealIds": [], "notes": []}
    logger.info(
        "Shadow exploration batches prepared user=%s archetypes=%s",
        user_id,
        [
            f"{uid}:{len(batch)}"
            for uid, batch in archetype_batches
        ],
    )
    streamed_meal_ids: Dict[str, List[str]] = defaultdict(list)

    def handle_streamed_meal(archetype_uid: str, meal_id: str) -> None:
        if not archetype_uid or not meal_id:
            return
        streamed_meal_ids[archetype_uid].append(meal_id)
        logger.info(
            "Shadow exploration streamed meal user=%s archetype=%s meal_id=%s streamed_so_far=%s",
            user_id,
            archetype_uid,
            meal_id,
            len(streamed_meal_ids[archetype_uid]),
        )

    settings_proxy = _ShadowExplorationSettings(settings)
    timeout = settings.recommendation_learning_exploration_timeout_seconds
    logger.info(
        "Shadow exploration scoring starting user=%s archetype_batch_count=%s timeout=%s",
        user_id,
        len(archetype_batches),
        timeout,
    )
    archetype_payloads, pending_tasks = await exploration_score_archetype_batches(
        user_id=user_id,
        profile_payload=profile_payload,
        archetype_batches=archetype_batches,
        settings=settings_proxy,
        on_stream_meal=handle_streamed_meal,
        timeout_seconds=timeout,
        get_streamed_ids=lambda uid: list(streamed_meal_ids.get(uid, [])),
    )
    if pending_tasks:
        logger.info(
            "Shadow exploration cancelling %s pending archetype tasks user=%s",
            len(pending_tasks),
            user_id,
        )
        for task in pending_tasks:
            task.cancel()
    logger.info(
        "Shadow exploration responses received user=%s archetypes=%s",
        user_id,
        [archetype_uid for archetype_uid, _, _ in archetype_payloads],
    )
    archetype_meal_map: Dict[str, List[Any]] = {}
    for archetype_uid, parsed_payload, batch_details in archetype_payloads:
        selections = parsed_payload.get("explorationSet") or []
        meals = exploration_materialize_meals(selections, batch_details, None)
        if meals:
            archetype_meal_map[archetype_uid] = meals
            logger.info(
                "Shadow exploration archetype ready user=%s archetype=%s selections=%s",
                user_id,
                archetype_uid,
                len(meals),
            )
    exploration_target = settings.recommendation_learning_exploration_meal_count
    exploration_meals = exploration_balance_archetype_meals(archetype_meal_map, exploration_target)
    meal_ids = [meal.mealId for meal in exploration_meals]
    logger.info(
        "Shadow exploration shortlist prepared user=%s shortlisted=%s target=%s",
        user_id,
        len(meal_ids),
        exploration_target,
    )
    return {"mealIds": meal_ids, "notes": []}


async def _run_shadow_recommendation(
    *,
    settings,
    usage_snapshot: Dict[str, Any],
    feedback_summary: MealFeedbackSummary,
    event_context: Dict[str, Any],
    candidate_details: Sequence[CandidateMealDetail],
) -> Dict[str, Any]:
    target = settings.recommendation_learning_meal_count
    candidate_payload = _build_candidate_payload(candidate_details, limit=len(candidate_details))
    logger.info(
        "Shadow recommendation starting candidates=%s target=%s",
        len(candidate_details),
        target,
    )
    prompt_payload = {
        "userProfile": usage_snapshot,
        "feedback": feedback_summary.serialize_for_prompt(),
        "eventContext": event_context,
        "candidates": candidate_payload,
        "targetCount": target,
    }
    system_prompt = (
        "You are Yummi's recommendation refresher. Build a final lineup that stays exciting if cooked back-to-back. "
        "Blend reliable hits with inventive curveballs, but never contradict explicit dislikes."
    )
    user_prompt = (
        f"Select exactly {target} meals (or fewer if the candidate list is smaller). "
        "Return JSON {\"recommendations\":[{\"meal_id\":\"...\"}],\"notes\":[\"why\"]}. "
        f"PAYLOAD:\n```json\n{format_json(prompt_payload)}\n```"
    )
    raw = await _call_model(
        model=settings.openai_recommendation_learning_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=settings.openai_recommendation_learning_max_output_tokens,
        top_p=settings.openai_recommendation_learning_top_p,
        reasoning=settings.openai_recommendation_learning_reasoning_effort,
        timeout=settings.recommendation_learning_timeout_seconds,
    )
    parsed = _parse_json_response(raw)
    ranked_ids = _normalize_selection(parsed.get("recommendations"))
    meals = _hydrate_recommendation_meals(candidate_details, ranked_ids, target)
    logger.info(
        "Shadow recommendation completed ranked=%s hydrated=%s notes=%s",
        len(ranked_ids),
        len(meals),
        len(parsed.get("notes") or []),
    )
    return {"meals": meals, "notes": parsed.get("notes") or []}


def _restrict_candidates(
    candidate_details: Sequence[CandidateMealDetail],
    allowed_ids: Sequence[str],
) -> List[CandidateMealDetail]:
    if not allowed_ids:
        return list(candidate_details)
    allowed = {str(mid) for mid in allowed_ids}
    filtered = [detail for detail in candidate_details if str(detail.meal.get("meal_id")) in allowed]
    return filtered


def _build_candidate_payload(details: Sequence[CandidateMealDetail], limit: int) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for detail in list(details)[:limit]:
        meal = detail.meal
        payload.append(
            {
                "meal_id": meal.get("meal_id"),
                "name": meal.get("name"),
                "tags": meal.get("meal_tags") or {},
                "archetype_id": detail.archetype_uid,
            }
        )
    return payload


def _hydrate_recommendation_meals(
    details: Sequence[CandidateMealDetail],
    ranked_ids: Sequence[str],
    target: int,
) -> List[RecommendationMeal]:
    lookup = {str(detail.meal.get("meal_id")): detail for detail in details}
    hydrated: List[RecommendationMeal] = []
    seen: set[str] = set()
    for rank, meal_id in enumerate(ranked_ids, start=1):
        detail = lookup.get(str(meal_id))
        if not detail or meal_id in seen:
            continue
        hydrated.append(_build_recommendation_meal(detail, rank))
        seen.add(meal_id)
        if len(hydrated) >= target:
            break
    if len(hydrated) < target:
        for detail in details:
            meal_id = str(detail.meal.get("meal_id"))
            if meal_id in seen:
                continue
            hydrated.append(_build_recommendation_meal(detail, len(hydrated) + 1))
            seen.add(meal_id)
            if len(hydrated) >= target:
                break
    return hydrated


def _build_recommendation_meal(detail: CandidateMealDetail, rank: int) -> RecommendationMeal:
    meal = detail.meal
    return RecommendationMeal(
        mealId=str(meal.get("meal_id")),
        name=meal.get("name"),
        description=meal.get("description"),
        tags=meal.get("meal_tags") or {},
        rank=rank,
        rationale=None,
        confidence=None,
        diversityAxes=[],
        skuSnapshot=[MealSkuSnapshot(**snapshot) for snapshot in extract_sku_snapshot(meal)],
        archetypeId=detail.archetype_uid,
    )


async def _call_model(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    top_p: float | None,
    reasoning: str | None,
    timeout: int,
) -> str:
    llm_call = asyncio.to_thread(
        call_openai_responses,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=max_tokens,
        top_p=top_p,
        reasoning_effort=reasoning,
    )
    if timeout:
        return await asyncio.wait_for(llm_call, timeout=timeout)
    return await llm_call


def _parse_json_response(raw_text: str | None) -> Dict[str, Any]:
    if not raw_text:
        return {}
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("Recommendation learning model returned invalid JSON")
        return {}


def _normalize_selection(payload: Any) -> List[str]:
    ordered: List[str] = []
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, str):
                ordered.append(entry)
            elif isinstance(entry, dict):
                value = entry.get("meal_id") or entry.get("mealId")
                if value:
                    ordered.append(str(value))
    elif isinstance(payload, dict):
        value = payload.get("meal_id") or payload.get("mealId")
        if value:
            ordered.append(str(value))
    return ordered


class _ShadowExplorationSettings:
    def __init__(self, settings) -> None:
        self.openai_exploration_model = settings.openai_recommendation_learning_exploration_model
        self.openai_exploration_max_output_tokens = settings.openai_recommendation_learning_exploration_max_output_tokens
        self.openai_exploration_top_p = settings.openai_recommendation_learning_exploration_top_p
        self.openai_exploration_reasoning_effort = settings.openai_recommendation_learning_exploration_reasoning_effort


async def _create_run_record_if_allowed(
    *,
    user_id: str,
    trigger: RecommendationLearningTrigger,
    event_context: Dict[str, Any],
    usage_snapshot: Dict[str, Any],
) -> Tuple[RecommendationLearningRun | None, str | None]:
    event_fingerprint = _fingerprint_payload(event_context)
    usage_fingerprint = _fingerprint_payload(usage_snapshot)

    async with get_session() as session:
        active_stmt = (
            select(RecommendationLearningRun.id)
            .where(
                RecommendationLearningRun.user_id == user_id,
                RecommendationLearningRun.status == LEARNING_STATUS_PENDING,
            )
            .limit(1)
        )
        active_result = await session.execute(active_stmt)
        if active_result.scalar_one_or_none():
            logger.info(
                "Recommendation learning guard blocked run (active pending) user=%s trigger=%s",
                user_id,
                trigger,
            )
            return None, "active_run_in_progress"

        duplicate_stmt = (
            select(RecommendationLearningRun)
            .where(
                RecommendationLearningRun.user_id == user_id,
                RecommendationLearningRun.trigger_event == trigger,
                RecommendationLearningRun.status == LEARNING_STATUS_COMPLETED,
            )
            .order_by(RecommendationLearningRun.created_at.desc())
            .limit(1)
        )
        duplicate_result = await session.execute(duplicate_stmt)
        last_run: RecommendationLearningRun | None = duplicate_result.scalar_one_or_none()
        if last_run:
            last_event_fp = _fingerprint_payload(last_run.event_context or {})
            last_usage_fp = _fingerprint_payload(last_run.usage_snapshot or {})
            if last_event_fp == event_fingerprint and last_usage_fp == usage_fingerprint:
                logger.info(
                    "Recommendation learning guard blocked run (duplicate context) user=%s trigger=%s run_id=%s",
                    user_id,
                    trigger,
                    last_run.id,
                )
                return None, "duplicate_context"

        run = RecommendationLearningRun(
            user_id=user_id,
            trigger_event=trigger,
            status=LEARNING_STATUS_PENDING,
            event_context=event_context,
            usage_snapshot=usage_snapshot,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        logger.info(
            "Recommendation learning run scheduled user=%s trigger=%s run_id=%s",
            user_id,
            trigger,
            run.id,
        )
        return run, None


async def _update_run_record(run_id: UUID, **fields: Any) -> None:
    if not fields:
        return
    async with get_session() as session:
        run = await session.get(RecommendationLearningRun, run_id)
        if not run:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        await session.commit()


def _json_safe(payload: Any) -> Any:
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    try:
        return json.loads(json.dumps(payload, default=_stringify))
    except TypeError:
        return _stringify(payload)


def _stringify(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, RecommendationMeal):
        return value.model_dump_json()
    return str(value)


def _fingerprint_payload(payload: Any) -> str:
    normalized = _json_safe(payload) or {}
    if isinstance(normalized, str):
        return normalized
    try:
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return str(normalized)


def _extract_latest_home_feed_ids(profile: UserPreferenceProfile | None) -> set[str]:
    if not profile or not profile.latest_recommendation_meal_ids:
        return set()
    return {str(meal_id) for meal_id in profile.latest_recommendation_meal_ids if meal_id}
