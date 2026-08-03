import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import background_tasks
from app.models import FulfillmentStatus, Order, OrderStatus, Product
from app.routers import checkout as checkout_router
from app.routers import products as products_router
from app.routers import webhooks as webhooks_router
from app.routers.checkout import release_expired_reservations
from app.services.fulfillment import fulfillment_service
from app.services.products import format_price, get_product, list_products


async def add_test_product(db_session: AsyncSession) -> Product:
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity=10,
        is_deleted=False,
    )
    db_session.add(product)
    await db_session.commit()
    return product


def fake_checkout_completed_event(
    *,
    event_id: str,
    order_id: int | str,
    product_id: str,
    amount: int = 4999,
    currency: str = "usd",
    session_id: str = "cs_test_123",
    payment_status: str = "paid",
    livemode: bool = False,
    payment_intent: str = "pi_test_123",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id,
        type="checkout.session.completed",
        data=SimpleNamespace(
            object=SimpleNamespace(
                id=session_id,
                payment_status=payment_status,
                client_reference_id=str(order_id),
                metadata={"product_id": product_id},
                amount_total=amount,
                currency=currency,
                livemode=livemode,
                payment_intent=payment_intent,
            )
        ),
    )


async def add_test_order(
    db_session: AsyncSession,
    product: Product,
) -> Order:
    order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_123",
        amount=product.price,
        currency=product.currency,
        livemode=False,
        reservation_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        stock_reserved=True,
    )

    db_session.add(order)
    await db_session.commit()

    return order


def test_format_price() -> None:
    assert format_price(4999, "usd") == "49.99 USD"


@pytest.mark.anyio
async def test_health_endpoint(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_products_endpoint(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    async def fake_list_products(db: Any) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id="speaker",
                name="Portable Speaker",
                price=4999,
                currency="USD",
                description="A waterproof Bluetooth speaker.",
                quantity=10,
            ),
            SimpleNamespace(
                id="laptop",
                name="15.6 inch Business Laptop",
                price=29999,
                currency="USD",
                description="A business laptop.",
                quantity=5,
            ),
            SimpleNamespace(
                id="camera",
                name="Full-Frame Mirrorless Camera",
                price=34999,
                currency="USD",
                description="A 33MP full-frame mirrorless camera.",
                quantity=3,
            ),
        ]

    monkeypatch.setattr(products_router, "list_products", fake_list_products)

    response = await client.get("/products")

    assert response.status_code == 200

    products = response.json()

    assert len(products) == 3
    assert products[0]["id"] == "speaker"
    assert products[0]["name"] == "Portable Speaker"
    assert products[0]["price"] == 4999
    assert products[0]["currency"] == "USD"
    assert products[0]["description"] == "A waterproof Bluetooth speaker."
    assert products[0]["quantity"] == 10
    assert products[0]["display_price"] == "49.99 USD"


