# Confirmed live execution readiness

The production path is designed so the strategy can reuse the same broker-neutral `OptionOrderRequest` objects used by PAPER while production-specific controls remain isolated.

## Production preparation flow

1. Reconcile the brokerage account, balances, positions, and current-session orders.
2. Require an active cash account.
3. Apply user-configured hard ceilings from `LIVE_MAX_CONTRACTS` and `LIVE_MAX_ORDER_DEBIT`.
4. Preview the exact order with Tradier.
5. Require the exact confirmation phrase `CONFIRM THIS LIVE ORDER` from an authenticated owner session.
6. Issue a short-lived ticket bound to the authenticated user and the exact order fingerprint.
7. Atomically consume the ticket before a production submit attempt. Tickets cannot be reused or applied to a changed symbol, side, quantity, strike, expiration, or limit price.
8. After a submit attempt, reconcile with the broker before any retry. A network timeout must be treated as an unknown order state rather than permission to submit again.

## Required production configuration

`TRADIER_ACCESS_TOKEN` and `TRADIER_ACCOUNT_ID` are supplied as deployment secrets and are never stored in source control.

The live safety shell also requires:

- `LIVE_MAX_CONTRACTS`
- `LIVE_MAX_ORDER_DEBIT`
- optional `LIVE_CONFIRMATION_TTL_SECONDS` (5-120 seconds, default 45)
- optional `LIVE_TICKET_DB_PATH` (otherwise stored next to the control database)

## Isolation rule

The autonomous PAPER runner must not call a production broker transmission method directly. The production path remains confirmation-bound even though market analysis, strategy, risk sizing, order construction, and dynamic-exit candidate generation can share the same code as PAPER.
