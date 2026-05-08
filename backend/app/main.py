"""FastAPI entrypoint for the GEX dashboard backend."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.live_engine import LiveEngine, get_engine, set_engine
from app.routers import gex as gex_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the Databento Live engine if both keys are configured."""
    settings = get_settings()
    if settings.opra_key and settings.glbx_key:
        engine = LiveEngine(
            opra_key=settings.opra_key,
            glbx_key=settings.glbx_key,
            risk_free_rate=settings.gex_risk_free_rate,
            dividend_yield=settings.gex_dividend_yield,
        )
        try:
            await engine.start()
            set_engine(engine)
            logger.info("LiveEngine running")
        except Exception:
            logger.exception("LiveEngine failed to start; falling back to REST-only mode")
    else:
        logger.warning("Databento keys not configured; LiveEngine disabled")

    try:
        yield
    finally:
        running = get_engine()
        if running is not None:
            await running.stop()
            set_engine(None)


settings = get_settings()
app = FastAPI(
    title="GEX Dashboard API",
    description="Per-strike Gamma Exposure for SPX with ES-futures basis projection.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gex_router.router)
app.include_router(gex_router.ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Cheap liveness probe (no key check)."""
    return {"status": "ok"}
