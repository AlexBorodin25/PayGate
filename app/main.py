from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="PayGate")
app.state.settings = settings


@app.get("/health")
def health():
    return {"status": "ok"}