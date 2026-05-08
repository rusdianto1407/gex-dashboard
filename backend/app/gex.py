"""Gamma Exposure (GEX) aggregation.

We use the "naive dealer" sign convention popularized by SqueezeMetrics
(`https://squeezemetrics.com/`) and replicated by GEXBot:

    Dealers are SHORT calls and LONG puts.

Therefore per strike:

    call_gex(K) = - gamma_call(K) * OI_call(K) * contract_multiplier * S^2 * 0.01
    put_gex(K)  = + gamma_put(K)  * OI_put(K)  * contract_multiplier * S^2 * 0.01
    net_gex(K)  = call_gex(K) + put_gex(K)

Units: USD of notional gamma per 1% move in the underlying. Positive net GEX
implies dealers are net long gamma at that strike (suppresses realized vol);
negative means short gamma (amplifies vol).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.greeks import bs_gamma, implied_vol
from app.models import GexLevel

CONTRACT_MULTIPLIER = 100.0  # standard US listed equity/index option multiplier


@dataclass(slots=True)
class ChainQuote:
    """A single option-side quote at a given strike.

    `mid` may be None if the side is uncrossable; in that case implied vol
    cannot be solved and gamma falls back to None.
    """

    strike: float
    is_call: bool
    mid: float | None
    open_interest: float = 0.0


def _pick_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return 0.5 * (bid + ask)


def quote_from_bbo(
    strike: float,
    is_call: bool,
    bid: float | None,
    ask: float | None,
    open_interest: float = 0.0,
) -> ChainQuote:
    """Build a ChainQuote from raw BBO inputs."""
    return ChainQuote(strike=strike, is_call=is_call, mid=_pick_mid(bid, ask),
                      open_interest=open_interest)


def aggregate_levels(
    quotes: list[ChainQuote],
    *,
    spot: float,
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> list[GexLevel]:
    """Aggregate per-strike GEX from a list of option quotes.

    Quotes for the same strike are combined; calls/puts each contribute one
    side. Implied vol is solved per side from the mid price; gamma comes from
    the BSM formula.
    """
    by_strike: dict[float, GexLevel] = {}
    for q in quotes:
        level = by_strike.setdefault(q.strike, GexLevel(strike=q.strike))
        iv = None
        gamma = None
        if q.mid is not None:
            iv = implied_vol(
                q.mid, spot, q.strike, time_to_expiry, risk_free_rate, dividend_yield, q.is_call
            )
            if iv is not None:
                gamma = bs_gamma(spot, q.strike, time_to_expiry, risk_free_rate,
                                 dividend_yield, iv)

        signed_gex = 0.0
        if gamma is not None and q.open_interest > 0:
            magnitude = gamma * q.open_interest * CONTRACT_MULTIPLIER * spot * spot * 0.01
            signed_gex = -magnitude if q.is_call else magnitude

        if q.is_call:
            level.call_oi = q.open_interest
            level.call_iv = iv
            level.call_gamma = gamma
            level.call_gex = signed_gex
        else:
            level.put_oi = q.open_interest
            level.put_iv = iv
            level.put_gamma = gamma
            level.put_gex = signed_gex

        level.net_gex = level.call_gex + level.put_gex

    return sorted(by_strike.values(), key=lambda lv: lv.strike)


def gamma_flip(levels: list[GexLevel]) -> float | None:
    """Find the strike at which cumulative net GEX (sorted ascending) crosses zero.

    Uses linear interpolation between the bracketing strikes. Returns None if
    cumulative GEX never changes sign or is empty.
    """
    if not levels:
        return None
    cumulative: list[tuple[float, float]] = []
    running = 0.0
    for lv in levels:
        running += lv.net_gex
        cumulative.append((lv.strike, running))
    for i in range(1, len(cumulative)):
        x0, y0 = cumulative[i - 1]
        x1, y1 = cumulative[i]
        if y0 == 0:
            return x0
        if y0 * y1 < 0:
            return x0 + (x1 - x0) * (-y0) / (y1 - y0)
    last_strike, last_y = cumulative[-1]
    if last_y == 0:
        return last_strike
    return None


def summarize(levels: list[GexLevel]) -> dict:
    """Convenience aggregate metrics for a snapshot."""
    if not levels:
        return {
            "total_call_gex": 0.0,
            "total_put_gex": 0.0,
            "total_net_gex": 0.0,
            "gamma_flip": None,
            "largest_positive_strike": None,
            "largest_negative_strike": None,
        }
    total_call = sum(lv.call_gex for lv in levels)
    total_put = sum(lv.put_gex for lv in levels)
    pos_levels = [lv for lv in levels if lv.net_gex > 0]
    neg_levels = [lv for lv in levels if lv.net_gex < 0]
    return {
        "total_call_gex": total_call,
        "total_put_gex": total_put,
        "total_net_gex": total_call + total_put,
        "gamma_flip": gamma_flip(levels),
        "largest_positive_strike": (
            max(pos_levels, key=lambda lv: lv.net_gex).strike if pos_levels else None
        ),
        "largest_negative_strike": (
            min(neg_levels, key=lambda lv: lv.net_gex).strike if neg_levels else None
        ),
    }
