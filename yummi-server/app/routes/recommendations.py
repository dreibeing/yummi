from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_principal
from ..db import get_session
from ..schemas import (
    ExplorationRunRequest,
    ExplorationRunResponse,
    MealSkuSnapshot,
    RecommendationMeal,
    RecommendationRunRequest,
    RecommendationRunResponse,
)
from ..services.exploration import (
    fetch_exploration_session,
    run_exploration_workflow,
)
from ..services.meal_representation import extract_sku_snapshot
from ..services.meals import get_meal_manifest
from ..services.preferences import get_user_preference_profile
from ..services.recommendation import run_recommendation_workflow

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/exploration", response_model=ExplorationRunResponse)
async def create_exploration_run(
    payload: ExplorationRunRequest,
    principal=Depends(get_current_principal),
) -> ExplorationRunResponse:
    user_id = principal.get("sub")
    return await run_exploration_workflow(user_id=user_id, request=payload)


@router.get("/exploration/{session_id}", response_model=ExplorationRunResponse)
async def get_exploration_run(
    session_id: UUID,
    principal=Depends(get_current_principal),
) -> ExplorationRunResponse:
    user_id = principal.get("sub")
    return await fetch_exploration_session(user_id=user_id, session_id=session_id)


@router.post("/feed", response_model=RecommendationRunResponse)
async def create_recommendation_feed(
    payload: RecommendationRunRequest,
    principal=Depends(get_current_principal),
) -> RecommendationRunResponse:
    user_id = principal.get("sub")
    return await run_recommendation_workflow(user_id=user_id, request=payload)


@router.get("/latest", response_model=RecommendationRunResponse)
async def get_latest_recommendations(
    principal=Depends(get_current_principal),
) -> RecommendationRunResponse:
    user_id = principal.get("sub")
    manifest = get_meal_manifest()
    async with get_session() as session:
        profile = await get_user_preference_profile(session, user_id)
    if not profile or not profile.latest_recommendation_meal_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="latest_recommendations_unavailable",
        )
    meals = _hydrate_latest_meals(
        manifest=manifest,
        meal_ids=profile.latest_recommendation_meal_ids,
    )
    if not meals:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="latest_recommendations_unavailable",
        )
    generated_at = profile.latest_recommendation_generated_at or datetime.now(timezone.utc)
    manifest_id = profile.latest_recommendation_manifest_id or manifest.get("manifest_id")
    tags_version = manifest.get("tags_version")
    return RecommendationRunResponse(
        generatedAt=generated_at,
        manifestId=manifest_id,
        tagsVersion=tags_version,
        notes=[],
        meals=meals,
    )


def _hydrate_latest_meals(
    *,
    manifest: dict,
    meal_ids: list[str],
) -> list[RecommendationMeal]:
    hydrated: list[RecommendationMeal] = []
    lookup = _build_manifest_lookup(manifest)
    for rank, meal_id in enumerate(meal_ids or [], start=1):
        meal, archetype_uid = lookup.get(str(meal_id), (None, None))
        if not meal:
            continue
        hydrated.append(
            RecommendationMeal(
                mealId=str(meal_id),
                name=meal.get("name"),
                description=meal.get("description"),
                tags=meal.get("meal_tags") or {},
                rank=rank,
                rationale=None,
                confidence=None,
                diversityAxes=[],
                skuSnapshot=[MealSkuSnapshot(**snapshot) for snapshot in extract_sku_snapshot(meal)],
                archetypeId=archetype_uid,
            )
        )
    return hydrated


def _build_manifest_lookup(manifest: dict) -> dict[str, tuple[dict, str | None]]:
    lookup: dict[str, tuple[dict, str | None]] = {}
    for archetype in manifest.get("archetypes", []):
        archetype_uid = archetype.get("uid")
        for meal in archetype.get("meals") or []:
            meal_id = meal.get("meal_id")
            if meal_id is None:
                continue
            lookup[str(meal_id)] = (meal, archetype_uid)
    return lookup
