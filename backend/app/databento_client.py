"""Databento Historical client wrappers and snapshot fetcher.

This module is responsible for translating Databento's raw schemas into the
flat option-chain shape the GEX engine consumes. Live streaming will be added
in a later iteration; the public surface here (`fetch_snapshot`) will be reused.

Design notes
------------
- We hit the Historical API for "now-ish" data because Databento publishes
  with very low lag (~7 min). For off-hours we widen the window to capture
  the previous session's close.
- Instead of subscribing to the full OPRA firehose, we first fetch the
  definition schema (cheap), filter to the requested expiry, and then re-fetch
  the BBO/statistics schemas constrained to those raw symbols only.
- Any failure (auth, empty windows, network) is converted to `SnapshotError`
  by `fetch_snapshot`. Routers may choose to fall back to `mock_snapshot`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.basis import AtmPair, compute_basis, find_atm_pair, synthetic_forward
from app.gex import ChainQuote
from app.models import BasisInfo

logger = logging.getLogger(__name__)

OPRA_DATASET = "OPRA.PILLAR"
GLBX_DATASET = "GLBX.MDP3"
SPX_PARENTS = ["SPX.OPT", "SPXW.OPT"]
ES_PARENT = "ES.FUT"

OPEN_INTEREST_STAT_TYPE = 9

DEFAULT_LOOKBACK_HOURS = 6
HISTORICAL_LAG_MINUTES = 20
DATASET_RANGE_TTL_S = 300


class SnapshotError(RuntimeError):
    """Raised when a real-data snapshot cannot be assembled."""


_dataset_end_cache: dict[str, tuple[float, datetime]] = {}


def _dataset_end_cached(client: Any, dataset: str) -> datetime | None:
    """Latest timestamp Databento has published for `dataset`, cached for 5 min.

    Markets close ~21:30 UTC (OPRA) / ~22:00 UTC (GLBX) but Historical replay
    can lag arbitrarily, so we query `metadata.get_dataset_range` to discover
    the real frontier instead of guessing with a constant lag.
    """
    now = datetime.now(UTC).timestamp()
    cached = _dataset_end_cache.get(dataset)
    if cached and now - cached[0] < DATASET_RANGE_TTL_S:
        return cached[1]
    try:
        info = client.metadata.get_dataset_range(dataset=dataset)
    except Exception as exc:  # pragma: no cover - network/auth path
        logger.warning("metadata.get_dataset_range(%s) failed: %s", dataset, exc)
        return None
    end_raw = (
        info.get("end")
        or info.get("end_date")
        or info.get("schema", {}).get("definition", {}).get("end")
    )
    if not end_raw:
        return None
    try:
        end_ts = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("could not parse dataset end %r", end_raw)
        return None
    if end_ts.tzinfo is None:
        end_ts = end_ts.replace(tzinfo=UTC)
    _dataset_end_cache[dataset] = (now, end_ts)
    return end_ts


def historical_window(
    *,
    lookback: timedelta,
    client: Any | None = None,
    dataset: str = OPRA_DATASET,
    lag: timedelta = timedelta(minutes=HISTORICAL_LAG_MINUTES),
) -> tuple[datetime, datetime]:
    """Return a `(start, end)` window safely behind the publication frontier.

    Two failure modes Databento Historical surfaces if you're sloppy:

    * `data_end_after_available_end` — `end` is past the dataset's frontier
      (publication lag spikes overnight after the close). Solved by clamping
      to `metadata.get_dataset_range` when a client is supplied, falling back
      to `now - lag`.
    * `data_start_too_precise_to_forward_fill` — sub-second `start` with no
      `end`. Solved by rounding both ends down to whole minutes.
    """
    candidate = (datetime.now(UTC) - lag).replace(second=0, microsecond=0)
    if client is not None:
        ds_end = _dataset_end_cached(client, dataset)
        if ds_end is not None:
            candidate = min(candidate, ds_end.replace(second=0, microsecond=0))
    end = candidate
    start = (end - lookback).replace(second=0, microsecond=0)
    return start, end


@dataclass(slots=True)
class Snapshot:
    """Bundle returned by `fetch_snapshot`."""
    quotes: list[ChainQuote]
    spot: float
    futures: float
    basis_info: BasisInfo
    as_of: datetime
    expiry: date


def _import_databento():
    """Lazy import so unit tests can run without the optional dep installed."""
    try:
        import databento as db
    except ImportError as exc:  # pragma: no cover
        raise SnapshotError(f"databento package not installed: {exc}") from exc
    return db


def _historical(api_key: str | None):
    if not api_key:
        raise SnapshotError("Databento API key not configured")
    db = _import_databento()
    return db.Historical(key=api_key)


def list_spx_expiries(opra_key: str | None, horizon_days: int) -> list[tuple[date, int]]:
    """Return (expiry_date, instrument_count) for SPX/SPXW within the horizon.

    Uses today's definition schema; SPXW are weeklies (incl. 0DTE) and SPX are
    standard monthlies. We only count *option* instruments (C/P), excluding
    any index-quote spurious rows.
    """
    client = _historical(opra_key)
    start, end = historical_window(
        lookback=timedelta(days=2), client=client, dataset=OPRA_DATASET
    )
    try:
        data = client.timeseries.get_range(
            dataset=OPRA_DATASET,
            schema="definition",
            symbols=SPX_PARENTS,
            stype_in="parent",
            start=start,
            end=end,
        )
        df = data.to_df()
    except Exception as exc:  # pragma: no cover - network/auth path
        raise SnapshotError(f"definition fetch failed: {exc}") from exc

    if df.empty:
        return []

    if "instrument_class" in df.columns:
        df = df[df["instrument_class"].isin(["C", "P"])]
    today = end.date()
    horizon = today + timedelta(days=horizon_days)
    df = df.copy()
    df["expiry_date"] = df["expiration"].dt.date
    df = df[(df["expiry_date"] >= today) & (df["expiry_date"] <= horizon)]
    if df.empty:
        return []
    grouped = (
        df.drop_duplicates(subset=["instrument_id"])
          .groupby("expiry_date")
          .size()
          .reset_index(name="count")
          .sort_values("expiry_date")
    )
    return [(row.expiry_date, int(row.count)) for row in grouped.itertuples()]


def _fetch_definitions(client: Any, expiry: date) -> Any:
    """Fetch SPX/SPXW definitions and filter to the requested expiry."""
    start, end = historical_window(
        lookback=timedelta(days=2), client=client, dataset=OPRA_DATASET
    )
    data = client.timeseries.get_range(
        dataset=OPRA_DATASET,
        schema="definition",
        symbols=SPX_PARENTS,
        stype_in="parent",
        start=start,
        end=end,
    )
    df = data.to_df()
    if df.empty:
        raise SnapshotError("no SPX definitions returned")
    if "instrument_class" in df.columns:
        df = df[df["instrument_class"].isin(["C", "P"])]
    df = df.copy()
    df["expiry_date"] = df["expiration"].dt.date
    df = df[df["expiry_date"] == expiry]
    if df.empty:
        raise SnapshotError(f"no SPX instruments for expiry {expiry.isoformat()}")
    df = df.drop_duplicates(subset=["instrument_id"])
    return df


def _fetch_latest_bbo(client: Any, raw_symbols: list[str], lookback_hours: int) -> Any:
    """Pull the most recent CBBO-1m record per instrument."""
    start, end = historical_window(
        lookback=timedelta(hours=lookback_hours),
        client=client,
        dataset=OPRA_DATASET,
    )
    data = client.timeseries.get_range(
        dataset=OPRA_DATASET,
        schema="cbbo-1m",
        symbols=raw_symbols,
        stype_in="raw_symbol",
        start=start,
        end=end,
    )
    df = data.to_df()
    if df.empty:
        return df
    df = df.sort_values("ts_event").groupby("instrument_id").tail(1).reset_index(drop=True)
    return df


def _fetch_open_interest(client: Any, raw_symbols: list[str]) -> Any:
    """Pull the latest OI per instrument (stat_type=OPEN_INTEREST)."""
    start, end = historical_window(
        lookback=timedelta(days=3), client=client, dataset=OPRA_DATASET
    )
    try:
        data = client.timeseries.get_range(
            dataset=OPRA_DATASET,
            schema="statistics",
            symbols=raw_symbols,
            stype_in="raw_symbol",
            start=start,
            end=end,
        )
        df = data.to_df()
    except Exception as exc:  # pragma: no cover - tolerate missing OI gracefully
        logger.warning("OI fetch failed (will fall back to OI=0): %s", exc)
        return None
    if df.empty:
        return None
    if "stat_type" in df.columns:
        df = df[df["stat_type"] == OPEN_INTEREST_STAT_TYPE]
    if df.empty:
        return None
    df = df.sort_values("ts_event").groupby("instrument_id").tail(1).reset_index(drop=True)
    return df


def _resolve_es_front_month(client: Any) -> tuple[str, date, float]:
    """Return (raw_symbol, expiry, last_trade_price) for the ES front-month future."""
    start, end = historical_window(
        lookback=timedelta(days=2), client=client, dataset=GLBX_DATASET
    )
    defs = client.timeseries.get_range(
        dataset=GLBX_DATASET,
        schema="definition",
        symbols=[ES_PARENT],
        stype_in="parent",
        start=start,
        end=end,
    ).to_df()
    if defs.empty:
        raise SnapshotError("no ES futures definitions returned")
    today = end.date()
    defs = defs.copy()
    defs["expiry_date"] = defs["expiration"].dt.date
    defs = defs[defs["expiry_date"] >= today]
    if defs.empty:
        raise SnapshotError("no live ES front-month found")
    if "instrument_class" in defs.columns:
        defs = defs[defs["instrument_class"] == "F"]
    if defs.empty:
        raise SnapshotError("no ES futures (instrument_class=F) found")
    defs = defs.drop_duplicates(subset=["instrument_id"]).sort_values("expiry_date")
    front = defs.iloc[0]
    raw_symbol = str(front["raw_symbol"])

    bbo_start, bbo_end = historical_window(
        lookback=timedelta(hours=DEFAULT_LOOKBACK_HOURS),
        client=client,
        dataset=GLBX_DATASET,
    )
    bbo = client.timeseries.get_range(
        dataset=GLBX_DATASET,
        schema="mbp-1",
        symbols=[raw_symbol],
        stype_in="raw_symbol",
        start=bbo_start,
        end=bbo_end,
    ).to_df()
    if bbo.empty:
        raise SnapshotError(f"no recent ES quotes for {raw_symbol}")
    last = bbo.sort_values("ts_event").iloc[-1]
    bid = float(last.get("bid_px_00") or 0.0)
    ask = float(last.get("ask_px_00") or 0.0)
    if bid > 0 and ask > 0:
        price = 0.5 * (bid + ask)
    else:
        price = float(last.get("price") or bid or ask)
    if price <= 0:
        raise SnapshotError("could not determine ES front-month price")
    return raw_symbol, front["expiry_date"], price


def fetch_snapshot(
    opra_key: str | None,
    glbx_key: str | None,
    expiry: date,
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> Snapshot:
    """Build a chain snapshot for the given SPX expiry.

    Raises SnapshotError if any required leg of the data graph is missing.
    """
    opra = _historical(opra_key)
    glbx = _historical(glbx_key)

    defs_df = _fetch_definitions(opra, expiry)
    raw_symbols = defs_df["raw_symbol"].astype(str).tolist()
    bbo_df = _fetch_latest_bbo(opra, raw_symbols, lookback_hours)
    oi_df = _fetch_open_interest(opra, raw_symbols)

    bbo_lookup: dict[int, dict] = {}
    if bbo_df is not None and not bbo_df.empty:
        for row in bbo_df.itertuples():
            bbo_lookup[int(row.instrument_id)] = {
                "bid": float(row.bid_px_00) if row.bid_px_00 == row.bid_px_00 else None,
                "ask": float(row.ask_px_00) if row.ask_px_00 == row.ask_px_00 else None,
            }

    oi_lookup: dict[int, float] = {}
    if oi_df is not None and not oi_df.empty:
        oi_field = "quantity" if "quantity" in oi_df.columns else "price"
        for row in oi_df.itertuples():
            oi_lookup[int(row.instrument_id)] = float(getattr(row, oi_field, 0.0) or 0.0)

    quotes_by_strike: dict[float, dict[str, float | None]] = {}
    quotes: list[ChainQuote] = []
    for row in defs_df.itertuples():
        instrument_id = int(row.instrument_id)
        is_call = row.instrument_class == "C"
        strike = float(row.strike_price)
        bbo = bbo_lookup.get(instrument_id)
        bid = bbo["bid"] if bbo else None
        ask = bbo["ask"] if bbo else None
        mid: float | None = None
        if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
            mid = 0.5 * (bid + ask)
        oi = oi_lookup.get(instrument_id, 0.0)
        quotes.append(ChainQuote(strike=strike, is_call=is_call, mid=mid, open_interest=oi))
        slot = quotes_by_strike.setdefault(strike, {"call_mid": None, "put_mid": None})
        slot["call_mid" if is_call else "put_mid"] = mid

    atm = find_atm_pair(quotes_by_strike)
    if atm is None:
        raise SnapshotError("could not locate ATM pair for spot estimation")
    spot_estimate = synthetic_forward(atm)

    es_symbol, es_expiry, futures_price = _resolve_es_front_month(glbx)
    basis = compute_basis(spot_estimate, futures_price)
    basis_info = BasisInfo(
        spot=spot_estimate,
        futures=futures_price,
        basis=basis,
        front_month_symbol=es_symbol,
        futures_expiry=es_expiry,
        spot_source=f"put_call_parity@K={atm.strike:.0f}",
    )

    return Snapshot(
        quotes=quotes,
        spot=spot_estimate,
        futures=futures_price,
        basis_info=basis_info,
        as_of=datetime.now(UTC),
        expiry=expiry,
    )


def mock_snapshot(expiry: date | None = None) -> Snapshot:
    """Deterministic SPX-like snapshot for offline development.

    Generates a strike grid centered around 5800 with a smile in IV (encoded
    via mid prices) and realistic OI distribution. Used as a fallback when the
    Databento API is unavailable or returns no data.
    """
    import math

    today = datetime.now(UTC).date()
    expiry = expiry or (today + timedelta(days=1))
    spot = 5800.0
    futures_price = spot + 12.0
    T = max((expiry - today).days, 1) / 365.0
    sigma = 0.16

    quotes: list[ChainQuote] = []
    quotes_by_strike: dict[float, dict[str, float | None]] = {}
    for k in range(-40, 41):
        strike = spot + k * 5.0
        moneyness = abs(strike - spot) / spot
        oi_call = max(50.0, 8000.0 * math.exp(-(moneyness * 28.0) ** 2))
        oi_put = max(50.0, 9500.0 * math.exp(-(moneyness * 26.0) ** 2))
        d1 = (math.log(spot / strike) + (0.045 - 0.013 + 0.5 * sigma * sigma) * T) / (
            sigma * math.sqrt(T)
        )
        d2 = d1 - sigma * math.sqrt(T)
        from scipy.stats import norm

        df_q = math.exp(-0.013 * T)
        df_r = math.exp(-0.045 * T)
        call_mid = float(spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2))
        put_mid = float(strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1))
        quotes.append(ChainQuote(strike=strike, is_call=True, mid=max(call_mid, 0.05),
                                 open_interest=oi_call))
        quotes.append(ChainQuote(strike=strike, is_call=False, mid=max(put_mid, 0.05),
                                 open_interest=oi_put))
        quotes_by_strike[strike] = {
            "call_mid": max(call_mid, 0.05),
            "put_mid": max(put_mid, 0.05),
        }

    atm = find_atm_pair(quotes_by_strike) or AtmPair(strike=spot, call_mid=1.0, put_mid=1.0)
    spot_estimate = synthetic_forward(atm)
    basis = compute_basis(spot_estimate, futures_price)
    basis_info = BasisInfo(
        spot=spot_estimate,
        futures=futures_price,
        basis=basis,
        front_month_symbol="ESM6 (mock)",
        futures_expiry=today + timedelta(days=45),
        spot_source=f"mock_put_call_parity@K={atm.strike:.0f}",
    )
    return Snapshot(
        quotes=quotes,
        spot=spot_estimate,
        futures=futures_price,
        basis_info=basis_info,
        as_of=datetime.now(UTC),
        expiry=expiry,
    )
