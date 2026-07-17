import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("STRIPE_SECRET_KEY", "test")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")
os.environ.setdefault("ORDERS_API_KEY", "test")

from app.db import get_db
from app.main import app
from app.models import Base, Order, OrderStatus, Product
from app.routers import checkout as checkout_router
from app.routers import products as products_router


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    db = TestSession()

    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session) -> TestClient:
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def fake_db() -> Iterator[None]:
    yield None


def add_test_product(db_session) -> Product:
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity_in_stock=10,
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_products_endpoint(monkeypatch: Any) -> None:
    app.dependency_overrides[get_db] = fake_db

    monkeypatch.setattr(
        products_router,
        "list_products",
        lambda db: [
            SimpleNamespace(
                id="speaker",
                name="Portable Speaker",
                price=4999,
                currency="USD",
                display_price="49.99 USD",
                description="A waterproof Bluetooth speaker.",
                quantity_in_stock=10,
            ),
            SimpleNamespace(
                id="laptop",
                name="15.6 inch Business Laptop",
                price=29999,
                currency="USD",
                display_price="299.99 USD",
                description="A business laptop.",
                quantity_in_stock=5,
            ),
            SimpleNamespace(
                id="camera",
                name="Full-Frame Mirrorless Camera",
                price=34999,
                currency="USD",
                display_price="349.99 USD",
                description="A 33MP full-frame mirrorless camera.",
                quantity_in_stock=3,
            ),
        ],
    )

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


def test_checkout_success(client, db_session, monkeypatch):
    add_test_product(db_session)

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

    order = db_session.get(Order, data["order_id"])
    assert order is not None
    assert order.status == OrderStatus.pending
    assert order.amount == 4999
    assert order.currency == "USD"
    assert order.stripe_session_id == "test_1"
    assert order.livemode is False


def test_checkout_unknown_product(client):
    response = client.post("/checkout", json={"product_id": "unknown"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


def test_checkout_connection_error_pending_order(client, db_session, monkeypatch):
    add_test_product(db_session)

    def raise_connection_error(**kwargs):
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

    order = db_session.query(Order).one()
    assert order.status == OrderStatus.pending
    assert order.stripe_session_id is None


def test_checkout_stripe_error_order_failed(client, db_session, monkeypatch):
    add_test_product(db_session)

    def raise_stripe_error(**kwargs):
        raise checkout_router.stripe.StripeError("Stripe rejected request")

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        raise_stripe_error,
    )

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = db_session.query(Order).one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


def test_checkout_without_url(client, db_session, monkeypatch):
    add_test_product(db_session)

    fake_session = SimpleNamespace(id="test_1", url=None)

    monkeypatch.setattr(
        checkout_router.stripe.checkout.Session,
        "create",
        lambda **kwargs: fake_session,
    )

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 502

    order = db_session.query(Order).one()
    assert order.status == OrderStatus.checkout_failed
    assert order.stripe_session_id is None


def test_checkout_out_of_stock(client, db_session):
    product = Product(
        id="speaker",
        name="Portable Speaker",
        price=4999,
        currency="USD",
        description="A waterproof Bluetooth speaker.",
        quantity_in_stock=0,
    )
    db_session.add(product)
    db_session.commit()

    response = client.post("/checkout", json={"product_id": "speaker"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Product is out of stock"

    assert db_session.query(Order).count() == 0


def test_checkout_uses_app_base_url(client, db_session, monkeypatch):
    add_test_product(db_session)

    captured_kwargs = {}

    def fake_create(**kwargs):
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


def test_success_page_does_not_mutate(client, db_session):
    response = client.get("/success")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Payment confirmation is being processed.",
    }
    assert db_session.query(Order).count() == 0


def test_cancel_page_does_not_mutate(client, db_session):
    response = client.get("/cancel")

    assert response.status_code == 200
    assert response.json() == {
        "status": "cancelled",
        "message": "Checkout cancelled.",
    }
    assert db_session.query(Order).count() == 0
