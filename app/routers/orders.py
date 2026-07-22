from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Order
from app.schemas import OrderResponse
from app.security import require_orders_api_key

router = APIRouter(tags=["Orders"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
RequireOrdersApiKey = Annotated[None, Depends(require_orders_api_key)]


@router.get(
    "/orders",
    response_model=list[OrderResponse],
    summary="List orders",
    description=(
        "Protected operator endpoint. Lists orders with payment status, "
        "fulfillment status, and fulfilled_at so stuck orders are visible."
    ),
    responses={
        200: {"description": "Orders returned"},
        401: {"description": "Missing or invalid X-API-Key"},
    },
)
async def list_orders(
    db: DatabaseSession,
    _: RequireOrdersApiKey,
) -> list[Order]:
    result = await db.scalars(select(Order).order_by(Order.created_at.desc()))
    return list(result)
