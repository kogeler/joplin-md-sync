"""Bounded execution and cancellation without replaying or releasing active work."""

from __future__ import annotations

import asyncio
import gc
import threading
import time
from concurrent.futures import CancelledError

import pytest

from joplin_md_sync.oauth_execution import CapacityExhausted, DaemonExecutor, ExecutionBudget


def test_worker_queue_is_bounded_and_pending_work_is_cancelled_on_shutdown():
    executor = DaemonExecutor(workers=1, pending=1)
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def mutation():
        entered.set()
        finish.wait(2)
        calls.append("applied")
        return "done"

    try:
        active = executor.submit(mutation)
        assert entered.wait(1)
        queued = executor.submit(lambda: calls.append("must-not-start"))
        with pytest.raises(CapacityExhausted):
            executor.submit(lambda: calls.append("overflow"))
        executor.shutdown(wait=False, cancel_futures=True)
        with pytest.raises(RuntimeError, match="closed"):
            executor.submit(lambda: None)
        finish.set()
        assert active.result(timeout=2) == "done"
        executor.join(2)
        with pytest.raises(CancelledError):
            queued.result(timeout=1)
        assert calls == ["applied"]
    finally:
        finish.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_request_cancellation_does_not_free_a_running_mutation_slot():
    async def exercise():
        executor = DaemonExecutor(workers=1)
        budget = ExecutionBudget(executor, limit=1)
        entered, finish = threading.Event(), threading.Event()
        calls = []

        def mutation():
            entered.set()
            finish.wait(2)
            calls.append("applied")

        try:
            task = asyncio.create_task(budget.run(mutation))
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(CapacityExhausted):
                await budget.run(lambda: calls.append("premature"))
            finish.set()
            deadline = time.monotonic() + 2
            while True:
                try:
                    await budget.run(lambda: calls.append("after"))
                    break
                except CapacityExhausted:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
            assert calls == ["applied", "after"]
        finally:
            finish.set()
            executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(exercise())


def test_worker_failure_is_reported_once_and_next_job_can_run():
    with DaemonExecutor(workers=1) as executor:
        calls = []

        def fail():
            calls.append("failed")
            raise ValueError("fixture worker failure")

        with pytest.raises(ValueError, match="fixture worker failure"):
            executor.submit(fail).result(timeout=1)
        assert executor.submit(lambda: "healthy").result(timeout=1) == "healthy"
        assert calls == ["failed"]


def test_cancelled_request_retrieves_late_worker_failure_without_logging_private_data():
    async def exercise():
        loop = asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        with DaemonExecutor(workers=1) as executor:
            budget = ExecutionBudget(executor, limit=1)
            entered, finish = threading.Event(), threading.Event()

            def fail():
                entered.set()
                finish.wait(2)
                raise ValueError("private fixture note body")

            task = asyncio.create_task(budget.run(fail))
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            del task
            finish.set()
            deadline = time.monotonic() + 2
            while True:
                try:
                    assert await budget.run(lambda: "healthy") == "healthy"
                    break
                except CapacityExhausted:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
            gc.collect()
            await asyncio.sleep(0)
            assert not errors

    asyncio.run(exercise())
