from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import get_current_principal
from ..schemas import (
    MealFeedbackSubmitRequest,
    MealFeedbackRecordResponse,
    MealFeedbackSummaryResponse,
)
from ..services.meal_feedback import (
    MealFeedbackReaction,
    MealFeedbackSource,
    load_feedback_summary,
    record_single_meal_feedback,
    clear_user_feedback,
)


router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/meals", response_model=MealFeedbackSummaryResponse)
async def get_meal_feedback_summary(
    principal=Depends(get_current_principal),
) -> MealFeedbackSummaryResponse:
    user_id = principal.get("sub")
    summary = await load_feedback_summary(user_id)
    return MealFeedbackSummaryResponse(
        likedMealIds=[entry.meal_id for entry in summary.liked],
        dislikedMealIds=[entry.meal_id for entry in summary.disliked],
    )


@router.post("/meals", response_model=MealFeedbackRecordResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_meal_feedback(
    payload: MealFeedbackSubmitRequest,
    principal=Depends(get_current_principal),
) -> MealFeedbackRecordResponse:
    user_id = principal.get("sub")
    reaction_value = (payload.reaction or "").lower()
    if reaction_value not in {"like", "dislike"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_reaction")
    source_value = (payload.source or "history").lower()
    try:
        reaction = MealFeedbackReaction(reaction_value)
    except ValueError as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_reaction") from exc
    try:
        source = MealFeedbackSource(source_value)
    except ValueError:
        source = MealFeedbackSource.HISTORY

    await record_single_meal_feedback(
        user_id=user_id,
        meal_id=payload.mealId,
        reaction=reaction,
        source=source,
        metadata=payload.metadata,
    )
    return MealFeedbackRecordResponse(
        mealId=payload.mealId,
        reaction=reaction.value,
        source=source.value,
        occurredAt=None,
        context=payload.metadata,
    )


@router.delete("/meals", status_code=status.HTTP_204_NO_CONTENT)
async def clear_meal_feedback_history(
    principal=Depends(get_current_principal),
) -> None:
    user_id = principal.get("sub")
    await clear_user_feedback(user_id)
    return None
