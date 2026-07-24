from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.routers.checkout import checkout
from app.schemas import CheckoutRequest
from app.services.products import format_price, list_products

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/")
async def product_page(
    request: Request, db: DatabaseSession
) -> Response:  # pragma: no cover
    products = await list_products(db)

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
                }
                for product in products
            ],
        },
    )


@router.post("/checkout-form")
async def checkout_form(
    product_id: Annotated[str, Form()], db: DatabaseSession
) -> RedirectResponse:  # pragma: no cover
    response = await checkout(CheckoutRequest(product_id=product_id), db)
    return RedirectResponse(response.checkout_url, status_code=303)
