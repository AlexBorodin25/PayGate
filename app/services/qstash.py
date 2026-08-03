import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

QSTASH_PUBLISH_URL = "https://qstash.upstash.io/v2/publish"


async def enqueue_fulfillment(
    *,
    order_id: int,
    session_id: str,
    event_id: str,
) -> None:
    destination = (
        f"{settings.app_base_url}/internal/fulfill/"
        f"{settings.internal_fulfillment_secret}"
    )
    encoded_destination = quote(destination, safe="")

    payload: dict[str, Any] = {
        "order_id": order_id,
        "session_id": session_id,
        "event_id": event_id,
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{QSTASH_PUBLISH_URL}/{encoded_destination}",
            headers={
                "Authorization": f"Bearer {settings.qstash_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        logger.error(
            "QStash enqueue failed for order_id=%s session_id=%s event_id=%s "
            "status_code=%s",
            order_id,
            session_id,
            event_id,
            response.status_code,
        )
        response.raise_for_status()

    logger.info(
        "QStash fulfillment enqueued for order_id=%s session_id=%s event_id=%s",
        order_id,
        session_id,
        event_id,
    )
