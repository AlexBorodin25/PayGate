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
    image_url: str | None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "speaker",
                "name": "Portable Speaker",
                "description": "A waterproof Bluetooth speaker.",
                "price": 4999,
                "currency": "USD",
                "quantity": 10,
                "display_price": "49.99 USD",
                "image_url": "https://example.com/speaker.jpg",
            }
        },
    )


class CheckoutRequest(BaseModel):
    product_id: str


class CheckoutResponse(BaseModel):
    order_id: int
    checkout_url: str


class OrderResponse(BaseModel):
    id: int
    product_id: str | None
    stripe_session_id: str | None
    stripe_payment_intent: str | None
    amount: int
    currency: str
    status: OrderStatus
    fulfillment_status: FulfillmentStatus
    fulfilled_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 123,
                "product_id": "speaker",
                "stripe_session_id": "cs_test_123",
                "stripe_payment_intent": "pi_test_123",
                "amount": 4999,
                "currency": "USD",
                "status": "paid",
                "fulfillment_status": "fulfilled",
                "fulfilled_at": "2026-08-07T12:00:00Z",
                "created_at": "2026-08-07T11:59:00Z",
            }
        },
    )


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_previous: bool


class RetryFulfillmentResponse(BaseModel):
    order_id: int
    queued: bool
    fulfillment_status: FulfillmentStatus

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_id": 123,
                "queued": True,
                "fulfillment_status": "pending",
            }
        },
    )
