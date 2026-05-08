"""Pydantic models for API responses.

These shapes are stable and consumed by the frontend (`frontend/src/lib/api.ts`).
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class GexLevel(BaseModel):
    """Per-strike gamma exposure breakdown.

    Units of `*_gex` fields: USD of dealer-gamma per 1% spot move (the standard
    SqueezeMetrics-style scaling: gamma * OI * 100 * S^2 * 0.01, with sign).
    """

    strike: float
    call_oi: float = 0.0
    put_oi: float = 0.0
    call_iv: float | None = None
    put_iv: float | None = None
    call_gamma: float | None = None
    put_gamma: float | None = None
    call_gex: float = 0.0
    put_gex: float = 0.0
    net_gex: float = 0.0


class ExpiryInfo(BaseModel):
    """Available expiry with derived metadata."""

    expiry: date
    dte: int
    instrument_count: int


class BasisInfo(BaseModel):
    """Spot vs futures pricing reference."""

    spot: float = Field(..., description="Estimated SPX cash / synthetic forward price")
    futures: float = Field(..., description="Front-month ES futures last trade")
    basis: float = Field(..., description="futures - spot, in index points")
    front_month_symbol: str = Field(..., description="e.g. ESM6 for June 2026 ES")
    futures_expiry: date | None = None
    spot_source: str = Field(..., description="how spot was derived (e.g. 'put_call_parity')")


class GexSnapshot(BaseModel):
    """Full snapshot returned by /api/gex/{symbol}."""

    symbol: str
    expiry: date
    as_of: datetime
    is_live: bool = False
    is_mock: bool = False
    levels: list[GexLevel]

    total_call_gex: float
    total_put_gex: float
    total_net_gex: float
    gamma_flip: float | None = Field(
        default=None,
        description="Strike at which cumulative net GEX crosses zero (linear interp)",
    )
    largest_positive_strike: float | None = None
    largest_negative_strike: float | None = None

    basis: BasisInfo


class Health(BaseModel):
    status: str
    opra_key_configured: bool
    glbx_key_configured: bool
    version: str = "0.1.0"
