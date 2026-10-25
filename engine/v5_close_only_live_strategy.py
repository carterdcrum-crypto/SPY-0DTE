"""User-selected V5 close-only unrestricted *cash* LIVE execution policy.

Faithful close-only challenger signal, no cap from the signal itself.
The user intentionally removed discretionary daily loss/gain, maximum
contracts, and exposure ceilings for this LIVE profile. Still enforce
actual Webull settled buying power and current NBBO depth.
The 500k archival replay did not have those checks. No AI decisions.
Never bypass owner LIVE Auto Trade switch or the single-writer executor.
"""
from __future__ import annotations

from .v4_live_strategy import V4QualityLiveStrategy
from .v5_close_only_operational import (
    PROFILE, paper_candidate, paper_exit_reason, V5_MIN_COMPLETED_BARS,
)
from .v4_operational import FEE, ET
from .live_autonomy_store import autonomy_db_path
from .v5_drawdown_sizing import observe_flat_session


class V5CloseOnlyLiveStrategy(V4QualityLiveStrategy):
    unrestricted_v5_live = True

    def __init__(self, reader, audit_path: str):
        super().__init__(reader, audit_path)
        # Historical close-only challenger only requires 36 completed
        # signal bars plus one finished confirmation/entry minute.
        self.bar_cache.minimum_history_bars=V5_MIN_COMPLETED_BARS

    def telemetry(self):
        return {
            "strategy": PROFILE,
            "sizing_mode": "full_cash_with_eod_drawdown_throttle_AND_VERIFIED_DEPTH",
            "signal_model": "CLOSE_ONLY_QUANT_NO_LLM",
            "exit_profile": "35pct_hard_25pct_trailing_close_reversal_18min",
            "risk_profile": "EOD_DD_2/5/10pct_to_100/70/40/15pct_cash_budget_verified_depth",
            "data_source": "timestamp_verified_webull_completed_m1_and_0dte_nbbo",
            "data_blocker": self.bar_cache.error,
            "historical_evidence": "UNVALIDATED_REUSED_CBBO_MINUTE_PROXY",
        }

    def entry(self, now, account, daily, limits):
        if account is None or daily is None:
            return None, {"state":"RISK_BLOCKED",
                          "reason":"missing verified live account or equity high watermark"}
        # The policy persists last observed FLAT equity across sessions.
        # Avoid treating a temporarily reserved option premium as a loss.
        # No per-order owner limit is substituted for this V5 EOD throttle.
        risk=observe_flat_session(autonomy_db_path(),now.astimezone(ET).date(),
                                  float(account["equity"]))
        proposal, diag=paper_candidate(
            now, self.bar_cache.bars, self.market_reader.quotes.values(),
            equity=float(account["equity"]),
            settled=float(account["settled_cash"]),
            peak=risk["prior_peak_equity"],
            risk_fraction=risk["fraction"],
            risk_opening_equity=risk["opening_equity"])
        if proposal is None:
            return None, {**diag,"strategy":PROFILE}
        # Final live executor rechecks the exact ask, fresh depth and settled
        # buying power; no arbitrary owner contract/day-profit/loss ceilings.
        return {
            "symbol":proposal["symbol"],
            "quantity":int(proposal["quantity"]),
            "reason":f"{PROFILE}; {proposal['setup']}; long0dte; user_selected_live",
            "cycle":proposal["cycle"],
            "model_proposed_cash_depth_quantity":int(proposal["quantity"]),
            "risk_fraction":risk["fraction"],
            "risk_opening_equity":risk["opening_equity"],
            "risk_peak_equity":risk["prior_peak_equity"],
            "risk_budget_usd":proposal["risk_budget_usd"],
        },{**diag,"state":"SIGNAL","strategy":PROFILE,
           "live_model":"user_selected_V5_full_cash_eod_drawdown_throttle",
           "risk":risk,
           "broker_settled_cash_and_nbbo_depth_enforced":True}

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
