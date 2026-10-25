"""Autonomous V5 CLOSE-ONLY unrestricted *PAPER* research trader.

Never transmits Webull orders. Existing V4 production LIVE remains original.
Fully settled cash may be spent in PAPER only where timestamped market
data and displayed option NBBO depth are independently verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .paper_autotrader import PaperAutoTrader, EASTERN
from .v4_paper_trader import V4QualityPaperTrader
from .v5_close_only_operational import (
    PROFILE, paper_candidate, paper_exit_reason,
)
from .v4_operational import FEE, SLIPPAGE_PER_SHARE
from .v5_drawdown_sizing import observe_flat_session


def manage_v5_position(trader,position,current,mode,eastern,quotes,released):
    """Compatible with V4 paper worker after user reselects old V4 mode."""
    quote=next((q for q in quotes
        if q.option_symbol==position["symbol"] and q.source=="webull_production"
        and q.timestamp_quality=="option_quote_timestamp" and
        0<=(current-q.timestamp).total_seconds()<=3 and q.bid>0),None)
    if quote is None:
        return {"state":"POSITION_WAITING_QUOTE","mode":mode,
            "reason":"V5 existing paper position requires genuine fresh bid",
            "position":position,"settled_released":released,"last_action":None}
    credit=max(0.,(quote.bid-SLIPPAGE_PER_SHARE)*100-FEE)
    peak=trader.account_store.record_exit_peak(position["symbol"],credit)
    position["peak_exit_value"]=peak
    reason=paper_exit_reason(position,quote,trader.bar_cache.bars,current)
    if not trader._market_session(eastern) or trader._minutes_to_close(eastern)<=15:
        reason="v5_before_expiry_liquidation"
    if mode not in ("PAPER","SHADOW"):
        reason="v5_paper_mode_stopped"
    if reason is None:
        return {"state":"POSITION_OPEN","mode":mode,"position":position,
            "reason":"observed V5 close-only premium-trailing management",
            "last_action":None,"settled_released":released}
    closed=trader.account_store.close_long(
        symbol=position["symbol"],quantity=int(position["quantity"]),
        fill_price=credit,closed_at=current,trade_date=eastern.date(),
        reason=reason)
    return {"state":"POSITION_CLOSED","mode":mode,"closed_trade":closed,
        "reason":reason,"last_action":"SELL","settled_released":released}


class V5CloseOnlyPaperTrader(V4QualityPaperTrader):
    @staticmethod
    def _finish(now,values):
        return PaperAutoTrader._finish(now,{**values,
            "strategy":PROFILE,"sizing_mode":"full_cash_with_eod_drawdown_throttle"})

    def tick(self, now:datetime|None=None):
        current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        eastern=current.astimezone(EASTERN)
        mode=self.mode_getter().strip().upper()
        released=self.account_store.settle_due(eastern.date())
        positions=self.account_store.positions()
        position=positions[0] if positions else None
        # Original V4 positions stay on their original manager, unaffected by
        # setting the paper challenger profile while a position is open.
        if position is not None and position.get("strategy")!=PROFILE:
            return super().tick(current)
        if mode not in ("PAPER","SHADOW") and position is None:
            return self._finish(current,{"state":"IDLE","mode":mode,
                "reason":"V5 paper strategy not armed",
                "last_action":None,"settled_released":released})
        if not self._market_session(eastern) and position is None:
            return self._finish(current,{"state":"MARKET_CLOSED","mode":mode,
                "last_action":None,"settled_released":released})
        self.bar_cache.refresh(self.bar_provider,current)
        quotes=self._quotes(current,position)
        if position:
            outcome=manage_v5_position(
                self,position,current,mode,eastern,quotes,released)
            return self._finish(current,outcome)
        if self.bar_cache.error is not None:
            return self._finish(current,{"state":"WAITING_REAL_SPY_M1","mode":mode,
                "reason":self.bar_cache.error,
                "last_action":None,"settled_released":released})
        if not quotes:
            return self._finish(current,{"state":"WAITING_FRESH_OPTIONS","mode":mode,
                "reason":self.last_data_error or "real-time option bid/ask unavailable",
                "last_action":None,"settled_released":released})
        acct=self.account_store.snapshot()
        equity=acct.settled_cash+acct.unsettled_cash
        # Only observe a FLAT account. Reserve premium while a position is
        # open; it is not an equity drawdown until its exit is confirmed.
        risk=observe_flat_session(self.account_store.path,eastern.date(),equity)
        proposed,decision=paper_candidate(
            current,self.bar_cache.bars,quotes,equity=equity,
            settled=acct.settled_cash,peak=risk["prior_peak_equity"],
            risk_fraction=risk["fraction"],
            risk_opening_equity=risk["opening_equity"])
        if proposed is None:
            return self._finish(current,{**decision,"mode":mode,
                "last_action":None,"settled_released":released})
        if self._in_cooldown(current):
            return self._finish(current,{"state":"COOLDOWN","mode":mode,
                "last_signal":proposed,"last_action":None,
                "reason":"paper cooldown; no re-entry",
                "settled_released":released})
        if mode=="SHADOW":
            return self._finish(current,{"state":"SHADOW_SIGNAL",
                "mode":mode,"last_signal":proposed,"last_action":"WOULD_BUY",
                "reason":proposed["reason"],"settled_released":released})
        debit=float(proposed["debit_per_contract"])
        quantity=int(proposed["quantity"])
        # Cash and displayed depth are revalidated in paper policy; NEVER
        # use unsettled sale proceeds or leverage.
        if quantity<1 or quantity*debit>acct.settled_cash+1e-8:
            return self._finish(current,{"state":"RISK_BLOCKED","mode":mode,
                "reason":"settled cash cannot cover whole option premium",
                "last_signal":proposed,"last_action":None,
                "settled_released":released})
        opened=self.account_store.open_long(
            symbol=proposed["symbol"],quantity=quantity,
            fill_price=debit,opened_at=current,
            entry_spot=proposed["underlying_spot"],strategy=PROFILE,
            stop_price=debit*.65,target_price=1e12,
            max_hold_seconds=18*60,reason=proposed["reason"])
        return self._finish(current,{"state":"POSITION_OPENED",
            "mode":mode,"position":opened,"last_signal":proposed,
            "last_action":"BUY","reason":proposed["reason"],
            "risk":{"full_premium_loss_usd":round(quantity*debit,2),
                    "full_cash_drawdown_throttle":True,
                    "risk_fraction":risk["fraction"],
                    "risk_budget_usd":proposed["risk_budget_usd"],
                    "live_broker_orders":False},
            "settled_released":released})
