from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import checkout, internal, orders, pages, products, webhooks

app = FastAPI(
    title="PayGate",
    description=(
        "A Stripe Checkout payment service with stock reservation, "
        "verified webhook payment reconciliation, durable QStash fulfillment, "
        "and protected operator order management."
    ),
)

app.state.settings = settings

app.include_router(products.router)
app.include_router(checkout.router)
app.include_router(webhooks.router)
app.include_router(pages.router)
app.include_router(orders.router)
app.include_router(internal.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns a simple application health status.",
)
def health() -> dict[str, str]:
    return {"status": "ok"}
