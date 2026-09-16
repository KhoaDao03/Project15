"""Bounded, ordered paper simulation in a process separate from live signals."""

import json
import multiprocessing
import os
import queue
import tempfile
import time
import uuid
from pathlib import Path

from .engine import Engine
from .storage import Store


class SignalStore(Store):
    """Transient strategy state; paper research records belong to the paper worker."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="btc15-signals-")
        super().__init__("sqlite:///" + str(Path(self.directory.name) / "signals.db"))

    def close(self):
        self.engine.dispose()
        self.directory.cleanup()

    def add(self, *args, **kwargs):
        return kwargs.get("record_id") or str(uuid.uuid4())

    def checkpoint(self, *args, **kwargs):
        pass


def paper_summary(engine, state, **extra):
    ex = engine.executor
    return dict(
        state=state,
        timestamp=time.time(),
        process_id=os.getpid(),
        run_id=engine.run_id,
        positions={k: vars(v).copy() for k, v in ex.positions.items()},
        realized_pnl=ex.risk.realized,
        exposure=sum(ex.risk.reserved.values()),
        daily=ex.risk.day(time.time()).copy(),
        halted=ex.risk.halted,
        settlement_recovery={k: v for k, v in ex.quarantines.items() if k in ex.positions},
        **extra,
    )


def _paper_main(database, config, run_id, resume, record_all, data_dir, inputs, outputs, failed, receipt):
    store = Store(database)
    engine = None
    owner = str(uuid.uuid4())
    acquired = False
    processed = 0
    parent = os.getppid()

    def publish(body):
        # Only the newest status matters. Never block order processing on a viewer.
        try:
            outputs.put_nowait(body)
        except queue.Full:
            pass

    try:
        store.acquire("paper-simulation", owner)
        acquired = True
        engine = Engine(
            store,
            config,
            "PAPER",
            run_id=run_id,
            execute=True,
            clock=time.time,
            resume=resume,
            record_evaluations=record_all,
        )
        if not resume:
            closed = {r["opportunity_id"] for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
            buys = {
                r["opportunity_id"]
                for r in store.list(kind="fill", mode="PAPER", limit=None)
                if r["body"]["action"] == "buy"
            }
            if buys - closed:
                raise RuntimeError("Unresolved paper positions: resume their run")
            engine.executor.restore_daily_history()
        engine.raw_archive = True
        store.checkpoint(engine.run_id, engine.executor.snapshot())
        publish(
            paper_summary(
                engine,
                "READY",
                contracts={
                    k: v for k, v in engine.executor.contracts.items() if k in engine.executor.positions
                },
            )
        )
        last_status = 0
        while not failed.is_set():
            if os.getppid() != parent:
                raise RuntimeError("Collector process disappeared")
            try:
                rows = inputs.get(timeout=0.2)
            except queue.Empty:
                continue
            if rows is None:
                break
            for row in rows:
                if failed.is_set():
                    raise RuntimeError("Paper input queue overflow; no events may be skipped")
                lock = receipt.get_lock()
                if not lock.acquire(timeout=0.05):
                    raise RuntimeError("Reference receipt marker unavailable")
                try:
                    latest = receipt.value
                finally:
                    lock.release()
                engine.executor.latest_reference_receipt = tuple(json.loads(latest)) if latest else None
                if (Path(data_dir) / "HALT").exists() and not engine.executor.risk.halted:
                    engine.executor.halt(time.time())
                if row.get("paper_stopping"):
                    from .runner import stop_entries

                    stop_entries(engine, row["received"])
                engine.ingest(row)
                processed += 1
            now = time.time()
            if now - last_status >= 0.25:
                engine.flush_rejections(now)
                publish(
                    paper_summary(
                        engine,
                        "RUNNING",
                        processed_events=processed,
                        processing_lag=max(0, now - rows[-1]["received"]),
                    )
                )
                last_status = now
        if failed.is_set():
            raise RuntimeError("Paper input stream interrupted")
    except BaseException as exc:
        failed.set()
        publish(dict(state="FAILED", timestamp=time.time(), reason=str(exc), process_id=os.getpid()))
        raise
    finally:
        if engine is not None:
            from .runner import stop_entries

            stop_entries(engine, time.time())
            for ticker in engine.executor.positions:
                engine.monitoring_state(ticker, time.time(), False, ["PAPER_WORKER_STOPPED"])
            engine.flush_rejections(time.time(), force=True)
            store.checkpoint(engine.run_id, engine.executor.snapshot())
            body = paper_summary(
                engine, "FAILED" if failed.is_set() else "STOPPED", processed_events=processed
            )
            store.publish_market_display(body, "paper_worker")
            publish(body)
        if acquired:
            store.release("paper-simulation", owner)
        store.engine.dispose()


class PaperWorker:
    def __init__(self, database, config, run_id, resume, record_all, data_dir, *, capacity=64):
        if database.endswith(":memory:"):
            raise ValueError("Separate paper simulation requires a file-backed database")
        context = multiprocessing.get_context("spawn")
        self.inputs = context.Queue(maxsize=capacity)  # At most 64 batches of 256 source events.
        self.outputs = context.Queue(maxsize=4)
        self.failed = context.Event()
        self.receipt = context.Array("c", 128)
        self.submitted_events = 0
        self.capacity = capacity * 256
        self.closed = False
        self.stopping = False
        self.status = dict(state="STARTING", timestamp=time.time())
        self.process = context.Process(
            target=_paper_main,
            args=(
                database,
                config,
                run_id,
                resume,
                record_all,
                data_dir,
                self.inputs,
                self.outputs,
                self.failed,
                self.receipt,
            ),
            daemon=True,
            name=f"paper-{config.asset}",
        )

    def start(self, timeout=30):
        self.process.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.poll()
            if self.status["state"] == "READY":
                return self.status
            if self.status["state"] == "FAILED":
                break
            time.sleep(0.01)
        self.close()
        raise RuntimeError("Paper worker did not become ready: " + str(self.status))

    def reference(self, row):
        lock = self.receipt.get_lock()
        if not lock.acquire(timeout=0.001):
            self.failed.set()
            return
        try:
            self.receipt.value = json.dumps([row["id"], row["received"]]).encode()
        finally:
            lock.release()

    def poll(self):
        if self.closed:
            return self.status
        while True:
            try:
                self.status = {**self.status, **self.outputs.get_nowait()}
            except queue.Empty:
                break
        unexpected_exit = (
            self.process.pid is not None
            and not self.process.is_alive()
            and not (self.stopping and self.process.exitcode == 0)
        )
        if unexpected_exit:
            self.failed.set()
        if self.failed.is_set():
            self.status = {
                **self.status,
                "state": "FAILED",
                "reason": self.status.get("reason", "Paper worker stopped or input queue overflowed"),
            }
        result = {
            **self.status,
            "outstanding_events": max(0, self.submitted_events - self.status.get("processed_events", 0)),
            "queue_capacity": self.capacity,
        }
        if result["state"] in ("READY", "RUNNING") and (
            time.time() - result["timestamp"] > 3 or result.get("processing_lag", 0) > 1
        ):
            result.update(
                state="LAGGING",
                reason="Paper simulation is behind live inputs; freshness checks block stale execution",
            )
        return result

    def submit(self, rows):
        if len(rows) > 256:
            raise ValueError("Paper batches cannot exceed 256 events")
        if self.closed:
            return False
        if self.poll()["state"] == "FAILED":
            return False
        try:
            self.inputs.put_nowait(rows)
            self.submitted_events += len(rows)
            return True
        except queue.Full:
            self.failed.set()
            self.poll()
            return False

    def close(self, timeout=30):
        if self.closed or self.process.pid is None:
            return
        self.stopping = True
        if self.process.is_alive() and not self.failed.is_set():
            try:
                self.inputs.put(None, timeout=min(timeout, 1))
            except queue.Full:
                self.failed.set()
        self.process.join(timeout)
        if self.process.is_alive():
            self.failed.set()
            self.process.terminate()
            self.process.join(5)
        self.poll()
        if self.process.exitcode == 0 and not self.failed.is_set():
            self.status = {**self.status, "state": "STOPPED", "reason": None}
        self.closed = True
        for channel in (self.inputs, self.outputs):
            channel.cancel_join_thread()
            channel.close()
