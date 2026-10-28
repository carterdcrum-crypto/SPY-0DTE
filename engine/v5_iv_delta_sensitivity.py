"""Archived V5 IV/RV and delta-gate sweep: research ONLY.

One read of each existing Railway sampled-CBBO day, nine predeclared arms.
Reproduces historical full_cash_with_dd_throttle baseline exactly, and checks
our parametrized candidate builder against the frozen original on each day.

Absolutely no broker access, production policy/limits changes, external data
purchases, synthetic quote/depth assumptions or order submission.
THIS IS CLOSE-ONLY, IN-SAMPLE, NONEXECUTABLE RESEARCH.
"""
from __future__ import annotations

import bisect
import json
import math
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import (
    FEE, SLIP, ARMS, close_proxy_signals, proxy_candidates, proxy_exit
)
from .v5_conditional_capital_study import (
    choose_qty, dd_multiplier, half_metrics, market_evidence,
    peak_to_trough_eod
)
from .weekly_options_data import NY
from quantconnect.v5_core import quality_score

FULL = ARMS[-1]
# Unoptimized tiny predeclared sensitivity grid. Baseline is frozen.
ARM_CONFIGS = {
    "baseline_iv1_5_delta40_60": (1.5, .40, .60),
    "iv2_0_delta40_60": (2.0, .40, .60),
    "iv2_5_delta40_60": (2.5, .40, .60),
    "iv1_5_delta35_65": (1.5, .35, .65),
    "iv1_5_delta30_70": (1.5, .30, .70),
    "iv2_0_delta35_65": (2.0, .35, .65),
    "iv2_0_delta30_70": (2.0, .30, .70),
    "iv2_5_delta35_65": (2.5, .35, .65),
    "iv2_5_delta30_70": (2.5, .30, .70),
}


def param_candidates(frames, *, iv_cap: float, delta_low: float,
                     delta_high: float):
    """Exact frozen proxy_candidates with only TWO screening gates varied."""
    times = [f.timestamp + timedelta(minutes=1) for f in frames]
    entries = []
    for i in range(35, len(frames)):
        signals = close_proxy_signals(frames, i, challenger=True)
        if not signals:
            continue
        event_time = times[i]
        for sig in signals:
            start = event_time + timedelta(minutes=1)
            end = event_time + timedelta(minutes=2)
            idx = bisect.bisect_left(times, start)
            chosen = None
            for j in range(idx, len(frames)):
                if times[j] > end:
                    break
                qlist = [q for q in frames[j].options
                         if q.right == sig.side and q.bid > 0 and q.ask > q.bid
                         and delta_low <= abs(q.delta) <= delta_high
                         and q.spread_fraction <= .15]
                if qlist:
                    q = min(qlist, key=lambda q: (
                        abs(abs(q.delta)-.50),
                        q.spread_fraction,
                        abs(q.strike-frames[j].market.spot),
                        q.symbol))
                    chosen = (j, q)
                    break
            if chosen is None:
                continue
            j, q = chosen
            market = frames[j].market
            if (q.spread_fraction > .03 or market.realized_volatility <= 0 or
                    q.implied_volatility <= 0 or
                    q.implied_volatility / market.realized_volatility > iv_cap):
                continue
            score = quality_score(sig, q.spread_fraction, q.delta,
                                  q.implied_volatility, market.realized_volatility)
            entries.append({
                "signal": {
                    "at": event_time, "side": sig.side, "setup": sig.setup,
                    "volume_ratio": sig.rvol, "trend_bps": sig.trend_bps,
                    "efficiency": sig.efficiency,
                },
                "entry_idx": j, "entry_at": times[j], "quote": q,
                "score": score,
                "debit": round((q.ask+SLIP)*100+FEE, 2),
                "realized_vol_at_entry": market.realized_volatility,
            })
    return sorted(entries, key=lambda x: (x["entry_at"], x["quote"].symbol))


