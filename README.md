# SPY-0DTE

Simulation-first autonomous SPY 0DTE research and execution platform.

## Build principle

The system is designed so that models estimate probabilities and market state, while deterministic risk mathematics controls whether capital may be deployed. Live trading is intentionally disabled until the research, backtest, paper-trading, and sandbox gates are passed.

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

## Initial components

- `engine/decision.py` — hard-gate trade approval
- `engine/risk.py` — log-growth sizing, fractional Kelly, CVaR and drawdown controls
- `engine/models.py` — typed market/model/trade inputs
- `engine/paper_broker.py` — deterministic paper fills and account state
- `tests/` — mathematical and safety invariants

## Objective

Maximize out-of-sample expected logarithmic wealth growth subject to explicit tail-risk, drawdown, liquidity, execution, data-health, and cash-settlement constraints. A positive model signal alone can never authorize a trade.
