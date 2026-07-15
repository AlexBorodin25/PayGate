from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Product
from app.services.products import list_products

router = APIRouter()

DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get("/products")
def get_products(db: DatabaseSession) -> list[Product]:
    return list_products(db)
