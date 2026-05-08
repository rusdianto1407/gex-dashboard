"""Greeks correctness tests against textbook values."""
from __future__ import annotations

import math

from app.greeks import bs_gamma, bs_price, implied_vol


def test_atm_call_put_parity():
    S, K, T, r, q, sigma = 100.0, 100.0, 0.5, 0.03, 0.0, 0.20
    c = bs_price(S, K, T, r, q, sigma, is_call=True)
    p = bs_price(S, K, T, r, q, sigma, is_call=False)
    parity = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert abs((c - p) - parity) < 1e-8


def test_gamma_atm_positive_and_symmetric():
    S, T, r, q, sigma = 100.0, 0.5, 0.03, 0.0, 0.20
    g_atm = bs_gamma(S, S, T, r, q, sigma)
    assert g_atm > 0
    g_far_otm = bs_gamma(S, S * 1.5, T, r, q, sigma)
    assert g_far_otm < g_atm


def test_implied_vol_roundtrip():
    S, K, T, r, q = 100.0, 105.0, 0.25, 0.04, 0.01
    for true_sigma in (0.10, 0.20, 0.40, 0.80):
        for is_call in (True, False):
            price = bs_price(S, K, T, r, q, true_sigma, is_call)
            recovered = implied_vol(price, S, K, T, r, q, is_call)
            assert recovered is not None
            assert abs(recovered - true_sigma) < 1e-4


def test_implied_vol_returns_none_on_arbitrage():
    S, K, T, r, q = 100.0, 90.0, 0.25, 0.04, 0.0
    too_low = -1.0
    assert implied_vol(too_low, S, K, T, r, q, True) is None
    way_too_high = 1e6
    assert implied_vol(way_too_high, S, K, T, r, q, True) is None


def test_degenerate_inputs():
    assert bs_gamma(100, 100, 0, 0.03, 0, 0.2) == 0.0
    assert bs_gamma(100, 100, 0.5, 0.03, 0, 0.0) == 0.0
    assert bs_price(100, 105, 0.0, 0.03, 0, 0.2, True) == 0.0
    assert bs_price(100, 95, 0.0, 0.03, 0, 0.2, True) == 5.0
