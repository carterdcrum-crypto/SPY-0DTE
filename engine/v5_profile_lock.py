"""Immutable runtime strategy selection for this SPY 0DTE production app.

The $867,017.60 archived replay uses V5 close-only, full-cash sizing
with EOD drawdown throttling (100/70/40/15%), not an unlimited leverage
policy. A mismatch disables NEW entries instead of selecting V4.
Existing positions are still managed by their entry-era exit rules.
"""
from __future__ import annotations

import os

from .v5_close_only_operational import PROFILE as REQUIRED_PROFILE


def selected_profile(variable: str) -> str:
    if variable not in {"LIVE_STRATEGY_PROFILE", "PAPER_STRATEGY_PROFILE"}:
        raise ValueError("unsupported strategy profile setting")
    # Missing config always means the sole supported V5 strategy, never V4.
    return os.environ.get(variable, REQUIRED_PROFILE).strip().lower()


def profile_is_locked(variable: str) -> bool:
    return selected_profile(variable) == REQUIRED_PROFILE
