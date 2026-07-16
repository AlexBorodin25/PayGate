from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import CheckoutRequest
from app.services.products import get_product

router = APIRouter()

DatabaseSession = Annotated[Session, Depends(get_db)]

@router.post("/checkout")
def checkout(request: CheckoutRequest, db: DatabaseSession) -> dict[str, str]:
    product = get_product(db, request.product_id)

    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product.id,
        "message": "Product available.",
    }