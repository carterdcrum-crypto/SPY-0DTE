"""Risk-variant backtest of restored Oct-23 three-signal V5 close-only proxy.

Runs ONLY on archived observed SPY closes and option minute CBBO. It is not a
true OHLCV V5 backtest or proof of executable option depth/fills. This module
does not alter the live or paper algorithm, trade or purchase data.
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
from .v5_archive_proxy import (
    ARMS, first_hit, proxy_candidates, proxy_exit, proxy_size,
)
from .weekly_options_data import NY

# The restored operational close-only V5 challenger uses 1.25x RVOL, all
# original three setups, no 2bp breakout buffer, an 18-minute maximum hold,
# 35% hard premium-loss stop and 25% trailing premium stop. All arms get the
# exact same entry opportunities and original exits.
ADAPTIVE_ARM = ARMS[2]
UNRESTRICTED_ARM = ARMS[-1]
POLICIES = {
    "original_unrestricted_cash_RESEARCH_ONLY": {"sizing": "unrestricted"},
    "original_v5_adaptive": {},
    "adaptive_max_20pct_per_trade": {"trade_limit": .20},
    "adaptive_max_15pct_per_trade": {"trade_limit": .15},
    "adaptive_one_contract": {"max_contracts": 1},
    "adaptive_daily_premium_35pct": {"daily_premium_limit": .35},
    "adaptive_daily_premium_20pct": {"daily_premium_limit": .20},
    "adaptive_score_4plus": {"min_score": 4},
    "adaptive_score_5plus": {"min_score": 5},
    "adaptive_pause_after_first_loss": {"pause_after_loss": True},
    "adaptive_pause_after_2pct_realized_daily_loss": {"loss_day_limit": .02},
    "adaptive_without_liquidity_reversal": {
        "exclude_setup": "liquidity_reversal_CLOSE_PROXY"},
    "adaptive_without_volatility_expansion": {
        "exclude_setup": "volatility_expansion_CLOSE_PROXY"},
    "adaptive_without_momentum_ignition": {
        "exclude_setup": "momentum_ignition_CLOSE_PROXY"},
}


def allowed_size(*, policy, base_qty, opening, debit,
                 premium_spent_today=0.):
    """Only scale *down* existing causal V5 decision; never exceed cash."""
    qty = base_qty
    if "trade_limit" in policy:
        qty = min(qty, int((policy["trade_limit"] * opening + 1e-8) // debit))
    if "max_contracts" in policy:
        qty = min(qty, policy["max_contracts"])
    if "daily_premium_limit" in policy:
        remaining = max(0., policy["daily_premium_limit"] * opening
                       - premium_spent_today)
        qty = min(qty, int((remaining + 1e-8) // debit))
    return max(0, qty)


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise ValueError("Actual historical Railway archive required")
    states = {
        name: {
            "settled": starting_cash, "pending": 0., "daily": [],
            "trades": [], "skips": Counter(), "exits": Counter()
        } for name in POLICIES
    }
    dates = []
    for path in paths:
        frames = list(load_canonical_path(path))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff = liquidation_cutoff(day)
        # The original pre-Oct-25 V5 challenger; no experimental entry gate.
        entries = proxy_candidates(frames, challenger=True)
        for name, policy in POLICIES.items():
            state = states[name]
            state["settled"] = round(state["settled"] + state["pending"], 8)
            state["pending"] = 0.
            opening = state["settled"]
            peak = max([starting_cash] + [x["equity_usd"] for x in state["daily"]])
            occupied_until = None
            premium_spent_today = 0.
            pnl_today = 0.
            losses_today = 0
            size_arm = (UNRESTRICTED_ARM if policy.get("sizing") == "unrestricted"
                        else ADAPTIVE_ARM)
            for item in entries:
                when = item["entry_at"]
                if when >= cutoff - timedelta(minutes=MAX_FIXED_MIN + 1):
                    state["skips"]["LATE_ENTRY"] += 1
                    continue
                if occupied_until is not None and when <= occupied_until:
                    state["skips"]["BUSY"] += 1
                    continue
                if item["signal"]["setup"] == policy.get("exclude_setup"):
                    state["skips"]["EXCLUDED_SIGNAL"] += 1
                    continue
                if item["score"] < policy.get("min_score", 0):
                    state["skips"]["SCORE_THRESHOLD"] += 1
                    continue
                if policy.get("pause_after_loss") and losses_today:
                    state["skips"]["DAILY_LOSS_PAUSE"] += 1
                    continue
                if ("loss_day_limit" in policy and
                        pnl_today <= -policy["loss_day_limit"] * opening):
                    state["skips"]["DAILY_LOSS_PAUSE"] += 1
                    continue
                debit = item["debit"]
                qty = proxy_size(
                    size_arm, equity=opening, settled=state["settled"],
                    peak=peak, debit=debit, score=item["score"],
                    starting_cash=starting_cash)
                qty = allowed_size(
                    policy=policy, base_qty=qty,
                    opening=opening, debit=debit,
                    premium_spent_today=premium_spent_today)
                if qty < 1:
                    state["skips"]["CASH_QUALITY_OR_SIZE"] += 1
                    continue
                if qty * debit > state["settled"] + 1e-8:
                    raise AssertionError("Insufficient T+1 settled cash")
                # The exact original 18-minute max, 25% premium trail,
                # 35% hard stop; only position sizing/entry gating changes.
                outcome = proxy_exit(size_arm, item, frames, cutoff)
                state["settled"] = round(state["settled"] - qty * debit, 8)
                premium_spent_today += qty * debit
                trade = {
                    "day": str(day), "setup": item["signal"]["setup"],
                    "contracts": qty, "symbol": item["quote"].symbol,
                    "entry_at": when.isoformat(), "score": item["score"],
                    "pnl_usd": None if outcome is None
                    else round(qty * outcome["pnl_usd"], 6)
                }
                if outcome is None:
                    occupied_until = cutoff
                    trade["exit_reason"] = "NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    state["pending"] += max(0., qty * debit + trade["pnl_usd"])
                    pnl_today += trade["pnl_usd"]
                    if trade["pnl_usd"] < 0:
                        losses_today += 1
                    occupied_until = outcome["exit_at"]
                    trade["exit_reason"] = outcome["exit_reason"]
                    state["exits"][outcome["exit_reason"]] += 1
                state["trades"].append(trade)
            state["daily"].append({
                "day": str(day),
                "equity_usd": round(state["settled"] + state["pending"], 4)
            })
        if len(dates) % 20 == 0:
            print(f"V5_DRAWDOWN_PROGRESS sessions={len(dates)} day={day}",
                  flush=True)
    if not dates:
        raise ValueError("Empty historical archive: no fabricated P&L")

    results = {}
    for name, state in states.items():
        all_trades = [t for t in state["trades"] if t["pnl_usd"] is not None]
        groups = defaultdict(list)
        for trade in all_trades:
            groups[trade["setup"]].append(trade)
        results[name] = {
            **summary(state, starting_cash=starting_cash),
            "first_5k_session": first_hit(state),
            "by_signal": {
                label: {
                    "scored_trades": len(trades),
                    "win_pct": round(100 * sum(t["pnl_usd"] > 0
                        for t in trades) / len(trades), 2),
                    "modeled_net_profit_usd": round(sum(t["pnl_usd"]
                        for t in trades), 2),
                } for label, trades in sorted(groups.items())
            }
        }
    baseline = results["original_v5_adaptive"]
    for name, result in results.items():
        result["relative_to_original_adaptive"] = {
            "net_profit_delta_usd": round(
                result["net_profit_usd"] - baseline["net_profit_usd"], 2),
            "eod_drawdown_delta_pp": round(
                result["max_end_of_day_drawdown_pct_of_preceding_peak"] -
                baseline["max_end_of_day_drawdown_pct_of_preceding_peak"], 2),
        }
    return {
        "status": "REUSED_IN_SAMPLE_CLOSE_ONLY_V5_RISK_SENSITIVITY_NOT_TRUE_V5",
        "first_day": min(dates), "last_day": max(dates), "sessions": len(dates),
        "starting_cash_usd": starting_cash,
        "base_strategy": "Pre-Oct-25 V5 three-signal challenger with original thresholds",
        "original_exits": {
            "max_hold_minutes": 18,
            "premium_hard_stop_loss_pct": 35,
            "premium_trailing_stop_drawdown_pct": 25,
            "spy_close_reversal_enabled": True,
        },
        "original_entry": {
            "min_relative_volume": 1.25,
            "iv_to_realized_vol_limit": 1.5,
            "max_option_spread_pct": 3,
            "no_2bp_expansion_buffer": True,
        },
        "sizing": "existing proxy_size function for baseline and optional reductions",
        "execution": "next sampled 1m CBBO ASK+0.01/share+0.65 entry; BID-0.01/share-0.65 exit",
        "settlement": "T+1; same-day sale proceeds not reused",
        "max_drawdown_basis": "end of day, not intraday",
        "limitations": [
            "Historical archived SPY minute HIGH, LOW, authentic full minute volumes absent",
            "Event-time executable NBBO and displayed quote depth absent",
            "Risk strategies compared on previously optimized/reused sample; no holdout",
            "Model does not include options market impact for large size",
            "Historical proxy outcomes are not broker-replicable returns",
        ],
        "no_broker_orders": True, "no_data_purchases": True,
        "production_strategy_unchanged": True,
        "policies": POLICIES, "results": results
    }


def main():
    data_dir = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    output_path = data_dir / "v5_eod_drawdown_2026_10_25" / "report.json"
    report = replay(_research_files(data_dir))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print("V5_DRAWDOWN_RESULT " + json.dumps(report, sort_keys=True),
          flush=True)
    os.execvp("python", ["python", "-m", "engine.forward_watch"])


if __name__ == "__main__":
    main()
