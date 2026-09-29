# SPY 0DTE backtesting protocol

The goal is not to find the prettiest historical equity curve. The goal is to estimate whether the strategy has a repeatable edge that survives unseen market periods, execution costs, and nearby parameter choices.

## 1. Use real chronological SPY 0DTE data

A production-quality 0DTE backtest needs timestamped SPY and same-day option bid/ask history. Dense rows from one session do **not** substitute for many independent trading days.

Before model selection, call `require_trading_day_coverage(...)` and record the dataset fingerprint from `dataset_fingerprint(...)`.

Research targets:

- Do not treat one or a handful of sessions as evidence of an edge.
- Prefer at least several months of distinct trading days before serious model comparison.
- Prefer a full year or more when practical so the sample includes different volatility, trend, gap, and event environments.
- Keep collecting our own timestamped snapshots even after external historical data is added.

Reconstructed or synthetic option prices may be useful for plumbing tests, but they are not proof of a tradable 0DTE edge.

## 2. Never random-shuffle time-series data

All splits are chronological. The existing backtester also enforces next-frame execution: a decision made with frame T can first execute on frame T+1.

Use:

1. **TRAIN** — fit models or estimate parameters.
2. **VALIDATION** — choose among a small, pre-declared candidate set.
3. **PURGE / EMBARGO** — leave a gap around split boundaries so overlapping labels and holding periods do not leak.
4. **TEST** — measure the selected candidate without using that result to choose the candidate.
5. Repeat through rolling walk-forward folds.

`walk_forward_candidate_selection(...)` implements this nested selection pattern.

## 3. Lock a final holdout before tuning

Reserve the newest block of history before tuning begins with `locked_holdout_partition(...)`.

Do all iteration on `partition.development`. Do not inspect or optimize against `partition.locked_holdout`. After candidate selection is frozen, call `evaluate_locked_holdout(...)` once for the final check.

If the strategy is changed because of the holdout result, that holdout has become development data. A newer untouched period is then required for another final test.

## 4. Limit parameter fishing

Every extra threshold, model, feature, or parameter combination is another chance to discover luck.

For each experiment:

- Declare the candidate list before reading the validation results.
- Keep the candidate grid deliberately small.
- Record every candidate tried, including failures.
- Prefer broad parameter plateaus over sharp optima.
- Use `selection_tolerance` so a simpler candidate wins when its validation score is effectively tied with a more complex one.
- Do not repeatedly narrow the grid around the best historical point until the curve looks perfect.

The candidate's `complexity` field exists specifically to prefer simpler models in near-ties.

## 5. Select on a risk-aware objective, not raw return

`conservative_selection_score(...)` requires a minimum number of completed trades and scores compounded growth after penalties for drawdown and left-tail loss.

A candidate that produces a giant return from a tiny number of trades should not automatically beat a steadier candidate with much more evidence.

Always report at least:

- completed trades,
- total return,
- geometric growth per trade,
- win rate,
- profit factor,
- maximum drawdown,
- 95% trade-return CVaR,
- worst walk-forward test return,
- median walk-forward test return,
- selection frequency across folds.

## 6. Model execution conservatively

Do not assume midpoint fills.

The backtester currently:

- executes one frame after the signal,
- buys at the next ask plus modeled slippage,
- sells at the next bid minus modeled slippage,
- charges per-contract fees,
- respects a maximum contract count,
- treats sale proceeds as unsettled until the next business day.

Before trusting an edge, repeat the evaluation under worse cost assumptions. A strategy that disappears with modestly worse spread/slippage assumptions is fragile.

## 7. Separate historical quant validation from AI validation

The quantitative market/option logic can be replayed on historical tape.

Do **not** backtest today's general-purpose LLM on old dates and call the result historical AI performance. A modern model can contain information learned after the historical date, which creates a subtle form of future leakage.

The AI overlay should be validated primarily with timestamped forward predictions recorded before outcomes are known. Historical AI testing is only valid when the model, prompt inputs, news/context, and information cutoff are all demonstrably restricted to what was available at that historical moment.

## 8. Require regime stability

A useful strategy should not rely on one market personality. Track results by pre-defined, contemporaneously knowable conditions such as:

- volatility level,
- opening gap size,
- intraday trend strength,
- liquidity/spread state,
- time of day,
- scheduled-event vs. ordinary session.

Do not define a regime after seeing which trades won. Regime labels and thresholds must be frozen before evaluating the test/holdout set.

## 9. Paper trading remains an independent forward test

Even a clean historical backtest can miss queue position, data latency, broker behavior, model latency, and live spread changes.

The autonomous paper account therefore remains a separate forward-validation layer. Historical backtest, walk-forward testing, locked holdout, and forward paper trading should agree before historical performance is treated as meaningful evidence.

## 10. Promotion rule

No single metric promotes a model. A candidate should only move toward live use when:

- it survives multiple chronological out-of-sample folds,
- the final locked holdout does not materially contradict development results,
- performance is not concentrated in one small regime,
- nearby reasonable parameter choices behave similarly,
- conservative execution costs do not erase the edge,
- trade count is large enough to make the statistics meaningful,
- forward paper behavior is consistent with the backtest assumptions.

Backtest results are evidence, not a guarantee of future profitability.
