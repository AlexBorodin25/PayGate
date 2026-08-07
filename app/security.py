import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import settings

orders_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_orders_api_key(
    api_key: Annotated[str | None, Depends(orders_api_key_header)],
) -> None:
    if api_key is None or not secrets.compare_digest(
        api_key,
        settings.orders_api_key,
    ):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
