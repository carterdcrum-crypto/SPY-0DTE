<p align="center">
  <img src="docs/brand/spy-0dte-chart-pulse.webp" alt="SPY 0DTE Chart Pulse logo" width="180" />
</p>

# SPY-0DTE

Simulation-first SPY 0DTE research, paper-trading, and guarded execution platform with an Android control app.

## Current state

The system has three distinct operating layers:

- **SHADOW** — runs the strategy and records decisions without placing trades.
- **PAPER** — runs the autonomous strategy against the local paper ledger, including dynamic sizing and exits.
- **LIVE** — uses a separate production quote reader and broker-confirmed ledger. The Auto trade switch authorizes autonomous entries and exits within standing owner limits.

Autonomous Webull entry, exit, reconciliation, and restart recovery are implemented. Deployment defaults keep real-money execution disabled until owner authentication, broker prerequisites, live gates, and the Auto trade switch are configured.

## Unattended operation

PAPER arming is persistent: Railway runs the strategy with the phone closed,
waits through closed sessions, and resumes on the next supported session. Stopping
new paper entries keeps management of existing paper positions enabled.

- Collector and paper workers restart after failures with bounded backoff. A
  120-second heartbeat watchdog requests a process restart for a stuck worker;
  it never launches a second trading thread alongside the stuck one. Railway's
  configured restart policy handles that process exit (currently five retries).
- A filesystem lock prevents two runner processes sharing the same paper database
  from executing concurrently. Cash/position writes are transactional, and restart
  recovery reads the existing ledger rather than resetting the account.
- AI advice expires after 120 seconds by default, or earlier when its market
  date, data mode, horizon or spot context becomes incompatible. Failed providers
  back off independently; stalled calls are bounded and late answers are discarded.
  The quantitative strategy continues under the same account and risk checks.
- Both realtime and delayed paper modes use forward AI validation and the latest
  hold-versus-sell exit policy. Delayed simulation stays explicitly labeled.
- Known NYSE holidays and early closes for 2026–2028 control collection, entries
  and time-to-close exits. The engine uses the earlier core-session close, even
  where eligible options trade later. Unknown calendar years block new entries.
  Emergency exchange closures require a calendar update or
  `MARKET_EXTRA_CLOSED_DATES`; a static calendar cannot predict unscheduled halts.
- `/health` reflects worker health; authenticated `/v1/status` reports heartbeat,
  restart counts and next session. Android reconnects automatically and marks a
  disconnected display offline. No new paid service is required by these changes.

Paper reports completed exits as `trade_count`, preserving the latest app's
completed-trade counter. `execution_count` counts buys plus sells, regardless of
contract quantity, with separate `buy_count` and `sell_count` fields. The app
labels executions explicitly. Realized P&L updates on sales; open-position P&L is
unrealized. Account snapshots read cash, P&L and counters in one transaction.

## Auto trade

The Glass Android dashboard has an iOS-style **Auto trade** switch. Turning it on
stores standing owner authorization and selects LIVE. Railway chooses qualified
entries, submits orders, reconciles actual fills, and manages exits without
per-order confirmation or an open phone. Turning it off cancels pending buys and
closes only positions opened by this automation when a fresh executable bid is
available. Switching to PAPER or SHADOW also turns Auto trade off.

Authorization and the owner-selected limits persist across sessions and restarts.
The algorithm dynamically chooses contracts, quantities, and exits within those
limits. Daily gain/loss stops latch for the rest of the Eastern trading date and
reset for the next session. The live risk profile is stricter than paper: it does
not force a one-contract trade when fractional Kelly rounds down to zero.

- A durable SQLite journal records a unique client order ID **before** submission.
  Broker-reported cumulative fills are applied idempotently. Partial entries are
  cancelled before their filled quantity can be closed. Exits are repriced only
  after cancellation is confirmed, using the remaining owned quantity.
