"""GEX aggregation tests."""
from __future__ import annotations

from app.gex import ChainQuote, aggregate_levels, gamma_flip, summarize
from app.models import GexLevel


def _build_chain(spot: float = 100.0):
    """Symmetric chain with known sign behavior.

    All strikes have equal call/put OI; the signed-GEX should be negative on
    calls and positive on puts under the naive convention.
    """
    quotes: list[ChainQuote] = []
    for k in range(85, 116, 5):
        quotes.append(ChainQuote(strike=float(k), is_call=True, mid=max(spot - k, 1) + 5.0,
                                 open_interest=1000.0))
        quotes.append(ChainQuote(strike=float(k), is_call=False, mid=max(k - spot, 1) + 5.0,
                                 open_interest=1000.0))
    return quotes


def test_aggregate_naive_sign_convention():
    quotes = _build_chain()
    levels = aggregate_levels(
        quotes, spot=100.0, time_to_expiry=0.25, risk_free_rate=0.04, dividend_yield=0.0
    )
    assert all(lv.call_gex <= 0 for lv in levels), "calls must be non-positive (dealer short)"
    assert all(lv.put_gex >= 0 for lv in levels), "puts must be non-negative (dealer long)"
    for lv in levels:
        assert abs(lv.net_gex - (lv.call_gex + lv.put_gex)) < 1e-9


def test_aggregate_strikes_sorted():
    quotes = _build_chain()
    levels = aggregate_levels(
        quotes, spot=100.0, time_to_expiry=0.25, risk_free_rate=0.04, dividend_yield=0.0
    )
    strikes = [lv.strike for lv in levels]
    assert strikes == sorted(strikes)


def test_summarize_handles_empty():
    s = summarize([])
    assert s["total_net_gex"] == 0.0
    assert s["gamma_flip"] is None
    assert s["largest_positive_strike"] is None


def test_gamma_flip_finds_zero_crossing():
    levels = [
        GexLevel(strike=95.0, net_gex=200.0),
        GexLevel(strike=100.0, net_gex=100.0),
        GexLevel(strike=105.0, net_gex=-200.0),
        GexLevel(strike=110.0, net_gex=-300.0),
    ]
    flip = gamma_flip(levels)
    assert flip is not None
    assert 105.0 < flip < 110.0


def test_gamma_flip_returns_none_when_no_crossing():
    levels = [
        GexLevel(strike=95.0, net_gex=10.0),
        GexLevel(strike=100.0, net_gex=20.0),
        GexLevel(strike=105.0, net_gex=30.0),
    ]
    assert gamma_flip(levels) is None


def test_gamma_flip_exact_zero_returns_strike():
    levels = [
        GexLevel(strike=95.0, net_gex=100.0),
        GexLevel(strike=100.0, net_gex=-100.0),
        GexLevel(strike=105.0, net_gex=0.0),
    ]
    assert gamma_flip(levels) == 100.0


def test_zero_oi_yields_zero_gex():
    quotes = [
        ChainQuote(strike=100.0, is_call=True, mid=5.0, open_interest=0.0),
        ChainQuote(strike=100.0, is_call=False, mid=5.0, open_interest=0.0),
    ]
    levels = aggregate_levels(
        quotes, spot=100.0, time_to_expiry=0.25, risk_free_rate=0.04, dividend_yield=0.0
    )
    assert len(levels) == 1
    assert levels[0].net_gex == 0.0
