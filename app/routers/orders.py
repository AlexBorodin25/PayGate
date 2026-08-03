import logging
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import FulfillmentStatus, Order, OrderStatus
from app.schemas import OrderListResponse, OrderResponse
from app.security import require_orders_api_key

router = APIRouter(tags=["Orders"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
RequireOrdersApiKey = Annotated[None, Depends(require_orders_api_key)]

OrderSortField = Literal[
    "created_at",
    "id",
    "amount",
    "status",
    "fulfillment_status",
    "product_id",
]
SortDirection = Literal["asc", "desc"]


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
        503: {"description": "Orders database is temporarily unavailable"},
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
    sort_by: OrderSortField = "created_at",
    sort_direction: SortDirection = "desc",
    include_total: bool = True,
) -> OrderListResponse:
    filters = []

    if status is not None:
        filters.append(Order.status == status)

    if fulfillment_status is not None:
        filters.append(Order.fulfillment_status == fulfillment_status)

    if product_id is not None:
        filters.append(Order.product_id == product_id)

    sort_columns = {
        "created_at": Order.created_at,
        "id": Order.id,
        "amount": Order.amount,
        "status": Order.status,
        "fulfillment_status": Order.fulfillment_status,
        "product_id": Order.product_id,
    }

    sort_column = sort_columns[sort_by]
    sort_expression = (
        sort_column.asc() if sort_direction == "asc" else sort_column.desc()
    )
    id_tiebreaker = Order.id.asc() if sort_direction == "asc" else Order.id.desc()

    try:
        total = 0
        total_pages = 0

        if include_total:
            total_result = await db.scalar(
                select(func.count()).select_from(Order).where(*filters)
            )
            total = total_result or 0
            total_pages = ceil(total / page_size) if total else 0

        offset = (page - 1) * page_size
        limit = page_size if include_total else page_size + 1

        rows = (
            await db.scalars(
                select(Order)
                .where(*filters)
                .order_by(sort_expression, id_tiebreaker)
                .offset(offset)
                .limit(limit)
            )
        ).all()

        has_extra_row = not include_total and len(rows) > page_size
        orders = list(rows[:page_size])

    except SQLAlchemyError as error:
        logger.exception(
            "Orders query failed page=%s page_size=%s status=%s "
            "fulfillment_status=%s product_id=%s sort_by=%s "
            "sort_direction=%s include_total=%s",
            page,
            page_size,
            status,
            fulfillment_status,
            product_id,
            sort_by,
            sort_direction,
            include_total,
        )
        raise HTTPException(
            status_code=503,
            detail="Orders database is temporarily unavailable.",
        ) from error

    return OrderListResponse(
        items=[OrderResponse.model_validate(order) for order in orders],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_next=page < total_pages if include_total else has_extra_row,
        has_previous=page > 1,
    )
