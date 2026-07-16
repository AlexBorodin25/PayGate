from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Order, OrderStatus
from app.schemas import CheckoutRequest, CheckoutResponse
from app.services.products import get_product

router = APIRouter()

DatabaseSession = Annotated[Session, Depends(get_db)]

stripe.api_key = settings.stripe_secret_key


@router.post("/checkout", response_model=CheckoutResponse)
def checkout(request: CheckoutRequest, db: DatabaseSession) -> CheckoutResponse:
    product = get_product(db, request.product_id)

    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    order = Order(
        amount=product.price,
        currency=product.currency,
        status=OrderStatus.pending,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            client_reference_id=str(order.id),
            metadata={
                "order_id": str(order.id),
                "product_id": str(product.id),
            },
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": product.currency.lower(),
                        "unit_amount": product.price,
                        "product_data": {
                            "name": product.name,
                            "description": product.description,
                        },
                    },
                }
            ],
            idempotency_key=f"checkout-order-{order.id}",
            success_url=f"{settings.app_base_url}/success",
            cancel_url=f"{settings.app_base_url}/cancel",
        )

    except stripe.APIConnectionError as error:
        raise HTTPException(
            status_code=503,
            detail="Stripe checkout status is unavailable.",
        ) from error

    except stripe.StripeError as error:
        order.status = OrderStatus.checkout_failed
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Could not create checkout session.",
        ) from error

    if session.url is None:
        order.status = OrderStatus.checkout_failed
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Stripe checkout session did not include a URL",
        )

    order.stripe_session_id = session.id
    db.commit()

    return CheckoutResponse(
        order_id=order.id,
        checkout_url=session.url,
    )
