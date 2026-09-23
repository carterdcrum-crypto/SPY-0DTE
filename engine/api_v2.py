from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import api as base
from .paper_account import PaperAccountStore


class PaperResetRequest(BaseModel):
    starting_cash: float = Field(gt=0, le=1_000_000)
    confirmation: str


def _default_paper_cash() -> float:
    raw = os.environ.get("PAPER_STARTING_CASH", "1000").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("PAPER_STARTING_CASH must be numeric") from exc
    if value <= 0:
        raise RuntimeError("PAPER_STARTING_CASH must be positive")
    return value


def _paper_store() -> PaperAccountStore:
    return PaperAccountStore(
        os.environ.get("PAPER_DB_PATH", "/data/spy_paper.sqlite"),
        default_starting_cash=_default_paper_cash(),
    )


_original_status_payload = base.status_payload


def status_payload() -> dict[str, Any]:
    payload = _original_status_payload()
    paper = _paper_store().snapshot().as_dict()
    payload["paper"] = paper

    decision = payload.get("decision")
    if isinstance(decision, dict) and decision.get("state") == "RESEARCH_ONLY":
        decision["reason"] = (
            "paper account and market feed are online; automated candidate-to-paper-order "
            "execution remains intentionally disabled until the live decision signal is wired"
        )
    return payload


# Existing routes in engine.api resolve status_payload from that module at call time,
# so replacing the module global enriches both /v1/status and /v1/live without
# duplicating authentication or websocket code.
base.status_payload = status_payload
app = base.app


@app.get("/v1/paper/account")
def paper_account(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return _paper_store().snapshot().as_dict()


@app.get("/v1/paper/positions")
def paper_positions(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return {"positions": _paper_store().positions()}


@app.get("/v1/paper/trades")
def paper_trades(
    limit: int = Query(default=20, ge=1, le=100),
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    return {"trades": _paper_store().recent_trades(limit)}


@app.post("/v1/paper/reset")
def reset_paper_account(
    request: PaperResetRequest,
    _: base.UserIdentity = Depends(base.require_user),
) -> dict[str, Any]:
    control = base._control_store()
    if control.get_mode() is base.TradingMode.LIVE:
        raise HTTPException(status_code=409, detail="paper account cannot be reset while LIVE mode is selected")
    if request.confirmation != "RESET PAPER ACCOUNT":
        raise HTTPException(status_code=400, detail="explicit paper-account reset confirmation required")
    return _paper_store().reset(request.starting_cash).as_dict()
