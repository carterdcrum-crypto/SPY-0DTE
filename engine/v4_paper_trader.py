"""SPY 0DTE V4 quality-3x autonomous PAPER execution (never broker orders).

Shares the SAME as-of V4 signal/quality/quantity rules as live adapter.
Reads entitled current production market quotes and completed stock M1
Webull bars if available, otherwise valid collector records. Delayed
sandbox market observations may never be presented as realtime fills.
"""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .paper_autotrader import PaperAutoTrader, EASTERN, _parse_datetime
from .v4_operational import (
    IDENTIFIER, FEE, MAX_SOURCE_DELAY_SECONDS, SLIPPAGE_PER_SHARE,
    WebullMinuteBarCache, candidate, exit_reason,
)


def source_snapshots(db:Path,now:datetime):
    """Read verified market observations, including timestamp provenance.

    Collector migrations add the underlying market time. Unknown columns,
    quote source or missing Greeks are *not* silently filled with defaults.
    """
    if not db.exists():
        return ()
    earliest=(now.astimezone(timezone.utc).timestamp()-10)
    with sqlite3.connect(str(db),timeout=2) as conn:
        conn.row_factory=sqlite3.Row
        try:
            rows=conn.execute("""
                SELECT timestamp,received_at,underlying_timestamp,
                       timestamp_quality,source,feed_delay_seconds,
                       option_symbol,expiration,right,strike,bid,ask,
                       bid_size,ask_size,underlying_price,
                       implied_volatility,delta
                FROM option_snapshots
                WHERE received_at >= ?
                ORDER BY received_at DESC LIMIT 120
            """,(datetime.fromtimestamp(earliest,timezone.utc).isoformat(),)).fetchall()
        except sqlite3.Error:
            return ()
    out={}
    for row in rows:
        try:
            market=_parse_datetime(str(row["timestamp"]))
            received=_parse_datetime(str(row["received_at"]))
            underlying=_parse_datetime(str(row["underlying_timestamp"]))
            if not 0<=(now-market).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
                continue
            if not 0<=(now-received).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
                continue
            if not 0<=(now-underlying).total_seconds()<=MAX_SOURCE_DELAY_SECONDS:
                continue
            symbol=str(row["option_symbol"])
            if symbol in out:
                continue
            iv=row["implied_volatility"]
            delta=row["delta"]
            if iv is None or delta is None:
                continue
            out[symbol]=SimpleNamespace(
                timestamp=market,received_at=received,
                underlying_timestamp=underlying,
                timestamp_quality=str(row["timestamp_quality"]),
                source=str(row["source"]),option_symbol=symbol,
                expiration=datetime.fromisoformat(str(row["expiration"])).date(),
                right=str(row["right"]).lower(),strike=float(row["strike"]),
                bid=float(row["bid"]),ask=float(row["ask"]),
                bid_size=int(row["bid_size"]),ask_size=int(row["ask_size"]),
                underlying_price=float(row["underlying_price"]),
                greeks=SimpleNamespace(delta=float(delta),implied_volatility=float(iv)),
            )
        except (ValueError,TypeError,KeyError,OverflowError):
            continue
    return tuple(out.values())


class PaperMinuteBarCache(WebullMinuteBarCache):
    """Paper-only 15s M1 pacing avoids doubling live Webull bar API load."""

    def refresh(self, provider, now):
        if self.last_attempt is not None:
            seconds=(now-self.last_attempt).total_seconds()
            if 0 <= seconds < 15:
                return self.bars
        return super().refresh(provider,now)


