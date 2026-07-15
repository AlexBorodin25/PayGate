Product = dict[str, str | int]

PRODUCTS: dict[str, Product] = {
    "speaker": {
        "id": "speaker",
        "name": "Portable Speaker",
        "price": 4999,
        "currency": "USD",
        "description": "A waterproof bluetooth speaker with up to "
        "10 hours of battery life.",
    },
    "laptop": {
        "id": "laptop",
        "name": "15.6 inch Business Laptop",
        "price": 29999,
        "currency": "USD",
        "description": "A business laptop with a 15.6 inch screen, "
        "Intel Core i7 processor, 16GB of RAM, and a 512GB SSD.",
    },
    "camera": {
        "id": "camera",
        "name": "Full-Frame Mirrorless Camera",
        "price": 34999,
        "currency": "USD",
        "description": "A 33MP full-frame mirrorless camera, "
        "capable of up to 4K 60p in all recording formats.",
    },
}


def list_products() -> list[Product]:
    return list(PRODUCTS.values())


def get_product(product_id: str) -> Product | None:
    return PRODUCTS.get(product_id)
