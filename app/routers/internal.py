import base64
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel
from qstash import Receiver
from sqlalchemy.exc import IntegrityError

from app.background_tasks import run_fulfillment
from app.config import settings
from app.db import standalone_session
from app.models import ProcessedQstashMessage

router = APIRouter(tags=["Internal"])
logger = logging.getLogger(__name__)


class FulfillmentRequest(BaseModel):
    order_id: int
    session_id: str
    event_id: str


def decode_qstash_jti(signature: str) -> str:
    parts = signature.split(".")

    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid QStash signature.")

    payload = parts[1]
    padded_payload = payload + "=" * (-len(payload) % 4)

    try:
        decoded_payload = base64.urlsafe_b64decode(padded_payload)
        claims = json.loads(decoded_payload)
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=401,
            detail="Invalid QStash signature.",
        ) from error

    jti = claims.get("jti")

    if not isinstance(jti, str) or not jti:
        raise HTTPException(
            status_code=401,
            detail="QStash signature missing jti.",
        )

    return jti


async def verify_qstash_request(
    *,
    body: bytes,
    signature: str | None,
    destination_url: str,
) -> str:
    if signature is None:
        raise HTTPException(
            status_code=401,
            detail="QStash signature is required.",
        )

    receiver = Receiver(
        current_signing_key=settings.qstash_current_signing_key,
        next_signing_key=settings.qstash_next_signing_key,
    )

    try:
        receiver.verify(
            body=body.decode("utf-8"),
            signature=signature,
            url=destination_url,
        )
    except Exception as error:
        raise HTTPException(
            status_code=401,
            detail="Invalid QStash signature.",
        ) from error

    return decode_qstash_jti(signature)


async def reject_qstash_replay(jti: str) -> None:
    try:
        async with standalone_session() as db:
            db.add(ProcessedQstashMessage(jti=jti))
            await db.flush()
    except IntegrityError as error:
        logger.warning("Rejected replayed QStash message jti=%s", jti)
        raise HTTPException(
            status_code=409,
            detail="QStash message replay rejected.",
        ) from error


@router.post(
    "/internal/fulfill/{secret}",
    include_in_schema=False,
)
async def fulfill_order(
    request: Request,
    secret: Annotated[str, Path()],
    upstash_signature: Annotated[
        str | None,
        Header(alias="Upstash-Signature"),
    ] = None,
) -> dict[str, bool]:
    if secret != settings.internal_fulfillment_secret:
        raise HTTPException(status_code=404, detail="Not found")

    body = await request.body()
    destination_url = (
        f"{settings.app_base_url}/internal/fulfill/"
        f"{settings.internal_fulfillment_secret}"
    )

    jti = await verify_qstash_request(
        body=body,
        signature=upstash_signature,
        destination_url=destination_url,
    )
    await reject_qstash_replay(jti)

    fulfillment_request = FulfillmentRequest.model_validate_json(body)

    await run_fulfillment(
        order_id=fulfillment_request.order_id,
        session_id=fulfillment_request.session_id,
        event_id=fulfillment_request.event_id,
    )

    return {"received": True}
