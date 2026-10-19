"""Read-only Slack notifier for durable LIVE, PAPER and worker events.

Separate webhooks. Never submits, cancels, previews or modifies broker orders.
Trade alerts are at-least-once: a process crash after Slack accepts a message
and before the delivery checkpoint may duplicate an alert, never a trade.
"""
from __future__ import annotations
import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("spy0dte.slack")
VARS = {"live": "SLACK_LIVE_WEBHOOK_URL",
        "paper": "SLACK_PAPER_WEBHOOK_URL",
        "system": "SLACK_SYSTEM_WEBHOOK_URL"}
WARNINGS = {"LIVE_LOCKED", "RECOVERING", "ORDER_UNKNOWN",
            "POSITION_RECONCILIATION", "UNMANAGED_POSITION",
            "ACCOUNT_UNAVAILABLE", "EXIT_WAITING_QUOTE"}
REJECTIONS = {"NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE",
              "INSUFFICIENT_SETTLED_CASH"}


def valid_url(text):
    try:
        item = urllib.parse.urlsplit(text)
        return (item.scheme == "https" and
                item.netloc in ("hooks.slack.com", "hooks.slack-gov.com") and
                item.path.startswith("/services/") and
                not item.query and not item.fragment)
    except (TypeError, ValueError):
        return False


