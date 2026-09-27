import asyncio
from threading import Event
import time

import pytest

from services import request_budget as budgets
from services.runtime_db import runtime_scope, identity


def test_timeout_keeps_slot_until_noncooperative_work_exits(monkeypatch):
    monkeypatch.setenv('RAG_CHAT_DEADLINE_SECONDS', '.05')
    monkeypatch.setenv('RAG_CHAT_TENANT_CAPACITY', '1')
    entered, release = Event(), Event()

    def work():
        entered.set()
        release.wait(3)

    async def exercise():
        try:
            with pytest.raises(budgets.BudgetExceeded, match='request_timeout'):
                await budgets.execute_chat(work, 'bounded')
            assert entered.is_set()
            assert budgets.admission.tenants['bounded'] == 1
            with pytest.raises(budgets.CapacityExceeded):
                await budgets.execute_chat(lambda: None, 'bounded')
            # A stuck tenant cannot consume every slot.
            assert await budgets.execute_chat(lambda: 42, 'other') == 42
        finally:
            release.set()
        for _ in range(100):
            if 'bounded' not in budgets.admission.tenants:
                break
            await asyncio.sleep(.01)
        assert 'bounded' not in budgets.admission.tenants

    asyncio.run(exercise())


def test_disconnect_stops_cooperative_work_and_preserves_identity(monkeypatch):
    monkeypatch.setenv('RAG_CHAT_DEADLINE_SECONDS', '2')
    exited = Event()

    class Request:
        async def is_disconnected(self):
            return True

    def work():
        try:
            assert identity() == ('t', 'u')
            while True:
                budgets.checkpoint()
                time.sleep(.005)
        finally:
            exited.set()

    async def exercise():
        with runtime_scope('t', 'u'):
            with pytest.raises(budgets.BudgetExceeded, match='request_cancelled'):
                await budgets.execute_chat(work, 't', Request())
        # The executor can observe cancellation before calling work.
        for _ in range(100):
            if 't' not in budgets.admission.tenants:
                break
            await asyncio.sleep(.01)
        assert 't' not in budgets.admission.tenants

    asyncio.run(exercise())


def test_local_timeout_is_capped_by_total_budget():
    token = budgets.current_budget.set(budgets.Budget(time.monotonic()+.1))
    try:
        assert 0 < budgets.remaining_timeout(60) <= .1
    finally:
        budgets.current_budget.reset(token)


def test_invalid_budget_configuration_fails(monkeypatch):
    for value in ('0', '-1', 'nan', 'inf'):
        monkeypatch.setenv('RAG_CHAT_DEADLINE_SECONDS', value)
        with pytest.raises(ValueError):
            budgets.positive('RAG_CHAT_DEADLINE_SECONDS', 30)
