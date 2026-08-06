import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def enqueue_fulfillment(
    *,
    order_id: int,
    session_id: str,
    event_id: str,
) -> None:
    destination = f"{settings.app_base_url}/internal/fulfill"
    failure_callback = f"{settings.app_base_url}/internal/qstash-failure"
    publish_url = f"{settings.qstash_publish_url}/{destination}"

    payload: dict[str, Any] = {
        "order_id": order_id,
        "session_id": session_id,
        "event_id": event_id,
    }

    headers = {
        "Authorization": f"Bearer {settings.qstash_token}",
        "Content-Type": "application/json",
        "Upstash-Forward-X-Internal-Fulfillment-Secret": (
            settings.internal_fulfillment_secret
        ),
        "Upstash-Retries": "3",
        "Upstash-Retry-Delay": "exp(2.5 * retried) * 1000",
        "Upstash-Failure-Callback": failure_callback,
    }

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            publish_url,
            headers=headers,
            json=payload,
        )

    if not 200 <= response.status_code < 300:
        logger.error(
            "QStash enqueue failed for order_id=%s session_id=%s event_id=%s "
            "status_code=%s publish_url=%s response_body=%s",
            order_id,
            session_id,
            event_id,
            response.status_code,
            publish_url,
            response.text,
        )
        raise httpx.HTTPStatusError(
            "QStash enqueue failed",
            request=response.request,
            response=response,
        )

    logger.info(
        "QStash fulfillment enqueued for order_id=%s session_id=%s event_id=%s",
        order_id,
        session_id,
        event_id,
    )
