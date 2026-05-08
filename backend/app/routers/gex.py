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
GET /api/engine/status
    Live engine introspection (uptime, last message, expiry coverage).
WS  /ws/gex/{symbol}?expiry=YYYY-MM-DD&use_mock=false
    Streams a `GexSnapshot` JSON message on connect, then again on every
    update from the Databento Live engine. If the engine is not running,
    the connection falls back to a single mock snapshot and stays open
    sending heartbeats so the UI keeps a stable connection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.config import Settings, get_settings
from app.databento_client import (
    Snapshot,
    SnapshotError,
    fetch_snapshot,
    list_spx_expiries,
    mock_snapshot,
)
from app.gex import aggregate_levels, summarize
from app.live_engine import get_engine
from app.models import ExpiryInfo, GexSnapshot, Health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["gex"])
ws_router = APIRouter(tags=["gex-ws"])

SUPPORTED_SYMBOLS = {"SPX"}
WS_HEARTBEAT_S = 15.0


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


@router.get("/engine/status")
def engine_status() -> dict[str, Any]:
    """Introspection for the Databento Live engine (used by the UI badge)."""
    engine = get_engine()
    if engine is None:
        return {"running": False}
    expiries = engine.available_expiries()
    last = engine.last_message_at
    started = engine.started_at
    return {
        "running": engine.is_running,
        "started_at": started.isoformat() if started else None,
        "last_message_at": last.isoformat() if last else None,
        "expiries": [
            {"expiry": d.isoformat(), "instrument_count": c} for d, c in expiries
        ],
    }


def _resolve_snapshot(
    symbol: str,
    expiry: date,
    settings: Settings,
    *,
    use_mock: bool,
) -> GexSnapshot:
    """Build a snapshot for one expiry — engine first, then historical, then mock."""
    if not use_mock:
        engine = get_engine()
        if engine is not None and engine.is_running:
            live_snap = engine.build_snapshot(symbol, expiry)
            if live_snap is not None:
                return live_snap

    if use_mock or not (settings.opra_key and settings.glbx_key):
        return _to_response(symbol, expiry, mock_snapshot(expiry), settings, is_mock=True)

    try:
        snap = fetch_snapshot(
            opra_key=settings.opra_key,
            glbx_key=settings.glbx_key,
            expiry=expiry,
        )
    except SnapshotError as exc:
        logger.warning("falling back to mock snapshot: %s", exc)
        return _to_response(symbol, expiry, mock_snapshot(expiry), settings, is_mock=True)
    return _to_response(symbol, expiry, snap, settings, is_mock=False)


@ws_router.websocket("/ws/gex/{symbol}")
async def gex_ws(
    websocket: WebSocket,
    symbol: str,
    expiry: Annotated[date | None, Query()] = None,
    use_mock: Annotated[bool, Query()] = False,
) -> None:
    """Streams a `GexSnapshot` JSON message on connect, then on every engine update."""
    await websocket.accept()
    if symbol.upper() not in SUPPORTED_SYMBOLS:
        await websocket.close(code=1003, reason=f"symbol not supported: {symbol}")
        return

    settings = get_settings()
    today = datetime.now(UTC).date()
    chosen_expiry = expiry or (today + timedelta(days=1))

    try:
        initial = _resolve_snapshot(symbol, chosen_expiry, settings, use_mock=use_mock)
        await websocket.send_text(initial.model_dump_json())
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("ws initial snapshot failed: %s", exc)
        await websocket.close(code=1011, reason="initial snapshot failed")
        return

    engine = None if use_mock else get_engine()
    if engine is None or not engine.is_running:
        await _heartbeat_until_disconnect(websocket)
        return

    queue = engine.subscribe(chosen_expiry)
    try:
        while True:
            try:
                snap = await asyncio.wait_for(queue.get(), timeout=WS_HEARTBEAT_S)
                await websocket.send_text(snap.model_dump_json())
            except TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws stream failed")
    finally:
        engine.unsubscribe(chosen_expiry, queue)


async def _heartbeat_until_disconnect(websocket: WebSocket) -> None:
    """Keep the socket alive with periodic heartbeats when no engine is running."""
    try:
        while True:
            await asyncio.sleep(WS_HEARTBEAT_S)
            await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except WebSocketDisconnect:
        return
    except Exception:
        return
