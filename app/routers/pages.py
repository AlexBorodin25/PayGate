from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Product
from app.routers.checkout import checkout
from app.schemas import CheckoutRequest
from app.services.products import format_price

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/")
async def product_page(
    request: Request,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    search: str | None = None,
) -> Response:  # pragma: no cover
    search = search.strip() if search else None

    page_size = 6
    offset = (page - 1) * page_size

    filters = [Product.is_deleted.is_(False)]

    if search:
        filters.append(Product.name.ilike(f"%{search}%"))

    total = await db.scalar(select(func.count()).select_from(Product).where(*filters))
    total = total or 0
    total_pages = ceil(total / page_size) if total else 1

    products = (
        await db.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.id.asc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "products": [
                {
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "display_price": format_price(product.price, product.currency),
                    "quantity": product.quantity,
                    "image_url": product.image_url,
                }
                for product in products
            ],
            "search": search or "",
            "page": page,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
    )


@router.post("/checkout-form")
async def checkout_form(
    product_id: Annotated[str, Form()],
    db: DatabaseSession,
) -> RedirectResponse:  # pragma: no cover
    response = await checkout(CheckoutRequest(product_id=product_id), db)
    return RedirectResponse(response.checkout_url, status_code=303)
