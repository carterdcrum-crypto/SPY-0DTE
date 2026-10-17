from engine.live_decision_audit import record_decision, recent_decisions


def test_audit_persists_blocked_entry_reason_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DECISION_AUDIT_DB_PATH", str(tmp_path/"audit.sqlite"))
    record_decision({
        "state": "NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE",
        "reason": "no fresh bid and depth",
        "strategy": "v5_close_only_unrestricted_research",
        "data_blocker": None,
        "market": {"source": "webull_production",
                   "data_age_seconds": 1.0, "feed_delay_seconds": .3},
        "account": {"account_id": "never persist", "secret": "sensitive"},
    })
    rows=recent_decisions()
    assert len(rows)==1
    assert rows[0]["state"]=="NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE"
    assert rows[0]["reason"]=="no fresh bid and depth"
    assert rows[0]["quote_source"]=="webull_production"
    assert "account_id" not in rows[0]
    assert rows[0]["signal_detected"] == 0


def test_audit_records_signal_progression(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DECISION_AUDIT_DB_PATH", str(tmp_path/"audit.sqlite"))
    for state in ("SIGNAL", "ORDER_SUBMITTED", "POSITION_OPEN", "EXIT_WAITING_QUOTE"):
        record_decision({"state":state,"reason":"test","strategy":"v5_close_only_unrestricted_research"})
    assert [row["state"] for row in recent_decisions()] == [
        "EXIT_WAITING_QUOTE", "POSITION_OPEN", "ORDER_SUBMITTED", "SIGNAL"]
