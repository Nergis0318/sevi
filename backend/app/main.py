from fastapi import FastAPI

from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="sevi", version="0.1.0")
    app.state.settings = settings

    from app.api.health import router as health_router

    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
