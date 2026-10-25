"""Research-only matched comparison: current V5 close proxy vs 2bp liquidity sweep.

Preserves the currently selected 2bp volatility-expansion buffer, momentum
ignition, option selection, IV/RV=1.5 gate, sizing and exit. Only the
liquidity-reversal *prior sweep* threshold changes. Not true V5: historical
minute OHLCV, event-time executable NBBO and size/depth are unavailable.
No broker use or market-data purchases. No live/paper policy mutation.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import MAX_FIXED_MIN, START_CASH, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import (
    ARMS, close_proxy_signals, proxy_candidates, proxy_exit, proxy_size,
)
from .weekly_options_data import NY

LIQUIDITY_SETUP = "liquidity_reversal_CLOSE_PROXY"
PROFILES = {
    "current": 0,
    "liquidity_prior_sweep_2bps": 2,
}
SIZING = {
    "adaptive": ARMS[2],
    "unrestricted_research_only": ARMS[-1],
}


def combined_signals(frames, i, *, challenger=True, sweep_bps=0):
    """Unchanged V5 three-signal proxy, apart from optional prior sweep gate.

    All signal observations are on the same completed minute bar. A 2bp
    swept prior-close condition is additional, never substituting a
    liquidity-reversal for another kind of entry.
    """
    original = close_proxy_signals(frames, i, challenger=challenger)
    if not sweep_bps or not any(x.setup == LIQUIDITY_SETUP for x in original):
        return original
    if sweep_bps < 0:
        raise ValueError("sweep_bps must be nonnegative")
    old = [f.market.spot for f in frames[i-22:i-2]]
    prev = frames[i-1].market.spot
    band = sweep_bps / 10000.0
    enough_high = prev > max(old) * (1.0 + band)
    enough_low = prev < min(old) * (1.0 - band)
    return tuple(x for x in original
                 if x.setup != LIQUIDITY_SETUP
                 or (enough_high if x.side == "put" else enough_low))


def replay(paths, starting_cash=START_CASH):
    if not paths:
        raise ValueError("Missing Railway historical archive; no synthetic performance")
    arms = {
        f"{profile}__{sizing}": (sweep, sizing, sizing_arm)
        for profile, sweep in PROFILES.items()
        for sizing, sizing_arm in SIZING.items()
    }
    states = {
        name: {"settled": starting_cash, "pending": 0.0,
               "daily": [], "trades": [], "skips": Counter(), "exits": Counter()}
        for name in arms
    }
    dates = []
    for path in paths:
        frames = list(load_canonical_path(path))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff = liquidation_cutoff(day)
        # Build each signal set once per day. Both sizing policies replay
        # exactly the same matched entry opportunities and option quotes.
        pools = {}
        for profile, sweep in PROFILES.items():
            def builder(items, idx, *, challenger, _sweep=sweep):
                return combined_signals(items, idx,
                                        challenger=challenger,
                                        sweep_bps=_sweep)
            pools[profile] = proxy_candidates(frames, challenger=True,
                                              signal_builder=builder)
        for name, (sweep, sizing, size_arm) in arms.items():
            state = states[name]
            state["settled"] += state["pending"]
            state["pending"] = 0.0
            opening = state["settled"]
            prior_peak = max([starting_cash] +
                             [x["equity_usd"] for x in state["daily"]])
            busy_until = None
            profile = "current" if sweep == 0 else "liquidity_prior_sweep_2bps"
            for item in pools[profile]:
                when = item["entry_at"]
                if when >= cutoff - timedelta(minutes=MAX_FIXED_MIN + 1):
                    state["skips"]["LATE_ENTRY"] += 1
                    continue
                if busy_until is not None and when <= busy_until:
                    state["skips"]["BUSY"] += 1
                    continue
                debit = item["debit"]
                qty = proxy_size(size_arm, equity=opening,
                                 settled=state["settled"], peak=prior_peak,
                                 debit=debit, score=item["score"],
                                 starting_cash=starting_cash)
                if qty <= 0:
                    state["skips"]["CASH_QUALITY_OR_SIZE"] += 1
                    continue
                if qty * debit > state["settled"] + 1e-8:
                    raise AssertionError("Cash T+1 settlement violated")
                outcome = proxy_exit(size_arm, item, frames, cutoff)
                state["settled"] = round(state["settled"] - qty * debit, 8)
                row = {
                    "day": str(day),
                    "setup": item["signal"]["setup"],
                    "symbol": item["quote"].symbol,
                    "entry_at": when.isoformat(),
                    "contracts": qty,
                    "pnl_usd": None if outcome is None
                    else round(qty * outcome["pnl_usd"], 6),
                }
                if outcome is None:
                    busy_until = cutoff
                    row["exit_reason"] = "UNVERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    state["pending"] += max(0.0, qty * debit + row["pnl_usd"])
                    busy_until = outcome["exit_at"]
                    row["exit_reason"] = outcome["exit_reason"]
                    state["exits"][outcome["exit_reason"]] += 1
                state["trades"].append(row)
            state["daily"].append({
                "day": str(day),
                "equity_usd": round(state["settled"] + state["pending"], 4),
            })
        if len(dates) % 20 == 0:
            print(f"V5_LIQUIDITY_COMBINED_PROGRESS sessions={len(dates)} day={day}",
                  flush=True)
    if not dates:
        raise ValueError("Historical archive contains no usable sessions")

    results = {}
    for name, state in states.items():
        by_setup = defaultdict(list)
        for trade in state["trades"]:
            if trade["pnl_usd"] is not None:
                by_setup[trade["setup"]].append(trade)
        results[name] = {
            **summary(state, starting_cash=starting_cash),
            "by_setup": {
                setup: {
                    "positions": len(trades),
                    "win_rate_pct": round(
                        100.0 * sum(t["pnl_usd"] > 0 for t in trades) / len(trades),
                        2),
                    "sum_modeled_pnl_usd": round(
                        sum(t["pnl_usd"] for t in trades), 2),
                }
                for setup, trades in sorted(by_setup.items())
            },
            "trade_days": len(set(t["day"] for t in state["trades"])),
        }
    deltas = {}
    for sizing in SIZING:
        base = results[f"current__{sizing}"]
        variant = results[f"liquidity_prior_sweep_2bps__{sizing}"]
        deltas[sizing] = {
            "ending_equity_delta_usd": round(
                variant["ending_equity_usd"] - base["ending_equity_usd"], 2),
            "scored_positions_delta": (
                variant["scored_positions"] - base["scored_positions"]),
            "max_eod_drawdown_delta_pp": round(
                variant["max_end_of_day_drawdown_pct_of_preceding_peak"] -
                base["max_end_of_day_drawdown_pct_of_preceding_peak"], 2),
        }
    return {
        "status": "IN_SAMPLE_CLOSE_ONLY_COMBINED_V5_PROXY_NOT_TRUE_V5",
        "study": "Matched three-component V5 challenger plus 2bp prior-sweep liquidity gate",
        "first_day": min(dates), "last_day": max(dates),
        "sessions": len(dates), "starting_cash": starting_cash,
        "baseline": "Existing V5 challenger with selected 2bp volatility-expansion breakout buffer",
        "only_changed_rule": "liquidity reversal: previous SPY close must sweep prior 20-close extreme by >2bps",
        "unchanged_signals": ["momentum_ignition_CLOSE_PROXY",
                              "volatility_expansion_CLOSE_PROXY"],
        "unchanged_filters": {
            "iv_to_realized_vol_max": 1.5,
            "max_hold_minutes": 18,
            "option_max_spread_fraction": .03,
            "cash_settlement": "next-session",
            "simulated_fill": "ASK+0.01/share+0.65 entry; sampled BID-0.01/share-0.65 exit",
        },
        "missing": ["genuine_SPY_1m_OHLCV", "event_time_executable_option_NBBO",
                    "bid_ask_depth", "independent_holdout"],
        "max_drawdown_basis": "end-of-day only",
        "unrestricted_sizing_warning": "hypothetical all-settled-cash, ignores depth/price impact",
        "no_broker_orders": True, "no_paid_data": True,
        "main_unchanged": True, "live_unchanged": True,
        "deltas": deltas,
        "results": results,
    }


def main():
    root = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    output = root / "v5_combined_liquidity_sweep_2026_10_25" / "report.json"
    result = replay(_research_files(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("V5_LIQUIDITY_COMBINED_RESULT " +
          json.dumps(result, sort_keys=True), flush=True)
    # Preserve research worker uptime after a one-shot experiment.
    os.execvp("python", ["python", "-m", "engine.forward_watch"])


if __name__ == "__main__":
    main()
