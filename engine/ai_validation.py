from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

from .ablation import block_bootstrap_ablation
from .paper_autotrader import EASTERN
from .paper_dynamic_autotrader import _env_float


class AIValidationStore:
    """Persistent paired forecast ledger for quant/AI/hybrid comparisons.

    A forecast is written at market time T and can only be resolved by a later
    market snapshot at or after T + horizon. That makes every Brier-score pair
    genuinely out-of-sample with respect to the recorded snapshot.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_validation_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_cycle TEXT NOT NULL UNIQUE,
                    cycle_epoch REAL NOT NULL,
                    target_epoch REAL NOT NULL,
                    horizon_seconds REAL NOT NULL,
                    observed_spot REAL NOT NULL,
                    quant_probability_up REAL NOT NULL,
                    ai_probability_up REAL NOT NULL,
                    hybrid_probability_up REAL NOT NULL,
                    effective_ai_weight REAL NOT NULL,
                    ai_confidence REAL NOT NULL,
                    ai_disagreement REAL NOT NULL,
                    quant_action TEXT NOT NULL,
                    old_veto_action TEXT NOT NULL,
                    hybrid_action TEXT NOT NULL,
                    providers TEXT NOT NULL,
                    outcome_spot REAL,
                    outcome_up REAL,
                    resolved_market_cycle TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_validation_due
                ON ai_validation_samples(resolved_market_cycle, target_epoch)
                """
            )

    def record(
        self,
        *,
        market_cycle: datetime,
        horizon_seconds: float,
        spot: float,
        quant_probability_up: float,
        ai_probability_up: float,
        hybrid_probability_up: float,
        effective_ai_weight: float,
        ai_confidence: float,
        ai_disagreement: float,
        quant_action: str,
        old_veto_action: str,
        hybrid_action: str,
        providers: tuple[str, ...],
    ) -> bool:
        cycle = market_cycle.astimezone(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO ai_validation_samples (
                    market_cycle,
                    cycle_epoch,
                    target_epoch,
                    horizon_seconds,
                    observed_spot,
                    quant_probability_up,
                    ai_probability_up,
                    hybrid_probability_up,
                    effective_ai_weight,
                    ai_confidence,
                    ai_disagreement,
                    quant_action,
                    old_veto_action,
                    hybrid_action,
                    providers
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle.isoformat(),
                    cycle.timestamp(),
                    cycle.timestamp() + max(1.0, float(horizon_seconds)),
                    max(1.0, float(horizon_seconds)),
                    float(spot),
                    float(quant_probability_up),
                    float(ai_probability_up),
                    float(hybrid_probability_up),
                    float(effective_ai_weight),
                    float(ai_confidence),
                    float(ai_disagreement),
                    str(quant_action),
                    str(old_veto_action),
                    str(hybrid_action),
                    ",".join(sorted(set(providers))),
                ),
            )
            return cursor.rowcount > 0

    def resolve_due(self, *, market_cycle: datetime, spot: float) -> int:
        cycle = market_cycle.astimezone(timezone.utc)
        with self._connect() as connection:
            due = connection.execute(
                """
                SELECT id, observed_spot
                FROM ai_validation_samples
                WHERE resolved_market_cycle IS NULL
                  AND target_epoch <= ?
                ORDER BY target_epoch, id
                """,
                (cycle.timestamp(),),
            ).fetchall()
            for row in due:
                observed_spot = float(row["observed_spot"])
                outcome_up = (
                    1.0
                    if float(spot) > observed_spot
                    else 0.0
                    if float(spot) < observed_spot
                    else 0.5
                )
                connection.execute(
                    """
                    UPDATE ai_validation_samples
                    SET outcome_spot = ?,
                        outcome_up = ?,
                        resolved_market_cycle = ?
                    WHERE id = ? AND resolved_market_cycle IS NULL
                    """,
                    (float(spot), outcome_up, cycle.isoformat(), int(row["id"])),
                )
            return len(due)

    def _resolved_rows(self) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM ai_validation_samples
                WHERE resolved_market_cycle IS NOT NULL
                ORDER BY cycle_epoch, id
                """
            ).fetchall()
        return tuple(rows)

    @staticmethod
    def _action_precision(rows: tuple[sqlite3.Row, ...], field: str) -> tuple[int, float | None]:
        active = [
            row
            for row in rows
            if str(row[field]) in {"call", "put"}
            and float(row["outcome_up"]) != 0.5
        ]
        if not active:
            return 0, None
        correct = sum(
            1
            for row in active
            if (
                (str(row[field]) == "call" and float(row["outcome_up"]) == 1.0)
                or (str(row[field]) == "put" and float(row["outcome_up"]) == 0.0)
            )
        )
        return len(active), correct / len(active)

    def summary(self) -> dict[str, object]:
        rows = self._resolved_rows()
        with self._connect() as connection:
            unresolved = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM ai_validation_samples
                    WHERE resolved_market_cycle IS NULL
                    """
                ).fetchone()["count"]
            )
        if not rows:
            return {
                "resolved_samples": 0,
                "unresolved_samples": unresolved,
                "quant_brier": None,
                "ai_brier": None,
                "hybrid_brier": None,
                "hybrid_brier_improvement_vs_quant": None,
                "quant_signals": 0,
                "quant_signal_precision": None,
                "old_veto_signals": 0,
                "old_veto_signal_precision": None,
                "hybrid_signals": 0,
                "hybrid_signal_precision": None,
            }

        outcomes = [float(row["outcome_up"]) for row in rows]
        quant_scores = [
            (float(row["quant_probability_up"]) - outcome) ** 2
            for row, outcome in zip(rows, outcomes)
        ]
        ai_scores = [
            (float(row["ai_probability_up"]) - outcome) ** 2
            for row, outcome in zip(rows, outcomes)
        ]
        hybrid_scores = [
            (float(row["hybrid_probability_up"]) - outcome) ** 2
            for row, outcome in zip(rows, outcomes)
        ]
        quant_count, quant_precision = self._action_precision(rows, "quant_action")
        veto_count, veto_precision = self._action_precision(rows, "old_veto_action")
        hybrid_count, hybrid_precision = self._action_precision(rows, "hybrid_action")
        quant_brier = fmean(quant_scores)
        hybrid_brier = fmean(hybrid_scores)
        return {
            "resolved_samples": len(rows),
            "unresolved_samples": unresolved,
            "quant_brier": quant_brier,
            "ai_brier": fmean(ai_scores),
            "hybrid_brier": hybrid_brier,
            "hybrid_brier_improvement_vs_quant": quant_brier - hybrid_brier,
            "quant_signals": quant_count,
            "quant_signal_precision": quant_precision,
            "old_veto_signals": veto_count,
            "old_veto_signal_precision": veto_precision,
            "hybrid_signals": hybrid_count,
            "hybrid_signal_precision": hybrid_precision,
        }

    def paired_ablation(self) -> dict[str, object] | None:
        rows = self._resolved_rows()
        if len(rows) < 5:
            return None
        quant_scores = tuple(
            (float(row["quant_probability_up"]) - float(row["outcome_up"])) ** 2
            for row in rows
        )
        hybrid_scores = tuple(
            (float(row["hybrid_probability_up"]) - float(row["outcome_up"])) ** 2
            for row in rows
        )
        result = block_bootstrap_ablation(
            "ai_hybrid_probability",
            hybrid_scores,
            quant_scores,
            block_size=min(5, len(rows)),
            repetitions=500,
            seed=17,
        )
        return {
            "feature_group": result.feature_group,
            "hybrid_brier": result.full_score,
            "quant_brier": result.ablated_score,
            "delta_quant_minus_hybrid": result.delta,
            "lower_95": result.lower_ci,
            "upper_95": result.upper_ci,
            "samples": result.samples,
            "interpretation": "positive delta means the hybrid forecast scored better",
        }


