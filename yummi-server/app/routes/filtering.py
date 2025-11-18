from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_principal
from ..db import get_session
from ..schemas import CandidateFilterRequest, CandidateFilterResponse
from ..services.filtering import generate_candidate_pool
from ..services.meals import get_meal_manifest
from ..services.preferences import (
    get_user_preference_profile,
    load_tag_manifest,
)

router = APIRouter(prefix="/filter", tags=["filtering"])


@router.post("", response_model=CandidateFilterResponse)
async def build_candidate_pool(
    payload: CandidateFilterRequest,
    principal=Depends(get_current_principal),
) -> CandidateFilterResponse:
    user_id = principal.get("sub") if principal else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user",
        )

    manifest = get_meal_manifest()
    manifest_version = manifest.get("manifest_id")
    if payload.mealVersion and manifest_version and payload.mealVersion != manifest_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Requested mealVersion does not match the latest manifest",
        )

    async with get_session() as session:
        profile = await get_user_preference_profile(session, user_id)
    tag_manifest = load_tag_manifest()

    return generate_candidate_pool(
        manifest=manifest,
        tag_manifest=tag_manifest,
        profile=profile,
        request=payload,
        user_id=user_id,
    )
