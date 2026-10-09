# SPY — score direction AND movement size (joint probability experiment)

**Research-only, $0 in purchased market data, adaptive trade filtering remains OFF.**

## Question and hypothesis

The prior 13/7 confirmed continuation research found that many predictions
were directionally right but moved SPY only a fraction of the way needed
to overcome **hypothetical underlying** costs. The next relevant question
is therefore not just "up or down?", but **how likely is a move in the
correct direction larger than 2bps or 5bps?**

These are **NOT real SPY 0DTE option break-even thresholds**. Option bid/ask
spreads, contract sizing, gamma, theta, volatility and brokerage fees are
unknown, so this study never simulates option trades or account P&L.

## Frozen model (no parameter optimization)

Start with the identical 15-prior-**close** SPY breakout opportunities
and the three-minute causal confirmation protocol used in previous
research. At the end of signal minute T+3, use only data known then:

- Was the previously frozen three-minute continuation rule satisfied?
- Does the causal rolling thirty-completed-minute volatility estimate
  predict a ten-minute **absolute SPY price move >=5 basis points**?

Those two facts define **four** immutable feature groups. Every opportunity
has the same hypothetical SPY **entry proxy** of the **next completed
one-minute close (T+4)** and exit after another ten complete minutes
(T+14). The outcome is a *signed SPY underlying price change* in bps,
**not an executable price or option trade**.

Before scoring session D, use **only the previous 60 complete trading
sessions**, with an initial warm-up of at least 40 completed sessions
and at least 100 historical signal events. This is a rolling
**observed past-session outcome forecast**, not a trade-admission
adaptive signal filter. All data, including earlier losing events and
events the old filters would have skipped, count as historical evidence.
No labels from D are used until the next complete session begins.

For each forecast emit joint/ordered binary probabilities of:
1. Signed SPY move **>0 bps** (direction right).
2. Signed SPY move **>2 bps** in the chosen direction.
3. Signed SPY move **>5 bps** in the chosen direction.
4. Signed SPY move **<−5 bps** (material adverse direction).

The controls are:
- **Lagged unconditional base rate** of all prior events, with a fixed
  Beta(1,1) smoothing prior.
- **Lagged two-feature conditional frequency**, shrunk toward the
  matching-date unconditional baseline using **40 pseudo-observations**.
  No coefficients are tuned to the archive; tiny continuation groups
  are automatically heavily shrunk toward the baseline.

All predictions occur before the current-session outcome. The model
is NEVER connected to a live executor or trade selection.

## Exact predeclared evaluation

Brier score (mean squared forecast probability error; **lower is better**)
separately for each of the four outcomes, unconditional vs conditional
on the **same session/timestamp/opportunity**. Report each period's
event count and actual outcome incidence, and **conditional minus baseline
Brier difference**. An improvement is a **negative** delta.

Use a 1,000-replicate seeded paired **circular 5-trading-session**
block bootstrap of **daily mean score differences**, including
zero-event and warmup days as zero. These intervals are descriptive
and cannot make this pre-inspected dataset into an independent holdout.

Report fixed ten probability bins (0.0–0.1, …, 0.9–1.0) with each
group's average forecast vs observed incidence, explicitly flagging
cells with fewer than twenty events. Store complete per-event
predictions, prior training counts, prior sub-group counts and
AFTER-THE-FACT realized outcome labels. Never use these realized
labels to filter the same-day model.

Chronological diagnostic partitions match earlier research:
Apr–Jul 2026 development; Aug 3–Sep 4 validation; Sep 8–Oct 6
**previously studied diagnostic**. ALL dates have been examined before,
so none is a truly untouched holdout. The model is NOT eligible for
automatic options trading based on these results.

**Predeclared go/no-go:** the conditional model should improve Brier
for both >2 and >5 basis-point signed targets across at least the
Aug–Sep and Sep–Oct historical partitions, with supportive paired
uncertainty, before investing more in this direction. Even then
this is only a SPY underlying forecast calibration improvement; real
0DTE net P&L requires timestamped options quotes, fees and liquidity,
plus future untouched sample testing.

## Reproduce without buying data

```bash
python -m pytest -q tests/test_joint_probability_audit.py
python -m engine.joint_probability_audit \
  --data-dir /data/research \
  --output-dir /data/research/joint_probability
```

Isolated Railway research-only service `spy-0dte-research`:
`RESEARCH_MODE=jointprobability`. The existing licensed historic
volume persists:
- `joint_probability/summary.json`
- `joint_probability/predictions_AND_future_outcome_labels_NOT_OPTION_TRADES.csv`
- `joint_probability/fixed_probability_bins.csv`

No data purchases, no Webull API or broker order, no Auto Trade toggle
change; research hosting still consumes existing Railway resources.
