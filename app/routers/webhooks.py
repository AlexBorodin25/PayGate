import logging
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.background_tasks import run_fulfillment
from app.config import settings
from app.db import get_db
from app.models import (
    FulfillmentStatus,
    Order,
    OrderStatus,
    ProcessedWebhookEvent,
    Product,
)

router = APIRouter(tags=["Stripe Webhooks"])
logger = logging.getLogger(__name__)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/webhooks/stripe",
    summary="Receive Stripe webhook events",
    description=(
        "Verifies the Stripe signature, processes paid checkout.session.completed "
        "events, reconciles the Stripe session against the stored order, and updates "
        "payment and fulfillment state."
    ),
    responses={
        400: {"description": "Invalid, or unverifiable Stripe webhook payload"},
        500: {"description": "Temporary database error"},
    },
)
async def stripe_webhook(
    request: Request,
    db: DatabaseSession,
    background_tasks: BackgroundTasks,
    sig_header: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, bool]:
    if sig_header is None:
        raise HTTPException(status_code=400, detail="Stripe signature is required")

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
            payload,
            sig_header,
            settings.stripe_webhook_secret,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook payload.",
        ) from error
    except stripe.SignatureVerificationError as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe signature.",
        ) from error

    event_id = event.id
    if event_id is None:
        logger.warning("Stripe webhook missing event id.")
        return {"received": True}

    if event.type != "checkout.session.completed":
        return {"received": True}

    session = event.data.object

    if session.payment_status != "paid":
        return {"received": True}

    order_id = session.client_reference_id
    metadata = session.metadata or {}
    product_id = metadata.get("product_id")

    if order_id is None or product_id is None:
        logger.warning("Stripe webhook missing checkout metadata.")
        return {"received": True}

    try:
        parsed_order_id = int(order_id)
    except ValueError:
        logger.warning("Stripe webhook had malformed order_id=%s", order_id)
        return {"received": True}

    payment_intent = session.payment_intent
    should_schedule_fulfillment = False

    try:
        async with db.begin():
            db.add(ProcessedWebhookEvent(event_id=event_id))
            await db.flush()

            order = (
                await db.execute(
                    select(Order).where(Order.id == parsed_order_id).with_for_update()
                )
            ).scalar_one_or_none()

            if order is None:
                logger.warning(
                    "Stripe webhook referenced unknown order_id=%s.",
                    parsed_order_id,
                )
                return {"received": True}

            if order.stripe_session_id != session.id:
                logger.warning(
                    "Stripe session mismatch for order_id=%s session_id=%s event_id=%s",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.product_id != product_id:
                logger.warning(
                    "Stripe product mismatch for order_id=%s session_id=%s event_id=%s",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.amount != session.amount_total:
                logger.warning(
                    "Stripe amount mismatch for order_id=%s session_id=%s event_id=%s",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.currency.lower() != session.currency:
                logger.warning(
                    "Stripe currency mismatch for order_id=%s "
                    "session_id=%s event_id=%s",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.livemode != session.livemode:
                logger.warning(
                    "Stripe livemode mismatch for order_id=%s "
                    "session_id=%s event_id=%s",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.reservation_expires_at is None:
                order.status = OrderStatus.paid
                order.stock_reserved = False
                order.stripe_payment_intent = payment_intent
                order.fulfillment_status = FulfillmentStatus.payment_review

                logger.error(
                    "Paid order has no reservation expiration for order_id=%s "
                    "session_id=%s event_id=%s. Manual review or refund required.",
                    parsed_order_id,
                    session.id,
                    event_id,
                )
                return {"received": True}

            if order.reservation_expires_at <= datetime.now(UTC):
                if order.product_id is None:
                    order.status = OrderStatus.paid
                    order.stock_reserved = False
                    order.stripe_payment_intent = payment_intent
                    order.fulfillment_status = FulfillmentStatus.payment_review

                    logger.error(
                        "Paid order has no product_id after reservation expired "
                        "for order_id=%s session_id=%s event_id=%s. "
                        "Manual review or refund required.",
                        parsed_order_id,
                        session.id,
                        event_id,
                    )
                    return {"received": True}

                if order.stock_reserved:
                    order.stock_reserved = False
                else:
                    late_stock_update = cast(
                        CursorResult[Any],
                        await db.execute(
                            update(Product)
                            .where(Product.id == order.product_id)
                            .where(Product.quantity > 0)
                            .values(quantity=Product.quantity - 1)
                        ),
                    )

                    if late_stock_update.rowcount != 1:
                        order.status = OrderStatus.paid
                        order.stock_reserved = False
                        order.stripe_payment_intent = payment_intent
                        order.fulfillment_status = FulfillmentStatus.payment_review

                        logger.error(
                            "Paid order arrived after reservation expired but no "
                            "stock remained for order_id=%s session_id=%s "
                            "event_id=%s. Manual review or refund required.",
                            parsed_order_id,
                            session.id,
                            event_id,
                        )
                        return {"received": True}

            paid_update = cast(
                CursorResult[Any],
                await db.execute(
                    update(Order)
                    .where(Order.id == parsed_order_id)
                    .where(
                        Order.status.in_(
                            [OrderStatus.pending, OrderStatus.checkout_failed]
                        )
                    )
                    .values(
                        status=OrderStatus.paid,
                        stock_reserved=False,
                        stripe_payment_intent=payment_intent,
                        fulfillment_status=FulfillmentStatus.pending,
                    )
                ),
            )

            should_schedule_fulfillment = paid_update.rowcount == 1

            if not should_schedule_fulfillment:
                refreshed_order = await db.get(Order, parsed_order_id)

                should_schedule_fulfillment = (
                    refreshed_order is not None
                    and refreshed_order.status == OrderStatus.paid
                    and refreshed_order.fulfillment_status == FulfillmentStatus.pending
                )

    except IntegrityError:
        await db.rollback()

        logger.info("Duplicate Stripe webhook event received for event_id=%s", event_id)

        async with db.begin():
            order = (
                await db.execute(
                    select(Order).where(Order.id == parsed_order_id).with_for_update()
                )
            ).scalar_one_or_none()

            should_schedule_fulfillment = (
                order is not None
                and order.stripe_session_id == session.id
                and order.product_id == product_id
                and order.amount == session.amount_total
                and order.currency.lower() == session.currency
                and order.livemode == session.livemode
                and order.status == OrderStatus.paid
                and order.fulfillment_status == FulfillmentStatus.pending
            )

    except SQLAlchemyError as error:
        logger.exception("Transient database error while processing Stripe webhook.")
        raise HTTPException(
            status_code=500,
            detail="Temporary database error.",
        ) from error

    if should_schedule_fulfillment:
        background_tasks.add_task(
            run_fulfillment,
            parsed_order_id,
            session.id,
            event_id,
        )

    return {"received": True}
