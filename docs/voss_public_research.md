# Voss Trading (@officialvosstrading) — Separate Public-Principles Research

**Channel:** https://www.youtube.com/@officialvosstrading
**Public education site:** https://vosstradingvip.com/
**Public free community:** https://whop.com/vte/vtefree/

## Attribution and verification honesty

The user specified the YouTube handle **@officialvosstrading**. This research
is grounded in public Voss Trading Education materials linked to that
handle. The official public site (accessed Oct 11–12, 2026) describes
these broad teaching priorities:

1. Start with **market context**, identify support/resistance levels,
   and understand whether price is trending or ranging.
2. Wait for **price confirmation** at important levels, rather than
   reacting impulsively to the first crossing.
3. Define **trade invalidation/risk** before entry.
4. Review trades to understand the decision process.

The site's training excerpt is behind an email form, and substantial VIP
lessons are not freely readable. Automated YouTube access provided the
channel location, **not individual playable, transcribed videos or
Voss's exact mechanical entry/exit formulas**. We cannot honestly
claim any specific numerical formula, indicator or performance
statistic belongs to Voss, nor that we watched specific videos whose
contents were unavailable.

**What follows is a NEW, objectively testable interpretation of those
PUBLIC principles — not a proprietary Voss strategy reproduction, not
an endorsement, and not a claim that he uses these parameters.**

## Independent hypothesis family

Study is isolated on branch `research/voss-price-structure-20261012`.
No merges or mixture with the prior Regime Reclaim research.
Predeclared before running historical test:

| Arm | Entry concept |
|---|---|
| `voss_level_break_retest` | Map 20 prior completed SPY CLOSES for a multi-touch level; confirm a closing break, a separately completed retest within ±3.5bp of frozen reference, then separate direction-confirmation close >2bp away within 8 bars. |
| `voss_support_resistance_rejection` | Two or more touches separated by 3 bars of a historical 20-close support/resistance ZONE, prior completed close near zone, current completed close rejects away by >2bp. |
| `voss_confirmed_trend_pullback` | Prior 15-close directional trend >8bp, prior close near/beyond the 5-close mean, current close reclaims it in trend direction by >2bp. |
| `voss_structure_combo` | Single-position chronological stream accepting the three setups independently. |
| `voss_structure_combo_option_cost_gate` | Same stream, but only option-entry quote spread <=3.5% of midpoint and option IV / contemporaneous realized-vol proxy <=1.5. |

**Data limitation:** Archived SPY records contain 1-minute CLOSE and
relative-volume proxy, not independently timestamped complete OHLCV,
high/low, true VWAP or order-book data. Accordingly, these
support/resistance levels are CLOSE RANGE PROXIES; the strategy does
not pretend they represent true candle highs/lows or exact
futures/stock market structure. Earlier than about 09:55 ET cannot
qualify because of required completed price history.

**Options and exits:** Select approximately 50-delta 0DTE SPY
contracts using modeled 1m quote selection. Entire position exits
on the previous frozen **30% bid-liquidation trailing** or separate
**35% original premium loss**, or mandatory **10-minute** hold.
NO profit-taking target / partial sale / runner.
One position at a time, $1,000 hypothetical settled cash, T+1
cash settlement, no same-day sale proceeds reused.

Historical options CBBO **minute bucket** bid/ask observations
do not establish actual event-observed NBBO, fill timestamps,
intraminute stop executions or realistic purchaseability.

## Outcome and next-validation requirements

The run report is at
`research/results/voss_public_structure_2026_10_12.json`
and `docs/voss_public_structure_backtest_2026_10_12.md` if
the experiment completes.

Whatever appears profitable in this 128-session archive is a
**previously inspected, multiple-tested hypothesis only**.
It is NOT a verified historical Voss strategy and cannot justify
live trading. Prospective unviewed market days, actual completed
OHLCV and as-of option event quotes must independently validate
direction, option net expectancy, cash-account drawdown and
the 30% stop before any promotion. **No purchases or orders.**

## Reproduction

```bash
python -m pytest -q tests/test_voss_public_structure_research.py
python -m engine.voss_public_structure_research \
  --data-dir /data/research \
  --output-dir /data/research/voss_public_structure
```
