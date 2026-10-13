"""Owner-limit-constrained Webull production adapter for V4 Entry Quality 3x.

The broker executor's existing journal/reconciliation/cash and daily-loss
checks remain authoritative; no autonomous order is placed by this module.
The signal is NOT the historical replay's OPRA NBBO feed and is labelled as
a production-source adaptation. Incomplete market data blocks entries.
"""
from __future__ import annotations

from datetime import datetime
from .live_strategy import LiveStrategy
from .v4_operational import (
    IDENTIFIER, WebullMinuteBarCache, candidate, exit_reason,
)


class V4QualityLiveStrategy(LiveStrategy):
    def __init__(self,reader,audit_path:str):
        super().__init__(reader,audit_path)
        self.bar_cache=WebullMinuteBarCache()

    def refresh_bars(self,provider,now:datetime):
        return self.bar_cache.refresh(provider,now)

    def telemetry(self):
        return {
            "strategy":IDENTIFIER,
            "risk_profile":"v4_owner_limits_35pct_cash_plus_depth_hard_v1",
            "exit_profile":"v4_full_exit_30pct_trailing_35pct_stop_conditional_10min",
            "source":"webull_production_real_m1_and_timestamped_options",
            "data_blocker":self.bar_cache.error,
            "research_status":"UNVALIDATED_HISTORICAL_SIGNAL_LIVE_ADAPTATION",
        }

    def entry(self,now,account,daily,limits):
        if limits is None or account is None or daily is None:
            return None,{"state":"RISK_BLOCKED",
                "reason":"missing verified live account or owner risk envelope"}
        if daily.get("halt_reason"):
            return None,{"state":"DAILY_HALT","reason":daily["halt_reason"]}
        # Owner-set risk and the original 35% limit can only REDUCE sizing.
        maximum=min(3,int(limits.max_contracts))
        exposure=min(.35,float(limits.max_account_exposure_pct))
        result,telemetry=candidate(
            now,self.bar_cache.bars,self.market_reader.quotes.values(),
            equity=float(account["equity"]),
            settled=float(account["settled_cash"]),
            peak=float(daily["high_watermark"]),
            max_contracts=maximum,exposure=exposure,
        )
        return result,telemetry

    def exit(self,position,now):
        # Existing positions opened by prior quant AI MUST keep their original
        # exit policy even after deployment changes the entry strategy.
        if not str(position.get("entry_reason","")).startswith(IDENTIFIER+";"):
            return super().exit(position,now)
        q=self.market_reader.quotes.get(position["symbol"])
        if q is None:
            return None
        return exit_reason(position,q,self.bar_cache.bars,now,
                           fee=self.settings.fee_per_contract)
