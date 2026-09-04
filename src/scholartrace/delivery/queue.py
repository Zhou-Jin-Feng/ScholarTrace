"""Small bounded worker pool for the local single-user delivery runtime."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic


class QueueFullError(RuntimeError):
    """The bounded local queue cannot accept another task."""


class QueueClosedError(RuntimeError):
    """The executor is no longer accepting tasks."""


@dataclass
class QueueSubmission:
    """Control handles retained for the lifetime of one submitted task."""

    task_id: str
    cancel_event: threading.Event
    finished: threading.Event


@dataclass
class _WorkItem:
    task_id: str
    callback: Callable[[threading.Event], None]
    submission: QueueSubmission


class BoundedTaskExecutor:
    """A bounded, daemon-backed worker pool with cooperative cancellation.

    The queue is intentionally process-local. It is suitable for the current
    single-user SQLite deployment and exposes enough evidence to decide later
    whether a cross-process broker is justified.
    """

    def __init__(self, *, max_workers: int = 1, max_queue_size: int = 4) -> None:
        if max_workers < 1 or max_queue_size < 1:
            raise ValueError("worker and queue sizes must be positive")
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self._queue: queue.Queue[_WorkItem | None] = queue.Queue(maxsize=max_queue_size)
        self._lock = threading.RLock()
        self._accepting = True
        self._workers: list[threading.Thread] = []
        self._handles: dict[str, QueueSubmission] = {}
        self._active: set[str] = set()

    def submit(self, task_id: str, callback: Callable[[threading.Event], None]) -> QueueSubmission:
        with self._lock:
            if not self._accepting:
                raise QueueClosedError("task queue is shutting down")
            if task_id in self._handles:
                raise ValueError(f"task already submitted: {task_id}")
            submission = QueueSubmission(
                task_id=task_id,
                cancel_event=threading.Event(),
                finished=threading.Event(),
            )
            try:
                self._queue.put_nowait(_WorkItem(task_id, callback, submission))
            except queue.Full as exc:
                raise QueueFullError("task queue is full") from exc
            self._handles[task_id] = submission
            self._ensure_workers()
            return submission

    def cancel(self, task_id: str) -> QueueSubmission | None:
        with self._lock:
            submission = self._handles.get(task_id)
            if submission is not None:
                submission.cancel_event.set()
            return submission

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "accepting": self._accepting,
                "max_workers": self.max_workers,
                "max_queue_size": self.max_queue_size,
                "queued": self._queue.qsize(),
                "active": len(self._active),
                "submitted": len(self._handles),
            }

    def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        """Stop accepting work and wait for accepted work to drain."""

        if timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")
        with self._lock:
            self._accepting = False
            submissions = list(self._handles.values())
        deadline = monotonic() + timeout_seconds
        while True:
            if self._queue.unfinished_tasks == 0:
                break
            if monotonic() >= deadline:
                for submission in submissions:
                    submission.cancel_event.set()
                break
            threading.Event().wait(min(0.05, max(0.0, deadline - monotonic())))

        for worker in self._workers:
            remaining = max(0.0, deadline - monotonic())
            worker.join(remaining)
        return all(not worker.is_alive() for worker in self._workers)

    def _ensure_workers(self) -> None:
        for index in range(self.max_workers):
            if any(
                worker.is_alive() and worker.name == f"scholartrace-task-{index}"
                for worker in self._workers
            ):
                continue
            worker = threading.Thread(
                target=self._worker,
                name=f"scholartrace-task-{index}",
                daemon=True,
            )
            self._workers.append(worker)
            worker.start()

    def _worker(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if not self._accepting and self._queue.empty():
                        return
                continue
            if item is None:
                self._queue.task_done()
                return
            with self._lock:
                self._active.add(item.task_id)
            try:
                item.callback(item.submission.cancel_event)
            finally:
                with self._lock:
                    self._active.discard(item.task_id)
                    self._handles.pop(item.task_id, None)
                    item.submission.finished.set()
                self._queue.task_done()
