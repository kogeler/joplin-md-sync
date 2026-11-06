"""A bounded shared budget for synchronous work in the embedded HTTP server."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from typing import Any, TypeVar

T = TypeVar("T")


class CapacityExhausted(Exception):
    pass


class DaemonExecutor(Executor):
    """Fixed daemon workers: shutdown never waits indefinitely for network I/O."""

    def __init__(self, workers: int = 8, pending: int = 16) -> None:
        self._queue: queue.Queue[
            tuple[Future[Any], Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = queue.Queue(pending)
        self._lock = threading.Lock()
        self._closed = False
        self._cancel_pending = False
        self._threads = [
            threading.Thread(target=self._work, name=f"jms-http-{i}", daemon=True)
            for i in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        with self._lock:
            if self._closed:
                raise RuntimeError("HTTP executor is closed")
            future: Future[T] = Future()
            try:
                self._queue.put_nowait((future, fn, args, kwargs))
            except queue.Full:
                raise CapacityExhausted("HTTP executor is busy") from None
            return future

    def _work(self) -> None:
        while True:
            try:
                future, fn, args, kwargs = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._closed:
                    return
                continue
            if self._cancel_pending:
                future.cancel()
            elif future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    future.set_exception(exc)
            self._queue.task_done()

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closed = True
            self._cancel_pending = cancel_futures
        if wait:
            self.join(30)

    def join(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))


class ExecutionBudget:
    def __init__(self, executor: Executor, limit: int = 16) -> None:
        self.executor = executor
        self._slots = asyncio.Semaphore(limit)

    async def run(self, fn: Callable[..., T], *args: Any) -> T:
        if self._slots.locked():
            raise CapacityExhausted("HTTP execution capacity exhausted")
        await self._slots.acquire()
        try:
            future = asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)
        except BaseException:
            self._slots.release()
            raise

        # A separate waiter detaches request cancellation from active work.
        # Unlike shield() on Python 3.14 it does not log late private failures.
        waiter: asyncio.Future[T] = asyncio.get_running_loop().create_future()

        def finished(completed: asyncio.Future[T]) -> None:
            self._slots.release()
            error = None if completed.cancelled() else completed.exception()
            if waiter.done():
                return
            if completed.cancelled():
                waiter.cancel()
            elif error is not None:
                waiter.set_exception(error)
            else:
                waiter.set_result(completed.result())

        future.add_done_callback(finished)
        return await waiter