- An uncertain submission is reconciled by its original ID and never blindly
  reposted. Existing external positions/orders block entries. Inventory mismatches
  pause execution rather than guessing ownership or selling extra contracts.
- Live quotes come from an independent Webull production reader with real option
  and underlying timestamps. Delayed paper snapshots and shifted simulation clocks
  cannot reach the live executor. Held contracts retain a quote slot.
- Live sizes use explicit broker **settled cash**, equity, remaining daily loss
  allowance, full premium plus a fee cushion, and the exposure/contract ceilings.
  Total cash and generic buying power cannot substitute for missing settled cash.
- One supervised writer owns the account journal. Deploy one replica with a
  persistent `/data` volume. The local file lock protects processes sharing that
  volume; it is not a distributed lock for independent volumes.
- The app displays live worker decisions separately from paper results. Opening
  more screens does not trigger additional broker polling. Credentials are
  encrypted server-side and never returned to the phone.

A missing API response, broker reauthorization, unreconciled external trade, or
expired contract can require owner attention. Automation cannot guarantee a fill
or loss ceiling during a trading halt, illiquid market, or service outage. These
are server-managed limit exits, not exchange-held protective stops. Paper results
and simulated-broker tests do not establish live profitability.

### One-time live setup

1. Configure Google owner authentication: Railway `GOOGLE_WEB_CLIENT_ID` and
   `APP_ALLOWED_EMAILS`; the same public OAuth client ID goes in the GitHub
   repository variable `GOOGLE_WEB_CLIENT_ID` used by the Android build.
2. Use Webull production OpenAPI credentials with the needed account/option and
   realtime-data permissions. Enter the **app key and app secret** in Settings, or
   set `WEBULL_LIVE_APP_KEY` and `WEBULL_LIVE_APP_SECRET` on Railway. Sandbox keys
   remain separate. Legacy `WEBULL_APP_KEY/SECRET` are accepted for live only when
   `WEBULL_ENVIRONMENT=production` is explicit.
3. Set `APP_PREVIEW_MODE=false`, `LIVE_BROKER=webull`,
   `LIVE_ORDER_EXECUTOR_READY=true`, and `ALLOW_LIVE_ORDERS=true` after deployment
   verification. Shipping the code does not change these switches or activate
   real trading. Keep the persistent data volume and encrypted-key backup.
4. Sign in, save live limits, then turn **Auto trade** on. There are no buy/sell
   confirmation dialogs and no daily re-arm action. A single accessible cash
   account is discovered and pinned automatically; multiple accounts require
   `WEBULL_ACCOUNT_ID`. The app shows any unmet prerequisite.

The API key alone does not grant broker permissions, realtime options access, or
owner authentication. No credentials are included in the repository or APK.
The paper T+1 model currently uses weekdays; live settlement comes from Webull.
Market-history collection pauses below 16 MiB of free disk space to preserve
room for execution journals, and resumes when capacity is restored. It does not
delete research history or reset account records; monitor and expand the volume.

Execution interface references:
[Webull order detail](https://developer.webull.com/apis/docs/reference/order-detail/),
[account positions](https://developer.webull.com/apis/docs/reference/account-position/),
[option snapshots](https://developer.webull.com/apis/docs/reference/option-snapshot/).
The adapter is pinned to Webull Python SDK 3.0.2; verification uses fake broker
responses, never real orders.

Calendar source: [NYSE holidays and trading hours](https://www.nyse.com/markets/hours-calendars),
verified September 26, 2026.

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

**Deployment default: live execution disabled.**

The live executor is implemented, but activation requires the configured owner, production broker access, explicit deployment gates, and the Auto trade switch. Automated tests exercise failure recovery using a fake broker.


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

Secrets belong only in the backend environment or encrypted credential store. Never commit provider or broker keys to GitHub or package them inside the Android app.

## Objective

Maximize out-of-sample expected logarithmic wealth growth subject to explicit tail-risk, drawdown, liquidity, execution, data-health, cash-settlement, and cost constraints. A positive model signal alone never authorizes a trade.
