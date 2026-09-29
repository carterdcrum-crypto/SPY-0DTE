# SPY-0DTE

Simulation-first SPY 0DTE research, paper-trading, and guarded execution platform with an Android control app.

## Current state

The system has three distinct operating layers:

- **SHADOW** — runs the strategy and records decisions without placing trades.
- **PAPER** — runs the autonomous strategy against the local paper ledger, including dynamic sizing and exits.
- **LIVE** — connects the same qualified strategy path to the production Webull boundary, but only behind owner authentication, a cash-account check, fresh-data requirements, a daily risk envelope, broker reconciliation, and explicit order authorization.

The production Webull adapter is implemented, but **autonomous production order submission is not enabled in the current code**. Live entries still use the guarded prepare/confirm workflow. This distinction is intentional and should remain visible in both the API status and Android UI until the live autonomy path has complete entry, exit, idempotency, and reconciliation coverage.

## Build principle

Models estimate probability, direction, market state, and forward expectancy. Deterministic execution and risk controls decide whether capital may be deployed. A model signal by itself can never bypass cash, exposure, stale-data, broker-state, or daily-stop checks.

## Cost principle: free first

The default runtime policy keeps paid external services optional. Paid market-data or AI providers are adapters, not hard requirements, and should only be enabled deliberately when measured out-of-sample improvement justifies the cost.

The free-first path is:

1. Use free SPY underlying history and broker/delayed data where available.
2. Compute IV and common Greeks locally from ordinary option bid/ask quotes instead of paying for a Greeks feed.
3. Accumulate timestamped option snapshots for research and calibration.
4. Use delayed/sandbox data to validate execution, accounting, reconciliation, and Android/backend plumbing.
5. Treat premium historical options feeds as a research accelerator only when they produce measurable improvement.

Free data cannot honestly reproduce every historical 1-minute NBBO for SPY options. Results produced from reconstructed or synthetic option data must therefore be labeled research estimates and cannot be treated as proof of a live trading edge.

## Core decision pipeline

1. Forecast a probability distribution for SPY returns across multiple horizons.
2. Calibrate model probabilities and dynamically weight models by recent out-of-sample performance.
3. Blend configured AI consensus into the quantitative forecast within bounded weights.
4. Reprice candidate 0DTE contracts across simulated future states.
5. Require positive lower-confidence-bound expected value after spread, slippage, fees, and uncertainty buffers.
6. Size approved trades with dynamic risk logic while enforcing cash and exposure constraints.
7. Enforce CVaR, drawdown, settled-cash, liquidity, stale-data, broker-state, and out-of-distribution vetoes.
8. Manage paper exits from current forward expectancy rather than fixed emotional profit/loss targets.
9. Record live, paper, shadow, and counterfactual outcomes for later model evaluation.

## Live boundary

The current Webull live path includes:

- Google owner authentication.
- Production Webull credential/configuration checks.
- Cash-account enforcement.
- Fresh market-data and timestamp-quality gates.
- Daily live loss, gain, exposure, and contract ceilings.
- Broker balance, open-position, and pending-order reconciliation.
- Exact SPY option construction from a qualified strategy alert.
- One-time order authorization and duplicate/uncertain-submit reconciliation.
- A manual confirmation boundary before a production order is sent.

The next live-autonomy milestone is to replace the per-order confirmation boundary only after automatic live exits, durable signal idempotency, restart recovery, and broker reconciliation are covered end-to-end.

## Implemented components

- `engine/decision.py` — trade approval gates.
- `engine/risk.py` — log-growth sizing, fractional Kelly, CVaR, and drawdown controls.
- `engine/market.py` — normalized SPY and option quote types.
- `engine/ensemble.py` — reliability-weighted forecast aggregation.
- `engine/scenario.py` — deterministic return/IV scenario generation.
- `engine/opportunity.py` — option EV, uncertainty penalty, and candidate ranking.
- `engine/paper_ai_autotrader.py` — AI-augmented autonomous paper/shadow strategy loop.
- `engine/paper_dynamic_exit.py` — forward-expectancy-driven paper exit logic.
- `engine/live_risk.py` — daily live risk envelope.
- `engine/live_alert_policy.py` — converts qualified strategy output into bounded live alerts.
- `engine/webull_live.py` — production Webull account, guard, preview, placement, and reconciliation adapter.
- `engine/webull_live_api.py` — authenticated live Webull API boundary.
- `engine/backtest.py` — next-frame fills, modeled slippage/fees, and cash settlement.
- `engine/metrics.py` — geometric growth, drawdown, profit factor, CVaR, and bootstrap ruin diagnostics.
- `engine/walkforward.py` — purged chronological train/validation/test splits.
- `android/` — Android Glass control app.
- `tests/` — mathematical, execution, no-lookahead, settlement, provider, and safety invariants.

## Optional premium providers

ThetaData remains available as an optional historical-options adapter for research. It is not required to run the core engine, backtester, paper broker, or Android/backend stack.

```bash
pip install -e '.[thetadata]'
```

Secrets belong only in the backend runtime environment. Never commit provider or broker keys to GitHub or package them inside the Android app.

## Objective

Maximize out-of-sample expected logarithmic wealth growth subject to explicit tail-risk, drawdown, liquidity, execution, data-health, cash-settlement, and cost constraints. A positive model signal alone never authorizes a trade.
