# V4 is research-only. The already-inspected trailing year remains development data.
BACKTEST_START = (2025, 10, 1)
BACKTEST_END = (2026, 9, 30)

# Keep signal research separate from the $100 deployability question.
RESEARCH_CASH = 10000.0
DEPLOY_CASH = 100.0

# Align the forecast target with the maximum intended holding window.
FORECAST_HORIZON_MINUTES = 15
SCORING_INTERVAL_MINUTES = 5

# Quant remains the controlling model. The learned spread-outcome challenger
# can earn weight only from already-resolved candidate outcomes.
LEARNED_PRIOR = 0.10
LEARNED_MIN = 0.00
LEARNED_MAX = 0.20
LEARNED_WARMUP = 250
LEARNED_MAX_STEP = 0.002
EVIDENCE_WINDOW = 500
EVIDENCE_Z = 1.64

# Direction-expert adaptation stays causal and regime-aware.
EXPERT_ETA = 0.05
EXPERT_SHARE = 0.03
EXPERT_MIN_WEIGHT = 0.05
EXPERT_MAX_WEIGHT = 0.60
REGIME_BLEND = 0.70
MAX_EXPERT_DISAGREEMENT = 0.18

# Candidate-learning / spread-EV research.
TRAIN_CANDIDATES_PER_SIDE = 2
MAX_PENDING_SAMPLE_MINUTES = 20
PAYOFF_EWMA_ALPHA = 0.03
PAYOFF_PRIOR_WIN_RETURN = 0.50
PAYOFF_PRIOR_LOSS_RETURN = 0.35
MIN_EXPECTED_VALUE_DOLLARS = 0.0

# Keep V3 execution rails fixed so the architecture, rather than parameter
# mining, is what changes in this experiment.
MAX_LEG_RELATIVE_SPREAD = 0.10
MAX_MONEYNESS_FRACTION = 0.02
MAX_SPREAD_WIDTH = 2.0
MIN_REWARD_RISK = 1.25
COOLDOWN_MINUTES = 5
MAX_HOLD_MINUTES = 15
STOP_LOSS_FRACTION = 0.35
TAKE_PROFIT_FRACTION = 0.50
FEE_PER_LEG_EACH_SIDE = 0.0

RESEARCH_PREFERRED_RISK_FRACTION = 0.025
RESEARCH_HARD_RISK_FRACTION = 0.05
DEPLOY_PREFERRED_RISK_FRACTION = 0.15
DEPLOY_HARD_RISK_FRACTION = 0.20

FIRST_SIGNAL_HOUR = 9
FIRST_SIGNAL_MINUTE = 45
LAST_SIGNAL_HOUR = 15
LAST_SIGNAL_MINUTE = 35
FORCE_FLAT_HOUR = 15
FORCE_FLAT_MINUTE = 45