def post_slack(url, message):
    if not valid_url(url):
        raise ValueError("invalid Slack incoming webhook URL")
    request = urllib.request.Request(
        url, data=json.dumps({"text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200 or response.read(64).strip() != b"ok":
            raise RuntimeError("Slack did not acknowledge notification")


def _rows(path, sql):
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql)]


class SlackNotifier:
    def __init__(self, *, paths=None, webhooks=None, send=None, workers=None):
        self.paths = paths or {
            "state": os.environ.get("SLACK_ALERTS_DB_PATH", "/data/spy_slack_alerts.sqlite"),
            "live": os.environ.get("LIVE_AUTONOMY_DB_PATH", "/data/spy_live_autonomy.sqlite"),
            "paper": os.environ.get("PAPER_DB_PATH", "/data/spy_paper.sqlite"),
            "audit": os.environ.get("LIVE_DECISION_AUDIT_DB_PATH", "/data/spy_live_decisions.sqlite")}
        self.webhooks = {
            name: str(url).strip() for name, url in
            (webhooks if webhooks is not None
             else {name: os.environ.get(var, "") for name, var in VARS.items()}).items()
            if str(url).strip()}
        if any(name not in VARS or not valid_url(url)
               for name, url in self.webhooks.items()):
            raise ValueError("invalid or unrecognized Slack webhook")
        self.send = send or post_slack
        self.workers = workers
        Path(self.paths["state"]).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sent (
                    key TEXT PRIMARY KEY, delivered_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoint (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        self._seed()

    def _db(self):
        return sqlite3.connect(self.paths["state"], timeout=7)

    def _live(self):
        return _rows(self.paths["live"],
            "SELECT client_id,symbol,request,status,filled,average_price,created_at"
            " FROM live_journal ORDER BY created_at DESC LIMIT 500")

    def _paper(self):
        return _rows(self.paths["paper"],
            "SELECT id,timestamp,symbol,side,quantity,fill_price,realized_pnl"
            " FROM paper_trades ORDER BY id DESC LIMIT 500")

    def _audit(self):
        return _rows(self.paths["audit"],
            "SELECT id,timestamp,state FROM live_decisions ORDER BY id DESC LIMIT 1000")

    @staticmethod
    def _lk(row):
        return f"live:{row['client_id']}:{row['status']}:{row['filled']}"

    @staticmethod
    def _pk(row):
        return f"paper:{row['id']}:{row['timestamp']}:{row['side']}"

    @staticmethod
    def _insert(db, key):
        db.execute("INSERT OR IGNORE INTO sent VALUES(?,?)",
                   (key, datetime.now(timezone.utc).isoformat()))

    def _seed(self):
        # On first enable, do not present old historical trades as fresh.
        with self._db() as db:
            if db.execute("SELECT 1 FROM checkpoint WHERE name='seeded'").fetchone():
                return
            db.execute("BEGIN IMMEDIATE")
            for row in self._live():
                self._insert(db, self._lk(row))
            for row in self._paper():
                self._insert(db, self._pk(row))
            last = max([r["id"] for r in self._audit()], default=0)
            db.execute("INSERT OR REPLACE INTO checkpoint VALUES('audit',?)",
                       (str(last),))
            db.execute("INSERT INTO checkpoint VALUES('seeded','1')")

    def _emit(self, destination, key, message):
        url = self.webhooks.get(destination)
        if not url:
            return False
        # The outgoing request only locks this separate notification DB.
        # No broker, paper, decision or quote database transaction is held.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone():
                return False
            self.send(url, message)
            self._insert(db, key)
        return True

    def live(self):
        for row in reversed(self._live()):
            status = str(row["status"]).upper()
            try:
                request = json.loads(row["request"])
                side, count = request["side"], int(request["quantity"])
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(row["created_at"])).total_seconds()
                if side not in ("BUY", "SELL") or count < 1:
                    continue
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if status == "UNKNOWN":
                if age < 20:
                    continue
                title = "ORDER OUTCOME UNKNOWN — NO BROKER FILL CONFIRMED"
            elif status in ("PENDING", "SUBMITTED"):
                title = "WORKING ORDER — NO FILL CONFIRMED"
            elif status == "PARTIAL_FILLED":
                title = "BROKER CONFIRMED PARTIAL FILL"
            elif status == "FILLED":
                title = "BROKER CONFIRMED FILL"
            elif status in ("CANCELLED", "FAILED"):
                title = "ORDER " + status
            else:
                continue
            symbol = str(row["symbol"])[:32]
            filled = int(row["filled"])
            price = float(row["average_price"])
            message = (f"*LIVE {title}*\n"
                       f"SPY option {symbol} | {side} | requested {count} | filled {filled}")
            if filled > 0 and price > 0:
                message += f" | avg USD {price:.2f}/share"
            self._emit("live", self._lk(row), message)

    def paper(self):
        for row in reversed(self._paper()):
            if row["side"] not in ("BUY", "SELL"):
                continue
            message = (f"*PAPER SIMULATED {row['side']} — NO BROKER ORDER*\n"
                       f"SPY option {str(row['symbol'])[:32]} | "
                       f"contracts {int(row['quantity'])} | "
                       f"modeled USD {float(row['fill_price']):.2f} per contract")
            if row["side"] == "SELL":
                message += f" | realized paper P&L USD {float(row['realized_pnl']):+.2f}"
            self._emit("paper", self._pk(row), message)

    def decisions(self):
        with self._db() as db:
            row = db.execute("SELECT value FROM checkpoint WHERE name='audit'").fetchone()
        cursor = int(row[0]) if row else 0
        for decision in sorted((r for r in self._audit() if int(r["id"]) > cursor),
                               key=lambda x: x["id"]):
            state = str(decision["state"]).upper()
            if state in WARNINGS | REJECTIONS:
                try:
                    time_bucket = int(datetime.fromisoformat(
                        decision["timestamp"]).timestamp() // 900)
                except (TypeError, ValueError):
                    time_bucket = int(time.time() // 900)
                title = "SIGNAL/QUOTE REJECTION" if state in REJECTIONS else "LIVE ENGINE WARNING"
                message = (f"*{title}*\nState {state}\n"
                           "See Railway logs and Positions > Entry Checks for details.")
                self._emit("system", f"system:decision:{state}:{time_bucket}", message)
            with self._db() as db:
                db.execute("""INSERT INTO checkpoint(name,value) VALUES('audit',?)
                              ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                           (str(decision["id"]),))

    def worker_faults(self):
        if not self.workers:
            return
        for name, worker in self.workers().get("workers", {}).items():
            if name not in ("live", "paper", "collector"):
                continue
            state = str(worker.get("state") or "")
            if state not in ("RECOVERING", "STALLED"):
                continue
            error_type = str(worker.get("last_error") or "unknown").split(":", 1)[0][:35]
            key = f"system:worker:{name}:{state}:{error_type}:{int(time.time()//900)}"
            self._emit("system", key,
                f"*TRADING WORKER FAULT*\nWorker: {name} | state: {state}"
                f" | exception type: {error_type}")

    def poll_once(self):
        for scan in (self.live, self.paper, self.decisions, self.worker_faults):
            try:
                scan()
            except Exception as exc:
                # HTTP errors may embed the secret URL; never log exception body.
                log.warning("Slack notification retry error_type=%s", type(exc).__name__)


def run_forever(context):
    from .runtime import runtime_status
    notifier = SlackNotifier(workers=runtime_status)
    log.info("Slack notifier started channels=%s", ",".join(sorted(notifier.webhooks)))
    while not context.stop.is_set():
        notifier.poll_once()
        context.heartbeat()
        context.wait(5)
