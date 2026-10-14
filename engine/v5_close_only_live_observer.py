"""Research-only V5 close-only observer alongside unchanged live V4 orders.

Installed in the same LIVE read-only decision process; V5 proposals are
diagnostics ONLY. The ORIGINAL V4 entry/exit and order authorization remain
authoritative. It is NOT possible for this module to request a V5 broker order.
"""
from __future__ import annotations

from .v4_live_strategy import V4QualityLiveStrategy
from .v5_close_only_operational import paper_candidate, PROFILE


class V5ObservedOriginalV4LiveStrategy(V4QualityLiveStrategy):
    def telemetry(self):
        return {**super().telemetry(),
                "v5_observer":PROFILE,
                "v5_orders_enabled":False,
                "v5_validation":"BLOCKED_UNVALIDATED_PROXY_AND_REALTIME_ENTITLEMENT"}

    def entry(self,now,account,daily,limits):
        original,decision=super().entry(now,account,daily,limits)
        # V5 observer must NEVER change the original V4 decision. Unknown data
        # or calculation errors are a diagnostic blocker, not an order.
        if account is None or daily is None or limits is None:
            return original,{**decision,"v5_live_observer":{
                "state":"BLOCKED_ACCOUNT_OR_LIMITS","no_live_order":True}}
        try:
            candidate,diag=paper_candidate(
                now,self.bar_cache.bars,self.market_reader.quotes.values(),
                equity=float(account["equity"]),
                settled=float(account["settled_cash"]),
                peak=float(daily["high_watermark"]))
            v5={**diag,"no_live_order":True,
                "hypothetical_cash_and_depth_quantity":(
                    int(candidate["quantity"]) if candidate else None),
                "candidate_symbol":candidate["symbol"] if candidate else None,
                "v5_unrestricted_live_allowed":False}
        except (ValueError,TypeError,AttributeError,OverflowError,ZeroDivisionError):
            v5={"state":"V5_RESEARCH_DATA_INVALID","no_live_order":True}
        return original,{**decision,"v5_live_observer":v5}
