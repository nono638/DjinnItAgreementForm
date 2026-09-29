"""Runs slow work (OCR, PDF parsing, Ollama) off the UI thread."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # reported to the UI, never crashes the app
            traceback.print_exc()
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.finished.emit(result)


class Runner:
    """Keeps tasks alive until they report back."""

    def __init__(self) -> None:
        self.pool = QThreadPool.globalInstance()
        self._live: set[Task] = set()

    def start(self, fn: Callable, *args, on_done: Callable | None = None,
              on_error: Callable | None = None, **kwargs) -> Task:
        task = Task(fn, *args, **kwargs)
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
