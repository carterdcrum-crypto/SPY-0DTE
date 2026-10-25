"""Research-only SPY 0DTE V5 signal prioritization with unrestricted settled-cash sizing.

Runs the exact restored three-signal close-only proxy without changing production
paper/live execution, exits or candidate generation. No volume/high/low invention,
event-time option NBBO/depth, or broker orders. Previously inspected archive
is IN-SAMPLE; unrealistic large simulated sizes MUST NOT be viewed as fillable.

A priority can reorder only options observable at the SAME candidate timestamp;
it cannot look ahead to a later signal to override an earlier executable trade.
Score and regime screens are causal, built from signal data available at entry.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import ARMS, proxy_candidates, proxy_exit, proxy_size
from .weekly_options_data import NY

ARM = ARMS[-1]  # full available settled cash; NOT an executable size
L = "liquidity_reversal_CLOSE_PROXY"
M = "momentum_ignition_CLOSE_PROXY"
V = "volatility_expansion_CLOSE_PROXY"
SIGNALS = (M, L, V)

# Each policy keeps all three V5 setups unless explicitly named 'exclude'.
# The first 6 policies change ONLY how competing same-timestamp orders are
# ranked. Rank and gate never consult realized/future option outcomes.
POLICIES = {
    "original_timestamp_symbol": {"order": ()},
    "momentum_first": {"order": (M, V, L)},
    "liquidity_first": {"order": (L, V, M)},
    "expansion_first": {"order": (V, L, M)},
    "quality_score_first": {"order": "quality"},
    "causal_regime_first": {"order": "regime"},
    # As-of quality gates can skip weak candidates and wait for a *future*
    # signal; they do not choose retrospectively based on that future signal.
    "quality_min_3_then_regime": {"order": "regime", "min_score": 3},
    "quality_min_4_then_regime": {"order": "regime", "min_score": 4},
    "expansion_liquidity_only": {"order": (V, L, M), "exclude": (M,)},
    "momentum_liquidity_only": {"order": (M, L, V), "exclude": (V,)},
    "liquidity_expansion_only": {"order": (L, V, M), "exclude": (M,)},
    "all_three_one_trade_day": {"order": "regime", "max_daily_trades": 1},
}

def rank_candidates(entries, policy):
    """Stable causal choice for candidates at identical entry timestamp."""
    order = policy.get("order")
    if order == "quality":
        def key(item):
            return (item["entry_at"], -item["score"], item["quote"].symbol,
                    item["signal"]["setup"])
    elif order == "regime":
        def key(item):
            signal = item["signal"]
            # Strong, orderly trend favors momentum; inefficient/weak trend
            # favors reversal; else prefer expansion. All as-of signal values.
            if abs(signal["trend_bps"]) >= 4 and signal["efficiency"] >= .4:
                ranks = (M, V, L)
            elif abs(signal["trend_bps"]) <= 6 and signal["efficiency"] <= .4:
                ranks = (L, V, M)
            else:
                ranks = (V, L, M)
            return (item["entry_at"], ranks.index(signal["setup"]),
                    -item["score"], item["quote"].symbol)
    elif order:
        def key(item):
            return (item["entry_at"], order.index(item["signal"]["setup"]),
                    item["quote"].symbol)
    else:
        def key(item):
            return (item["entry_at"], item["quote"].symbol)
    return sorted(entries, key=key)


def true_max_drawdown(daily, starting_cash):
    peak = starting_cash
    max_fraction = 0.
    peak_day = "initial"
    worst_peak_day = peak_day
    worst_trough_day = peak_day
    for row in daily:
        value = row["equity_usd"]
        if value > peak:
            peak = value
            peak_day = row["day"]
        drawdown = (peak - value) / peak if peak else 1.
        if drawdown > max_fraction:
            max_fraction = drawdown
            worst_peak_day = peak_day
            worst_trough_day = row["day"]
    return {"max_eod_drawdown_pct": round(100 * max_fraction, 2),
            "peak_day": worst_peak_day, "trough_day": worst_trough_day}


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise ValueError("Actual preexisting research archive required")
    states = {
        name: {
            "settled": starting_cash, "pending": 0.,
            "daily": [], "trades": [], "skips": Counter(), "exits": Counter(),
            "multi_signal_tie_events": 0,
        } for name in POLICIES
    }
    days = []
    for path in paths:
        frames = list(load_canonical_path(path))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        days.append(str(day))
        cutoff = liquidation_cutoff(day)
        # Shared identical restored Oct-23 V5 entries and option screening:
        # IV/RV<=1.5, <=3% spread, delta 0.40-0.60, 1-2m delayed bids.
        entries = proxy_candidates(frames, challenger=True)
        by_time = defaultdict(set)
        for item in entries:
            by_time[item["entry_at"]].add(item["signal"]["setup"])
        competing = {at for at, setups in by_time.items() if len(setups) > 1}
        for name, policy in POLICIES.items():
            state = states[name]
            state["multi_signal_tie_events"] += len(competing)
            state["settled"] += state["pending"]
            state["pending"] = 0.
            opening = state["settled"]
            prior_peak = max([starting_cash] +
                             [x["equity_usd"] for x in state["daily"]])
            busy_until = None
            trade_count = 0
            for item in rank_candidates(entries, policy):
                when = item["entry_at"]
                if when >= cutoff - timedelta(minutes=MAX_FIXED_MIN + 1):
                    state["skips"]["LATE_ENTRY"] += 1
                    continue
                if busy_until is not None and when <= busy_until:
                    state["skips"]["BUSY"] += 1
                    continue
                if item["signal"]["setup"] in policy.get("exclude", ()):
                    state["skips"]["LOW_PRIORITY_EXCLUSION"] += 1
                    continue
                if item["score"] < policy.get("min_score", 0):
                    state["skips"]["QUALITY_GATE"] += 1
                    continue
                if trade_count >= policy.get("max_daily_trades", 100000000):
                    state["skips"]["DAILY_TRADE_CAP"] += 1
                    continue
                debit = item["debit"]
                quantity = proxy_size(ARM, equity=opening,
                                      settled=state["settled"], peak=prior_peak,
                                      debit=debit, score=item["score"],
                                      starting_cash=starting_cash)
                if quantity <= 0:
                    state["skips"]["NO_SETTLED_CASH"] += 1
                    continue
                if quantity * debit > state["settled"] + 1e-8:
                    raise AssertionError("Cash-account T+1 sizing violated")
                # Exact same original 18 minute stop/hold/exit for all arms.
                outcome = proxy_exit(ARM, item, frames, cutoff)
                state["settled"] = round(state["settled"]-quantity*debit, 8)
                row = {"day": str(day), "setup": item["signal"]["setup"],
                       "contracts": quantity, "entry_at": when.isoformat(),
                       "entry_debit_usd": debit,
                       "score": item["score"], "symbol": item["quote"].symbol}
                trade_count += 1
                if outcome is None:
                    busy_until = cutoff
                    state["trades"].append({**row, "pnl_usd": None,
                        "exit_reason": "NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"})
                    continue
                pnl = round(quantity*outcome["pnl_usd"], 6)
                state["pending"] += max(0., quantity*debit+pnl)
                busy_until = outcome["exit_at"]
                state["exits"][outcome["exit_reason"]] += 1
                state["trades"].append({
                    **row, "pnl_usd": pnl, "exit_reason": outcome["exit_reason"],
                    "exit_at": outcome["exit_at"].isoformat(),
                })
            state["daily"].append({
                "day": str(day),
                "equity_usd": round(state["settled"]+state["pending"], 4)
            })
        if len(days) % 20 == 0:
            print("V5_PRIORITY_PROGRESS " + json.dumps({
                "sessions": len(days), "day": str(day)
            }), flush=True)
    if not days:
        raise ValueError("No usable genuine archived sessions")
    results = {}
    for name, state in states.items():
        scored = [x for x in state["trades"] if x["pnl_usd"] is not None]
        signals = defaultdict(list)
        for trade in scored:
            signals[trade["setup"]].append(trade)
        results[name] = {
            **summary(state, starting_cash=starting_cash),
            **true_max_drawdown(state["daily"], starting_cash),
            "multi_signal_tie_events": state["multi_signal_tie_events"],
            "by_signal": {setup: {
                "positions": len(trades),
                "win_rate_pct": round(100*sum(
                    x["pnl_usd"] > 0 for x in trades)/len(trades), 2),
                "modeled_net_pnl_usd": round(sum(x["pnl_usd"]
                                                for x in trades), 2)
            } for setup, trades in sorted(signals.items())},
            "day_count_with_trades": len(set(t["day"] for t in scored))
        }
    reference = results["original_timestamp_symbol"]
    for result in results.values():
        result["delta_ending_equity_vs_original_usd"] = round(
            result["ending_equity_usd"]-reference["ending_equity_usd"], 2)
        result["delta_eod_drawdown_vs_original_pp"] = round(
            result["max_eod_drawdown_pct"]-reference["max_eod_drawdown_pct"], 2)
    return {
        "status": "IN_SAMPLE_CLOSE_ONLY_RESEARCH_NOT_FULL_OHLCV_V5",
        "first_day": min(days), "last_day": max(days), "sessions": len(days),
        "start_cash_usd": starting_cash, "priority_policies": list(POLICIES),
        "base": "Restored V5 challenger, all-settled-cash sizing, all 3 signals",
        "one_trade_at_a_time": True, "no_intraday_cash_recycling": True,
        "full_cash_sizing_ignores_missing_executable_depth": True,
        "max_drawdown_basis": "peak-to-trough session-close sampled equity",
        "fees_slippage": "entry ASK+0.01/share+$0.65 per contract, exit sampled BID-0.01/share-$0.65",
        "max_hold_minutes": 18, "iv_rv_cap": 1.5,
        "premium_stop_loss_pct": 35, "premium_trail_pct": 25,
        "missing_inputs": [
            "SPY historical intraminute high/low/authentic OHLCV",
            "event-time executable NBBO and displayed option depth",
            "independent unseen validation sessions",
            "realistic market impact at unrestricted contract quantities",
        ],
        "no_broker_orders": True, "no_paid_data": True,
        "production_live_config_unchanged": True, "results": results,
    }


def main():
    root = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    result = replay(_research_files(root))
    path = root/"v5_priority_unrestricted_2026_10_25"/"report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print("V5_PRIORITY_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    os.execvp("python", ["python", "-m", "engine.forward_watch"])


if __name__ == "__main__":
    main()
