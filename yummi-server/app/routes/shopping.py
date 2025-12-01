from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..auth import get_current_principal
from ..schemas import (
    RecommendationLearningTriggerRequest,
    ShoppingListBuildRequest,
    ShoppingListBuildResponse,
)
from ..services.recommendationlearning import (
    build_learning_context,
    schedule_recommendation_learning_run,
)
from ..services.shopping_list import run_shopping_list_workflow


router = APIRouter(prefix="/shopping-list", tags=["shopping-list"])


@router.post("/build", response_model=ShoppingListBuildResponse)
async def create_shopping_list(
    payload: ShoppingListBuildRequest,
    principal=Depends(get_current_principal),
) -> ShoppingListBuildResponse:
    user_id = principal.get("sub")
    return await run_shopping_list_workflow(user_id=user_id, request=payload)


@router.post("/learning/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_recommendation_learning(
    payload: RecommendationLearningTriggerRequest,
    principal=Depends(get_current_principal),
) -> dict:
    user_id = principal.get("sub")
    schedule_recommendation_learning_run(
        user_id=user_id,
        trigger=payload.trigger,
        event_context=build_learning_context(
            request_payload={"trigger": payload.trigger, "context": payload.context},
            metadata=payload.metadata,
        ),
    )
    return {"status": "scheduled"}
