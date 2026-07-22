from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import FulfillmentStatus, OrderStatus


class ProductResponse(BaseModel):
    id: str
    name: str
    price: int
    currency: str
    display_price: str
    description: str
    quantity: int

    model_config = ConfigDict(from_attributes=True)


class CheckoutRequest(BaseModel):
    product_id: str


class CheckoutResponse(BaseModel):
    order_id: int
    checkout_url: str


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stripe_session_id: str | None
    stripe_payment_intent: str | None
    amount: int
    currency: str
    status: OrderStatus
    fulfillment_status: FulfillmentStatus
    fulfilled_at: datetime | None
    created_at: datetime
