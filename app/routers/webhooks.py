import logging
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Order, OrderStatus, Product

router = APIRouter()
logger = logging.getLogger(__name__)

DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: DatabaseSession,
    sig_header: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, bool]:
    if sig_header is None:
        raise HTTPException(status_code=400, detail="Stripe signature is required")

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.stripe_webhook_secret,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="Invalid webhook payload."
        ) from error
    except stripe.SignatureVerificationError as error:
        raise HTTPException(
            status_code=400, detail="Invalid Stripe signature."
        ) from error

    if event["type"] != "checkout.session.completed":
        return {"received": True}

    session = event["data"]["object"]

    if session.get("payment_status") != "paid":
        return {"received": True}

    order_id = session.get("client_reference_id")
    product_id = session.get("metadata", {}).get("product_id")

    if order_id is None or product_id is None:
        logger.warning("Stripe webhook missing checkout metadata.")
        return {"received": True}

    order = db.get(Order, int(order_id))

    if order is None:
        logger.warning("Stripe webhook referenced unknown order_id.")
        return {"received": True}

    if order.status == OrderStatus.paid:
        return {"received": True}

    if order.stripe_session_id != session.get("id"):
        logger.warning("Stripe session does not match order_id.")
        return {"received": True}

    if order.amount != session.get("amount_total"):
        logger.warning("Stripe amount mismatch for order_id.")
        return {"received": True}

    if order.currency.lower() != session.get("currency"):
        logger.warning("Stripe currency mismatch for order_id.")
        return {"received": True}

    if order.livemode != session.get("livemode"):
        logger.warning("Stripe livemode mismatch for order_id.")
        return {"received": True}

    product = db.get(Product, product_id)

    if product is None:
        logger.warning("Stripe webhook referenced unknown for product_id")
        return {"received": True}

    if product.quantity_in_stock <= 0:
        logger.warning("Paid order_id cannot be fulfilled: out of stock")
        return {"received": True}

    product.quantity_in_stock -= 1
    order.status = OrderStatus.paid
    order.stripe_payment_intent = session.get("payment_intent")

    db.commit()

    return {"received": True}

