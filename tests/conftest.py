import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

os.environ["STRIPE_SECRET_KEY"] = "test"
os.environ["STRIPE_WEBHOOK_SECRET"] = "test"
os.environ["APP_BASE_URL"] = "http://test"
os.environ["ORDERS_API_KEY"] = "test"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres:postgres@localhost:5433/paygate_test"
)
os.environ["QSTASH_TOKEN"] = "test-qstash-token"
os.environ["INTERNAL_FULFILLMENT_SECRET"] = "test-internal-secret"
os.environ["QSTASH_CURRENT_SIGNING_KEY"] = "test-current-signing-key"
os.environ["QSTASH_NEXT_SIGNING_KEY"] = "test-next-signing-key"

from app.db import get_db
from app.main import app
from app.models import Base


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def test_sessionmaker(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
    )


@pytest.fixture
async def db_session(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with test_sessionmaker() as session:
        yield session


@pytest.fixture
async def client(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()
