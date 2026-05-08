"""Black-Scholes greeks and implied-volatility solver.

We follow the European-option model with continuous dividend yield (q). For
SPX index options, q is the dividend yield of the index. For options-on-futures
(if extended later), set q = r so the formula collapses to the Black-76 form.

Conventions
-----------
S : underlying price (SPX cash / synthetic forward in spot-space)
K : strike
T : year-fraction to expiry (ACT/365)
r : continuous risk-free rate
q : continuous dividend yield
sigma : annualized volatility

Gamma per the standard formula:
    gamma = exp(-q*T) * phi(d1) / (S * sigma * sqrt(T))
"""
from __future__ import annotations

import math

from scipy.optimize import brentq
from scipy.stats import norm

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _phi(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    return (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def bs_price(
    S: float, K: float, T: float, r: float, q: float, sigma: float, is_call: bool
) -> float:
    """Black-Scholes price of a European option.

    Returns 0.0 for non-positive T or sigma (degenerate cases).
    """
    if T <= 0.0 or sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        intrinsic = max(0.0, (S - K) if is_call else (K - S))
        return intrinsic
    d1 = _d1(S, K, T, r, q, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    if is_call:
        return S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
    return K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)


def bs_gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """Gamma of a European option (same for calls and puts under BSM).

    Returns 0.0 for degenerate inputs.
    """
    if T <= 0.0 or sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        return 0.0
    d1 = _d1(S, K, T, r, q, sigma)
    return math.exp(-q * T) * _phi(d1) / (S * sigma * math.sqrt(T))


def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    is_call: bool,
    *,
    lower: float = 1e-4,
    upper: float = 5.0,
    tol: float = 1e-6,
) -> float | None:
    """Solve for implied volatility via Brent's method.

    Returns None if no solution exists in [lower, upper] (e.g. arbitrage
    violation, sub-intrinsic mid, or super-bound mid).
    """
    if market_price is None or market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None

    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    intrinsic = max(0.0, (S * df_q - K * df_r) if is_call else (K * df_r - S * df_q))
    if market_price < intrinsic - 1e-8:
        return None
    upper_bound = S * df_q if is_call else K * df_r
    if market_price > upper_bound + 1e-8:
        return None

    def diff(sigma: float) -> float:
        return bs_price(S, K, T, r, q, sigma, is_call) - market_price

    try:
        f_lo = diff(lower)
        f_hi = diff(upper)
        if f_lo * f_hi > 0:
            return None
        return float(brentq(diff, lower, upper, xtol=tol, maxiter=100))
    except (ValueError, RuntimeError):
        return None
