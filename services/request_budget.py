"""Cooperative deadline with bounded isolation for synchronous model work.

An HTTP timeout never releases a slot while its worker is still executing.
No asynchronous thread killing and no automatic retry of business writes.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import math
import os
from threading import Event, Lock
import time
from uuid import uuid4


def positive(name, default):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')
    return value


def capacity(name, default):
    value = positive(name, default)
    if not value.is_integer():
        raise ValueError(f'{name} must be an integer')
    return int(value)


class BudgetExceeded(Exception):
    def __init__(self, code='request_timeout'):
        self.code = code
        super().__init__(code)


class CapacityExceeded(Exception):
    code = 'capacity_rejected'


@dataclass
class Budget:
    deadline: float
    request_id: str = field(default_factory=lambda: uuid4().hex)
    cancelled: Event = field(default_factory=Event)
    reason: str = 'request_cancelled'

    def remaining(self):
        if self.cancelled.is_set():
            raise BudgetExceeded(self.reason)
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise BudgetExceeded()
        return remaining


current_budget: ContextVar[Budget | None] = ContextVar('request_budget', default=None)


def checkpoint():
    budget = current_budget.get()
    if budget is not None:
        budget.remaining()


def remaining_timeout(local):
    budget = current_budget.get()
    return min(local, budget.remaining()) if budget else local


class Admission:
    def __init__(self):
        self.lock = Lock()
        self.active = 0
        self.tenants = {}

    def acquire(self, tenant):
        with self.lock:
            if self.active >= capacity('RAG_CHAT_CAPACITY', 4) or self.tenants.get(tenant, 0) >= capacity('RAG_CHAT_TENANT_CAPACITY', 2):
                raise CapacityExceeded()
            self.active += 1
            self.tenants[tenant] = self.tenants.get(tenant, 0)+1

    def release(self, tenant):
        with self.lock:
            self.active -= 1
            self.tenants[tenant] -= 1
            if not self.tenants[tenant]:
                del self.tenants[tenant]


admission = Admission()
_executor = None
_executor_lock = Lock()
_model_lock = Lock()
_model_active = 0
_model_waiters = 0


class RequestBudgetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            scope.setdefault('state', {})['budget_started'] = time.monotonic()
        await self.app(scope, receive, send)


@contextmanager
def model_slot():
    global _model_active, _model_waiters
    acquired = False
    with _model_lock:
        _model_waiters += 1
    try:
        while not acquired:
            checkpoint()
            with _model_lock:
                if _model_active < capacity('RAG_MODEL_CAPACITY', 1):
                    _model_active += 1
                    _model_waiters -= 1
                    acquired = True
            if not acquired:
                if current_budget.get() is None:
                    raise CapacityExceeded()
                time.sleep(min(.02, remaining_timeout(.02)))
        yield
    finally:
        if acquired:
            with _model_lock:
                _model_active -= 1
        else:
            with _model_lock:
                _model_waiters -= 1


async def execute_chat(function, tenant, request=None):
    global _executor
    started = getattr(getattr(request, 'state', None), 'budget_started', time.monotonic())
    budget = Budget(started+positive('RAG_CHAT_DEADLINE_SECONDS', 30))
    admission.acquire(tenant)
    token = current_budget.set(budget)
    context = copy_context()
    current_budget.reset(token)

    def run():
        try:
            checkpoint()
            from services.call_ledger import call_scope
            with call_scope(tenant, request_id=budget.request_id):
                result = function()
            checkpoint()
            return result
        finally:
            admission.release(tenant)

    try:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=capacity('RAG_CHAT_CAPACITY', 4), thread_name_prefix='chat-budget')
        future = _executor.submit(context.run, run)
    except BaseException:
        admission.release(tenant)
        raise
    wrapped = asyncio.wrap_future(future)
    # Retrieve errors even after the HTTP coroutine exits. Do not cancel the
    # concurrent future: even queued work must execute its release finally.
    wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
    try:
        while not wrapped.done():
            budget.remaining()
            if request is not None and await request.is_disconnected():
                raise BudgetExceeded('request_cancelled')
            await asyncio.wait({wrapped}, timeout=min(.05, budget.remaining()))
        return wrapped.result()
    except BudgetExceeded as error:
        budget.reason = error.code
        budget.cancelled.set()
        raise
    except asyncio.CancelledError:
        budget.cancelled.set()
        raise
