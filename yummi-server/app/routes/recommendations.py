from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..auth import get_current_principal
from ..schemas import ExplorationRunRequest, ExplorationRunResponse
from ..services.exploration import (
    fetch_exploration_session,
    run_exploration_workflow,
)

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
