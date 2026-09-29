"""Supervise long-lived workers without ever starting a second copy of a stuck one."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger("spy0dte.runtime")


class WorkerContext:
    def __init__(self, *, stall_seconds: float = 120.0) -> None:
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.stall_seconds = stall_seconds
        self._deadline = time.monotonic() + stall_seconds
        self._status: dict[str, object] = {
            "state": "STARTING", "last_heartbeat": None, "last_error": None,
            "restarts": 0, "consecutive_failures": 0,
        }

    def heartbeat(self, *, state: str = "RUNNING", **details: object) -> None:
        with self._lock:
            self._deadline = time.monotonic() + self.stall_seconds
            self._status.update(details, state=state, last_heartbeat=datetime.now(timezone.utc).isoformat())

    def wait(self, seconds: float) -> bool:
        # Scheduled idle/backoff is expected, not a hung worker.
        with self._lock:
            self._deadline = time.monotonic() + max(0.0, seconds) + self.stall_seconds
        return self.stop.wait(seconds)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            status = dict(self._status)
            stalled = not self.stop.is_set() and time.monotonic() > self._deadline
        if stalled:
            status["state"] = "STALLED"
        status["healthy"] = status["state"] in {"RUNNING", "IDLE"} and not self.stop.is_set()
        return status


class SupervisedWorker:
    def __init__(
        self, name: str, target: Callable[[WorkerContext], None], *,
        retry_seconds: float = 1.0, maximum_backoff: float = 60.0,
        stall_seconds: float = 120.0, fatal_exit: Callable[[int], object] = os._exit,
    ) -> None:
        self.name, self.target = name, target
        self.retry_seconds, self.maximum_backoff = retry_seconds, maximum_backoff
        self.context = WorkerContext(stall_seconds=stall_seconds)
        self._fatal_exit = fatal_exit
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name=f"{name}-watchdog", daemon=True)

    def start(self) -> "SupervisedWorker":
        self._thread.start()
        self._watchdog.start()
        return self

    def close(self, timeout: float = 2.0) -> None:
        self.context.stop.set()
        self._thread.join(timeout)
        if not self._thread.is_alive():
            self.context.heartbeat(state="STOPPED")

    def snapshot(self) -> dict[str, object]:
        return {"name": self.name, "thread_alive": self._thread.is_alive(), **self.context.snapshot()}

    def _run(self) -> None:
        attempts = 0
        while not self.context.stop.is_set():
            self.context.heartbeat(state="STARTING", restarts=attempts)
            started = time.monotonic()
            try:
                self.target(self.context)
                if self.context.stop.is_set():
                    break
                raise RuntimeError("worker returned unexpectedly")
            except Exception as exc:
                attempts += 1
                # A sustained successful run resets the retry delay.
                if time.monotonic() - started >= self.context.stall_seconds:
                    failures = 1
                else:
                    failures = int(self.context.snapshot()["consecutive_failures"]) + 1
                delay = min(self.maximum_backoff, self.retry_seconds * 2 ** min(failures - 1, 8))
                self.context.heartbeat(
                    state="RECOVERING", restarts=attempts, consecutive_failures=failures,
                    last_error=type(exc).__name__, retry_in_seconds=delay,
                )
                # Exception text can contain signed provider URLs or credentials.
                log.warning("worker=%s failed type=%s retry_in=%.1fs", self.name, type(exc).__name__, delay)
                self.context.wait(delay)
        self.context.heartbeat(state="STOPPED")

    def check_stall(self) -> bool:
        if self.context.snapshot()["state"] != "STALLED":
            return False
        # Python cannot safely kill a stuck thread. Restart the process so the
        # deployment supervisor recovers it, without concurrent ledger writers.
        log.critical("worker=%s stalled; requesting process restart", self.name)
        self._fatal_exit(1)
        return True

    def _watch(self) -> None:
        while not self.context.stop.wait(min(5.0, self.context.stall_seconds)):
            if self.check_stall():
                return


_workers: dict[str, SupervisedWorker] = {}
_workers_lock = threading.Lock()


def start_worker(name: str, target: Callable[[WorkerContext], None]) -> SupervisedWorker:
    with _workers_lock:
        existing = _workers.get(name)
        if existing and existing.snapshot()["thread_alive"]:
            raise RuntimeError(f"worker already running: {name}")
        worker = SupervisedWorker(name, target)
        _workers[name] = worker
        return worker.start()


def runtime_status() -> dict[str, object]:
    with _workers_lock:
        workers = {name: worker.snapshot() for name, worker in _workers.items()}
    active = {name: value for name, value in workers.items() if value["state"] != "STOPPED"}
    return {"healthy": all(value["healthy"] for value in active.values()), "workers": workers}