def comparable(item):
    return (item["signal"]["at"], item["signal"]["side"],
            item["signal"]["setup"], item["entry_idx"], item["entry_at"],
            item["quote"].symbol, item["score"], item["debit"])


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise RuntimeError("No archived Railway option CBBO sessions; no fabricated backtest")
    states = {key: {
        "settled": float(starting_cash), "pending": 0.,
        "daily": [], "trades": [], "skips": Counter(), "exits": Counter()
    } for key in ARM_CONFIGS}
    dates = []
    total_eligible = Counter()
    for file in paths:
        frames = list(load_canonical_path(file))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff = liquidation_cutoff(day)

        # Enforce exact 1.5x and [0.40,0.60] reproduction per real archive day.
        frozen = proxy_candidates(frames, challenger=True)
        candidates = {
            key: param_candidates(frames, iv_cap=cfg[0],
                                  delta_low=cfg[1], delta_high=cfg[2])
            for key, cfg in ARM_CONFIGS.items()
        }
        assert [comparable(x) for x in candidates["baseline_iv1_5_delta40_60"]] == [
            comparable(x) for x in frozen], f"Frozen historical baseline drift: {day}"

        for key, entries in candidates.items():
            total_eligible[key] += len(entries)
            state = states[key]
            state["settled"] = round(state["settled"] + state["pending"], 8)
            state["pending"] = 0.
            opening = state["settled"]
            prior_peak = max([starting_cash] + [x["equity_usd"] for x in state["daily"]])
            busy_until = None
            observations = defaultdict(set)
            for item in entries:
                observations[(item["entry_at"], item["signal"]["side"])].add(
                    item["signal"]["setup"])

            for item in entries:
                at = item["entry_at"]
                if at >= cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    state["skips"]["LATE_ENTRY"] += 1
                    continue
                if busy_until is not None and at <= busy_until:
                    state["skips"]["BUSY"] += 1
                    continue
                evidence = market_evidence(
                    item, observations[(at, item["signal"]["side"])])
                qty, fraction = choose_qty(
                    ("full_throttle",), item=item,
                    opening=opening, settled=state["settled"],
                    peak=prior_peak, evidence=evidence,
                    starting_cash=starting_cash,
                )
                if qty < 1:
                    state["skips"]["CASH_OR_RISK_BUDGET"] += 1
                    continue
                debit = item["debit"]
                if qty * debit > state["settled"] + 1e-8:
                    raise AssertionError("T+1 cash settlement violated")
                exit_info = proxy_exit(FULL, item, frames, cutoff)
                state["settled"] = round(state["settled"] - qty*debit, 8)
                trade = {
                    "day": str(day), "setup": item["signal"]["setup"],
                    "contracts": qty, "entry_at": at.isoformat(),
                    "score": item["score"], "risk_tier": evidence["tier"],
                    "risk_fraction": round(fraction, 4),
                    "confluence": evidence["confluence"],
                    "entry_debit_usd": debit,
                    "pnl_usd": None if exit_info is None
                        else round(qty*exit_info["pnl_usd"], 6),
                }
                if exit_info is None:
                    busy_until = cutoff
                    trade["exit_reason"] = "NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    state["pending"] += max(0., qty*debit + trade["pnl_usd"])
                    busy_until = exit_info["exit_at"]
                    trade["exit_reason"] = exit_info["exit_reason"]
                    state["exits"][exit_info["exit_reason"]] += 1
                state["trades"].append(trade)
            state["daily"].append({"day": str(day),
                                   "equity_usd": round(state["settled"]+state["pending"],4)})
        if len(dates) % 20 == 0:
            print("V5_IV_DELTA_PROGRESS "+json.dumps({"sessions":len(dates),"day":str(day)}), flush=True)

    if not dates:
        raise RuntimeError("No usable historical sessions")
    results = {}
    for key, s in states.items():
        middle = len(s["daily"]) // 2
        result = {
            **summary(s, starting_cash=starting_cash),
            **peak_to_trough_eod(s["daily"], starting_cash),
            "eligible_setup_quote_candidates": total_eligible[key],
            "first_half": half_metrics(s["daily"], 0, middle, starting_cash),
            "second_half": half_metrics(
                s["daily"], middle, len(s["daily"]),
                s["daily"][middle-1]["equity_usd"]),
            "max_hypothetical_contracts_in_one_trade":
                max([x["contracts"] for x in s["trades"]], default=0),
            "total_trades_recorded": len(s["trades"]),
        }
        results[key] = result
    # Fail rather than publish if the locked $867k reference does not reproduce.
    baseline = results["baseline_iv1_5_delta40_60"]
    assert abs(float(baseline["ending_equity_usd"])-867017.60) < .02, (
        "Historical $867017.60 baseline did not reproduce exactly: " +
        repr(baseline))
    assert len(dates) == 128, f"Expected 128 archived sessions, found {len(dates)}"
    return {
        "status": "IN_SAMPLE_CLOSE_ONLY_IV_DELTA_ABLATION_NO_EXECUTABLE_DEPTH",
        "first_day": min(dates), "last_day": max(dates),
        "sessions": len(dates), "starting_cash": starting_cash,
        "arms": ARM_CONFIGS, "results": results,
        "baseline_match": True,
        "changed_only": ["IV divided by realized volatility upper bound",
                         "absolute option delta selection bounds"],
        "preserved": ["three V5 close-proxy signal types","full-cash EOD drawdown throttle",
                      "35% hard stop","25% trailing stop","18-minute maximum hold",
                      "3% bid-ask spread","sampled option bid/ask",
                      "1 cent/share slippage and 65 cents/contract per side",
                      "next-session cash settlement","one position at a time"],
        "limitations": [
            "Previously repeatedly studied 128 sessions; no independent holdout",
            "Minute-sampled option CBBO is not executable NBBO and size is absent",
            "SPY close and volume_ratio proxies, not genuine full 1-minute OHLCV",
            "No market impact or live limit order fill validation",
            "Hypothetical contracts may vastly exceed actual displayed depth",
            "Max end-of-day drawdown understates unknown intraday drawdown",
            "Historical modeled performance is not a live profit forecast",
        ],
        "live_and_paper_unchanged": True,
        "data_purchased": False,
        "broker_orders": 0,
    }


def main():
    root = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    paths = _research_files(root)
    report = replay(paths)
    output = root / "v5_iv_delta_sweep_2026_10_28" / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    # One short log line per arm so Railway bounded logs can be read easily.
    print("V5_IV_DELTA_META "+json.dumps({k:report[k] for k in
        ("status","first_day","last_day","sessions","baseline_match")}),flush=True)
    for key, result in report["results"].items():
        print("V5_IV_DELTA_RESULT "+json.dumps({
            "arm":key,"params":ARM_CONFIGS[key],**result},sort_keys=True),flush=True)
    print("V5_IV_DELTA_REPORT_SAVED "+str(output), flush=True)
    # Restore the pre-existing research watch process after completing the
    # sweep. Does NOT touch live/paper workers or call broker.
    os.execvp("python", ["python","-m","engine.forward_watch"])


if __name__ == "__main__":
    main()
