import logging

logger = logging.getLogger(__name__)

class FulfillmentService:
    async def deliver_product(self, order_id: int) -> None:
        logger.info("Delivered digital product for order_id=%s.", order_id)

fulfillment_service = FulfillmentService()