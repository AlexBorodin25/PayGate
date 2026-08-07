from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import ProductResponse
from app.services.products import format_price, list_products

router = APIRouter(tags=["Products"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/products",
    summary="List available products",
    description=(
        "Returns active products available for checkout, including price, "
        "stock quantity, display price, and optional image URL."
    ),
)
async def get_products(db: DatabaseSession) -> list[ProductResponse]:
    products = await list_products(db)

    return [
        ProductResponse(
            id=product.id,
            name=product.name,
            price=product.price,
            currency=product.currency,
            description=product.description,
            quantity=product.quantity,
            display_price=format_price(product.price, product.currency),
            image_url=product.image_url,
        )
        for product in products
    ]