@pytest.mark.anyio
async def test_get_product_returns_active_product(
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    found = await get_product(db_session, product.id)

    assert found is not None
    assert found.id == "speaker"


@pytest.mark.anyio
async def test_get_product_returns_none_for_missing_product(
    db_session: AsyncSession,
) -> None:
    found = await get_product(db_session, "missing")

    assert found is None


@pytest.mark.anyio
async def test_checkout_success(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    fake_session = SimpleNamespace(
        id="test_1",
        url="https://checkout.stripe.com/test-session",
        livemode=False,
    )

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        lambda **kwargs: fake_session,
    )

    response = await client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 200

    data = response.json()
    assert data["order_id"] > 0
    assert data["checkout_url"] == "https://checkout.stripe.com/test-session"

    updated_product = await db_session.get(Product, "speaker")
    assert updated_product is not None
    assert updated_product.quantity == 9

    order = await db_session.get(Order, data["order_id"])
    assert order is not None
    assert order.status == OrderStatus.pending
    assert order.amount == 4999
    assert order.currency == "USD"
    assert order.stripe_session_id == "test_1"
    assert order.livemode is False
    assert order.product_id == "speaker"
    assert order.reservation_expires_at is not None
    assert order.stock_reserved is True


@pytest.mark.anyio
async def test_checkout_unknown_product(client: AsyncClient) -> None:
    response = await client.post("/checkout", json={"product_id": "unknown"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


@pytest.mark.anyio
async def test_checkout_connection_error_pending_order(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    def raise_connection_error(**kwargs: Any) -> None:
        raise checkout_router.stripe.APIConnectionError(
            message="Connection error",
        )

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        raise_connection_error,
    )

    response = await client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 503

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.pending
    assert order.stripe_session_id is None

    updated_product = await db_session.get(Product, "speaker")

    assert order.product_id == "speaker"
    assert order.stock_reserved is True
    assert order.reservation_expires_at is not None
    assert updated_product is not None
    assert updated_product.quantity == 9


@pytest.mark.anyio
async def test_checkout_stripe_error_order_failed(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)

    product_id = product.id

    def raise_stripe_error(**kwargs: Any) -> None:
        raise checkout_router.stripe.StripeError("Stripe rejected request")

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        raise_stripe_error,
    )

    response = await client.post("/checkout", json={"product_id": product_id})

    assert response.status_code == 502
    assert response.json()["detail"] == "Could not create checkout session."

    order = (await db_session.execute(select(Order))).scalar_one()
    updated_product = await db_session.get(Product, product_id)

    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None

    assert updated_product is not None
    assert updated_product.quantity == 10
    assert order.product_id == product_id
    assert order.stock_reserved is False
    assert order.reservation_expires_at is not None


@pytest.mark.anyio
async def test_checkout_without_url(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)

    product_id = product.id

    fake_session = SimpleNamespace(
        id="test_1",
        url=None,
        livemode=False,
    )

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        lambda **kwargs: fake_session,
    )

    response = await client.post("/checkout", json={"product_id": product_id})

    assert response.status_code == 502
    assert response.json()["detail"] == "Stripe checkout session did not include a URL"

    order = (await db_session.execute(select(Order))).scalar_one()
    updated_product = await db_session.get(Product, product_id)

    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None

    assert updated_product is not None
    assert updated_product.quantity == 10
    assert order.product_id == product_id
    assert order.stock_reserved is False
    assert order.reservation_expires_at is not None


@pytest.mark.anyio
async def test_checkout_out_of_stock(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity=0,
        is_deleted=False,
    )
    db_session.add(product)
    await db_session.commit()

    response = await client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Product is out of stock"

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0


@pytest.mark.anyio
async def test_checkout_uses_app_base_url(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    captured_kwargs = {}

    def fake_create(**kwargs: Any) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            id="test_1",
            url="https://checkout.stripe.com/test-session",
            livemode=False,
        )

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        fake_create,
    )

    response = await client.post(
        "/checkout",
        json={"product_id": "speaker"},
        headers={"host": "example.com"},
    )

    assert response.status_code == 200
    assert captured_kwargs["success_url"] == (
        "http://test/success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert captured_kwargs["cancel_url"] == (
        "http://test/cancel?session_id={CHECKOUT_SESSION_ID}"
    )


@pytest.mark.anyio
async def test_checkout_uses_server_side_price(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    captured_kwargs = {}

    def fake_create(**kwargs: Any) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            id="cs_test_server_price",
            url="https://checkout.stripe.com/test",
            livemode=False,
        )

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        fake_create,
    )

    response = await client.post(
        "/checkout",
        json={"product_id": "speaker", "price": 100},
    )

    assert response.status_code == 200
    assert captured_kwargs["line_items"][0]["price_data"]["unit_amount"] == 4999


@pytest.mark.anyio
async def test_success_page_does_not_mutate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    response = await client.get(f"/success?order_id={order.id}")

    assert response.status_code == 200
    assert "Payment confirmation is being processed" in response.text
    assert "PayGate" in response.text

    await db_session.refresh(order)
    assert order.status == OrderStatus.pending
    assert order.fulfillment_status == FulfillmentStatus.pending


