from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class TradeCandidate:
    """A single long-premium SPY 0DTE candidate evaluated by the risk engine.

    scenario_returns are decimal returns on premium paid (for example, -1.0 means
    a total premium loss and +0.50 means a 50% gain). scenario_probabilities must
    align one-for-one and sum to a positive value; normalization is handled by
    the risk module.
    """

    option_symbol: str
    entry_cost_per_contract: float
    scenario_returns: Tuple[float, ...]
    scenario_probabilities: Tuple[float, ...]
    lower_confidence_edge: float
    model_confidence: float
    calibration_score: float
    agreement_score: float
    regime_match_score: float
    liquidity_score: float
    spread_fraction: float
    data_age_seconds: float
    ood_score: float


@dataclass(frozen=True)
class RiskState:
    account_equity: float
    high_watermark: float
    settled_cash: float
    daily_start_equity: float
    daily_realized_pnl: float

    @property
    def drawdown_fraction(self) -> float:
        if self.high_watermark <= 0:
            return 1.0
        return max(0.0, 1.0 - self.account_equity / self.high_watermark)

    @property
    def daily_loss_fraction(self) -> float:
        if self.daily_start_equity <= 0:
            return 1.0
        return max(0.0, -self.daily_realized_pnl / self.daily_start_equity)


@dataclass(frozen=True)
class DecisionConfig:
    # Hard vetoes
    minimum_lower_confidence_edge: float = 0.01
    minimum_liquidity_score: float = 0.60
    maximum_spread_fraction: float = 0.08
    maximum_data_age_seconds: float = 3.0
    maximum_ood_score: float = 0.35
    maximum_drawdown_fraction: float = 0.10
    maximum_daily_loss_fraction: float = 0.03

    # Capital controls
    kelly_safety_fraction: float = 0.25
    maximum_position_fraction: float = 0.02
    maximum_cvar_fraction_of_equity: float = 0.01
    cvar_confidence_level: float = 0.99
    minimum_trade_fraction: float = 0.0


@dataclass(frozen=True)
class DecisionResult:
    allowed: bool
    reason: str
    contracts: int = 0
    capital_to_deploy: float = 0.0
    account_fraction: float = 0.0
    raw_kelly_fraction: float = 0.0
    cvar_dollars: float = 0.0
