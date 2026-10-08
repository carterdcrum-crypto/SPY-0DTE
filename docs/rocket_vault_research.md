# Rocket Vault: fast growth, then preserve the winnings (research-only)

## Goal

Study an aggressively compounded SPY 0DTE **Event Alpha** strategy that
switches automatically from growth to an untouchable-in-the-simulation
profit reserve. Compare the same event detector, 15-minute holding horizon,
quote snapshots, and 0/1/2 adverse ticks against the existing one-contract
baseline. This module does **not** change or enable the Webull live executor.

## Rules frozen before backtesting

- Below **2x starting equity**, risk up to 90% of *eligible settled cash* in
  a directional call entry, limited to five contracts and the same option
  selection filters used by Event Alpha. Positions are long premium only.
- Once realized flat account equity reaches **2x**, permanently reserve at
  least 65% of the gains above starting equity. As realized account highs
  increase, the locked share of profit smoothly rises toward 90%. The
  reserve never decreases after a drawdown.
- Trade budget is min(settled cash, total equity minus locked reserve), times
  the 90% exposure fraction. **Next-frame fill cost is capped by that budget.**
  A gap up in ask at execution results in **no trade**, rather than quietly
  spending reserve cash.
- Rachet only from **flat, realized** account equity, never from unrealized
  option marks. Preserved cash remains on the simulated account ledger but
  is not available to the strategy; it is *not* an actual broker cash
  withdrawal or government-insured segregated account.
- This is a **profit vault**, not an options straddle. Buying a call and a put
  does not insure the trading account: both can decay to zero. A true
  multi-leg hedging strategy requires its own costed backtest and portfolio
  execution engine. A risk cap cannot guarantee a broker-account equity floor
  after operational failures, external trades, and extreme market events.

## Research mode

Configure the isolated research Railway service (not the collector or live
execution service):

    RESEARCH_MODE=rocketvault
    RESEARCH_ROCKET_VAULT_BALANCES=300,1000
    RESEARCH_ROCKET_VAULT_HOLDS=15

It reuses the optional `RESEARCH_EVENT_ALPHA_START`,
`RESEARCH_EVENT_ALPHA_END`, and `RESEARCH_EVENT_ALPHA_TRADE_START` values.
The worker prints per-scenario ending equity, return, max drawdown, trade
count, win rate, locked reserve, realized peak, and daily geometric growth.

Do not extrapolate the existing +91% Event Alpha **development** backtest to
the multiple-contract Rocket Vault: a larger fraction of capital at risk
can amplify drawdowns and the strategy may skip entry if fills jump beyond
its budget. At small balances, 0DTE contract indivisibility can cause
either no exposure or a large fraction of wealth to be risked.

## Required validation before any live proposal

1. Run paired baseline vs. vault on the same **development** dates at
   starting balances of $300 and $1,000, with 0, 1 and 2 adverse ticks.
2. Freeze parameters and evaluate an untouched chronological holdout and
   at least several independent volatility regimes. Track ruin, weekly and
   daily max drawdown, lower-tail CVaR, reserve floor breaches, profit
   factor, share of days not traded, and execution gaps.
3. Stress realistic slippage, fees, non-fill probability, poor liquidity,
   option size at bid/ask, delayed quotes, and settlement.
4. Forward-test with timestamped decisions and broker-side paper fills.
   Do **not** promote this research strategy to live from one good backtest.
