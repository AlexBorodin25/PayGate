import logging
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult

from app.db import standalone_session
from app.models import FulfillmentStatus, Order
from app.services.fulfillment import fulfillment_service

logger = logging.getLogger(__name__)


async def run_fulfillment(order_id: int, session_id: str, event_id: str) -> None:
    try:
        async with standalone_session() as db:
            claim_update = cast(
                CursorResult[Any],
                await db.execute(
                    update(Order)
                    .where(Order.id == order_id)
                    .where(Order.fulfillment_status == FulfillmentStatus.pending)
                    .values(fulfillment_status=FulfillmentStatus.processing)
                ),
            )

            if claim_update.rowcount != 1:
                logger.info(
                    "Fulfillment already claimed for order_id=%s "
                    "session_id=%s event_id=%s",
                    order_id,
                    session_id,
                    event_id,
                )
                return

            logger.info(
                "Fulfillment claimed for order_id=%s session_id=%s event_id=%s",
                order_id,
                session_id,
                event_id,
            )

            await fulfillment_service.deliver_product(order_id)

            await db.execute(
                update(Order)
                .where(Order.id == order_id)
                .where(Order.fulfillment_status == FulfillmentStatus.processing)
                .values(
                    fulfillment_status=FulfillmentStatus.fulfilled,
                    fulfilled_at=datetime.now(UTC),
                )
            )

    except Exception:
        async with standalone_session() as db:
            await db.execute(
                update(Order)
                .where(Order.id == order_id)
                .where(Order.fulfillment_status == FulfillmentStatus.processing)
                .values(fulfillment_status=FulfillmentStatus.pending)
            )

        logger.exception(
            "Fulfillment failed for order_id=%s session_id=%s event_id=%s",
            order_id,
            session_id,
            event_id,
        )
        raise
