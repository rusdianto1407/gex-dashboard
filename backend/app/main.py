"""FastAPI entrypoint for the GEX dashboard backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import gex as gex_router

settings = get_settings()
app = FastAPI(
    title="GEX Dashboard API",
    description="Per-strike Gamma Exposure for SPX with ES-futures basis projection.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gex_router.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Cheap liveness probe (no key check)."""
    return {"status": "ok"}
