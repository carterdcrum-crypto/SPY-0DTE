from __future__ import annotations

from typing import Any

from .live_broker_guard import LiveBrokerGuard
from .live_risk import LiveRiskEnvelope


def build_live_entry_alert(
    automation: dict[str, Any],
    envelope: LiveRiskEnvelope,
    guard: LiveBrokerGuard,
) -> dict[str, Any] | None:
    """Translate a qualified SHADOW signal into a bounded live review alert.

    The alert is intentionally not an order and this function has no broker
    write capability. Contract count is reduced to the owner's contract ceiling
    and current account-exposure budget before anything is shown to the user.
    """

    if not envelope.armed_today() or not guard.entry_allowed:
        return None
    if str(automation.get("state") or "") != "SHADOW_SIGNAL":
        return None

    signal = automation.get("last_signal")
    risk = automation.get("risk")
    if not isinstance(signal, dict) or not isinstance(risk, dict):
        return None

    try:
        model_contracts = max(0, int(risk.get("contracts") or 0))
        contract_cost = float(signal.get("contract_cost") or 0.0)
    except (TypeError, ValueError):
        return None
    if model_contracts < 1 or contract_cost <= 0.0:
        return None

    contract_cap = envelope.max_contracts or 0
    exposure_contracts = 0
    if guard.max_entry_debit is not None:
        exposure_contracts = int(max(0.0, guard.max_entry_debit) // contract_cost)
    final_contracts = min(model_contracts, contract_cap, exposure_contracts)
    if final_contracts < 1:
        return None

    bounded_risk = {
        **risk,
        "model_contracts": model_contracts,
        "contracts": final_contracts,
        "daily_contract_ceiling": contract_cap,
        "max_entry_debit": guard.max_entry_debit,
        "bounded_entry_debit": round(contract_cost * final_contracts, 2),
    }
    return {
        "kind": "ENTRY_SIGNAL",
        "generated_at": automation.get("last_tick"),
        "strategy": automation.get("strategy"),
        "reason": automation.get("reason"),
        "signal": signal,
        "risk": bounded_risk,
        "broker_guard": guard.as_dict(),
        "action_required": True,
        "broker_submitted": False,
    }
