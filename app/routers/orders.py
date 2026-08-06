import logging
from math import ceil
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import FulfillmentStatus, Order, OrderStatus
from app.schemas import OrderListResponse, OrderResponse, RetryFulfillmentResponse
from app.security import require_orders_api_key
from app.services.qstash import enqueue_fulfillment

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


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Get order detail",
    description="Protected operator endpoint. Returns one order by id.",
    responses={
        200: {"description": "Order returned"},
        401: {"description": "Missing or invalid X-API-Key"},
        404: {"description": "Order not found"},
    },
)
async def get_order_detail(
    order_id: int,
    db: DatabaseSession,
    _: RequireOrdersApiKey,
) -> OrderResponse:
    order = await db.get(Order, order_id)

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")

    return OrderResponse.model_validate(order)


@router.post(
    "/orders/{order_id}/retry-fulfillment",
    response_model=RetryFulfillmentResponse,
    summary="Retry order fulfillment",
    description=(
        "Protected operator endpoint. Re-queues fulfillment for a paid order "
        "that is not already fulfilled."
    ),
    responses={
        200: {"description": "Fulfillment retry queued"},
        401: {"description": "Missing or invalid X-API-Key"},
        404: {"description": "Order not found"},
        409: {"description": "Order is not eligible for fulfillment retry"},
        503: {"description": "Fulfillment retry could not be queued"},
    },
)
async def retry_fulfillment(
    order_id: int,
    db: DatabaseSession,
    _: RequireOrdersApiKey,
) -> RetryFulfillmentResponse:
    order = await db.get(Order, order_id)

    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")

    if order.status != OrderStatus.paid:
        raise HTTPException(
            status_code=409,
            detail="Only paid orders can be retried.",
        )

    if order.fulfillment_status == FulfillmentStatus.fulfilled:
        raise HTTPException(
            status_code=409,
            detail="Order is already fulfilled.",
        )

    if order.stripe_session_id is None:
        raise HTTPException(
            status_code=409,
            detail="Order is missing a Stripe session id.",
        )

    previous_fulfillment_status = order.fulfillment_status
    order.fulfillment_status = FulfillmentStatus.pending
    await db.commit()

    try:
        await enqueue_fulfillment(
            order_id=order.id,
            session_id=order.stripe_session_id,
            event_id=f"manual_retry_{order.id}_{uuid4()}",
        )
    except Exception as error:
        order.fulfillment_status = previous_fulfillment_status
        await db.commit()

        logger.exception(
            "Manual fulfillment retry enqueue failed order_id=%s",
            order.id,
        )
        raise HTTPException(
            status_code=503,
            detail="Fulfillment retry could not be queued.",
        ) from error

    return RetryFulfillmentResponse(
        order_id=order.id,
        queued=True,
        fulfillment_status=order.fulfillment_status,
    )
