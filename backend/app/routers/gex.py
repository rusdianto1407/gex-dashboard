"""HTTP API for the GEX dashboard.

Endpoints
---------
GET /api/health
    Liveness + key-config status.
GET /api/symbols
    List of supported symbols. SPX-only for now.
GET /api/expiries/{symbol}
    Available expiries with DTE & contract counts (24-hour cache).
GET /api/gex/{symbol}?expiry=YYYY-MM-DD&use_mock=false
    Full per-strike GEX snapshot (preferred; this is the chart's data source).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.databento_client import (
    Snapshot,
    SnapshotError,
    fetch_snapshot,
    list_spx_expiries,
    mock_snapshot,
)
from app.gex import aggregate_levels, summarize
from app.models import ExpiryInfo, GexSnapshot, Health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["gex"])

SUPPORTED_SYMBOLS = {"SPX"}


def _settings_dep() -> Settings:
    return get_settings()


@router.get("/health", response_model=Health)
def health(settings: Annotated[Settings, Depends(_settings_dep)]) -> Health:
    return Health(
        status="ok",
        opra_key_configured=bool(settings.opra_key),
        glbx_key_configured=bool(settings.glbx_key),
    )


@router.get("/symbols", response_model=list[str])
def symbols() -> list[str]:
    return sorted(SUPPORTED_SYMBOLS)


@router.get("/expiries/{symbol}", response_model=list[ExpiryInfo])
def expiries(
    symbol: str,
    settings: Annotated[Settings, Depends(_settings_dep)],
    horizon_days: Annotated[int, Query(ge=1, le=180)] = 14,
    use_mock: bool = False,
) -> list[ExpiryInfo]:
    _ensure_supported(symbol)
    today = datetime.now(UTC).date()
    if use_mock or not settings.opra_key:
        return _mock_expiries(today, horizon_days)
    try:
        rows = list_spx_expiries(settings.opra_key, horizon_days=horizon_days)
    except SnapshotError as exc:
        logger.warning("falling back to mock expiries: %s", exc)
        return _mock_expiries(today, horizon_days)
    if not rows:
        return _mock_expiries(today, horizon_days)
    return [
        ExpiryInfo(expiry=expiry, dte=(expiry - today).days, instrument_count=count)
        for expiry, count in rows
    ]


@router.get("/gex/{symbol}", response_model=GexSnapshot)
def gex_snapshot(
    symbol: str,
    settings: Annotated[Settings, Depends(_settings_dep)],
    expiry: Annotated[date | None, Query(description="YYYY-MM-DD; defaults to nearest")] = None,
    use_mock: Annotated[bool, Query(description="Force mock data (dev/offline)")] = False,
) -> GexSnapshot:
    _ensure_supported(symbol)
    today = datetime.now(UTC).date()
    chosen_expiry = expiry or (today + timedelta(days=1))

    if use_mock or not (settings.opra_key and settings.glbx_key):
        return _to_response(symbol, chosen_expiry, mock_snapshot(chosen_expiry),
                            settings, is_mock=True)

    try:
        snap = fetch_snapshot(
            opra_key=settings.opra_key,
            glbx_key=settings.glbx_key,
            expiry=chosen_expiry,
        )
    except SnapshotError as exc:
        logger.warning("falling back to mock snapshot: %s", exc)
        return _to_response(symbol, chosen_expiry, mock_snapshot(chosen_expiry),
                            settings, is_mock=True)
    return _to_response(symbol, chosen_expiry, snap, settings, is_mock=False)


def _ensure_supported(symbol: str) -> None:
    if symbol.upper() not in SUPPORTED_SYMBOLS:
        raise HTTPException(404, f"symbol not supported: {symbol}")


def _mock_expiries(today: date, horizon_days: int) -> list[ExpiryInfo]:
    expiries: list[ExpiryInfo] = []
    for delta in range(0, horizon_days + 1):
        d = today + timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        expiries.append(ExpiryInfo(expiry=d, dte=delta, instrument_count=160))
    return expiries[:8]


def _to_response(
    symbol: str,
    expiry: date,
    snap: Snapshot,
    settings: Settings,
    *,
    is_mock: bool,
) -> GexSnapshot:
    today = datetime.now(UTC).date()
    dte_days = max((expiry - today).days, 1)
    T = dte_days / 365.0
    levels = aggregate_levels(
        snap.quotes,
        spot=snap.spot,
        time_to_expiry=T,
        risk_free_rate=settings.gex_risk_free_rate,
        dividend_yield=settings.gex_dividend_yield,
    )
    summary = summarize(levels)
    return GexSnapshot(
        symbol=symbol.upper(),
        expiry=expiry,
        as_of=snap.as_of,
        is_live=False,
        is_mock=is_mock,
        levels=levels,
        total_call_gex=summary["total_call_gex"],
        total_put_gex=summary["total_put_gex"],
        total_net_gex=summary["total_net_gex"],
        gamma_flip=summary["gamma_flip"],
        largest_positive_strike=summary["largest_positive_strike"],
        largest_negative_strike=summary["largest_negative_strike"],
        basis=snap.basis_info,
    )
