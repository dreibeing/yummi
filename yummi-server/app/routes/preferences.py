from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_principal
from ..db import get_session
from ..schemas import PreferenceProfileResponse, PreferenceSaveRequest
from ..services.preferences import (
    get_user_preference_profile,
    load_tag_manifest,
    serialize_preference_profile,
    upsert_user_preference_profile,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("", response_model=PreferenceProfileResponse)
async def get_preferences(principal=Depends(get_current_principal)):
    async with get_session() as session:
        profile = await get_user_preference_profile(session, principal.get("sub"))
    manifest = load_tag_manifest()
    payload = serialize_preference_profile(profile, manifest)
    return PreferenceProfileResponse(**payload)


@router.put("", response_model=PreferenceProfileResponse)
async def update_preferences(
    payload: PreferenceSaveRequest,
    principal=Depends(get_current_principal),
):
    async with get_session() as session:
        try:
            profile, manifest = await upsert_user_preference_profile(
                session,
                user_id=principal.get("sub"),
                tags_version=payload.tagsVersion,
                responses=payload.responses,
                completion_stage=payload.completionStage,
                completed_at=payload.completedAt,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
    response_payload = serialize_preference_profile(profile, manifest)
    return PreferenceProfileResponse(**response_payload)
