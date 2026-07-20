from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as db:
        yield db


@asynccontextmanager
async def standalone_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise