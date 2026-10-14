"""User-selected V5 close-only unrestricted *cash* LIVE execution policy.

Faithful close-only challenger signal, no cap from the signal itself.
Existing broker risk envelope (settled cash, owner contracts/exposure/daily
loss limits), fresh timestamped real-market data and NBBO size ALWAYS apply.
The 500k archival replay did not have those checks. No AI decisions.
Never bypass owner LIVE Auto Trade switch or the single-writer executor.
"""
from __future__ import annotations

from .v4_live_strategy import V4QualityLiveStrategy
from .v5_close_only_operational import (
    PROFILE, paper_candidate, paper_exit_reason,
)
from .v4_operational import FEE


class V5CloseOnlyLiveStrategy(V4QualityLiveStrategy):
    def telemetry(self):
        return {
            "strategy": PROFILE,
            "sizing_mode": "unrestricted_cash_signal_BROKER_ENVELOPE_ENFORCED",
            "signal_model": "CLOSE_ONLY_QUANT_NO_LLM",
            "exit_profile": "35pct_hard_25pct_trailing_close_reversal_18min",
            "risk_profile": "owner_max_contracts_and_exposure_daily_stop_cash_depth",
            "data_source": "timestamp_verified_webull_completed_m1_and_0dte_nbbo",
            "data_blocker": self.bar_cache.error,
            "historical_evidence": "UNVALIDATED_REUSED_CBBO_MINUTE_PROXY",
        }

    def entry(self, now, account, daily, limits):
        if account is None or daily is None or limits is None:
            return None, {"state":"RISK_BLOCKED",
                          "reason":"missing verified live account/risk envelope"}
        if daily.get("halt_reason"):
            return None, {"state":"DAILY_HALT", "reason":daily["halt_reason"]}
        proposal, diag=paper_candidate(
            now, self.bar_cache.bars, self.market_reader.quotes.values(),
            equity=float(account["equity"]),
            settled=float(account["settled_cash"]),
            peak=float(daily["high_watermark"]))
        if proposal is None:
            return None, {**diag,"strategy":PROFILE}
        # Final live executor recomputes from the exact pre-trade ask and
        # applies owner-authenticated contract/exposure/daily-loss limits.
        return {
            "symbol":proposal["symbol"],
            "quantity":int(proposal["quantity"]),
            "reason":f"{PROFILE}; {proposal['setup']}; long0dte; user_selected_live",
            "cycle":proposal["cycle"],
            "model_proposed_cash_depth_quantity":int(proposal["quantity"]),
        },{**diag,"state":"SIGNAL","strategy":PROFILE,
           "live_model":"user_selected_V5_cash_unrestricted",
           "broker_risk_limits_enforced":True}

    def exit(self, position, now):
        reason=str(position.get("entry_reason",""))
        if not reason.startswith(PROFILE+";"):
            # Always finish any open original V4 position using its actual V4
            # exit policy; never reinterpret its strategy mid-position.
            return super().exit(position, now)
        q=self.market_reader.quotes.get(position["symbol"])
        if q is None:
            return None
        normalized={**position,"average_cost":
                    float(position["average_price"])*100+FEE}
        return paper_exit_reason(normalized,q,self.bar_cache.bars,now)
