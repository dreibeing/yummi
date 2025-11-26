from __future__ import annotations

import json
import logging
import asyncio
from textwrap import dedent
import random
from typing import Any, Callable, Dict, Iterable, List, Sequence

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
    reaction_groups = _bucket_reactions_by_sentiment(request.reactions)
    disliked_meal_ids = set(reaction_groups.get("dislike") or [])
    liked_meal_ids = [meal_id for meal_id in (reaction_groups.get("like") or []) if meal_id]
    exploration_streamed_details: List[CandidateMealDetail] = []
    if exploration_session:
        streamed_ids = _extract_streamed_meal_ids(exploration_session)
        filtered_ids = _filter_streamed_meal_ids(
            streamed_ids,
            excluded_ids=declined_ids.union(disliked_meal_ids),
        )
        if filtered_ids:
            exploration_streamed_details = _build_detail_records_from_manifest(
                manifest=manifest,
                meal_ids=filtered_ids,
            )
    filter_request = CandidateFilterRequest(
        mealVersion=request.mealVersion,
        hardConstraints=request.hardConstraints,
        declinedMealIds=list(declined_ids),
        limit=candidate_limit,
    )
    filter_response, generated_detail_records = generate_candidate_pool_with_details(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=filter_request,
        user_id=user_id,
    )
    if (filter_response.returnedCount == 0 or not generated_detail_records) and not exploration_streamed_details:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No meals available for the selected preferences. Please adjust your constraints.",
        )
    detail_records = exploration_streamed_details or generated_detail_records
    using_streamed_candidates = bool(exploration_streamed_details)

    blocked_archetypes = _derive_blocked_archetypes(
        reaction_groups.get("dislike") or [],
        manifest,
    )
    if blocked_archetypes:
        detail_records = [
            detail
            for detail in detail_records
            if detail.archetype_uid not in blocked_archetypes
        ]
        if not detail_records:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No meals available after removing disliked archetypes. Please adjust your dislikes or try again later.",
            )

    liked_recommendations = _hydrate_preselected_recommendations(
        liked_meal_ids,
        detail_records,
        manifest,
    )
    liked_meal_ids_set = {meal.mealId for meal in liked_recommendations}
    llm_detail_records = [
        detail
        for detail in detail_records
        if str(detail.meal.get("meal_id")) not in liked_meal_ids_set
    ]

    if len(llm_detail_records) > candidate_limit:
        llm_detail_records = random.sample(llm_detail_records, candidate_limit)

    profile_payload = serialize_preference_profile(profile, tag_manifest)
    feedback_payload = _build_feedback_payload(
        reaction_groups=reaction_groups,
        manifest=manifest,
        exploration_session=exploration_session,
        declined_ids=declined_ids,
    )
    llm_meal_target = max(meal_target - len(liked_recommendations), 0)
    llm_meals: List[RecommendationMeal] = []
    parsed: Dict[str, Any] = {"recommendations": [], "notes": []}
    if llm_meal_target > 0 and llm_detail_records:
        candidate_payload_limit = len(llm_detail_records) if using_streamed_candidates else candidate_limit
        candidate_payload = _prepare_candidate_payload(llm_detail_records, candidate_payload_limit)
        system_prompt, user_prompt = _build_prompts(
            profile_payload=profile_payload,
            feedback_payload=feedback_payload,
            candidates=candidate_payload,
            meal_target=llm_meal_target,
        )

        def handle_streamed_recommendation(meal_id: str) -> None:
            logger.info("Recommendation stream update user=%s meal_id=%s", user_id, meal_id)

        stream_accumulator = _RecommendationStreamingAccumulator(handle_streamed_recommendation)
        llm_call = asyncio.to_thread(
            call_openai_responses,
            model=settings.openai_recommendation_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=settings.openai_recommendation_max_output_tokens,
            top_p=settings.openai_recommendation_top_p,
            reasoning_effort=settings.openai_recommendation_reasoning_effort,
            stream=True,
            on_stream_delta=stream_accumulator.handle_delta,
        )
        stream_timeout = settings.recommendation_stream_timeout_seconds
        llm_text: str | None = None
        try:
            if stream_timeout and stream_timeout > 0:
                llm_text = await asyncio.wait_for(llm_call, timeout=stream_timeout)
            else:
                llm_text = await llm_call
        except asyncio.TimeoutError:
            logger.warning(
                "Recommendation model timed out after %ss; using streamed results",
                stream_timeout,
            )
        final_text = llm_text or stream_accumulator.buffer
        parsed_payload: Dict[str, Any] | None = None
        if final_text:
            try:
                parsed_payload = _parse_llm_response(final_text)
            except HTTPException as exc:
                if stream_accumulator.meal_ids:
                    logger.warning(
                        "Recommendation model returned invalid JSON; using streamed fallback",
                    )
                    parsed_payload = _build_recommendation_stream_fallback(stream_accumulator.meal_ids)
                else:
                    raise exc
        elif stream_accumulator.meal_ids:
            parsed_payload = _build_recommendation_stream_fallback(stream_accumulator.meal_ids)
        if not parsed_payload:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Recommendation model returned no output",
            )
        parsed = parsed_payload
        llm_meals = _materialize_recommendations(
            selections=parsed_payload.get("recommendations") or [],
            detail_records=llm_detail_records,
            meal_target=llm_meal_target,
            random_fill=using_streamed_candidates,
        )

    final_meals = _merge_and_shuffle_recommendations(liked_recommendations, llm_meals)
    if not final_meals:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recommendation flow returned no meals.",
        )
    meal_ids = [meal.mealId for meal in final_meals]
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
        meals=final_meals,
    )


