"""Bounded Runtime timing events. Trace records never include request text or images."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from queue import Full, Queue
from threading import Thread
from time import perf_counter_ns
from uuid import uuid4


active_trace = ContextVar("architect_trace", default=None)


class Trace:
    def __init__(self, path):
        self.path = path
        self.enabled = os.getenv("MC_AI_BUILDER_PROFILE") == "1"
        self.run_id = uuid4().hex if self.enabled else None
        self.queue = Queue(maxsize=256) if self.enabled else None
        self.dropped = 0
        self.started = perf_counter_ns()
        self.writer = None

    def start(self):
        if self.enabled and self.writer is None:
            self.writer = Thread(target=self._write, name="architect-trace", daemon=True)
            self.writer.start()

    def close(self):
        if self.writer is not None:
            try:
                self.queue.put(None, timeout=.5)
            except Full:
                self.dropped += 1
            self.writer.join(2)
            self.writer = None

    def _write(self):
        try:
            with self.path.open("a", encoding="utf-8", buffering=8192) as stream:
                while (row := self.queue.get()) is not None:
                    stream.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
                    if self.queue.empty():
                        stream.flush()
                stream.write(json.dumps({"run": self.run_id, "event": "trace_closed", "dropped": self.dropped}) + "\n")
        except (OSError, ValueError):
            # Optional diagnostic output must never interrupt chat or the Minecraft job.
            self.enabled = False

    @contextmanager
    def bind(self, **ids):
        previous = active_trace.set((self, ids))
        try:
            yield
        finally:
            active_trace.reset(previous)

    def emit(self, event, **values):
        if not self.enabled:
            return
        context = active_trace.get()
        ids = context[1] if context and context[0] is self else {}
        row = {"run": self.run_id, "t_ms": (perf_counter_ns() - self.started) / 1e6,
               "event": event, **ids, **values}
        try:
            self.queue.put_nowait(row)
        except Full:
            self.dropped += 1


@contextmanager
def timed(event, **fields):
    context = active_trace.get()
    if context is None or not context[0].enabled:
        yield
        return
    started = perf_counter_ns()
    try:
        yield
    finally:
        context[0].emit(event, duration_ms=(perf_counter_ns() - started) / 1e6, **fields)


def model_turn(kind, metrics):
    context = active_trace.get()
    if context is not None:
        context[0].emit("model_turn", kind=kind, error_kind=metrics.get("error", {}).get("kind"), **{
            key: metrics[key] for key in ("elapsed_ms", "sdk_duration_ms", "status", "tokens",
                                          "observation_id", "effective")
            if key in metrics
        })
