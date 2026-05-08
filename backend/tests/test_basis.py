"""Basis utility tests."""
from __future__ import annotations

from app.basis import AtmPair, compute_basis, find_atm_pair, synthetic_forward


def test_synthetic_forward_recovers_strike_when_call_equals_put():
    pair = AtmPair(strike=5800.0, call_mid=12.0, put_mid=12.0)
    assert synthetic_forward(pair) == 5800.0


def test_synthetic_forward_uses_put_call_skew():
    pair = AtmPair(strike=5800.0, call_mid=15.0, put_mid=10.0)
    assert synthetic_forward(pair) == 5805.0


def test_compute_basis_signed():
    assert compute_basis(spot=5800.0, futures=5812.5) == 12.5
    assert compute_basis(spot=5810.0, futures=5800.0) == -10.0


def test_find_atm_pair_picks_smallest_skew():
    quotes = {
        5790.0: {"call_mid": 30.0, "put_mid": 12.0},
        5800.0: {"call_mid": 15.0, "put_mid": 14.5},
        5810.0: {"call_mid": 8.0, "put_mid": 24.0},
    }
    atm = find_atm_pair(quotes)
    assert atm is not None
    assert atm.strike == 5800.0


def test_find_atm_pair_skips_invalid_sides():
    quotes = {
        5790.0: {"call_mid": None, "put_mid": 12.0},
        5800.0: {"call_mid": 0.0, "put_mid": 14.5},
        5810.0: {"call_mid": 10.0, "put_mid": 11.0},
    }
    atm = find_atm_pair(quotes)
    assert atm is not None
    assert atm.strike == 5810.0


def test_find_atm_pair_returns_none_when_empty():
    assert find_atm_pair({}) is None
