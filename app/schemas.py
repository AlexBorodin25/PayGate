from pydantic import BaseModel, ConfigDict


class ProductResponse(BaseModel):
    id: str
    name: str
    price: int
    currency: str
    display_price: str
    description: str
    quantity_in_stock: int

    model_config = ConfigDict(from_attributes=True)

class CheckoutRequest(BaseModel):
    product_id: str