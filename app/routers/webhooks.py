import stripe
from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
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

    return {"received": True}
