"""Append-once model attempts, correlated with request/task and retry.

No prompts, outputs, keys, or exception text are stored. Missing accounting or
confirmed prices stays unknown. Started rows survive interrupted processes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
import inspect
from uuid import uuid4
from functools import wraps

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Numeric, String, Table, Float, select, func

from services.ingestion.db import get_engine
from services.request_budget import BudgetExceeded

metadata = MetaData()
calls = Table('runtime_model_calls', metadata,
              Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False, index=True),
              Column('created_by', String(64), nullable=False), Column('created_at', DateTime, nullable=False),
              Column('updated_at', DateTime, nullable=False), Column('deleted_at', DateTime),
              Column('request_id', String(64), nullable=False, index=True), Column('job_id', String(64), nullable=False, index=True),
              Column('attempt', Integer, nullable=False), Column('provider', String(32), nullable=False),
              Column('model', String(256), nullable=False), Column('kind', String(32), nullable=False),
              Column('started_at', DateTime, nullable=False), Column('ended_at', DateTime),
              Column('status', String(32), nullable=False), Column('error_code', String(64)),
              Column('prompt_tokens', Integer), Column('completion_tokens', Integer), Column('total_tokens', Integer),
              Column('counting_method', String(64)), Column('price_version', String(128)), Column('currency', String(16)),
              Column('amount', Numeric(24, 12)), Column('elapsed_ms', Float), Column('resources', JSON))
_scope = ContextVar('model_call_scope', default=None)
_usage = ContextVar('model_call_usage', default=None)


@contextmanager
def call_scope(tenant_id, *, request_id='', job_id='', attempt=1):
    from services.runtime_db import identity
    try:
        scope_tenant, actor = identity()
    except PermissionError:
        if not job_id and request_id:
            actor = 'internal-request'
        else:
            actor = 'ingestion-worker'
    else:
        if scope_tenant != tenant_id:
            raise PermissionError('ledger_identity_mismatch')
    token = _scope.set({'tenant_id': tenant_id, 'request_id': request_id, 'job_id': job_id, 'attempt': attempt, 'created_by': actor})
    try:
        yield
    finally:
        _scope.reset(token)


def capture_usage(usage):
    target = _usage.get()
    if target is not None:
        target.update(usage)


def tracked_route(function):
    signature = inspect.signature(function)

    @wraps(function)
    def invoke(*args, **kwargs):
        auth = signature.bind(*args, **kwargs).arguments['auth']
        with call_scope(auth.tenant_id, request_id=auth.request_id or uuid4().hex):
            return function(*args, **kwargs)
    return invoke


def tracked_model(provider, kind, model):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            from services.request_budget import checkpoint, model_slot
            from contextlib import nullcontext
            resolved = model(*args, **kwargs) if callable(model) else model
            checkpoint()
            with model_slot() if kind == 'generation' else nullcontext():
                with model_call(provider, resolved, kind):
                    result = function(*args, **kwargs)
                    if isinstance(result, dict):
                        capture_usage(result.get('token_usage') or result.get('usage') or {})
                    checkpoint()
                    return result
        return invoke
    return decorate


def price_for(provider, model):
    path = os.getenv('RAG_MODEL_PRICES_FILE', '').strip()
    if not path:
        return None
    row = json.loads(Path(path).read_text(encoding='utf-8')).get(provider+'/'+model)
    if not row or row.get('confirmed') is not True:
        return None
    if not row.get('version') or not row.get('currency'):
        raise ValueError('price_version_and_currency_required')
    prices = [Decimal(str(row[k])) for k in ('input_per_million', 'output_per_million')]
    if any(not value.is_finite() or value < 0 for value in prices):
        raise ValueError('invalid_model_price')
    return {**row, 'input_per_million': prices[0], 'output_per_million': prices[1]}


@contextmanager
def model_call(provider, model, kind):
    scope = _scope.get()
    usage = {}
    token = _usage.set(usage)
    if scope is None:
        try:
            yield usage
        finally:
            _usage.reset(token)
        return
    call_id = uuid4().hex
    start, cpu = time.monotonic(), time.thread_time()
    try:
        price = price_for(provider, model)
        with get_engine().begin() as conn:
            conn.execute(calls.insert().values(id=call_id, **scope, provider=provider, model=model, kind=kind,
                                              created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                              updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                              started_at=datetime.now(timezone.utc).replace(tzinfo=None), status='started',
                                              price_version=price['version'] if price else None,
                                              currency=price['currency'] if price else None))
    except BaseException:
        _usage.reset(token)
        raise  # fail before dispatch: no unaccounted provider call
    status, code = 'succeeded', None
    try:
        yield usage
    except BudgetExceeded as error:
        status, code = ('cancelled' if error.code == 'request_cancelled' else 'timeout'), error.code
        raise
    except BaseException as error:
        status, code = 'failed', type(error).__name__[:64]
        raise
    finally:
        _usage.reset(token)
        values = {key: usage.get(key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'counting_method')}
        amount = None
        if price and values['prompt_tokens'] is not None and values['completion_tokens'] is not None:
            amount = (Decimal(values['prompt_tokens'])*price['input_per_million']
                      + Decimal(values['completion_tokens'])*price['output_per_million']) / Decimal(1000000)
        with get_engine().begin() as conn:
            conn.execute(calls.update().where(calls.c.id == call_id, calls.c.status == 'started').values(
                **values, ended_at=datetime.now(timezone.utc).replace(tzinfo=None),
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None), status=status, error_code=code,
                amount=amount, elapsed_ms=(time.monotonic()-start)*1000,
                resources={'calling_thread_cpu_seconds': time.thread_time()-cpu,
                           'cpu_scope': 'calling_thread_only_excludes_native_pool_threads'}))


def ledger_summary(tenant):
    with get_engine().connect() as conn:
        rows = conn.execute(select(calls.c.status, func.count().label('count'),
                            func.sum(calls.c.total_tokens).label('tokens')).where(calls.c.tenant_id == tenant, calls.c.deleted_at.is_(None))
                            .group_by(calls.c.status)).mappings().all()
        prices = conn.execute(select(calls.c.currency, func.sum(calls.c.amount).label('amount'),
                             func.count().label('priced_calls')).where(calls.c.tenant_id == tenant, calls.c.amount.is_not(None), calls.c.deleted_at.is_(None))
                             .group_by(calls.c.currency)).mappings().all()
        total = sum(r['count'] for r in rows)
        priced = sum(r['priced_calls'] for r in prices)
    return {'scope': 'model_attempts_not_chat_turns', 'attempts': total, 'outcomes': [dict(r) for r in rows],
            'unknown_cost_calls': total-priced, 'priced_totals': [dict(r) for r in prices],
            'all_calls_cost_known': total > 0 and total == priced}
