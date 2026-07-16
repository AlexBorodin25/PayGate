from fastapi import FastAPI

from app.config import settings
from app.routers.products import router as products_router

app = FastAPI(title="PayGate")
app.state.settings = settings

app.include_router(products_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
