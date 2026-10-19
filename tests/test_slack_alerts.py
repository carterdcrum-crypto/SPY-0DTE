"""No-network tests for isolated, durable Slack notification routing."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from engine.slack_alerts import SlackNotifier, valid_url

HOOKS = {
    "live": "https://hooks.slack.com/services/test/live/demo",
    "paper": "https://hooks.slack.com/services/test/paper/demo",
    "system": "https://hooks.slack.com/services/test/system/demo",
}


def prepare(tmp_path):
    paths = {key: str(tmp_path / (key + ".sqlite"))
             for key in ("state", "live", "paper", "audit")}
    with sqlite3.connect(paths["live"]) as db:
        db.execute("""CREATE TABLE live_journal(
            client_id TEXT, symbol TEXT, request TEXT, status TEXT,
            filled INTEGER, average_price REAL, created_at TEXT)""")
    with sqlite3.connect(paths["paper"]) as db:
        db.execute("""CREATE TABLE paper_trades(
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT,
            side TEXT, quantity INTEGER, fill_price REAL, realized_pnl REAL)""")
    with sqlite3.connect(paths["audit"]) as db:
        db.execute("""CREATE TABLE live_decisions(
            id INTEGER PRIMARY KEY, timestamp TEXT, state TEXT)""")
    return paths


def push_live(paths, *, status="UNKNOWN", old_seconds=30):
    at = (datetime.now(timezone.utc) - timedelta(seconds=old_seconds)).isoformat()
    order = {"account_id": "ACCOUNT_SHOULD_NOT_BE_IN_SLACK",
             "side": "BUY", "quantity": 1,
             "client_order_id": "order-test-123"}
    with sqlite3.connect(paths["live"]) as db:
        db.execute("""INSERT INTO live_journal VALUES(?,?,?,?,?,?,?)""",
                   ("order-test-123", "SPY261019C00770000",
                    json.dumps(order), status, int(status == "FILLED"),
                    .52 if status == "FILLED" else 0, at))


def push_paper(paths, *, side="BUY", trade_id=1, timestamp=None):
    at = timestamp or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(paths["paper"]) as db:
        db.execute("INSERT INTO paper_trades VALUES(?,?,?,?,?,?,?)",
                   (trade_id, at, "SPY261019C00770000",
                    side, 2, 45.3, 20 if side == "SELL" else 0))


def push_audit(paths, *, id=1, state="NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE"):
    with sqlite3.connect(paths["audit"]) as db:
        db.execute("INSERT INTO live_decisions VALUES(?,?,?)",
                   (id, datetime.now(timezone.utc).isoformat(), state))


def test_webhook_destinations_block_non_slack_hosts():
    assert valid_url(HOOKS["live"])
    assert not valid_url("http://hooks.slack.com/services/demo")
    assert not valid_url("https://hooks.slack.com.evil.example/services/demo")
    assert not valid_url("https://127.0.0.1/services/demo")
    assert not valid_url("https://hooks.slack.com/services/demo?secret=1")


def test_all_three_channels_routes_events_once_and_survives_restart(tmp_path):
    paths = prepare(tmp_path)
    sent = []
    notifier = SlackNotifier(
        paths=paths, webhooks=HOOKS,
        send=lambda url, message: sent.append((url, message)))
    push_live(paths, status="UNKNOWN")
    push_paper(paths)
    push_audit(paths)
    notifier.poll_once()
    assert len(sent) == 3
    assert any(url == HOOKS["live"] and "UNKNOWN" in msg for url, msg in sent)
    assert any(url == HOOKS["paper"] and "PAPER SIMULATED BUY" in msg for url, msg in sent)
    assert any(url == HOOKS["system"] and "REJECTION" in msg for url, msg in sent)
    assert all("ACCOUNT_SHOULD_NOT_BE_IN_SLACK" not in message for _, message in sent)
    notifier.poll_once()
    assert len(sent) == 3

    # Order becomes confirmed only after the durable broker journal reports a fill.
    with sqlite3.connect(paths["live"]) as db:
        db.execute("""UPDATE live_journal SET status='FILLED',
                      filled=1,average_price=.52 WHERE client_id='order-test-123'""")
    notifier.poll_once()
    assert len(sent) == 4
    assert "BROKER CONFIRMED FILL" in sent[-1][1]

    # An operator restart must not resend historical trading events.
    reloaded = SlackNotifier(
        paths=paths, webhooks=HOOKS,
        send=lambda url, message: sent.append((url, message)))
    reloaded.poll_once()
    assert len(sent) == 4
    # A later paper exit is independent of the LIVE order journal.
    push_paper(paths, side="SELL", trade_id=2)
    reloaded.poll_once()
    assert len(sent) == 5
    assert sent[-1][0] == HOOKS["paper"]
    assert "realized paper P&L" in sent[-1][1]


def test_first_start_does_not_broadcast_stale_trade_history(tmp_path):
    paths = prepare(tmp_path)
    push_live(paths, status="FILLED")
    push_paper(paths)
    push_audit(paths)
    sent = []
    n = SlackNotifier(paths=paths, webhooks=HOOKS,
                      send=lambda url, message: sent.append(message))
    n.poll_once()
    assert not sent


def test_one_slack_failure_does_not_interrupt_other_categories_and_retries(tmp_path):
    paths = prepare(tmp_path)
    delivered = []
    failed = set()
    def send(url, message):
        if url == HOOKS["live"] and not failed:
            failed.add(1)
            raise OSError("synthetic simulated network failure")
        delivered.append((url, message))

    n = SlackNotifier(paths=paths, webhooks=HOOKS, send=send)
    push_live(paths)
    push_paper(paths)
    n.poll_once()
    assert len(delivered) == 1
    assert delivered[0][0] == HOOKS["paper"]
    n.poll_once()
    assert len(delivered) == 2
    assert delivered[-1][0] == HOOKS["live"]


def test_two_rejections_in_same_fifteen_minutes_only_notify_once(tmp_path):
    paths = prepare(tmp_path)
    sent = []
    n = SlackNotifier(paths=paths, webhooks=HOOKS,
                      send=lambda url, message: sent.append((url,message)))
    push_audit(paths,id=1)
    push_audit(paths,id=2)
    n.poll_once()
    assert len(sent) == 1
    assert sent[0][0] == HOOKS["system"]
