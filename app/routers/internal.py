from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.background_tasks import run_fulfillment
from app.config import settings

router = APIRouter(tags=["Internal"])


class FulfillmentRequest(BaseModel):
    order_id: int
    session_id: str
    event_id: str


@router.post(
    "/internal/fulfill/{secret}",
    include_in_schema=False,
)
async def fulfill_order(
    secret: Annotated[str, Path()],
    request: FulfillmentRequest,
) -> dict[str, bool]:
    if secret != settings.internal_fulfillment_secret:
        raise HTTPException(status_code=404, detail="Not found")

    await run_fulfillment(
        order_id=request.order_id,
        session_id=request.session_id,
        event_id=request.event_id,
    )

    return {"received": True}
