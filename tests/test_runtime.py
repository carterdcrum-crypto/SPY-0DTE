import threading
import time

from engine.runtime import SupervisedWorker, WorkerContext


def test_startup_failure_recovers_without_exposing_exception_text():
    ready = threading.Event()
    attempts = []

    def target(context):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("https://provider.invalid?secret=do-not-log")
        context.heartbeat()
        ready.set()
        context.stop.wait(2)

    worker = SupervisedWorker("test", target, retry_seconds=0.001).start()
    try:
        assert ready.wait(2)
        status = worker.snapshot()
        assert status["healthy"] is True
        assert status["restarts"] == 1
        assert "do-not-log" not in str(status)
    finally:
        worker.close()
    assert worker.snapshot()["state"] == "STOPPED"


def test_unexpected_worker_return_is_restarted():
    ready = threading.Event()
    attempts = []

    def target(context):
        attempts.append(1)
        if len(attempts) == 1:
            return
        context.heartbeat()
        ready.set()
        context.stop.wait(2)

    worker = SupervisedWorker("test-return", target, retry_seconds=0.001).start()
    try:
        assert ready.wait(2)
        assert len(attempts) == 2
    finally:
        worker.close()


def test_stalled_worker_requests_process_restart_without_duplicate_worker():
    exits = []
    calls = []
    worker = SupervisedWorker("stuck", lambda context: calls.append(1), fatal_exit=exits.append)
    worker.context._deadline = time.monotonic() - 1
    assert worker.check_stall() is True
    assert exits == [1]
    assert calls == []


def test_scheduled_wait_extends_heartbeat_deadline():
    context = WorkerContext(stall_seconds=0.01)
    context.heartbeat(state="IDLE")
    context.stop.set()
    context.wait(300)
    assert context._deadline > time.monotonic() + 299
