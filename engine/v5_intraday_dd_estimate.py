"""Sampled intraday account drawdown for V5 baseline vs looser reversal.

RESEARCH ONLY: archived minute-bucket bid quotes, NOT tradable tick NBBO.
Replays EXACT previously tested fills, sizing and terminal exits; checks the
original EOD balances against their frozen $1,808,494.60/$2,055,376.50
reference, and computes observed minute-sampled mark-to-bid account equity.

Do not call this a true overall or tick-level max drawdown. Missing intraminute
prices, executable depth and market impact make this a partial observation.
Do not place orders or change live/paper rules.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff, quote_path
from .v5_archive_proxy import FEE, SLIP, ARMS, proxy_exit
from .v5_signal_variations import param_candidates
from .v5_conditional_capital_study import choose_qty, market_evidence, peak_to_trough_eod
from .weekly_options_data import NY

FULL = ARMS[-1]
ARMS_TO_CHECK = {
    "baseline_all_three": 1808494.60,
    "reversal_looser": 2055376.50,
}


class EquityTracker:
    def __init__(self, capital):
        self.peak = float(capital)
        self.peak_at = "starting_cash"
        self.maximum = 0.0
        self.peak_when_worst = self.peak
        self.trough_when_worst = self.peak
        self.worst_peak_at = self.peak_at
        self.worst_trough_at = self.peak_at
        self.observations = 0
        self.trough_in_dollars = 0.0
        self.negative_observations = 0

    def update(self, at, equity):
        if equity < -1e-5:
            raise AssertionError("No borrowing or negative simulated equity")
        eq = float(equity)
        label = at.isoformat() if hasattr(at, "isoformat") else str(at)
        self.observations += 1
        if eq > self.peak:
            self.peak, self.peak_at = eq, label
        current = (self.peak-eq)/self.peak if self.peak > 0 else 1.
        if current > self.maximum:
            self.maximum = current
            self.peak_when_worst = self.peak
            self.trough_when_worst = eq
            self.worst_peak_at = self.peak_at
            self.worst_trough_at = label
            self.trough_in_dollars = self.peak - eq
        if current > 0:
            self.negative_observations += 1

    def report(self):
        return {
            "sampled_intraday_peak_to_trough_pct": round(self.maximum*100, 2),
            "worst_peak_at": self.worst_peak_at,
            "worst_trough_at": self.worst_trough_at,
            "worst_peak_equity_usd": round(self.peak_when_worst, 2),
            "worst_trough_equity_usd": round(self.trough_when_worst, 2),
            "worst_peak_to_trough_usd": round(self.trough_in_dollars, 2),
            "observed_account_equity_points": self.observations,
        }


def exit_bid_value(bid):
    """Observed theoretical sell-at-bid proceeds net fees/slippage."""
    return max(0., (float(bid)-SLIP)*100-FEE)


def run(paths, *, starting_cash=START_CASH):
    if not paths:
        raise RuntimeError("Missing archived real option quotes; do not fabricate intraday DD")
    states = {
        name: {
            "settled": float(starting_cash), "pending": 0.,
            "daily": [], "count": 0,
            "tracker": EquityTracker(starting_cash),
            "observed_option_marks": 0,
            "expected_full_minute_marks": 0,
            "max_missing_minutes_in_holding_interval": 0,
            "trade_missing_mark_intervals": 0,
            "zero_terminal_quotes": 0,
            "peak_premium_allocation_pct": 0.,
            "max_one_order_contracts": 0,
        }
        for name in ARMS_TO_CHECK
    }
    count_days = 0
    for file in paths:
        frames = list(load_canonical_path(file))
        if not frames:
            continue
        count_days += 1
        day = frames[0].timestamp.astimezone(NY).date()
        cutoff = liquidation_cutoff(day)
        for name, state in states.items():
            # Settle prior-day long-option sales before trading again.
            state["settled"] = round(state["settled"]+state["pending"], 8)
            state["pending"] = 0.
            opening = state["settled"]
            peak_eod = max([starting_cash]+[row["equity_usd"] for row in state["daily"]])
            state["tracker"].update(frames[0].timestamp, opening)
            entries = param_candidates(frames, variant=name)
            observations = defaultdict(set)
            for item in entries:
                observations[(item["entry_at"], item["signal"]["side"])].add(item["signal"]["setup"])
            busy_until = None
            for item in entries:
                at = item["entry_at"]
                if at >= cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    continue
                if busy_until is not None and at <= busy_until:
                    continue
                evidence = market_evidence(
                    item, observations[(at,item["signal"]["side"])])
                qty, fraction = choose_qty(
                    ("full_throttle",), item=item,
                    opening=opening, settled=state["settled"], peak=peak_eod,
                    evidence=evidence, starting_cash=starting_cash,
                )
                if qty < 1:
                    continue
                debit = item["debit"]
                if qty*debit > state["settled"]+1e-8:
                    raise AssertionError("T+1 settled cash violated")
                terminal = proxy_exit(FULL, item, frames, cutoff)
                if terminal is not None and terminal["exit_at"] <= at:
                    raise AssertionError("Exit must follow entry")

                # The prior study reserved buy debit immediately, paid sell
                # credits into pending only at a valid observed terminal bid.
                # Reproduce this exactly to preserve both historical balances.
                before = state["settled"]+state["pending"]
                state["settled"] = round(state["settled"]-qty*debit, 8)
                state["count"] += 1
                state["max_one_order_contracts"] = max(state["max_one_order_contracts"], qty)
                state["peak_premium_allocation_pct"] = max(
                    state["peak_premium_allocation_pct"], 100.*qty*debit/before)
                q = item["quote"]
                # A hypothetical newly bought option can be LIQUIDATED at
                # currently observed bid, not ask; include both-side fees.
                state["tracker"].update(
                    at, state["settled"]+state["pending"]+qty*exit_bid_value(q.bid))
                last_seen = at
                end = terminal["exit_at"] if terminal else cutoff
                # Quote_path excludes entry minute and returns only OBSERVED
                # positive, coherent sampled bid quotes, no backfill.
                for quote_at, bid in quote_path(frames,item["entry_idx"],q.symbol,cutoff):
                    if quote_at > end:
                        break
                    if quote_at <= at:
                        continue
                    elapsed = int((quote_at-last_seen).total_seconds()//60)
                    if elapsed > 1:
                        state["trade_missing_mark_intervals"] += 1
                        state["max_missing_minutes_in_holding_interval"] = max(
                            state["max_missing_minutes_in_holding_interval"],elapsed-1)
                    state["observed_option_marks"] += 1
                    state["tracker"].update(
                        quote_at, state["settled"]+state["pending"]+
                        qty*exit_bid_value(bid))
                    last_seen = quote_at
                expected = max(0,int((end-at).total_seconds()//60))
                state["expected_full_minute_marks"] += expected
                if (end-last_seen).total_seconds() > 60:
                    state["trade_missing_mark_intervals"] += 1
                    state["max_missing_minutes_in_holding_interval"] = max(
                        state["max_missing_minutes_in_holding_interval"],
                        int((end-last_seen).total_seconds()//60))
                if terminal is None:
                    state["zero_terminal_quotes"] += 1
                    busy_until = cutoff
                else:
                    realized_credit = qty*(debit+terminal["pnl_usd"])
                    state["pending"] += max(0.,realized_credit)
                    busy_until = terminal["exit_at"]
                    # After selling at observed bid, mark exactly the cash
                    # proceeds to reflect the modeled exit rather than
                    # allowing unknown same-day equity fluctuations.
                    state["tracker"].update(
                        terminal["exit_at"], state["settled"]+state["pending"])
            eod = round(state["settled"]+state["pending"],4)
            state["daily"].append({"day":str(day),"equity_usd":eod})
            state["tracker"].update(cutoff,eod)
        if count_days % 20 == 0:
            print("V5_INTRADAY_PROGRESS "+json.dumps({"sessions":count_days,"day":str(day)}),flush=True)
    if count_days != 128:
        raise AssertionError(f"Expected 128 archived sessions, found {count_days}")

    results = {}
    for name,expected in ARMS_TO_CHECK.items():
        state = states[name]
        ending = state["daily"][-1]["equity_usd"]
        if abs(ending-expected) > .02:
            raise AssertionError(
                f"{name} benchmark changed {ending} != {expected}; fail, do not publish")
        eod = peak_to_trough_eod(state["daily"], starting_cash)
        coverage = (state["observed_option_marks"] /
                    state["expected_full_minute_marks"] if
                    state["expected_full_minute_marks"] else None)
        if coverage is not None and coverage>1.00001:
            raise AssertionError("A sampled option bid cannot cover more than one minute")
        results[name] = {
            **state["tracker"].report(),
            "end_of_day_max_drawdown_pct": eod["true_max_eod_drawdown_pct"],
            "eod_worst_peak_date":eod["worst_dd_peak_day"],
            "eod_worst_trough_date":eod["worst_dd_trough_day"],
            "ending_equity_usd":ending,
            "positions":state["count"],
            "observed_option_bid_marks":state["observed_option_marks"],
            "expected_one_minute_bid_marks":state["expected_full_minute_marks"],
            "sampled_bid_minute_coverage_pct": round(100*coverage,2) if coverage is not None else None,
            "holding_intervals_with_unobserved_minutes":state["trade_missing_mark_intervals"],
            "longest_undocumented_quote_gap_in_minutes":state["max_missing_minutes_in_holding_interval"],
            "unscored_no_terminal_quotes":state["zero_terminal_quotes"],
            "max_hypothetical_contracts_single_order":state["max_one_order_contracts"],
            "max_position_premium_fraction_of_account_pct":round(state["peak_premium_allocation_pct"],2),
        }
    return {
        "status":"HISTORICAL_SAMPLED_INTRADAY_BID_MARK_ESTIMATE_NOT_TICK_DRAWDOWN",
        "sessions":count_days, "first_day":str(min(
            f for f in (row["day"] for row in states["baseline_all_three"]["daily"]))),
        "last_day":states["baseline_all_three"]["daily"][-1]["day"],
        "initial_cash":starting_cash,
        "results":results,
        "strict_benchmark_equity_match":True,
        "assumptions":[
            "1 minute archived option CBBO positive bids used for long option liquidation marks",
            "No interpolation or stale forward-fill for missing observations",
            "One position at a time, identical prior entry/sizing/exit sequencing",
            "Exact previously modeled closing cash checked against benchmarks",
            "Theoretical bid - 1 cent/share slippage - $0.65/contract exit",
            "No intraminute options quote path, timestamp-aligned executable depth or impact",
            "Observed maximum is neither an upper nor a lower bound on live drawdown",
            "Overfitted 128 in-sample sessions; synthetic order sizes may exceed available liquidity",
            "Missing quote terminal positions retain full debit reserved like original simulation",
        ],
        "broker_orders":0,"live_and_paper_unchanged":True,
    }


def main():
    root = Path(os.getenv("RESEARCH_DATA_DIR","/data/research"))
    result=run(_research_files(root))
    destination=root/"v5_sampled_intraday_dd_2026_10_28"/"report.json"
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("V5_INTRADAY_META "+json.dumps({
        "sessions":result["sessions"],
        "baseline_match":result["strict_benchmark_equity_match"],
        "status":result["status"],
    }),flush=True)
    for name, row in result["results"].items():
        print("V5_INTRADAY_RESULT "+json.dumps({"arm":name,**row},sort_keys=True),flush=True)
    print("V5_INTRADAY_REPORT_SAVED "+str(destination),flush=True)
    os.execvp("python",["python","-m","engine.forward_watch"])


if __name__=="__main__":
    main()
