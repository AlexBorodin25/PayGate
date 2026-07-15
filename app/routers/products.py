from fastapi import APIRouter

from app.services.products import Product, list_products

router = APIRouter()


@router.get("/products")
def get_products() -> list[Product]:
    return list_products()
