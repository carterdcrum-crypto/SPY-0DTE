# V5 unrestricted LIVE/PAPER: discretionary limits removed (2026-10-14)

User-selected `LIVE_STRATEGY_PROFILE=v5_close_only_unrestricted_research`
and `PAPER_STRATEGY_PROFILE=v5_close_only_unrestricted_research`
enable the close-only V5 candidate. The selected V5 **LIVE profile**
now ignores and does not require any owner-entered:

- Maximum contracts, fixed per-position account exposure percentage;
- Daily realized-loss stop, daily gain/profit cap, daily manual limit rearming;
- Fixed take-profit ceiling (V5 already trails profitable positions).

**No arbitrary V5 contract maximum**, but orders are never allowed to
exceed the current broker's verified settled cash/buying power, *as-of*
displayed bid/ask size or current ask plus an adverse fee cushion.
A 35% stop and 25% trailing stop remain the V5 strategy's algorithmic
exit triggers, **not guaranteed premium loss caps**.
Underlying reversal and the 18-minute maximum holding period also remain.
V5 can now potentially lose **the entire settled account balance**
in a single options trade despite the stop triggers.

**Owner control remains required:** authenticated enable/disable Auto
Trade toggle, broker authorization, account pinning, reconciliation,
idempotent write-ahead journal, no short options/no margin, fresh real
options quotes/underlying bars and expiry cutoff remain intact. This
change does NOT turn on Auto Trade automatically, purchase data, or
guarantee live orders. Previously observed Webull source delay was ~915s
(15 minutes); quote age >3s blocks new orders. Webull may reject large
orders/insufficient displayed depth; no synthetic liquidity is supplied.

Original **V4 3x** continues to require and enforce persistent owner
limits; after switching to V4, complete limits are required before
any new original V4 entries. Existing positions exit according to the
original strategy of their entry, regardless of profile switching.

**Account simulation warning:** the prior $507,706.50 archival result
assumed up to 7,350 option contracts with no verified depth or price
impact and saw 92.93% maximum EOD drawdown. It is not an executable
prediction. V5 research has not undergone real-time forward validation.
