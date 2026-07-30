from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import FulfillmentStatus, Order, OrderStatus
from app.schemas import OrderListResponse, OrderResponse
from app.security import require_orders_api_key

router = APIRouter(tags=["Orders"])
templates = Jinja2Templates(directory="app/templates")

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
RequireOrdersApiKey = Annotated[None, Depends(require_orders_api_key)]


@router.get(
    "/orders-ui",
    summary="Orders UI",
    description="Operator page for accessing orders with an API key.",
)
async def orders_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={},
    )


@router.get(
    "/orders",
    response_model=OrderListResponse,
    summary="List orders",
    description=(
        "Protected operator endpoint. Returns paginated orders with optional "
        "status, fulfillment_status, and product_id filters."
    ),
    responses={
        200: {"description": "Orders returned"},
        401: {"description": "Missing or invalid X-API-Key"},
    },
)
async def list_orders(
    db: DatabaseSession,
    _: RequireOrdersApiKey,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: OrderStatus | None = None,
    fulfillment_status: FulfillmentStatus | None = None,
    product_id: str | None = None,
) -> OrderListResponse:
    filters = []

    if status is not None:
        filters.append(Order.status == status)

    if fulfillment_status is not None:
        filters.append(Order.fulfillment_status == fulfillment_status)

    if product_id is not None:
        filters.append(Order.product_id == product_id)

    total = await db.scalar(select(func.count()).select_from(Order).where(*filters))
    total = total or 0

    total_pages = ceil(total / page_size) if total else 0
    offset = (page - 1) * page_size

    orders = (
        await db.scalars(
            select(Order)
            .where(*filters)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()

    return OrderListResponse(
        items=[OrderResponse.model_validate(order) for order in orders],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
    )
