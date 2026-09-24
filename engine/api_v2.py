from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import api as base
from .paper_account import PaperAccountStore
from .paper_autotrader import automation_status
from .paper_dynamic_autotrader import run_forever as run_paper_autotrader


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


def _paper_db_path() -> str:
    explicit = os.environ.get("PAPER_DB_PATH", "").strip()
    if explicit:
        return explicit

    control_path = os.environ.get("CONTROL_DB_PATH", "").strip()
    if control_path:
        return str(Path(control_path).with_name("spy_paper.sqlite"))
    return "/data/spy_paper.sqlite"


def _paper_store() -> PaperAccountStore:
    return PaperAccountStore(
        _paper_db_path(),
        default_starting_cash=_default_paper_cash(),
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_original_status_payload = base.status_payload
_original_lifespan = base.app.router.lifespan_context


def status_payload() -> dict[str, Any]:
    payload = _original_status_payload()
    paper = _paper_store().snapshot().as_dict()
    automation = automation_status()
    payload["paper"] = paper
    payload["paper_automation"] = automation

    decision = payload.get("decision")
    if isinstance(decision, dict):
        state = str(automation.get("state") or "STOPPED")
        decision["state"] = state
        decision["reason"] = str(
            automation.get("reason")
            or "autonomous paper decision loop is starting"
        )
    return payload


base.status_payload = status_payload


@asynccontextmanager
async def lifespan(app):
    if _env_bool("START_PAPER_AUTOTRADER", False):
        thread = threading.Thread(
            target=run_paper_autotrader,
            args=(lambda: base._control_store().get_mode().value,),
            name="spy0dte-paper-autotrader",
            daemon=True,
        )
        thread.start()

    async with _original_lifespan(app):
        yield


base.app.router.lifespan_context = lifespan
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


@app.get("/v1/paper/automation")
def paper_automation(_: base.UserIdentity = Depends(base.require_user)) -> dict[str, Any]:
    return automation_status()


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
