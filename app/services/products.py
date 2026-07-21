from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product


async def list_products(db: AsyncSession) -> list[Product]:
    result = await db.scalars(
        select(Product).where(Product.is_deleted.is_(False)).order_by(Product.name)
    )
    return list(result)


async def get_product(db: AsyncSession, product_id: str) -> Product | None:
    product = await db.get(Product, product_id)

    if product is None or product.is_deleted:
        return None

    return product


def format_price(price: int, currency: str) -> str:
    return f"{price / 100:.2f} {currency.upper()}"
