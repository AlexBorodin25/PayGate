from fastapi import FastAPI

from app.config import settings
from app.routers.checkout import router as checkout_router
from app.routers.products import router as products_router

app = FastAPI(title="PayGate")
app.state.settings = settings

app.include_router(products_router)
app.include_router(checkout_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
