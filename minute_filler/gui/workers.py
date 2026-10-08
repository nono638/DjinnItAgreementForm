"""Runs slow work (OCR, PDF parsing, Ollama) off the UI thread."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..log import error as log_error


class _Signals(QObject):
    """A QRunnable is not a QObject and cannot have signals, so each Task owns one of these. The pool thread
    emits them and Qt queues the calls to the UI thread."""
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)  # done, total, what is being worked on


class Task(QRunnable):
    """Calls fn(*args, **kwargs) on a pool thread and emits `finished` with its result, or `failed` with
    "ErrorType: message" if it raised."""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        """Runs on the pool thread (Qt calls it): the result or the error goes out as a signal."""
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # reported to the UI, never crashes the app
            log_error("a background task failed", e)
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.finished.emit(result)


class Runner:
    """Starts tasks on a thread pool (the global one unless `pool` is given) and keeps a reference to each until
    it reports back. Without that reference Python could free the Task (and its signals) while it is still
    running."""

    def __init__(self, pool: QThreadPool | None = None) -> None:
        self.pool = pool or QThreadPool.globalInstance()
        self._live: set[Task] = set()

    def start(self, fn: Callable, *args, on_done: Callable | None = None,
              on_error: Callable | None = None, on_progress: Callable | None = None, **kwargs) -> Task:
        """Runs fn(*args, **kwargs) in the background. on_done gets its result and on_error gets the error
        text, both on the UI thread. With on_progress, fn is also called with progress=<function(done, total,
        name)>, and each call reaches on_progress on the UI thread. Returns the Task."""
        task = Task(fn, *args, **kwargs)
        if on_progress:
            task.kwargs["progress"] = task.signals.progress.emit
            task.signals.progress.connect(on_progress)
        task.setAutoDelete(False)
        self._live.add(task)

        def done(result):
            self._live.discard(task)
            if on_done:
                on_done(result)

        def failed(msg):
            self._live.discard(task)
            if on_error:
                on_error(msg)

        task.signals.finished.connect(done)
        task.signals.failed.connect(failed)
        self.pool.start(task)
        return task

    def drop_queued(self) -> int:
        """Takes the tasks that haven't started off the pool's queue, without running them (New job: their answers
        would be thrown away, and on a one-thread pool the next job's tasks would wait behind them). A task
        already running finishes. Returns how many were dropped."""
        dropped = [t for t in list(self._live) if self.pool.tryTake(t)]
        self._live.difference_update(dropped)
        return len(dropped)
