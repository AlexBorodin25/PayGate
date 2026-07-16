import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

os.environ.setdefault("STRIPE_SECRET_KEY", "test")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("APP_BASE_URL", "http://localhost:8000")
os.environ.setdefault("ORDERS_API_KEY", "test")

from app.db import get_db
from app.main import app
from app.routers import products as products_router


def fake_db() -> Iterator[None]:
    yield None


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
