"""Causal SPY stock time-and-sales confirmation, research-only.

Requires individually timestamped *real underlying SPY trades*. The existing
1-minute OHLCV/OPRA CBBO-1m research archive does not contain this tape.
No synthetic tape, guessed aggressor side from candle direction, or fallback.
"""
from __future__ import annotations

import csv
import gzip
import math
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Literal

from .data import HistoricalFrame
from .quarter_risk import QuarterRiskConfig, QuarterRiskStrategy
from .backtest import BacktestContext, BacktestConfig, BacktestSignal, BacktestResult, run_backtest_stream
from .event_alpha import EventAlphaConfig

TapeSide = Literal["buy", "sell", "unknown"]
TapeDirection = Literal["call", "put"]


@dataclass(frozen=True)
class TapePrint:
    # Event-time and observed-time distinct: only observed-time is tradable.
    event_time: datetime
    observed_at: datetime
    price: float
    shares: int
    aggressor: TapeSide
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self) -> None:
        if self.event_time.tzinfo is None or self.observed_at.tzinfo is None:
            raise ValueError("tape timestamps must be timezone-aware")
        if self.observed_at < self.event_time:
            raise ValueError("trade cannot be observed before it occurred")
        if not math.isfinite(self.price) or self.price <= 0 or self.shares < 1:
            raise ValueError("invalid price or size")
        if self.aggressor not in ("buy", "sell", "unknown"):
            raise ValueError("invalid aggressor")
        if self.bid is not None and (not math.isfinite(self.bid) or self.bid <= 0):
            raise ValueError("invalid bid")
        if self.ask is not None and (not math.isfinite(self.ask) or self.ask <= 0):
            raise ValueError("invalid ask")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("crossed quotes cannot classify trade side")


@dataclass(frozen=True)
class TapeConfig:
    window_seconds: int = 30
    stale_after_seconds: int = 5
    min_prints: int = 12
    min_shares: int = 1500
    # Volume delta of aggressive buys vs sells, normalized by ALL prints.
    min_signed_imbalance: float = 0.32
    min_confirming_move_bps: float = 1.0
    max_chase_move_bps: float = 12.0
    # Trading venue stock NBBO sanity. Full book depth not yet available.
    max_stock_spread_bps: float = 3.0
    max_observation_delay_seconds: float = 2.0
    structure_lookback_minutes: int = 8
    min_structure_trend_bps: float = 2.0

    def __post_init__(self) -> None:
        if min(self.window_seconds,self.stale_after_seconds,self.min_prints,self.min_shares) < 1:
            raise ValueError("positive tape window/age/print/volume required")
        if not 0 < self.min_signed_imbalance < 1:
            raise ValueError("min_signed_imbalance must be in (0,1)")
        if not 0 < self.min_confirming_move_bps < self.max_chase_move_bps:
            raise ValueError("invalid confirming-move interval")
        if not 0 < self.max_stock_spread_bps < 100:
            raise ValueError("invalid spread guard")
        if self.max_observation_delay_seconds <= 0:
            raise ValueError("invalid latency guard")
        if self.structure_lookback_minutes < 3 or self.min_structure_trend_bps <= 0:
            raise ValueError("invalid price-structure confirmation")


@dataclass(frozen=True)
class TapeSnapshot:
    as_of: datetime
    direction: TapeDirection | None
    signed_imbalance: float
    total_shares: int
    print_count: int
    price_move_bps: float
    stale: bool
    reason: str


class TapeReader:
    """Windowed print-imbalance + confirming price response, fail closed."""

    def __init__(self, config: TapeConfig = TapeConfig()) -> None:
        self.config = config
        self._prints: deque[TapePrint] = deque()
        self._last_observed_at: datetime | None = None

    def ingest(self, trade: TapePrint) -> None:
        if self._last_observed_at is not None and trade.observed_at < self._last_observed_at:
            raise ValueError("tape observations must be chronological")
        self._last_observed_at = trade.observed_at
        self._prints.append(trade)

    def snapshot(self, as_of: datetime) -> TapeSnapshot:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        # Drop anything older than the window, including yesterday's tape.
        cutoff = as_of - timedelta(seconds=self.config.window_seconds)
        while self._prints and self._prints[0].observed_at < cutoff:
            self._prints.popleft()
        current = tuple(t for t in self._prints if t.observed_at <= as_of)
        def reject(reason: str, *, delta:float=0, total:int=0, move:float=0):
            return TapeSnapshot(as_of,None,delta,total,len(current),move,reason in ("stale","no_tape"),reason)
        if not current:
            return reject("no_tape")
        newest = current[-1]
        if (as_of-newest.observed_at).total_seconds() > self.config.stale_after_seconds:
            return reject("stale")
        if (newest.observed_at-newest.event_time).total_seconds() > self.config.max_observation_delay_seconds:
            return reject("late_feed")
        if len(current) < self.config.min_prints:
            return reject("too_few_prints")
        total = sum(item.shares for item in current)
        if total < self.config.min_shares:
            return reject("too_little_volume",total=total)
        if newest.bid is None or newest.ask is None:
            return reject("missing_quotes",total=total)
        if (newest.ask-newest.bid)/newest.price*10000 > self.config.max_stock_spread_bps:
            return reject("wide_spread",total=total)
        signed = sum(item.shares * (1 if item.aggressor=="buy" else (-1 if item.aggressor=="sell" else 0)) for item in current)
        delta = signed / total
        price_move = (current[-1].price/current[0].price - 1)*10000
        if abs(delta) < self.config.min_signed_imbalance:
            return reject("mixed_flow",delta=delta,total=total,move=price_move)
        # Failure of price to respond to large aggressive flow = possible
        # absorption; cannot reliably call an iceberg or sweep from prints.
        if delta * price_move <= 0 or abs(price_move) < self.config.min_confirming_move_bps:
            return reject("possible_absorption",delta=delta,total=total,move=price_move)
        if abs(price_move) > self.config.max_chase_move_bps:
            return reject("chasing",delta=delta,total=total,move=price_move)
        return TapeSnapshot(as_of,"call" if delta>0 else "put",delta,total,len(current),price_move,False,"confirmed")


