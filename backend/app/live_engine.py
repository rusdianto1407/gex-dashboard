"""Databento Live streaming engine.

Maintains an in-memory option-chain state populated from two Live sessions:

* OPRA.PILLAR  — `definition` + `cbbo-1s` for SPX/SPXW options.
* GLBX.MDP3    — `definition` + `bbo-1s` for ES futures (front-month resolved
  on the fly from the cheapest non-expired definition).

A periodic Historical fetch refreshes open interest (OPRA `statistics`,
`stat_type=OPEN_INTEREST`) since OI is published EOD and doesn't change
intraday — pulling it via Live would be wasteful.

A broadcast task wakes every `BROADCAST_INTERVAL_S` and, for each expiry
that has dirty quotes (or whose ES basis changed), recomputes a `GexSnapshot`
and fans it out to every subscribed asyncio.Queue.

The engine is consumed by the WebSocket router (`/ws/gex/SPX`).
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.basis import compute_basis, find_atm_pair, synthetic_forward
from app.databento_client import (
    GLBX_DATASET,
    OPEN_INTEREST_STAT_TYPE,
    OPRA_DATASET,
    SnapshotError,
    _historical,
)
from app.gex import ChainQuote, aggregate_levels, summarize
from app.models import BasisInfo, GexSnapshot

logger = logging.getLogger(__name__)

PRICE_SCALE = 1_000_000_000  # Databento int64 price → $ : divide by 1e9
UNDEF_PRICE_SENTINEL = 9_223_372_036_854_775_807  # int64.max from databento

SPX_PARENTS = ["SPX.OPT", "SPXW.OPT"]
ES_PARENT = "ES.FUT"

BROADCAST_INTERVAL_S = 0.5
OI_REFRESH_INTERVAL_S = 300  # 5 minutes
RECONNECT_BACKOFF_SECS = (1, 2, 5, 10, 30)


@dataclass(slots=True)
class _Instrument:
    expiry: date
    strike: float
    is_call: bool
    raw_symbol: str


@dataclass(slots=True)
class _ESInstrument:
    instrument_id: int
    raw_symbol: str
    expiry: date | None


@dataclass(slots=True)
class _State:
    """All mutable engine state. Always access under `LiveEngine._lock`."""

    instruments: dict[int, _Instrument] = field(default_factory=dict)
    mids: dict[int, float] = field(default_factory=dict)
    oi: dict[int, float] = field(default_factory=dict)

    es_instruments: dict[int, _ESInstrument] = field(default_factory=dict)
    es_front_id: int | None = None
    es_mids: dict[int, float] = field(default_factory=dict)

    dirty_expiries: set[date] = field(default_factory=set)
    es_dirty: bool = False
    last_oi_refresh: datetime | None = None
    last_message_at: datetime | None = None


def _safe_price(value: int | float | None) -> float | None:
    """Convert a Databento int64 price (1e-9 fixed point) to a positive float."""
    if value is None:
        return None
    iv = int(value)
    if iv == UNDEF_PRICE_SENTINEL or iv <= 0:
        return None
    return iv / PRICE_SCALE


def _mid_from_levels(record: Any) -> float | None:
    """Pull a sane mid out of a CBBO/MBP/CMBP record."""
    bid_raw: int | None = None
    ask_raw: int | None = None

    levels = getattr(record, "levels", None)
    if levels:
        try:
            level0 = levels[0]
        except (IndexError, TypeError):
            level0 = None
        if level0 is not None:
            bid_raw = getattr(level0, "bid_px", None)
            ask_raw = getattr(level0, "ask_px", None)

    if bid_raw is None:
        bid_raw = getattr(record, "bid_px_00", None) or getattr(record, "bid_px", None)
    if ask_raw is None:
        ask_raw = getattr(record, "ask_px_00", None) or getattr(record, "ask_px", None)

    bid = _safe_price(bid_raw)
    ask = _safe_price(ask_raw)
    if bid is not None and ask is not None and ask >= bid:
        return 0.5 * (bid + ask)
    return bid if bid is not None else ask


def _expiry_from_record(record: Any) -> date | None:
    """Extract expiration date from an InstrumentDefMsg, robust to representation."""
    raw = getattr(record, "expiration", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, int):
        if raw <= 0:
            return None
        return datetime.fromtimestamp(raw / 1e9, tz=UTC).date()
    return None


class LiveEngine:
    """Long-lived engine wired into FastAPI's lifespan.

    Publishes throttled `GexSnapshot` messages per (symbol, expiry) to any
    subscribed asyncio.Queue. The websocket router is the only consumer.
    """

    def __init__(
        self,
        *,
        opra_key: str,
        glbx_key: str,
        risk_free_rate: float,
        dividend_yield: float,
    ) -> None:
        self.opra_key = opra_key
        self.glbx_key = glbx_key
        self.r = risk_free_rate
        self.q = dividend_yield

        self._state = _State()
        self._lock = threading.Lock()

        self._subscribers: dict[date, set[asyncio.Queue[GexSnapshot]]] = defaultdict(set)

        self._tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started_at: datetime | None = None

    # ---- lifecycle ---------------------------------------------------------

    @property
    def is_running(self) -> bool:
        if self._started_at is None or self._stop_event is None:
            return False
        return not self._stop_event.is_set()

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def last_message_at(self) -> datetime | None:
        with self._lock:
            return self._state.last_message_at

    async def start(self) -> None:
        if self.is_running:
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._started_at = datetime.now(UTC)
        self._tasks = [
            asyncio.create_task(
                self._run_with_retry("opra-live", self._run_opra), name="opra-live"
            ),
            asyncio.create_task(
                self._run_with_retry("glbx-live", self._run_glbx), name="glbx-live"
            ),
            asyncio.create_task(self._oi_refresh_loop(), name="oi-refresh"),
            asyncio.create_task(self._broadcast_loop(), name="broadcast"),
        ]
        logger.info("LiveEngine started with %d background tasks", len(self._tasks))

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._started_at = None
        logger.info("LiveEngine stopped")

    # ---- public read api ---------------------------------------------------

    def available_expiries(self) -> list[tuple[date, int]]:
        with self._lock:
            counts: dict[date, int] = defaultdict(int)
            for inst in self._state.instruments.values():
                counts[inst.expiry] += 1
        today = datetime.now(UTC).date()
        return [(d, c) for d, c in sorted(counts.items()) if d >= today]

    def chain_for_expiry(self, expiry: date) -> tuple[list[ChainQuote], BasisInfo | None]:
        with self._lock:
            quotes: list[ChainQuote] = []
            quotes_by_strike: dict[float, dict[str, float | None]] = {}
            for instrument_id, inst in self._state.instruments.items():
                if inst.expiry != expiry:
                    continue
                mid = self._state.mids.get(instrument_id)
                oi = self._state.oi.get(instrument_id, 0.0)
                quotes.append(
                    ChainQuote(strike=inst.strike, is_call=inst.is_call, mid=mid, open_interest=oi)
                )
                slot = quotes_by_strike.setdefault(
                    inst.strike, {"call_mid": None, "put_mid": None}
                )
                slot["call_mid" if inst.is_call else "put_mid"] = mid

            es_front_id = self._state.es_front_id
            es_mid: float | None = None
            es_inst: _ESInstrument | None = None
            if es_front_id is not None:
                es_mid = self._state.es_mids.get(es_front_id)
                es_inst = self._state.es_instruments.get(es_front_id)

        atm = find_atm_pair(quotes_by_strike)
        if atm is None or es_inst is None or es_mid is None:
            return quotes, None
        spot = synthetic_forward(atm)
        basis_info = BasisInfo(
            spot=spot,
            futures=es_mid,
            basis=compute_basis(spot, es_mid),
            front_month_symbol=es_inst.raw_symbol,
            futures_expiry=es_inst.expiry,
            spot_source=f"live_put_call_parity@K={atm.strike:.0f}",
        )
        return quotes, basis_info

    def build_snapshot(self, symbol: str, expiry: date) -> GexSnapshot | None:
        quotes, basis_info = self.chain_for_expiry(expiry)
        if basis_info is None:
            return None
        today = datetime.now(UTC).date()
        dte_days = max((expiry - today).days, 1)
        T = dte_days / 365.0
        levels = aggregate_levels(
            quotes,
            spot=basis_info.spot,
            time_to_expiry=T,
            risk_free_rate=self.r,
            dividend_yield=self.q,
        )
        if not levels:
            return None
        summary = summarize(levels)
        return GexSnapshot(
            symbol=symbol.upper(),
            expiry=expiry,
            as_of=datetime.now(UTC),
            is_live=True,
            is_mock=False,
            levels=levels,
            total_call_gex=summary["total_call_gex"],
            total_put_gex=summary["total_put_gex"],
            total_net_gex=summary["total_net_gex"],
            gamma_flip=summary["gamma_flip"],
            largest_positive_strike=summary["largest_positive_strike"],
            largest_negative_strike=summary["largest_negative_strike"],
            basis=basis_info,
        )

    # ---- subscription api --------------------------------------------------

    def subscribe(self, expiry: date) -> asyncio.Queue[GexSnapshot]:
        queue: asyncio.Queue[GexSnapshot] = asyncio.Queue(maxsize=8)
        self._subscribers[expiry].add(queue)
        with self._lock:
            self._state.dirty_expiries.add(expiry)
        return queue

    def unsubscribe(self, expiry: date, queue: asyncio.Queue[GexSnapshot]) -> None:
        subs = self._subscribers.get(expiry)
        if subs is not None:
            subs.discard(queue)

    # ---- live tasks --------------------------------------------------------

    async def _run_with_retry(self, name: str, fn: Any) -> None:
        attempt = 0
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await fn()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s task failed", name)
            backoff = RECONNECT_BACKOFF_SECS[min(attempt, len(RECONNECT_BACKOFF_SECS) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                break
            except TimeoutError:
                continue

    async def _run_opra(self) -> None:
        import databento as db

        live = db.Live(key=self.opra_key)
        live.subscribe(
            dataset=OPRA_DATASET,
            schema="definition",
            symbols=SPX_PARENTS,
            stype_in="parent",
        )
        live.subscribe(
            dataset=OPRA_DATASET,
            schema="cbbo-1s",
            symbols=SPX_PARENTS,
            stype_in="parent",
        )
        try:
            async for record in live:
                self._handle_opra_record(record)
        finally:
            try:
                await asyncio.shield(asyncio.to_thread(live.terminate))
            except Exception:
                pass

    async def _run_glbx(self) -> None:
        import databento as db

        live = db.Live(key=self.glbx_key)
        live.subscribe(
            dataset=GLBX_DATASET,
            schema="definition",
            symbols=[ES_PARENT],
            stype_in="parent",
        )
        live.subscribe(
            dataset=GLBX_DATASET,
            schema="bbo-1s",
            symbols=[ES_PARENT],
            stype_in="parent",
        )
        try:
            async for record in live:
                self._handle_glbx_record(record)
        finally:
            try:
                await asyncio.shield(asyncio.to_thread(live.terminate))
            except Exception:
                pass

    def _handle_opra_record(self, record: Any) -> None:
        rtype_name = type(record).__name__
        if rtype_name == "InstrumentDefMsg":
            self._handle_opra_definition(record)
        elif rtype_name in ("CBBO1SMsg", "CBBOMsg", "CMBP1Msg"):
            self._handle_opra_quote(record)

    def _handle_opra_definition(self, record: Any) -> None:
        instrument_id = int(getattr(record, "instrument_id", 0))
        instrument_class: Any = getattr(record, "instrument_class", None)
        if instrument_class is None:
            return
        cls_value: Any
        if hasattr(instrument_class, "value"):
            cls_value = instrument_class.value
        else:
            cls_value = instrument_class
        cls_str: str
        if isinstance(cls_value, int):
            cls_str = chr(cls_value)
        else:
            cls_str = str(cls_value)
        if cls_str not in ("C", "P"):
            return
        strike_raw = getattr(record, "strike_price", None)
        strike = _safe_price(strike_raw)
        if strike is None:
            return
        expiry = _expiry_from_record(record)
        if expiry is None:
            return
        raw_symbol = str(getattr(record, "raw_symbol", "") or "")
        is_call = cls_str == "C"
        with self._lock:
            self._state.instruments[instrument_id] = _Instrument(
                expiry=expiry, strike=strike, is_call=is_call, raw_symbol=raw_symbol
            )

    def _handle_opra_quote(self, record: Any) -> None:
        instrument_id = int(getattr(record, "instrument_id", 0))
        mid = _mid_from_levels(record)
        if mid is None:
            return
        with self._lock:
            inst = self._state.instruments.get(instrument_id)
            if inst is None:
                return
            self._state.mids[instrument_id] = mid
            self._state.dirty_expiries.add(inst.expiry)
            self._state.last_message_at = datetime.now(UTC)

    def _handle_glbx_record(self, record: Any) -> None:
        rtype_name = type(record).__name__
        if rtype_name == "InstrumentDefMsg":
            self._handle_glbx_definition(record)
        elif rtype_name in ("CBBO1SMsg", "CBBOMsg", "MBP1Msg", "MBOMsg"):
            self._handle_glbx_quote(record)

    def _handle_glbx_definition(self, record: Any) -> None:
        instrument_id = int(getattr(record, "instrument_id", 0))
        instrument_class: Any = getattr(record, "instrument_class", None)
        if instrument_class is None:
            return
        cls_value: Any
        if hasattr(instrument_class, "value"):
            cls_value = instrument_class.value
        else:
            cls_value = instrument_class
        cls_str: str
        if isinstance(cls_value, int):
            cls_str = chr(cls_value)
        else:
            cls_str = str(cls_value)
        # Skip spreads/options-on-futures; we only want plain ES futures (class 'F').
        if cls_str not in ("F", "f"):
            return
        raw_symbol = str(getattr(record, "raw_symbol", "") or "")
        if not raw_symbol or not raw_symbol.startswith("ES"):
            return
        expiry = _expiry_from_record(record)
        with self._lock:
            self._state.es_instruments[instrument_id] = _ESInstrument(
                instrument_id=instrument_id, raw_symbol=raw_symbol, expiry=expiry
            )
            self._refresh_es_front_locked()

    def _handle_glbx_quote(self, record: Any) -> None:
        instrument_id = int(getattr(record, "instrument_id", 0))
        mid = _mid_from_levels(record)
        if mid is None:
            return
        with self._lock:
            if instrument_id not in self._state.es_instruments:
                return
            self._state.es_mids[instrument_id] = mid
            self._refresh_es_front_locked()
            if self._state.es_front_id == instrument_id:
                self._state.es_dirty = True
                self._state.last_message_at = datetime.now(UTC)

    def _refresh_es_front_locked(self) -> None:
        """Pick the nearest non-expired ES future as the front-month."""
        today = datetime.now(UTC).date()
        candidates = [
            (inst.expiry, instrument_id)
            for instrument_id, inst in self._state.es_instruments.items()
            if inst.expiry is not None and inst.expiry >= today
        ]
        if not candidates:
            self._state.es_front_id = None
            return
        candidates.sort()
        new_front = candidates[0][1]
        if new_front != self._state.es_front_id:
            self._state.es_front_id = new_front
            self._state.es_dirty = True

    # ---- periodic OI refresh ---------------------------------------------

    async def _oi_refresh_loop(self) -> None:
        assert self._stop_event is not None
        # First refresh runs after a short delay so definitions can populate.
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=20)
            return
        except TimeoutError:
            pass
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(self._refresh_open_interest)
            except Exception:
                logger.exception("OI refresh failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=OI_REFRESH_INTERVAL_S)
                return
            except TimeoutError:
                continue

    def _refresh_open_interest(self) -> None:
        with self._lock:
            raw_symbols = sorted(
                {
                    inst.raw_symbol
                    for inst in self._state.instruments.values()
                    if inst.raw_symbol
                }
            )
            instruments_by_symbol = {
                inst.raw_symbol: instrument_id
                for instrument_id, inst in self._state.instruments.items()
            }
        if not raw_symbols:
            return
        try:
            client = _historical(self.opra_key)
        except SnapshotError:
            return
        from app.databento_client import historical_window
        start, end = historical_window(lookback=timedelta(days=3))
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
        except Exception as exc:
            logger.warning("statistics fetch failed: %s", exc)
            return
        if df is None or df.empty:
            return
        if "stat_type" in df.columns:
            df = df[df["stat_type"] == OPEN_INTEREST_STAT_TYPE]
        if df.empty:
            return
        if "ts_event" in df.columns:
            df = df.sort_values("ts_event").groupby("instrument_id").tail(1)
        oi_field = "quantity" if "quantity" in df.columns else "price"
        new_oi: dict[int, float] = {}
        for row in df.itertuples():
            instrument_id = int(row.instrument_id)
            value = float(getattr(row, oi_field, 0.0) or 0.0)
            if math.isnan(value):
                continue
            new_oi[instrument_id] = value
        if not new_oi:
            return
        dirty: set[date] = set()
        with self._lock:
            for instrument_id, value in new_oi.items():
                self._state.oi[instrument_id] = value
                inst = self._state.instruments.get(instrument_id)
                if inst is not None:
                    dirty.add(inst.expiry)
            # Best-effort fall-back: link by symbol when ID mapping mismatches.
            if not dirty and "symbol" in df.columns:
                for row in df.itertuples():
                    sym = str(getattr(row, "symbol", "") or "")
                    fallback_id = instruments_by_symbol.get(sym)
                    if fallback_id is None:
                        continue
                    value = float(getattr(row, oi_field, 0.0) or 0.0)
                    if math.isnan(value):
                        continue
                    self._state.oi[fallback_id] = value
                    inst = self._state.instruments.get(fallback_id)
                    if inst is not None:
                        dirty.add(inst.expiry)
            self._state.dirty_expiries |= dirty
            self._state.last_oi_refresh = datetime.now(UTC)
        logger.info("Refreshed OI for %d instruments (%d dirty expiries)", len(new_oi), len(dirty))

    # ---- broadcast loop ----------------------------------------------------

    async def _broadcast_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=BROADCAST_INTERVAL_S)
                return
            except TimeoutError:
                pass
            await self._broadcast_tick()

    async def _broadcast_tick(self) -> None:
        # Snapshot the dirty set & ES flag, then publish only to expiries that have subscribers.
        with self._lock:
            dirty = set(self._state.dirty_expiries)
            es_dirty = self._state.es_dirty
            self._state.dirty_expiries.clear()
            self._state.es_dirty = False

        if es_dirty:
            dirty |= set(self._subscribers.keys())

        if not dirty:
            return

        for expiry in dirty:
            subs = self._subscribers.get(expiry)
            if not subs:
                continue
            try:
                snap = self.build_snapshot("SPX", expiry)
            except Exception:
                logger.exception("failed to build snapshot for %s", expiry)
                continue
            if snap is None:
                continue
            for queue in list(subs):
                try:
                    queue.put_nowait(snap)
                except asyncio.QueueFull:
                    # Drop oldest, push newest — clients should always see latest.
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(snap)
                    except asyncio.QueueFull:
                        pass


_engine_singleton: LiveEngine | None = None


def set_engine(engine: LiveEngine | None) -> None:
    global _engine_singleton
    _engine_singleton = engine


def get_engine() -> LiveEngine | None:
    return _engine_singleton
