from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import orders, pages
from app.routers.checkout import router as checkout_router
from app.routers.products import router as products_router
from app.routers.webhooks import router as webhooks_router

app = FastAPI(
    title="PayGate",
    description=("A Stripe Checkout payment service for digital products."),
)

app.state.settings = settings

app.include_router(products_router)
app.include_router(checkout_router)
app.include_router(webhooks_router)
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
