"""Spot vs futures basis utilities.

For SPX, the natural quoting space is the cash index. The user wants the GEX
chart's strike axis to be optionally re-projected into ES futures terms.

We define `basis = F_front - S_spot` (in index points). When the chart toggles
to "futures view" the strike axis is shifted by +basis. (Strikes themselves
do not change; this is purely a labeling transform.)

Spot estimate
-------------
SPX cash isn't part of OPRA quotes. We approximate it via put-call parity at
the nearest at-the-money strike of a short-dated expiry (ideally 0DTE), where
PV-discount effects are minimal:

    F_synthetic ≈ K + (C_mid - P_mid)  (for very short T, F ≈ S)

If a 0DTE chain is unavailable, callers can pass a longer expiry and its
synthetic forward; the result will then be ES_front-style forward minus the
SPX-equivalent forward at that expiry, which is still a useful basis for UI.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AtmPair:
    """Closest-to-spot call/put mids at the same strike."""
    strike: float
    call_mid: float
    put_mid: float


def synthetic_forward(pair: AtmPair) -> float:
    """SPX synthetic forward at the pair's expiry, ignoring discount factor."""
    return pair.strike + pair.call_mid - pair.put_mid


def find_atm_pair(quotes_by_strike: dict[float, dict]) -> AtmPair | None:
    """Pick the strike with the smallest |call_mid - put_mid| as ATM proxy.

    Expects a mapping like:
        {strike: {"call_mid": float|None, "put_mid": float|None}, ...}

    Strikes missing either side are skipped.
    """
    best: AtmPair | None = None
    best_diff: float | None = None
    for strike, sides in quotes_by_strike.items():
        c = sides.get("call_mid")
        p = sides.get("put_mid")
        if c is None or p is None or c <= 0 or p <= 0:
            continue
        diff = abs(c - p)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = AtmPair(strike=strike, call_mid=c, put_mid=p)
    return best


def compute_basis(spot: float, futures: float) -> float:
    """basis = futures - spot (index points)."""
    return futures - spot
