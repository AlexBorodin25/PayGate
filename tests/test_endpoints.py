import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("STRIPE_SECRET_KEY", "test")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")
os.environ.setdefault("ORDERS_API_KEY", "test")

from app.db import get_db
from app.main import app
from app.models import Base, Order, OrderStatus, Product
from app.routers import checkout as checkout_router
from app.routers import products as products_router


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestSession = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as db:
        yield db

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[TestClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


async def fake_db() -> AsyncIterator[None]:
    yield None


async def add_test_product(db_session: AsyncSession) -> Product:
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity_in_stock=10,
    )
    db_session.add(product)
    await db_session.commit()
    return product


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_products_endpoint(monkeypatch: Any) -> None:
    app.dependency_overrides[get_db] = fake_db

    async def fake_list_products(db: Any) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id="speaker",
                name="Portable Speaker",
                price=4999,
                currency="USD",
                description="A waterproof Bluetooth speaker.",
                quantity_in_stock=10,
            ),
            SimpleNamespace(
                id="laptop",
                name="15.6 inch Business Laptop",
                price=29999,
                currency="USD",
                description="A business laptop.",
                quantity_in_stock=5,
            ),
            SimpleNamespace(
                id="camera",
                name="Full-Frame Mirrorless Camera",
                price=34999,
                currency="USD",
                description="A 33MP full-frame mirrorless camera.",
                quantity_in_stock=3,
            ),
        ]

    monkeypatch.setattr(products_router, "list_products", fake_list_products)

    client = TestClient(app)
    response = client.get("/products")

    app.dependency_overrides.clear()

    assert response.status_code == 200

    products = response.json()

    assert len(products) == 3
    assert products[0]["id"] == "speaker"
    assert products[0]["name"] == "Portable Speaker"
    assert products[0]["price"] == 4999
    assert products[0]["currency"] == "USD"
    assert products[0]["description"] == "A waterproof Bluetooth speaker."
    assert products[0]["quantity_in_stock"] == 10
    assert products[0]["display_price"] == "49.99 USD"


@pytest.mark.anyio
async def test_checkout_success(
    client: TestClient, db_session: AsyncSession, monkeypatch: Any
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

    response = client.post("/checkout", json={"product_id": "speaker"})

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
async def test_checkout_unknown_product(client: TestClient) -> None:
    response = client.post("/checkout", json={"product_id": "unknown"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


@pytest.mark.anyio
async def test_checkout_connection_error_pending_order(
    client: TestClient,
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

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 503

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.pending
    assert order.stripe_session_id is None


@pytest.mark.anyio
async def test_checkout_stripe_error_order_failed(
    client: TestClient,
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

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


@pytest.mark.anyio
async def test_checkout_without_url(
    client: TestClient,
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

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


@pytest.mark.anyio
async def test_checkout_out_of_stock(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity_in_stock=0,
    )
    db_session.add(product)
    await db_session.commit()

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Product is out of stock"

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0


@pytest.mark.anyio
async def test_checkout_uses_app_base_url(
    client: TestClient,
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

    response = client.post(
        "/checkout",
        json={"product_id": "speaker"},
        headers={"host": "example.com"},
    )

    assert response.status_code == 200
    assert captured_kwargs["success_url"] == "http://localhost:8000/success"
    assert captured_kwargs["cancel_url"] == "http://localhost:8000/cancel"


@pytest.mark.anyio
async def test_success_page_does_not_mutate(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    response = client.get("/success")

    assert response.status_code == 200
    assert response.json() == {
        "status": "pending_confirmation",
        "message": "Payment confirmation is being processed.",
    }

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0


@pytest.mark.anyio
async def test_cancel_page_does_not_mutate(
    client: TestClient,
    db_session: AsyncSession,
) -> None:
    response = client.get("/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "status": "cancelled",
        "message": "Checkout cancelled.",
    }

    orders = (await db_session.execute(select(Order))).scalars().all()
    assert len(orders) == 0
