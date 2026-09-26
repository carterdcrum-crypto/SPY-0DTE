from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import api as base
from . import webull_live_api


_original_live_gate = base._live_gate


def _preview_identity(_: str | None) -> base.UserIdentity:
    """Allow login-free paper-preview access without enabling live trading."""
    return base.UserIdentity(subject="preview", email="preview@local")


def _preview_live_gate(store: base.ControlStore) -> dict[str, Any]:
    gate = _original_live_gate(store)
    reasons = ["preview_mode_live_locked", *list(gate.get("reasons", []))]
    return {**gate, "ready": False, "reasons": list(dict.fromkeys(reasons))}


def _block_credential_writes():
    raise HTTPException(
        status_code=403,
        detail="broker credential storage is disabled while login-free preview mode is enabled",
    )


# Preview keeps the full Railway/Webull status surface visible but broker writes
# still require a real authenticated owner in webull_live_api.
base._verify_google_bearer = _preview_identity
base._live_gate = _preview_live_gate
base._cipher = _block_credential_writes

app = webull_live_api.app
