from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FulfillmentStatus, Order, OrderStatus, Product
from app.routers import checkout as checkout_router
from app.routers import products as products_router
from app.routers import webhooks as webhooks_router


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


async def add_test_order(
    db_session: AsyncSession,
    product: Product,
) -> Order:
    order = Order(
        stripe_session_id="cs_test_123",
        amount=product.price,
        currency=product.currency,
        livemode=False,
    )

    db_session.add(order)
    await db_session.commit()

    return order


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

    order = await db_session.get(Order, data["order_id"])
    assert order is not None
    assert order.status == OrderStatus.pending
    assert order.amount == 4999
    assert order.currency == "USD"
    assert order.stripe_session_id == "test_1"
    assert order.livemode is False


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


@pytest.mark.anyio
async def test_checkout_stripe_error_order_failed(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    def raise_stripe_error(**kwargs: Any) -> None:
        raise checkout_router.stripe.StripeError("Stripe rejected request")

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        raise_stripe_error,
    )

    response = await client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


@pytest.mark.anyio
async def test_checkout_without_url(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: Any,
) -> None:
    await add_test_product(db_session)

    fake_session = SimpleNamespace(id="test_1", url=None, livemode=False)

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        lambda **kwargs: fake_session,
    )

    response = await client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


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
    assert captured_kwargs["success_url"] == "http://test/success"
    assert captured_kwargs["cancel_url"] == "http://test/cancel"

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
    response = await client.get("/success")

    assert response.status_code == 200
    assert response.json() == {
        "status": "pending_confirmation",
        "message": "Payment confirmation is being processed.",
    }

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0


@pytest.mark.anyio
async def test_cancel_page_does_not_mutate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.get("/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "status": "cancelled",
        "message": "Checkout cancelled.",
    }

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0


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
async def test_webhook_marks_order_paid_and_fulfilled(
    client: AsyncClient,
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    product_id = product.id
    product_price = product.price
    product_currency = product.currency

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
        webhooks_router.fulfillment_service,
        "deliver_product",
        fake_deliver_product,
    )
    monkeypatch.setattr(
        webhooks_router,
        "standalone_session",
        fake_standalone_session,
    )
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_paid",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test_123",
                    payment_status="paid",
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
    assert response.json() == {"received": True}

    db_session.expire_all()

    updated_order = await db_session.get(Order, order_id)
    updated_product = await db_session.get(Product, product_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.paid
    assert updated_order.fulfillment_status == FulfillmentStatus.fulfilled
    assert updated_order.fulfilled_at is not None
    assert updated_order.stripe_payment_intent == "pi_test_123"

    assert updated_product is not None
    assert updated_product.quantity == 10

    assert delivered_orders == [order_id]

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
async def test_fulfillment_failure_leaves_order_pending(
    client: AsyncClient,
    db_session: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    product = await add_test_product(db_session)
    order = await add_test_order(db_session, product)

    order_id = order.id
    product_id = product.id
    product_price = product.price
    product_currency = product.currency

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
        webhooks_router.fulfillment_service,
        "deliver_product",
        fail_delivery,
    )
    monkeypatch.setattr(
        webhooks_router,
        "standalone_session",
        fake_standalone_session,
    )
    monkeypatch.setattr(
        webhooks_router.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret: SimpleNamespace(
            id="evt_test_delivery_fails",
            type="checkout.session.completed",
            data=SimpleNamespace(
                object=SimpleNamespace(
                    id="cs_test_123",
                    payment_status="paid",
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

    db_session.expire_all()
    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.status == OrderStatus.paid
    assert updated_order.fulfillment_status == FulfillmentStatus.pending
    assert updated_order.fulfilled_at is None


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
    order = Order(
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

    orders = response.json()

    assert len(orders) == 1
    assert orders[0]["id"] == order_id
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
        webhooks_router.fulfillment_service,
        "deliver_product",
        fake_deliver_product,
    )
    monkeypatch.setattr(webhooks_router, "standalone_session", fake_standalone_session)

    await asyncio.gather(
        webhooks_router.run_fulfillment(order_id, "cs_test_123", "evt_test_1"),
        webhooks_router.run_fulfillment(order_id, "cs_test_123", "evt_test_1"),
    )

    updated_order = await db_session.get(Order, order_id)

    assert updated_order is not None
    assert updated_order.fulfillment_status == FulfillmentStatus.fulfilled
    assert updated_order.fulfilled_at is not None
    assert delivered_orders == [order_id]