class AIValidationMixin:
    """Continuously A/B the current hybrid against quant-only and old AI veto.

    Historical LLM outputs were not persisted before the hybrid rollout, so
    retroactively manufacturing them would not be a clean backtest. This ledger
    starts a paired no-lookahead forward replay using the exact AI outputs the
    engine actually saw at each market snapshot.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ai_validation_store = AIValidationStore(self.account_store.path)
        self._ai_validation_last_blend_id: int | None = None
        self._ai_validation_cached_ablation: dict[str, object] | None = None
        self._ai_validation_ablation_samples = -1

    @staticmethod
    def _action(probability_up: float, threshold: float) -> str:
        if probability_up >= threshold:
            return "call"
        if probability_up <= 1.0 - threshold:
            return "put"
        return "flat"

    def _update_ai_validation(self) -> dict[str, object]:
        cycles = self.market_reader.latest_cycles(1)
        if not cycles or cycles[0].spot <= 0.0:
            summary = self._ai_validation_store.summary()
            return {
                **summary,
                "method": "paired_forward_no_lookahead",
                "historical_ai_archive_available": False,
                "paired_block_bootstrap": self._ai_validation_cached_ablation,
            }

        latest = cycles[0]
        self._ai_validation_store.resolve_due(
            market_cycle=latest.received_at,
            spot=latest.spot,
        )

        blend = getattr(self, "_last_decision_blend", None)
        advice = getattr(self, "_last_decision_consensus", None)
        blend_id = None if blend is None else id(blend)
        if (
            blend is not None
            and advice is not None
            and bool(advice.signals)
            and blend_id != self._ai_validation_last_blend_id
        ):
            threshold = _env_float(
                "PAPER_ENSEMBLE_DIRECTION_THRESHOLD",
                0.55,
                minimum=0.50,
                maximum=0.80,
            )
            minimum_confidence = _env_float(
                "PAPER_AI_MIN_CONFIDENCE",
                0.45,
                minimum=0.0,
                maximum=1.0,
            )
            veto_threshold = _env_float(
                "PAPER_AI_VETO_DIRECTION_SUPPORT",
                0.38,
                minimum=0.05,
                maximum=0.49,
            )
            horizon_minutes = min(
                _env_float(
                    "PAPER_ENSEMBLE_HORIZON_MINUTES",
                    5.0,
                    minimum=0.5,
                    maximum=30.0,
                ),
                max(
                    0.5,
                    self._minutes_to_close(latest.received_at.astimezone(EASTERN)) - 1.0,
                ),
            )
            quant_action = self._action(blend.quant_probability_up, threshold)
            hybrid_action = self._action(blend.forecast.probability_up, threshold)
            old_veto_action = quant_action
            if quant_action != "flat" and advice.confidence >= minimum_confidence:
                support = (
                    advice.probability_up
                    if quant_action == "call"
                    else 1.0 - advice.probability_up
                )
                if support < veto_threshold:
                    old_veto_action = "flat"

            self._ai_validation_store.record(
                market_cycle=latest.received_at,
                horizon_seconds=horizon_minutes * 60.0,
                spot=latest.spot,
                quant_probability_up=blend.quant_probability_up,
                ai_probability_up=blend.ai_probability_up,
                hybrid_probability_up=blend.forecast.probability_up,
                effective_ai_weight=blend.effective_weight,
                ai_confidence=advice.confidence,
                ai_disagreement=advice.disagreement,
                quant_action=quant_action,
                old_veto_action=old_veto_action,
                hybrid_action=hybrid_action,
                providers=tuple(signal.provider for signal in advice.signals),
            )
            self._ai_validation_last_blend_id = blend_id

        summary = self._ai_validation_store.summary()
        resolved = int(summary["resolved_samples"])
        if resolved >= 5 and resolved != self._ai_validation_ablation_samples:
            self._ai_validation_cached_ablation = self._ai_validation_store.paired_ablation()
            self._ai_validation_ablation_samples = resolved

        return {
            **summary,
            "method": "paired_forward_no_lookahead",
            "historical_ai_archive_available": False,
            "paired_block_bootstrap": self._ai_validation_cached_ablation,
        }

    def _finish(self, now: datetime, values: dict[str, object]) -> dict[str, object]:
        return super()._finish(
            now,
            {
                **values,
                "ai_validation": self._update_ai_validation(),
            },
        )