def _merge_declined_ids(
    declined_ids: Sequence[str] | None,
    reactions: Sequence[MealReaction],
) -> set[str]:
    """Return only explicit declines as hard exclusions.

    Reaction buttons (like/neutral/dislike) are handled as soft-preference
    signals inside the LLM prompt so we can continue exploring nearby options.
    """
    return {mid for mid in (declined_ids or []) if mid}


def _build_feedback_payload(
    *,
    reaction_groups: Dict[str, List[str]],
    manifest: Dict[str, Any],
    exploration_session: MealExplorationSession | None,
    declined_ids: set[str],
) -> Dict[str, Any]:
    likes = reaction_groups.get("like") or []
    neutrals = reaction_groups.get("neutral") or []
    dislikes = reaction_groups.get("dislike") or []
    return {
        "explorationSessionId": str(exploration_session.id) if exploration_session else None,
        "likedMeals": _materialize_feedback_entries(likes, manifest, exploration_session),
        "neutralMeals": _materialize_feedback_entries(neutrals, manifest, exploration_session),
        "dislikedMeals": _materialize_feedback_entries(dislikes, manifest, exploration_session),
        "declinedMealIds": sorted(declined_ids),
        "counts": {
            "likes": len(likes),
            "neutral": len(neutrals),
            "dislikes": len(dislikes),
        },
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
    meal, _ = _find_manifest_meal_with_archetype(manifest, meal_id)
    return meal


def _find_manifest_meal_with_archetype(
    manifest: Dict[str, Any], meal_id: str
) -> tuple[Dict[str, Any] | None, str | None]:
    for archetype in manifest.get("archetypes") or []:
        for meal in archetype.get("meals") or []:
            if str(meal.get("meal_id")) == str(meal_id):
                return meal, archetype.get("uid")
    return None, None


def _prepare_candidate_payload(
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
    *,
    profile_payload: Dict[str, Any],
    feedback_payload: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    meal_target: int,
) -> tuple[str, str]:
    system_prompt = (
        "You are Yummi's weekly meal curator. Select meals the user will love while ensuring variety if cooked back-to-back. "
        "All hard constraints are already applied (Audience, Diet/Ethics, Avoidances/Allergens); treat remaining tags as preferences, not hard filters. "
        "Use the FEEDBACK_SUMMARY (recent and, when present, historical likes/dislikes) as soft preference signals rather than strict rules so we continue exploring nearby options. Never invent meals and keep output JSON valid."
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
        1. Choose exactly {meal_target} meals when the candidate list has at least {meal_target} entries; only return fewer if the pool itself is smaller. Return them in rank order from best match to exploratory picks.
        2. Infer soft patterns from FEEDBACK_SUMMARY (likedMeals/neutralMeals/dislikedMeals) and USER_PROFILE (selected/disliked tags). Treat likes as strong positives, neutrals as mild/uncertain signals, and dislikes as negative signals to down-rank (not hard bans unless a meal is in declined IDs). Prefer themes seen in likes/selectedTags, gently explore neutrals, de-prioritize dislikedMeals/dislikedTags, and maintain varied cuisines, proteins, heat, and prep times while aligning to inferred preferences.
        3. NutritionFocus (and similar categories) are preferences only; treat "NoNutritionFocus" as neutral. Do not hard-filter on these.
        4. Do not add hard filters beyond the candidate pool. Audience, Diet/Ethics, and Allergens are already satisfied. If the user set no allergen avoidance, treat all candidates as acceptable.
        5. Respond in JSON: {{"recommendations":[{{"meal_id":"meal_uid_1"}},{{"meal_id":"meal_uid_2"}},{{"meal_id":"meal_uid_3"}}], "notes":["short variety notes describing learned themes and variety rationale"]}}. Each recommendation entry must only include the `meal_id` field—no names, descriptions, rationales, or other metadata.
        6. Use only provided information. Never hallucinate tags, meals, or SKUs.
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
    random_fill: bool = False,
) -> List[RecommendationMeal]:
    lookup = {str(detail.meal.get("meal_id")): detail for detail in detail_records}
    hydrated: List[RecommendationMeal] = []
    normalized_selections = _normalize_selection_payload(selections)
    seen_ids: set[str] = set()
    for index, meal_id in enumerate(normalized_selections, start=1):
        if not meal_id:
            continue
        detail = lookup.get(str(meal_id))
        if not detail:
            continue
        hydrated.append(_hydrate_recommendation_meal(detail, rank=index))
        seen_ids.add(str(detail.meal.get("meal_id")))
        if len(hydrated) >= meal_target:
            break

    if len(hydrated) < meal_target:
        logger.warning(
            "Recommendation model returned %s selections (target %s); auto-filling remainder",
            len(hydrated),
            meal_target,
        )
        remaining_details = [
            detail
            for detail in detail_records
            if detail.meal.get("meal_id") and str(detail.meal.get("meal_id")) not in seen_ids
        ]
        if random_fill and remaining_details:
            random.shuffle(remaining_details)
        next_rank = len(hydrated) + 1
        for detail in remaining_details:
            meal_id = str(detail.meal.get("meal_id"))
            if not meal_id or meal_id in seen_ids:
                continue
            hydrated.append(_hydrate_recommendation_meal(detail, rank=next_rank))
            seen_ids.add(meal_id)
            next_rank += 1
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


def _derive_blocked_archetypes(
    disliked_meal_ids: Sequence[str],
    manifest: Dict[str, Any],
) -> set[str]:
    blocked: set[str] = set()
    for meal_id in disliked_meal_ids or []:
        _, archetype_uid = _find_manifest_meal_with_archetype(manifest, meal_id)
        if archetype_uid:
            blocked.add(str(archetype_uid))
    return blocked


def _bucket_reactions_by_sentiment(
    reactions: Sequence[MealReaction],
) -> Dict[str, List[str]]:
    buckets = {"like": [], "neutral": [], "dislike": []}
    for reaction in reactions or []:
        sentiment = (reaction.reaction or "").lower()
        meal_id = reaction.mealId
        if sentiment in buckets and meal_id and meal_id not in buckets[sentiment]:
            buckets[sentiment].append(meal_id)
    return buckets


def _hydrate_recommendation_meal(
    detail: CandidateMealDetail,
    *,
    rank: int,
) -> RecommendationMeal:
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


def _extract_streamed_meal_ids(
    exploration_session: MealExplorationSession | None,
) -> List[str]:
    if not exploration_session or not exploration_session.exploration_results:
        return []
    raw_payload = exploration_session.exploration_results.get("rawResponse") or {}
    streamed_by_archetype = raw_payload.get("streamedMealIds") or {}
    ordered: List[str] = []
    for meal_ids in streamed_by_archetype.values():
        for meal_id in meal_ids or []:
            if meal_id is None:
                continue
            ordered.append(str(meal_id))
    return ordered


def _filter_streamed_meal_ids(
    meal_ids: Sequence[str],
    *,
    excluded_ids: set[str],
) -> List[str]:
    filtered: List[str] = []
    seen: set[str] = set()
    for meal_id in meal_ids or []:
        normalized = str(meal_id)
        if not normalized or normalized in seen or normalized in excluded_ids:
            continue
        filtered.append(normalized)
        seen.add(normalized)
    return filtered


def _build_detail_records_from_manifest(
    *,
    manifest: Dict[str, Any],
    meal_ids: Sequence[str],
) -> List[CandidateMealDetail]:
    details: List[CandidateMealDetail] = []
    for meal_id in meal_ids:
        meal, archetype_uid = _find_manifest_meal_with_archetype(manifest, meal_id)
        if not meal:
            continue
        details.append(
            CandidateMealDetail(
                archetype_uid=archetype_uid,
                meal=meal,
            )
        )
    return details


def _hydrate_preselected_recommendations(
    meal_ids: Sequence[str],
    detail_records: Sequence[CandidateMealDetail],
    manifest: Dict[str, Any],
) -> List[RecommendationMeal]:
    lookup = {str(detail.meal.get("meal_id")): detail for detail in detail_records}
    hydrated: List[RecommendationMeal] = []
    seen: set[str] = set()
    for meal_id in meal_ids or []:
        normalized = str(meal_id)
        if not normalized or normalized in seen:
            continue
        detail = lookup.get(normalized)
        if not detail:
            meal, archetype_uid = _find_manifest_meal_with_archetype(manifest, normalized)
            if meal:
                detail = CandidateMealDetail(archetype_uid=archetype_uid, meal=meal)
        if not detail:
            continue
        hydrated.append(_hydrate_recommendation_meal(detail, rank=len(hydrated) + 1))
        seen.add(normalized)
    return hydrated


def _merge_and_shuffle_recommendations(
    liked_meals: Sequence[RecommendationMeal],
    llm_meals: Sequence[RecommendationMeal],
) -> List[RecommendationMeal]:
    combined: List[RecommendationMeal] = []
    seen: set[str] = set()
    for meal in list(liked_meals) + list(llm_meals):
        if not meal.mealId or meal.mealId in seen:
            continue
        combined.append(meal)
        seen.add(meal.mealId)
    if not combined:
        return []
    random.shuffle(combined)
    ranked: List[RecommendationMeal] = []
    for index, meal in enumerate(combined, start=1):
        ranked.append(meal.model_copy(update={"rank": index}))
    return ranked


class _RecommendationStreamingAccumulator:
    def __init__(self, on_stream_meal: Callable[[str], None] | None = None) -> None:
        self.on_stream_meal = on_stream_meal
        self.buffer: str = ""
        self.meal_ids: List[str] = []
        self._emitted: set[str] = set()

    def handle_delta(self, delta: str | None) -> None:
        if not delta:
            return
        self.buffer += delta
        self._try_emit_ids()

    def _try_emit_ids(self) -> None:
        if not self.buffer:
            return
        try:
            payload = json.loads(self.buffer)
        except json.JSONDecodeError:
            return
        selections = payload.get("recommendations") or []
        ordered = _normalize_selection_payload(selections)
        for meal_id in ordered:
            if not meal_id or meal_id in self._emitted:
                continue
            self._emitted.add(meal_id)
            self.meal_ids.append(meal_id)
            if self.on_stream_meal:
                self.on_stream_meal(meal_id)


def _build_recommendation_stream_fallback(meal_ids: Sequence[str]) -> Dict[str, Any]:
    return {
        "recommendations": [{"meal_id": meal_id} for meal_id in meal_ids],
        "notes": [],
    }
