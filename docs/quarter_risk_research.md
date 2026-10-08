# Quarter Allocation / Weekly Doubling Research

**Objective (aspirational, not guaranteed):** Evaluate whether historical SPY 0DTE long-option trades can deliver approximately +100% in a five-session week **while containing maximum drawdown**.

## Executable constraints

- New-order premium debit (including modeled slippage/fees): **no more than 25% of account equity at signal time**.
- Total premium **actually paid** to open trades during the same trading day: **no more than 100% of account equity at that session's start**, even if positions close. Gross spent, not merely simultaneous outstanding risk.
- No fractional option contracts; a $300 account has only $75 to spend per entry, so qualifying SPY contracts may be unavailable. No order executes if the next-frame option ask breaks the cap.
- Cash-account settlement honored: sale proceeds are not reused until the next business day. No short options, intraday cash recycling, leverage, or margin.
- Continue locked realized-profit reserve from Adaptive Vault, deducting it from spendable equity.
- One position at a time; each subsequent trade re-evaluates the actual equity, remaining daily gross budget, and liquidity.

## Explicit variants

- `plain/call`: original bullish-breakout call.
- `plain/put`: buy a **real quoted PUT** at the same bullish-breakout event, optimizing closeness to 0.45 absolute delta rather than synthetically negating a call trade.
- `guarded/call` and `guarded/put`: same entries but trigger an exit when the **observed bid** falls 30% below the fill price. Pause new entries on flat equity if realized losses exceed 10% of opening-day equity or 15% of beginning-of-week equity. These are **triggers, not enforceable maximum losses**: orders execute only on the next market frame and may gap.
- All use a profit trailing exit and scale down with realized drawdowns from the preceding Adaptive Vault.

## Validation

Run `RESEARCH_MODE=quarterrisk`. Environment variables:

- `RESEARCH_QUARTER_BALANCES=300,1000`
- `RESEARCH_QUARTER_SIDES=call,put`
- `RESEARCH_QUARTER_POLICIES=plain,guarded`
- `RESEARCH_QUARTER_TICKS=1` (optionally `0,1,2` to stress bid/ask execution)
- `RESEARCH_QUARTER_START=2026-09-01`, `RESEARCH_QUARTER_TRADE_START=2026-09-09`, `RESEARCH_QUARTER_END=2026-10-06`.

The 20-session window has **already been examined** in developing Event Alpha; it is *not* untouched validation data. Historical dataset covers April 1–October 6, 2026 (128 sessions), but older days are also partially used during strategy development. An independent future-paper validation is necessary.

Report return, raw maximum drawdown, gross premium, worst single-day spend fraction, five-session-week returns, fraction of weeks doubled, fill count, entry freezes, stop triggers, skipped unaffordable signals, and how frequently held options had missing quotes. The original backtest currently marks held options at $0 when the option quote is absent, which can artificially increase *intraday* drawdown; never interpret raw max-DD as accurate without diagnosing those missing marks. Cash-account profit reserve in a simulation is **not a segregated broker account**.

### Interpretation

A 25% per-trade premium cap does **not** imply a 25% daily loss cap. Four full-premium losers can consume the entire day's starting capital, and a 30% premium-stop signal cannot guarantee the exit price. **+100% every week with low drawdown cannot be promised**, even if a short backtest achieves it. Avoid optimizing parameters on the last 20 trading sessions then calling the resulting success out-of-sample.


## 20-session development replay, October 8, 2026

Range: September 9–October 6, 2026, with September 1–8 historical warmup.
All runs start with cash accounts, execute on next-minute actual option bid/ask
plus one adverse tick on each side, and assume $0 in modeled transaction fees.

| Policy | Option side | $300 end | $300 raw max DD | $1,000 end | $1,000 raw max DD |
| --- | --- | ---: | ---: | ---: | ---: |
| plain | call | $334 (+11.3%) | 42.2% | $675 (-32.5%) | 44.3% |
| plain | put | $247 (-17.7%) | 45.6% | $881 (-11.9%) | 38.3% |
| guarded | call | $335 (+11.7%) | 26.9% | $731 (-26.9%) | 33.0% |
| guarded | put | $260 (-13.3%) | 40.8% | $917 (-8.3%) | 33.9% |

**Zero of the four nonoverlapping five-session blocks returned +100%** in any
of these eight studied combinations. Adding stops improved drawdown on this
sample but did not validate a strategy meeting both goals. Drawdown is based
on the full raw mark-to-market equity curve; quote gaps in held positions
occurred 0–1 times in these scenarios, and can create temporary valuation
distortions. Stops do not guarantee fills.

A third **optional and as-yet-unvalidated** experimental policy,
`QuarterRiskConfig(guard_enabled=True, lifetime_drawdown_lock_enabled=True)`,
permanently suspends new orders after an observed 15% portfolio drawdown
from the realized equity peak and requests closing an open position. An
exit executes on the next quote; this is not a strict 15% loss guarantee.
It passed unit tests but has **not** produced a completed historical run.
The live/paper account configuration is unaffected.

Given all eight initial weekly failure rates, do not enable this model for
autonomous real-money SPY 0DTE trading. The unresolved bottleneck is credible
directional edge and realized option P&L net of fills, *not* position sizing.
