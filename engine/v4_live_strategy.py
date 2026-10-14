"""Pure-quant original V4 3x Webull LIVE decision policy.

No AI engines, external LLM calls, hybrid forecasts, ensemble classes,
provider tokens, or daily manual limit arming. Does NOT transmit orders.
The existing live executor alone controls standing authorization, broker
reconciliation, cash/daily stops, and order submission.
"""
from __future__ import annotations

from datetime import datetime, time
from types import SimpleNamespace

from .live_risk import EASTERN
from .session_calendar import session_close
from .v4_operational import (
    IDENTIFIER, WebullMinuteBarCache, candidate, exit_reason,
)


class V4QualityLiveStrategy:
    def __init__(self, reader, audit_path: str):
        self.market_reader=reader
        self.bar_cache=WebullMinuteBarCache()
        self.settings=SimpleNamespace(fee_per_contract=.65)
        self.audit_path=audit_path

    def refresh_bars(self, provider, now: datetime):
        return self.bar_cache.refresh(provider,now)

    def _minutes_to_close(self, eastern: datetime) -> float:
        if eastern.tzinfo is None:
            raise ValueError("aware timestamp required")
        day=eastern.astimezone(EASTERN).date()
        close=session_close(day)
        if close is None:
            return -1.
        end=datetime.combine(day,close,EASTERN)
        return (end-eastern.astimezone(EASTERN)).total_seconds()/60.

    def telemetry(self):
        return {
            "strategy":IDENTIFIER,
            "risk_profile":"v4_standing_owner_safety_and_35pct_cap",
            "exit_profile":"original_v4_conditional_10min_whole_position",
            "source":"webull_production_real_m1_and_timestamped_options",
            "data_blocker":self.bar_cache.error,
            "signal_model":"QUANT_ONLY_NO_LLM",
            "research_status":"HISTORICAL_CBBO_PROXY_NOT_LIVE_VALIDATED",
        }

    def entry(self, now, account, daily, limits):
        if limits is None or account is None or daily is None:
            return None,{"state":"RISK_BLOCKED",
                "reason":"missing verified live account or standing safety envelope"}
        if daily.get("halt_reason"):
            return None,{"state":"DAILY_HALT","reason":daily["halt_reason"]}
        maximum=min(3,int(limits.max_contracts))
        exposure=min(.35,float(limits.max_account_exposure_pct))
        return candidate(
            now,self.bar_cache.bars,self.market_reader.quotes.values(),
            equity=float(account["equity"]),
            settled=float(account["settled_cash"]),
            peak=float(daily["high_watermark"]),
            max_contracts=maximum,exposure=exposure,
        )

    def exit(self, position, now):
        # Existing positions opened before V4 deployment still require
        # automatic exits. Use conservative QUANT bid/trail/safety rules,
        # never revive an old AI model to manage the user's money.
        q=self.market_reader.quotes.get(position["symbol"])
        if q is None:
            return None  # executor requires fresh bid; existing safety checks.
        return exit_reason(position,q,self.bar_cache.bars,now,
                           fee=self.settings.fee_per_contract)
