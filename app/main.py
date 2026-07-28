from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import checkout, orders, pages, products, webhooks

app = FastAPI(
    title="PayGate",
    description="A Stripe Checkout payment service for digital products.",
)

app.state.settings = settings

app.include_router(products.router)
app.include_router(checkout.router)
app.include_router(webhooks.router)
app.include_router(pages.router)
app.include_router(orders.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    description="Returns a simple application health status.",
)
def health() -> dict[str, str]:
    return {"status": "ok"}