@pytest.mark.anyio
async def test_order_status_endpoint(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    response = await client.get(f"/checkout-sessions/{order.stripe_session_id}/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "pending",
        "fulfillment_status": "pending",
        "fulfilled_at": None,
    }


@pytest.mark.anyio
async def test_order_status_unknown_order(
    client: AsyncClient,
) -> None:
    response = await client.get("/checkout-sessions/cs_missing/status")

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


@pytest.mark.anyio
async def test_cancel_page_does_not_mutate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    response = await client.get(f"/cancel?session_id={order.stripe_session_id}")

    assert response.status_code == 200
    assert "Checkout was not completed" in response.text
    assert "PayGate" in response.text

    await db_session.refresh(order)
    assert order.status == OrderStatus.pending
    assert order.fulfillment_status == FulfillmentStatus.pending


@pytest.mark.anyio
async def test_deleted_products_in_products(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    active_product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity=10,
        is_deleted=False,
    )
    deleted_product = Product(
        id="deleted-speaker",
        name="Deleted Speaker",
        price=4999,
        currency="USD",
        description="This product should not show.",
        quantity=10,
        is_deleted=True,
    )

    db_session.add_all([active_product, deleted_product])
    await db_session.commit()

    response = await client.get("/products")

    assert response.status_code == 200

    products = response.json()
    product_ids = {product["id"] for product in products}

    assert "speaker" in product_ids
    assert "deleted-speaker" not in product_ids


@pytest.mark.anyio
async def test_deleted_product_cannot_be_checked_out(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    deleted_product = Product(
        id="deleted-speaker",
        name="Deleted Speaker",
        price=4999,
        currency="USD",
        description="This product should not be purchasable.",
        quantity=10,
        is_deleted=True,
    )

    db_session.add(deleted_product)
    await db_session.commit()

    response = await client.post(
        "/checkout",
        json={"product_id": "deleted-speaker"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


@pytest.mark.anyio
async def test_webhook_missing_signature(client: AsyncClient) -> None:
    response = await client.post("/webhooks/stripe", content=b"{}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Stripe signature is required"


@pytest.mark.anyio
async def test_webhook_ignores_other_event_types(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_customer_created",
            type="customer.created",
            data=SimpleNamespace(object=SimpleNamespace()),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert orders == []


@pytest.mark.anyio
async def test_webhook_ignores_unpaid_checkout(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    product_id = product.id
    product_price = product.price
    product_currency = product.currency

    await db_session.rollback()

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_unpaid",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test_123",
                    payment_status="unpaid",
                    client_reference_id=str(order_id),
                    metadata={"product_id": product_id},
                    amount_total=product_price,
                    currency=product_currency.lower(),
                    livemode=False,
                    payment_intent="pi_test_123",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200

    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.pending
    assert updated_order.fulfillment_status == FulfillmentStatus.pending


@pytest.mark.anyio
async def test_webhook_does_not_fulfill_on_currency_mismatch(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    product_id = product.id
    product_price = product.price

    await db_session.rollback()

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_currency_mismatch",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test_123",
                    payment_status="paid",
                    client_reference_id=str(order_id),
                    metadata={"product_id": product_id},
                    amount_total=product_price,
                    currency="eur",
                    livemode=False,
                    payment_intent="pi_test_123",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200

    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.pending
    assert updated_order.fulfillment_status == FulfillmentStatus.pending
    assert updated_order.fulfilled_at is None


@pytest.mark.anyio
async def test_webhook_does_not_fulfill_on_amount_mismatch(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    product_id = product.id
    product_currency = product.currency

    await db_session.rollback()

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_amount_mismatch",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test_123",
                    payment_status="paid",
                    client_reference_id=str(order_id),
                    metadata={"product_id": product_id},
                    amount_total=999999,
                    currency=product_currency.lower(),
                    livemode=False,
                    payment_intent="pi_test_123",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200

    updated_order = await db_session.get(Order, order_id)
    updated_product = await db_session.get(Product, product_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.pending
    assert updated_order.fulfillment_status == FulfillmentStatus.pending
    assert updated_order.fulfilled_at is None

    assert updated_product is not None
    assert updated_product.quantity == 10


@pytest.mark.anyio
async def test_webhook_invalid_signature_returns_400(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    def raise_signature_error(payload: bytes, sig_header: str, secret: str) -> None:
        raise webhooks_router.stripe.SignatureVerificationError(
            message="bad signature",
            sig_header=sig_header,
        )

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        raise_signature_error,
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "bad-signature"},
    )

    assert response.status_code == 400

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert orders == []


@pytest.mark.anyio
async def test_webhook_missing_order_returns_200(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_missing_order",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_missing_order",
                    payment_status="paid",
                    client_reference_id="999999",
                    metadata={"product_id": "speaker"},
                    amount_total=4999,
                    currency="usd",
                    livemode=False,
                    payment_intent="pi_missing_order",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert orders == []


@pytest.mark.anyio
async def test_webhook_late_payment_with_stock_marks_paid_and_enqueues_fulfillment(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    product.quantity = 1

    order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_late_with_stock",
        amount=product.price,
        currency=product.currency,
        livemode=False,
        reservation_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        stock_reserved=False,
    )

    db_session.add(order)
    await db_session.commit()

    order_id = order.id
    product_id = product.id
    product_price = product.price
    product_currency = product.currency

    await db_session.rollback()

    queued_jobs: list[tuple[int, str, str]] = []

    async def fake_enqueue_fulfillment(
        *,
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        queued_jobs.append((order_id, session_id, event_id))

    monkeypatch.setattr(
        webhooks_router,
        "enqueue_fulfillment",
        fake_enqueue_fulfillment,
    )
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: fake_checkout_completed_event(
            event_id="evt_test_late_with_stock",
            order_id=order_id,
            product_id=product_id,
            amount=product_price,
            currency=product_currency.lower(),
            session_id="cs_test_late_with_stock",
            payment_intent="pi_test_late_with_stock",
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert queued_jobs == [
        (order_id, "cs_test_late_with_stock", "evt_test_late_with_stock")
    ]

    db_session.expire_all()
    updated_order = await db_session.get(Order, order_id)
    updated_product = await db_session.get(Product, product_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.paid
    assert updated_order.fulfillment_status == FulfillmentStatus.pending
    assert updated_order.stripe_payment_intent == "pi_test_late_with_stock"
    assert updated_order.stock_reserved is False
    assert updated_product is not None
    assert updated_product.quantity == 0


@pytest.mark.anyio
async def test_fulfillment_failure_leaves_order_pending(
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    await db_session.rollback()

    async def fail_delivery(order_id: int) -> None:
        raise RuntimeError("delivery failed")

    @asynccontextmanager
    async def fake_standalone_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        background_tasks.fulfillment_service,
        "deliver_product",
        fail_delivery,
    )
    monkeypatch.setattr(
        background_tasks,
        "standalone_session",
        fake_standalone_session,
    )

    await background_tasks.run_fulfillment(
        order_id,
        "cs_test_123",
        "evt_test_delivery_fails",
    )

    db_session.expire_all()
    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.fulfillment_status == FulfillmentStatus.pending
    assert updated_order.fulfilled_at is None


@pytest.mark.anyio
async def test_webhook_late_payment_without_stock_goes_to_payment_review(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    product.quantity = 0

    order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_late_no_stock",
        amount=product.price,
        currency=product.currency,
        livemode=False,
        reservation_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        stock_reserved=False,
    )

    db_session.add(order)
    await db_session.commit()

    order_id = order.id
    product_id = product.id
    product_price = product.price
    product_currency = product.currency

    await db_session.rollback()

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: fake_checkout_completed_event(
            event_id="evt_test_late_no_stock",
            order_id=order_id,
            product_id=product_id,
            amount=product_price,
            currency=product_currency.lower(),
            session_id="cs_test_late_no_stock",
            payment_intent="pi_test_late_no_stock",
        ),
    )

    assert order.stripe_session_id == "cs_test_late_no_stock"
    assert order.product_id == product_id
    assert order.amount == product_price
    assert order.currency.lower() == product_currency.lower()
    assert order.livemode is False

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200

    db_session.expire_all()

    updated_order = await db_session.get(Order, order_id)
    updated_product = await db_session.get(Product, product_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.paid
    assert updated_order.fulfillment_status == FulfillmentStatus.payment_review
    assert updated_order.fulfilled_at is None
    assert updated_order.stock_reserved is False
    assert updated_order.stripe_payment_intent == "pi_test_late_no_stock"

    assert updated_product is not None
    assert updated_product.quantity == 0


@pytest.mark.anyio
async def test_orders_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/orders")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing API key"


@pytest.mark.anyio
async def test_orders_reject_wrong_api_key(client: AsyncClient) -> None:
    response = await client.get(
        "/orders",
        headers={"X-API-Key": "wrong-key"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


@pytest.mark.anyio
async def test_orders_lists_status(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_orders",
        stripe_payment_intent="pi_test_orders",
        amount=4999,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        fulfilled_at=datetime.now(UTC),
        livemode=False,
    )

    db_session.add(order)
    await db_session.commit()

    order_id = order.id

    response = await client.get(
        "/orders",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 200

    data = response.json()
    orders = data["items"]

    assert data["page"] == 1
    assert data["page_size"] == 25
    assert data["total"] == 1
    assert data["total_pages"] == 1
    assert data["has_next"] is False
    assert data["has_previous"] is False

    assert len(orders) == 1
    assert orders[0]["id"] == order_id
    assert orders[0]["product_id"] == "speaker"
    assert orders[0]["stripe_session_id"] == "cs_test_orders"
    assert orders[0]["stripe_payment_intent"] == "pi_test_orders"
    assert orders[0]["amount"] == 4999
    assert orders[0]["currency"] == "USD"
    assert orders[0]["status"] == "paid"
    assert orders[0]["fulfillment_status"] == "fulfilled"
    assert orders[0]["fulfilled_at"] is not None
    assert orders[0]["created_at"] is not None


@pytest.mark.anyio
async def test_concurrent_identical_fulfillments_deliver_once(
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    await db_session.rollback()

    delivered_orders = []

    async def fake_deliver_product(order_id: int) -> None:
        delivered_orders.append(order_id)

    @asynccontextmanager
    async def fake_standalone_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        background_tasks.fulfillment_service,
        "deliver_product",
        fake_deliver_product,
    )
    monkeypatch.setattr(
        background_tasks,
        "standalone_session",
        fake_standalone_session,
    )

    await asyncio.gather(
        background_tasks.run_fulfillment(order_id, "cs_test_123", "evt_test_1"),
        background_tasks.run_fulfillment(order_id, "cs_test_123", "evt_test_1"),
    )

    db_session.expire_all()
    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.fulfillment_status == FulfillmentStatus.fulfilled
    assert updated_order.fulfilled_at is not None
    assert delivered_orders == [order_id]


@pytest.mark.anyio
async def test_webhook_invalid_payload_returns_400(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    def raise_value_error(payload: bytes, sig_header: str, secret: str) -> None:
        raise ValueError("invalid payload")

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        raise_value_error,
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"not-json",
        headers={"Stripe-Signature": "test"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid webhook payload."


@pytest.mark.anyio
async def test_webhook_missing_metadata_returns_200(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_missing_metadata",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test",
                    payment_status="paid",
                    client_reference_id=None,
                    metadata={},
                    amount_total=4999,
                    currency="usd",
                    livemode=False,
                    payment_intent="pi_test",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test"},
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_webhook_malformed_order_id_returns_200(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_bad_order_id",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test",
                    payment_status="paid",
                    client_reference_id="not-an-int",
                    metadata={"product_id": "speaker"},
                    amount_total=4999,
                    currency="usd",
                    livemode=False,
                    payment_intent="pi_test",
                )
            ),
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test"},
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_run_fulfillment_marks_order_fulfilled(
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    await db_session.rollback()

    delivered_orders = []

    async def fake_deliver_product(order_id: int) -> None:
        delivered_orders.append(order_id)

    @asynccontextmanager
    async def fake_standalone_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        background_tasks.fulfillment_service,
        "deliver_product",
        fake_deliver_product,
    )
    monkeypatch.setattr(
        background_tasks,
        "standalone_session",
        fake_standalone_session,
    )

    await background_tasks.run_fulfillment(order_id, "cs_test_123", "evt_test")

    db_session.expire_all()

    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.fulfillment_status == FulfillmentStatus.fulfilled
    assert updated_order.fulfilled_at is not None
    assert delivered_orders == [order_id]


@pytest.mark.anyio
async def test_run_fulfillment_does_nothing_when_not_pending(
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)
    order.fulfillment_status = FulfillmentStatus.fulfilled
    await db_session.commit()

    order_id = order.id
    await db_session.rollback()

    delivered_orders = []

    async def fake_deliver_product(order_id: int) -> None:
        delivered_orders.append(order_id)

    @asynccontextmanager
    async def fake_standalone_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        background_tasks.fulfillment_service,
        "deliver_product",
        fake_deliver_product,
    )
    monkeypatch.setattr(
        background_tasks,
        "standalone_session",
        fake_standalone_session,
    )

    await background_tasks.run_fulfillment(order_id, "cs_test_123", "evt_test")

    assert delivered_orders == []


@pytest.mark.anyio
async def test_release_expired_reservations_restores_stock(
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)
    product.quantity = 0

    order = Order(
        product_id=product.id,
        stripe_session_id="cs_expired_direct",
        amount=product.price,
        currency=product.currency,
        livemode=False,
        reservation_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        stock_reserved=True,
    )

    db_session.add(order)
    await db_session.commit()

    await checkout_router.release_expired_reservations(db_session)

    await db_session.refresh(order)
    await db_session.refresh(product)

    assert order.status == OrderStatus.checkout_failed
    assert order.stock_reserved is False
    assert product.quantity == 1


@pytest.mark.anyio
async def test_concurrent_checkouts_do_not_double_release_expired_reservation(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as setup_db:
        product = Product(
            id="speaker",
            name="Portable Speaker",
            price=4999,
            currency="USD",
            description="A waterproof Bluetooth speaker.",
            quantity=0,
            is_deleted=False,
        )
        setup_db.add(product)
        await setup_db.commit()

        order = Order(
            product_id=product.id,
            stripe_session_id="cs_test_expired",
            amount=product.price,
            currency=product.currency,
            livemode=False,
            status=OrderStatus.pending,
            fulfillment_status=FulfillmentStatus.pending,
            stock_reserved=True,
            reservation_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        setup_db.add(order)
        await setup_db.commit()

        order_id = order.id

    async def release_once() -> None:
        async with test_sessionmaker() as db:
            await release_expired_reservations(db)

    await asyncio.gather(
        release_once(),
        release_once(),
    )

    async with test_sessionmaker() as verify_db:
        updated_product = await verify_db.get(Product, "speaker")
        updated_order = await verify_db.get(Order, order_id)

        assert updated_product is not None
        assert updated_order is not None
        assert updated_product.quantity == 1
        assert updated_order.status == OrderStatus.checkout_failed
        assert updated_order.stock_reserved is False


@pytest.mark.anyio
async def test_restore_reserved_stock_restores_quantity(
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)
    product.quantity = 9

    order = Order(
        product_id=product.id,
        stripe_session_id=None,
        amount=product.price,
        currency=product.currency,
        livemode=False,
        reservation_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        stock_reserved=True,
    )

    db_session.add(order)
    await db_session.commit()

    await checkout_router.restore_reserved_stock(db_session, order, product.id)

    await db_session.refresh(order)
    await db_session.refresh(product)

    assert order.status == OrderStatus.checkout_failed
    assert order.stock_reserved is False
    assert product.quantity == 10


@pytest.mark.anyio
async def test_fulfillment_service_deliver_product_stub() -> None:
    await fulfillment_service.deliver_product(123)


@pytest.mark.anyio
async def test_list_products_returns_active_products(
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    products = await list_products(db_session)

    assert len(products) == 1
    assert products[0].id == product.id


@pytest.mark.anyio
async def test_orders_empty_list_with_valid_api_key(client: AsyncClient) -> None:
    response = await client.get(
        "/orders",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "page": 1,
        "page_size": 25,
        "total": 0,
        "total_pages": 0,
        "has_next": False,
        "has_previous": False,
    }


@pytest.mark.anyio
async def test_orders_support_pagination_and_filters(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    first_paid_order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_paid_1",
        amount=4999,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        livemode=False,
    )
    second_paid_order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_paid_2",
        amount=4999,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        livemode=False,
    )
    pending_order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_pending",
        amount=4999,
        currency="USD",
        status=OrderStatus.pending,
        fulfillment_status=FulfillmentStatus.pending,
        livemode=False,
    )

    db_session.add_all([first_paid_order, second_paid_order, pending_order])
    await db_session.commit()

    response = await client.get(
        (
            "/orders?page=1&page_size=1"
            "&status=paid"
            "&fulfillment_status=fulfilled"
            "&product_id=speaker"
        ),
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["total"] == 2
    assert data["total_pages"] == 2
    assert data["has_next"] is True
    assert data["has_previous"] is False

    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "paid"
    assert data["items"][0]["fulfillment_status"] == "fulfilled"
    assert data["items"][0]["product_id"] == "speaker"

    second_page_response = await client.get(
        (
            "/orders?page=2&page_size=1"
            "&status=paid"
            "&fulfillment_status=fulfilled"
            "&product_id=speaker"
        ),
        headers={"X-API-Key": "test"},
    )

    assert second_page_response.status_code == 200

    second_page = second_page_response.json()

    assert second_page["page"] == 2
    assert second_page["page_size"] == 1
    assert second_page["total"] == 2
    assert second_page["total_pages"] == 2
    assert second_page["has_next"] is False
    assert second_page["has_previous"] is True
    assert len(second_page["items"]) == 1


@pytest.mark.anyio
async def test_orders_large_page_number_returns_empty_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_large_page",
        amount=4999,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        livemode=False,
    )

    db_session.add(order)
    await db_session.commit()

    response = await client.get(
        "/orders?page=100500&page_size=25",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["items"] == []
    assert data["page"] == 100500
    assert data["page_size"] == 25
    assert data["total"] == 1
    assert data["total_pages"] == 1
    assert data["has_next"] is False
    assert data["has_previous"] is True


@pytest.mark.anyio
async def test_orders_rejects_too_large_page_size(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/orders?page=1&page_size=100500",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_orders_ui_page_loads(client: AsyncClient) -> None:
    response = await client.get("/orders-ui")

    assert response.status_code == 200
    assert "Orders" in response.text
    assert 'id="api-key"' in response.text
    assert "/orders?" in response.text
    assert 'class="table-sort"' in response.text
    assert 'data-sort="amount"' in response.text
    assert 'data-indicator="created_at"' in response.text


@pytest.mark.anyio
async def test_orders_support_sorting(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    cheap_order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_sort_cheap",
        amount=1000,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        livemode=False,
    )
    expensive_order = Order(
        product_id=product.id,
        stripe_session_id="cs_test_sort_expensive",
        amount=9999,
        currency="USD",
        status=OrderStatus.paid,
        fulfillment_status=FulfillmentStatus.fulfilled,
        livemode=False,
    )

    db_session.add_all([cheap_order, expensive_order])
    await db_session.commit()

    asc_response = await client.get(
        "/orders?sort_by=amount&sort_direction=asc",
        headers={"X-API-Key": "test"},
    )

    assert asc_response.status_code == 200

    asc_items = asc_response.json()["items"]

    assert asc_items[0]["amount"] == 1000
    assert asc_items[1]["amount"] == 9999

    desc_response = await client.get(
        "/orders?sort_by=amount&sort_direction=desc",
        headers={"X-API-Key": "test"},
    )

    assert desc_response.status_code == 200

    desc_items = desc_response.json()["items"]

    assert desc_items[0]["amount"] == 9999
    assert desc_items[1]["amount"] == 1000


@pytest.mark.anyio
async def test_orders_reject_invalid_sort(client: AsyncClient) -> None:
    response = await client.get(
        "/orders?sort_by=stripe_payment_intent",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_orders_reject_invalid_sort_direction(client: AsyncClient) -> None:
    response = await client.get(
        "/orders?sort_direction=sideways",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_orders_can_skip_total_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    product = await add_test_product(db_session)

    db_session.add_all(
        [
            Order(
                product_id=product.id,
                stripe_session_id=f"cs_test_skip_total_{index}",
                amount=4999,
                currency="USD",
                status=OrderStatus.paid,
                fulfillment_status=FulfillmentStatus.fulfilled,
                livemode=False,
            )
            for index in range(3)
        ]
    )
    await db_session.commit()

    response = await client.get(
        "/orders?page=1&page_size=2&include_total=false",
        headers={"X-API-Key": "test"},
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 2
    assert data["total"] == 0
    assert data["total_pages"] == 0
    assert data["has_next"] is True
    assert data["has_previous"] is False


@pytest.mark.anyio
async def test_webhook_marks_order_paid_and_enqueues_fulfillment(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)
    order_id = order.id
    product_id = product.id
    await db_session.commit()

    queued_jobs: list[tuple[int, str, str]] = []

    async def fake_enqueue_fulfillment(
        *,
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        queued_jobs.append((order_id, session_id, event_id))

    monkeypatch.setattr(
        webhooks_router,
        "enqueue_fulfillment",
        fake_enqueue_fulfillment,
    )

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: fake_checkout_completed_event(
            event_id="evt_test_paid",
            order_id=order_id,
            product_id=product_id,
            amount=product.price,
            currency=product.currency.lower(),
            session_id="cs_test_123",
            payment_intent="pi_test_123",
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert queued_jobs == [(order_id, "cs_test_123", "evt_test_paid")]


@pytest.mark.anyio
async def test_webhook_returns_503_when_qstash_enqueue_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)
    order_id = order.id
    product_id = product.id
    await db_session.commit()

    async def fail_enqueue_fulfillment(
        *,
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        raise httpx.ConnectError("qstash unavailable")

    monkeypatch.setattr(
        webhooks_router,
        "enqueue_fulfillment",
        fail_enqueue_fulfillment,
    )

    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: fake_checkout_completed_event(
            event_id="evt_test_qstash_fails",
            order_id=order_id,
            product_id=product_id,
            amount=product.price,
            currency=product.currency.lower(),
            session_id="cs_test_123",
            payment_intent="pi_test_123",
        ),
    )

    response = await client.post(
        "/webhooks/stripe",
        content=b"{}",
        headers={"Stripe-Signature": "test-signature"},
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_internal_fulfill_rejects_wrong_secret(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    called = False

    async def fake_run_fulfillment(
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "app.routers.internal.run_fulfillment",
        fake_run_fulfillment,
    )

    response = await client.post(
        "/internal/fulfill/wrong-secret",
        json={
            "order_id": 1,
            "session_id": "cs_test_123",
            "event_id": "evt_test_123",
        },
    )

    assert response.status_code == 404
    assert called is False


@pytest.mark.anyio
async def test_internal_fulfill_runs_fulfillment(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    called_with: list[tuple[int, str, str]] = []

    async def fake_verify_qstash_request(
        *,
        body: bytes,
        signature: str | None,
        destination_url: str,
    ) -> str:
        return "jti_internal_runs"

    async def fake_reject_qstash_replay(jti: str) -> None:
        return None

    async def fake_run_fulfillment(
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        called_with.append((order_id, session_id, event_id))

    monkeypatch.setattr(
        "app.routers.internal.verify_qstash_request",
        fake_verify_qstash_request,
    )
    monkeypatch.setattr(
        "app.routers.internal.reject_qstash_replay",
        fake_reject_qstash_replay,
    )
    monkeypatch.setattr(
        "app.routers.internal.run_fulfillment",
        fake_run_fulfillment,
    )

    response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json={
            "order_id": 123,
            "session_id": "cs_test_123",
            "event_id": "evt_test_123",
        },
        headers={"Upstash-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert called_with == [(123, "cs_test_123", "evt_test_123")]


@pytest.mark.anyio
async def test_enqueue_fulfillment_publishes_qstash_job(
    monkeypatch: Any,
) -> None:
    captured_request: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            captured_request["timeout"] = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> FakeResponse:
            captured_request["url"] = url
            captured_request["headers"] = headers
            captured_request["json"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.qstash.httpx.AsyncClient",
        FakeAsyncClient,
    )

    from app.services.qstash import enqueue_fulfillment

    await enqueue_fulfillment(
        order_id=123,
        session_id="cs_test_123",
        event_id="evt_test_123",
    )

    assert captured_request["timeout"] == 5.0
    assert captured_request["headers"]["Authorization"].startswith("Bearer ")
    assert captured_request["headers"]["Content-Type"] == "application/json"
    assert captured_request["json"] == {
        "order_id": 123,
        "session_id": "cs_test_123",
        "event_id": "evt_test_123",
    }
    assert "qstash.upstash.io/v2/publish" in captured_request["url"]


@pytest.mark.anyio
async def test_enqueue_fulfillment_raises_for_qstash_error(
    monkeypatch: Any,
) -> None:
    class FakeResponse:
        status_code = 500

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "QStash error",
                request=httpx.Request("POST", "https://qstash.upstash.io"),
                response=httpx.Response(500),
            )

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.qstash.httpx.AsyncClient",
        FakeAsyncClient,
    )

    from app.services.qstash import enqueue_fulfillment

    with pytest.raises(httpx.HTTPStatusError):
        await enqueue_fulfillment(
            order_id=123,
            session_id="cs_test_123",
            event_id="evt_test_123",
        )


@pytest.mark.anyio
async def test_internal_fulfill_requires_qstash_signature(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json={
            "order_id": 1,
            "session_id": "cs_test_123",
            "event_id": "evt_test_123",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "QStash signature is required."


@pytest.mark.anyio
async def test_internal_fulfill_rejects_invalid_qstash_signature(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json={
            "order_id": 1,
            "session_id": "cs_test_123",
            "event_id": "evt_test_123",
        },
        headers={"Upstash-Signature": "invalid"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_internal_fulfill_verified_qstash_request_runs_fulfillment(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    fulfilled_jobs: list[tuple[int, str, str]] = []

    async def fake_verify_qstash_request(
        *,
        body: bytes,
        signature: str | None,
        destination_url: str,
    ) -> str:
        return "jti_test_123"

    async def fake_run_fulfillment(
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        fulfilled_jobs.append((order_id, session_id, event_id))

    monkeypatch.setattr(
        "app.routers.internal.verify_qstash_request",
        fake_verify_qstash_request,
    )
    monkeypatch.setattr(
        "app.routers.internal.run_fulfillment",
        fake_run_fulfillment,
    )

    response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json={
            "order_id": 123,
            "session_id": "cs_test_123",
            "event_id": "evt_test_123",
        },
        headers={"Upstash-Signature": "test-signature"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert fulfilled_jobs == [(123, "cs_test_123", "evt_test_123")]


@pytest.mark.anyio
async def test_internal_fulfill_rejects_replayed_qstash_message(
    client: AsyncClient,
    monkeypatch: Any,
) -> None:
    seen_jtis: set[str] = set()

    async def fake_verify_qstash_request(
        *,
        body: bytes,
        signature: str | None,
        destination_url: str,
    ) -> str:
        return "jti_replay_test"

    async def fake_reject_qstash_replay(jti: str) -> None:
        if jti in seen_jtis:
            raise HTTPException(
                status_code=409,
                detail="QStash message replay rejected.",
            )

        seen_jtis.add(jti)

    async def fake_run_fulfillment(
        order_id: int,
        session_id: str,
        event_id: str,
    ) -> None:
        return None

    monkeypatch.setattr(
        "app.routers.internal.verify_qstash_request",
        fake_verify_qstash_request,
    )
    monkeypatch.setattr(
        "app.routers.internal.reject_qstash_replay",
        fake_reject_qstash_replay,
    )
    monkeypatch.setattr(
        "app.routers.internal.run_fulfillment",
        fake_run_fulfillment,
    )

    payload = {
        "order_id": 123,
        "session_id": "cs_test_123",
        "event_id": "evt_test_123",
    }

    first_response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json=payload,
        headers={"Upstash-Signature": "test-signature"},
    )

    second_response = await client.post(
        "/internal/fulfill/test-internal-secret",
        json=payload,
        headers={"Upstash-Signature": "test-signature"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "QStash message replay rejected."
