from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.ratelimit import RateLimiter
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="sevi", version="0.1.0")
    app.state.settings = settings
    app.state.limiter = RateLimiter(per_hour=settings.rate_limit_per_hour)

    from app.api.config import router as config_router
    from app.api.health import router as health_router
    from app.api.jobs import router as jobs_router

    app.include_router(health_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
