import logging
from typing import Annotated, Any, cast

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db, standalone_session
from app.models import FulfillmentStatus, Order, OrderStatus, ProcessedWebhookEvent

router = APIRouter(tags=["Stripe Webhooks"])
logger = logging.getLogger(__name__)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

async def run_fulfillment(order_id: int) -> None:
    async with standalone_session() as db:
        claim_update = cast(
            CursorResult[Any],
            await db.execute(
                update(Order)
                .where(Order.id == order_id)
                .where(Order.fulfillment_status == FulfillmentStatus.pending)
                .values(fulfillment_status=FulfillmentStatus.processing)
            )
        )

        if claim_update.rowcount != 1:
            logger.info("Fulfillment already claimed.")
            return

        logger.info("Fulfillment claimed.")

        await db.execute(
            update(Order)
            .where(Order.id == order_id)
            .where(Order.fulfillment_status == FulfillmentStatus.processing)
            .values(fulfillment_status=FulfillmentStatus.fulfilled)
        )

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
    product_id = metadata["product_id"] if "product_id" in metadata else None

    if order_id is None or product_id is None:
        logger.warning("Stripe webhook missing checkout metadata.")
        return {"received": True}

    try:
        parsed_order_id = int(order_id)
    except ValueError:
        logger.warning("Stripe webhook had malformed order_id", order_id)
        return {"received": True}

    payment_intent = session.payment_intent
    won_paid_transition = False

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
                    "Stripe webhook referenced unknown order_id.",
                    parsed_order_id,
                )
                return {"received": True}

            if order.stripe_session_id != session.id:
                logger.warning("Stripe session does not match order_id.")
                return {"received": True}

            if order.amount != session.amount_total:
                logger.warning("Stripe amount mismatch for order_id.")
                return {"received": True}

            if order.currency.lower() != session.currency:
                logger.warning("Stripe currency mismatch for order_id.")
                return {"received": True}

            if order.livemode != session.livemode:
                logger.warning("Stripe livemode mismatch for order_id.")
                return {"received": True}

            paid_update = cast(
                CursorResult[Any],
                await db.execute(
                    update(Order)
                    .where(Order.id == parsed_order_id)
                    .where(Order.status == OrderStatus.pending)
                    .values(
                        status=OrderStatus.paid,
                        stripe_payment_intent=payment_intent,
                        fulfillment_status=FulfillmentStatus.pending,
                    )
                ),
            )

            won_paid_transition = paid_update.rowcount == 1

    except IntegrityError:
        logger.info("Duplicate Stripe webhook event ignored")
        return {"received": True}
    except SQLAlchemyError as error:
        logger.exception("Transient database error while processing Stripe webhook.")
        raise HTTPException(
            status_code=500,
            detail="Temporary database error.",
        ) from error

    if won_paid_transition:
        background_tasks.add_task(run_fulfillment, parsed_order_id)

    return {"received": True}
