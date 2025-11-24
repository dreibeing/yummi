from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import get_current_principal
from ..schemas import ShoppingListBuildRequest, ShoppingListBuildResponse
from ..services.shopping_list import run_shopping_list_workflow


router = APIRouter(prefix="/shopping-list", tags=["shopping-list"])


@router.post("/build", response_model=ShoppingListBuildResponse)
async def create_shopping_list(
    payload: ShoppingListBuildRequest,
    principal=Depends(get_current_principal),
) -> ShoppingListBuildResponse:
    user_id = principal.get("sub")
    return await run_shopping_list_workflow(user_id=user_id, request=payload)
