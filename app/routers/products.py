from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.products import format_price, list_products

router = APIRouter()

DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get("/products")
def get_products(db: DatabaseSession) -> list[dict[str, str | int]]:
    products = list_products(db)

    return [
        {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "currency": product.currency,
            "display_price": format_price(product.price, product.currency),
            "description": product.description,
            "quantity_in_stock": product.quantity_in_stock,
        }
        for product in products
    ]