def _timestamp(text: str) -> datetime:
    value=datetime.fromisoformat(text.replace("Z","+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return value.astimezone(timezone.utc)


def _derive_side(text: str, price: float, bid:float|None, ask:float|None) -> TapeSide:
    # Databento MBP side B=buy aggressor, A=sell aggressor.
    value=text.strip().upper()
    if value in ("B","BUY"):
        return "buy"
    if value in ("A","SELL"):
        return "sell"
    if value not in ("N","UNKNOWN",""):
        raise ValueError("unrecognized tape side")
    # Only use PRE-TRADE NBBO, never a future/interval-aggregated quote.
    if bid is not None and ask is not None and ask > bid:
        if price >= ask - 1e-6:
            return "buy"
        if price <= bid + 1e-6:
            return "sell"
    return "unknown"


def iter_spy_stock_tape(path: str|Path, *, start_date:date|None=None, end_date:date|None=None) -> Iterator[TapePrint]:
    """Read real tick sidecars; absolutely no 1-minute candle fallback.

    One CSV row per completed SPY underlying trade. Fields:
    event_time,observed_at,price,shares,aggressor,bid,ask,dataset,schema,symbol
    bid/ask must be pre-trade NBBO, if provided. OPRA option prints are
    deliberately excluded: their signed aggressor side is not disseminated.
    """
    root=Path(path)
    files=sorted(set(root.glob("spy_tape_*.csv.gz"))|set(root.glob("spy_tape_*.csv")))
    last_at=None
    for file in files:
        session=date.fromisoformat(file.name[len("spy_tape_"):len("spy_tape_")+10])
        if start_date is not None and session < start_date:
            continue
        if end_date is not None and session > end_date:
            continue
        with (gzip.open(file,"rt",newline="") if file.suffix==".gz" else file.open("r",newline="")) as handle:
            for row in csv.DictReader(handle):
                if row.get("symbol","").strip().upper()!="SPY":
                    raise ValueError("tape sidecar may only contain underlying SPY trades")
                dataset=row.get("dataset","").strip().upper()
                if not dataset.startswith(("EQUS.","XNAS.","XNYS.","ARCX.","BATS.","IEXG.")):
                    raise ValueError("tape must be from an actual equities venue/data feed")
                if row.get("schema","").strip().lower() not in ("mbp-1","tbbo","tcbbo","trades"):
                    raise ValueError("tape requires individual trades; 1m bars are not valid")
                at=_timestamp(row["observed_at"])
                if last_at is not None and at < last_at:
                    raise ValueError("unsorted tape sidecars")
                last_at=at
                price=float(row["price"])
                bid=float(row["bid"]) if row.get("bid") else None
                ask=float(row["ask"]) if row.get("ask") else None
                yield TapePrint(
                    event_time=_timestamp(row["event_time"]),
                    observed_at=at,price=price,shares=int(row["shares"]),
                    aggressor=_derive_side(row.get("aggressor",""),price,bid,ask),
                    bid=bid,ask=ask,
                )


class TapeAwareQuarterStrategy(QuarterRiskStrategy):
    """Gate bullish momentum with tape and add bearish/bullish structure breakouts.

    Both types require nonstale true SPY prints to select CALL versus PUT.
    All supplemental entries pass through unchanged quarter-risk controls.
    """

    def __init__(
        self,*,tape_events:Iterable[TapePrint],starting_cash:float,
        tape_config:TapeConfig=TapeConfig(),
        quarter_config:QuarterRiskConfig=QuarterRiskConfig(guard_enabled=True),
        event_config:EventAlphaConfig=EventAlphaConfig(),
        trade_start_date:date|None=None,
    ) -> None:
        super().__init__(starting_cash=starting_cash,quarter_config=quarter_config,
                         event_config=event_config,trade_start_date=trade_start_date)
        self._events=iter(tape_events)
        self._next_event=next(self._events,None)
        self.tape=TapeReader(tape_config)
        self._spot_history: deque[float] = deque(maxlen=tape_config.structure_lookback_minutes)
        self._spot_history_session: date | None = None
        self.structure_entries = 0
        self.last_tape_snapshot:TapeSnapshot|None=None
        self.eligible_signals=0
        self.rejected_signals=0
        self.accepted_signals=0

    def decide(self,frame:HistoricalFrame,context:BacktestContext)->BacktestSignal:
        if self._spot_history_session != frame.timestamp.date():
            self._spot_history_session = frame.timestamp.date()
            self._spot_history.clear()
        while self._next_event is not None and self._next_event.observed_at <= frame.timestamp:
            self.tape.ingest(self._next_event)
            self._next_event=next(self._events,None)
        self.last_tape_snapshot=self.tape.snapshot(frame.timestamp)
        self._signal_direction=self.last_tape_snapshot.direction
        signal=super().decide(frame,context)
        # Add this frame only AFTER all decisions, preventing same-frame leakage
        # into the eight-minute price-structure reference window.
        self._spot_history.append(frame.market.spot)
        # The parent advances its full causal market-momentum stream first.
        if signal.action=="open":
            # Genuine quarter-risk budget and option pricing remain enforced.
            self.accepted_signals+=1
        return signal

    def _supplemental_entry_signal(
        self, frame: HistoricalFrame, context: BacktestContext, signal: BacktestSignal
    ) -> BacktestSignal:
        # Parent Event Alpha detects bullish acceleration. Also allow tape
        # to trigger a *fresh bearish breakdown* or a fresh bullish breakout
        # when its independently observed order flow confirms direction.
        if signal.action != "hold" or context.position is not None:
            return signal
        if self._signal_direction is None:
            return signal
        if self.trade_start_date is not None and frame.timestamp.date() < self.trade_start_date:
            return signal
        if frame.market.minutes_to_close < self.config.minimum_minutes_to_close:
            return signal
        previous = tuple(self._spot_history)
        if len(previous) < self.tape.config.structure_lookback_minutes:
            return signal
        spot = frame.market.spot
        move_bps = (spot/previous[0]-1.0)*10000
        if self._signal_direction == "call":
            confirmed = spot > max(previous) and move_bps >= self.tape.config.min_structure_trend_bps
        else:
            confirmed = spot < min(previous) and move_bps <= -self.tape.config.min_structure_trend_bps
        if not confirmed:
            return signal
        budget = self._entry_budget(context)
        if budget <= 0:
            return signal
        selected = self._select_entry_option(frame,budget)
        if selected is None:
            return signal
        proposed = self._entry_signal(selected,budget)
        if proposed.action != "open":
            return signal
        self.structure_entries += 1
        return BacktestSignal(
            action="open",option_symbol=proposed.option_symbol,
            quantity=proposed.quantity,
            reason=f"TAPE_CONFIRMED_STRUCTURE_{self._signal_direction.upper()}_QUARTER_CAP",
            max_total_cost=proposed.max_total_cost,
        )

    def _select_entry_option(self,frame:HistoricalFrame,budget:float):
        # This hook is called only after the historical price-based event fires;
        # count the candidate even if tape then vetoes entry.
        self.eligible_signals += 1
        if self._signal_direction is None:
            self.rejected_signals += 1
            return None
        option = super()._select_entry_option(frame,budget)
        if option is None:
            self.rejected_signals += 1
        return option


@dataclass(frozen=True)
class TapeResearchResult:
    results: tuple[BacktestResult,...]
    tape_files: int
    eligible: int
    rejected: int
    accepted: int


def run_tape_research_directory(
    root:str|Path,*,starting_cash:float,
    trade_start_date:date|None=None,
    start_date:date|None=None,end_date:date|None=None,
    adverse_ticks:tuple[int,...]=(1,),
    config:TapeConfig=TapeConfig(),
)->TapeResearchResult:
    path=Path(root)
    all_sidecars=list(path.glob("spy_tape_*.csv*"))
    if not all_sidecars:
        raise RuntimeError("NO_TAPE_DATA: archived EQUS.MINI 1m bars and OPRA CBBO 1m cannot backtest tape; do not invent prints")
    from .burst_research import iter_research_directory
    results=[]
    last=None
    for ticks in adverse_ticks:
        strategy=TapeAwareQuarterStrategy(
            tape_events=iter_spy_stock_tape(path,start_date=start_date,end_date=end_date),
            tape_config=config,starting_cash=starting_cash,trade_start_date=trade_start_date,
        )
        result=run_backtest_stream(
            iter_research_directory(path,start_date=start_date,end_date=end_date),strategy,
            config=BacktestConfig(starting_cash=starting_cash,fee_per_contract=0,
                                  slippage_spread_fraction=0,adverse_ticks_per_side=ticks,
                                  option_tick_size=.01,maximum_contracts=10),
        )
        results.append(result)
        last=strategy
    assert last is not None
    return TapeResearchResult(tuple(results),len(all_sidecars),
                              last.eligible_signals,last.rejected_signals,last.accepted_signals)
