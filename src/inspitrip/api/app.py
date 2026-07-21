from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from inspitrip import __version__
from inspitrip.api.routes import analytics, chat, location, recommendation
from inspitrip.api.settings import RuntimeSettings
from inspitrip.paths import SITE_DIR


def create_app() -> FastAPI:
    app = FastAPI(
        title="InspiTrip API",
        version=__version__,
        description="Feeling-driven travel recommendation with deterministic constraints and evidence gates.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null"],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(chat.router)
    app.include_router(recommendation.router)
    app.include_router(location.router)
    app.include_router(analytics.router)

    @app.get("/api/health", tags=["system"])
    def health():
        return {"ok": True, "service": "inspitrip", "mode": RuntimeSettings.load().mode}

    if SITE_DIR.is_dir():
        app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="site")
    return app


app = create_app()
