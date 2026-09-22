# SPY-0DTE

Simulation-first autonomous SPY 0DTE research and execution platform.

## Build principle

The system is designed so that models estimate probabilities and market state, while deterministic risk mathematics controls whether capital may be deployed. Live trading is intentionally disabled until the research, backtest, paper-trading, and sandbox gates are passed.

## Cost principle: free first

The default runtime policy has **zero paid external services enabled**. Paid market-data or AI providers are optional adapters, not requirements. A paid service may only be enabled deliberately and within a configured monthly budget.

The free-first path is:

1. Use free SPY underlying history and broker/sandbox-delayed data where available.
2. Compute IV and common Greeks locally from ordinary option bid/ask quotes instead of paying for a Greeks feed.
3. Accumulate our own timestamped option snapshots for research and calibration.
4. Use delayed/sandbox feeds to validate execution, accounting, reconciliation, and Android/backend plumbing.
5. Treat premium historical options feeds as a later research accelerator only if measured out-of-sample improvement justifies the cost.

Free data cannot honestly reproduce every historical 1-minute NBBO for SPY options. Results produced from reconstructed/synthetic option data must therefore be labeled research estimates and cannot be used as proof of a live trading edge.

## Core decision pipeline

1. Forecast a probability distribution for SPY returns across multiple horizons.
2. Calibrate model probabilities and dynamically weight models by recent out-of-sample performance.
3. Reprice candidate 0DTE contracts across simulated future states.
4. Require positive lower-confidence-bound expected value after spread, slippage, fees, and uncertainty buffers.
5. Size approved trades with constrained fractional Kelly.
6. Enforce CVaR, drawdown, settled-cash, liquidity, stale-data, and out-of-distribution vetoes.
7. Manage exits from current forward expectancy rather than fixed emotional profit/loss targets.
8. Record live and shadow/counterfactual outcomes for later model evaluation.

## Safety state

**Current mode: SIMULATION ONLY**

No production broker credentials or live-order path should be added until the test suite, walk-forward validation, sandbox execution, reconciliation, and kill-switch requirements are complete.

## Implemented components

- `engine/decision.py` — hard-gate trade approval
- `engine/risk.py` — log-growth sizing, fractional Kelly, CVaR and drawdown controls
- `engine/market.py` — normalized SPY and option quote types
- `engine/ensemble.py` — reliability-weighted forecast aggregation
- `engine/scenario.py` — deterministic return/IV scenario generation
- `engine/opportunity.py` — option EV, uncertainty penalty, and candidate ranking
- `engine/broker.py` / `engine/webull.py` — sandbox execution boundary
- `engine/data.py` — provider-neutral historical quote ingestion
- `engine/backtest.py` — next-frame fills, modeled slippage/fees, and T+1 cash settlement
- `engine/metrics.py` — geometric growth, drawdown, profit factor, CVaR, and bootstrap ruin diagnostics
- `engine/walkforward.py` — purged chronological train/validation/test splits
- `engine/capabilities.py` — zero-cost default runtime policy and explicit paid-service opt-in
- `engine/greeks.py` — locally computed IV/delta/gamma/theta/vega from ordinary quotes
- `engine/providers/thetadata.py` — optional paid historical-options adapter; not required by the engine
- `tests/` — mathematical, execution, no-lookahead, settlement, cost-policy, provider, and safety invariants

## Optional premium providers

ThetaData remains available as an optional historical-options adapter for future research. It is **not enabled by default** and is not required to run the core engine, backtester, paper broker, or Android/backend stack.

Install it only if we later decide the incremental data quality is worth the cost:

```bash
pip install -e '.[thetadata]'
```

Secrets belong only in the backend runtime environment. Never commit provider or broker keys to GitHub or package them inside the Android app.

## Objective

Maximize out-of-sample expected logarithmic wealth growth subject to explicit tail-risk, drawdown, liquidity, execution, data-health, cash-settlement, and cost constraints. A positive model signal alone can never authorize a trade.
