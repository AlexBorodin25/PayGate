from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers.checkout import cancel, checkout, success
from app.routers.orders import list_orders
from app.routers.pages import checkout_form, product_page
from app.routers.products import get_products
from app.routers.webhooks import stripe_webhook

app = FastAPI(
    title="PayGate",
    description=("A Stripe Checkout payment service for digital products."),
)

app.state.settings = settings

app.add_api_route("/", product_page, methods=["GET"])
app.add_api_route("/checkout-form", checkout_form, methods=["POST"])
app.add_api_route("/products", get_products, methods=["GET"])
app.add_api_route("/checkout", checkout, methods=["POST"])
app.add_api_route("/success", success, methods=["GET"])
app.add_api_route("/cancel", cancel, methods=["GET"])
app.add_api_route("/webhooks/stripe", stripe_webhook, methods=["POST"])
app.add_api_route("/orders", list_orders, methods=["GET"], response_model=None)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns a simple application health status.",
)
def health() -> dict[str, str]:
    return {"status": "ok"}
