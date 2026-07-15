from sqlalchemy.orm import Session

from app.models import Product


def list_products(db: Session) -> list[Product]:
    return db.query(Product).order_by(Product.name).all()


def get_product(db: Session, product_id: str) -> Product | None:
    return db.get(Product, product_id)
