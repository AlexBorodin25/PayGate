import secrets
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import settings


async def require_orders_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Missing API key")

    if not secrets.compare_digest(x_api_key, settings.orders_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