class V4QualityPaperTrader(PaperAutoTrader):
    def __init__(self,*,mode_getter,market_reader,account_store,settings,
                 production_provider=None,bar_provider=None):
        super().__init__(mode_getter=mode_getter,market_reader=market_reader,
                         account_store=account_store,settings=settings)
        self.production_provider=production_provider
        self.bar_provider=bar_provider or production_provider
        self.bar_cache=PaperMinuteBarCache()
        self.last_data_error=None

    @staticmethod
    def _finish(now,values):
        return PaperAutoTrader._finish(now,{**values,"strategy":IDENTIFIER,
            "sizing_mode":"adaptive_compound_paper"})

    def _quotes(self,now,position=None):
        if self.production_provider is not None:
            if position:
                self.production_provider.pinned_symbols={position["symbol"]}
            try:
                result=self.production_provider.collect_once(now.astimezone(EASTERN).date())
                self.last_data_error=None
                return tuple(result)
            except Exception as exc:
                self.last_data_error=f"{type(exc).__name__}: production options unavailable"
                return ()
        return source_snapshots(Path(self.market_reader.path),now)

    def tick(self, now:datetime|None=None):
        current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eastern=current.astimezone(EASTERN)
        mode=self.mode_getter().strip().upper()
        released=self.account_store.settle_due(eastern.date())
        positions=self.account_store.positions()
        # Always settle/monitor owned positions even when mode changes or
        # V4 entries lack feed entitlements.
        position=positions[0] if positions else None
        if mode not in ("PAPER","SHADOW") and not position:
            return self._finish(current,{"state":"IDLE","mode":mode,
                "reason":"paper trader not authorized outside PAPER/SHADOW mode",
                "last_signal":None,"last_action":None,"settled_released":released})
        if not self._market_session(eastern) and not position:
            return self._finish(current,{"state":"MARKET_CLOSED","mode":mode,
                "reason":"waiting for SPY regular session",
                "last_signal":None,"last_action":None,"settled_released":released})
        # Do not substitute sandbox/shifted bars for authentic production data.
        self.bar_cache.refresh(self.bar_provider,current)
        quotes=self._quotes(current,position)
        if position:
            if position.get("strategy")=="v5_close_only_unrestricted_research":
                # Keep managing existing V5 paper holdings correctly even
                # after selecting original V4 for NEW paper entries.
                from .v5_close_only_paper import manage_v5_position
                result=manage_v5_position(self,position,current,mode,eastern,quotes,released)
                return self._finish(current,{**result,"strategy":"v5_close_only_unrestricted_research"})
            if position.get("strategy")!=IDENTIFIER:
                result=super()._manage_position(position,current,mode)
                result["settled_released"]=released
                return self._finish(current,result)
            q=next((row for row in quotes if row.option_symbol==position["symbol"]
                and row.source=="webull_production"
                and row.timestamp_quality=="option_quote_timestamp"
                and 0<=(current-row.timestamp).total_seconds()<=3),None)
            if q is None or q.bid<=0:
                return self._finish(current,{"state":"POSITION_WAITING_QUOTE",
                    "mode":mode,"reason":self.last_data_error or "fresh production option bid required",
                    "last_signal":None,"last_action":None,"settled_released":released,
                    "position":position})
            credit=max(0.,(q.bid-SLIPPAGE_PER_SHARE)*100-FEE)
            peak=self.account_store.record_exit_peak(position["symbol"],credit)
            position["peak_exit_value"]=peak
            why=exit_reason(position,q,self.bar_cache.bars,current)
            if not self._market_session(eastern) or self._minutes_to_close(eastern)<=15:
                why="v4_session_flatten"
            if mode not in ("PAPER","SHADOW"):
                why="v4_paper_mode_off"
            if why is None:
                return self._finish(current,{"state":"POSITION_OPEN","mode":mode,
                    "reason":"V4 3x conditional exit monitoring","position":position,
                    "last_signal":None,"last_action":None,"settled_released":released})
            closed=self.account_store.close_long(
                symbol=position["symbol"],quantity=int(position["quantity"]),
                fill_price=credit,closed_at=current,
                trade_date=eastern.date(),reason=why)
            return self._finish(current,{"state":"POSITION_CLOSED","mode":mode,
                "reason":why,"closed_trade":closed,"last_signal":None,
                "last_action":"SELL","settled_released":released})
        if self.bar_cache.error is not None:
            return self._finish(current,{"state":"WAITING_V4_BARS","mode":mode,
                "reason":self.bar_cache.error,"last_signal":None,
                "last_action":None,"settled_released":released})
        acct=self.account_store.snapshot()
        if not quotes:
            return self._finish(current,{"state":"WAITING_V4_QUOTES","mode":mode,
                "reason":self.last_data_error or "no fresh production options in PAPER mode",
                "last_signal":None,"last_action":None,"settled_released":released})
        equity=acct.settled_cash+acct.unsettled_cash
        peak=max(acct.starting_cash,acct.starting_cash+acct.realized_pnl,equity)
        proposal,decision=candidate(current,self.bar_cache.bars,quotes,
            equity=equity,settled=acct.settled_cash,peak=peak,
            max_contracts=self.settings.maximum_contracts,
            exposure=min(.35,self.settings.maximum_position_fraction),
            sizing_mode="adaptive",starting_cash=acct.starting_cash)
        if proposal is None:
            return self._finish(current,{**decision,"mode":mode,
                "last_signal":None,"last_action":None,
                "settled_released":released})
        if self._in_cooldown(current):
            return self._finish(current,{"state":"COOLDOWN","mode":mode,
                "reason":"V4 paper cooldown", "last_signal":proposal,
                "last_action":None,"settled_released":released})
        if mode=="SHADOW":
            return self._finish(current,{"state":"SHADOW_SIGNAL","mode":mode,
                "reason":proposal["reason"],"last_signal":proposal,
                "last_action":"WOULD_BUY","settled_released":released})
        debit=proposal["debit_per_contract"]
        qty=proposal["quantity"]
        if debit*qty>acct.settled_cash+1e-8:
            return self._finish(current,{"state":"RISK_BLOCKED","mode":mode,
                "reason":"insufficient settled cash at order time",
                "last_signal":proposal,"last_action":None,"settled_released":released})
        opened=self.account_store.open_long(
            symbol=proposal["symbol"],quantity=qty,
            fill_price=debit,opened_at=current,
            entry_spot=proposal["underlying_spot"],strategy=IDENTIFIER,
            stop_price=.65*debit,target_price=1e12,max_hold_seconds=600,
            reason=proposal["reason"])
        return self._finish(current,{"state":"POSITION_OPENED","mode":mode,
            "reason":proposal["reason"],"last_signal":proposal,
            "last_action":"BUY","position":opened,"risk":{
                "profile":IDENTIFIER,"quantity":qty,"quality_score":proposal["quality_score"],
                "contract_full_premium_loss_usd":debit*qty},
            "settled_released":released})
