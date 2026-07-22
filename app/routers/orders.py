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
RequireOrderApiKey = Annotated[None, Depends(require_orders_api_key)]


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    db: DatabaseSession,
    _: RequireOrderApiKey,
) -> list[Order]:
    result = await db.scalars(select(Order).order_by(Order.created_at.desc()))
    return list(result)
