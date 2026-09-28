from fastapi import APIRouter, Request
from sqlmodel import text

from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"ok": db_ok, "service": "sevi"}
