"""Explicit runtime profiles; production never inherits development fallbacks."""
from __future__ import annotations

import os


def runtime_environment() -> str:
    value = os.getenv('RAG_ENV', 'production').strip().lower()
    if value not in {'production', 'development', 'test'}:
        raise ValueError('RAG_ENV must be production, development, or test')
    return value


def demo_endpoints_enabled() -> bool:
    return (runtime_environment() in {'development', 'test'}
            and os.getenv('RAG_ENABLE_DEMO_ENDPOINTS', 'false').strip().lower() == 'true')


def development_degradation_enabled() -> bool:
    return (runtime_environment() in {'development', 'test'}
            and os.getenv('RAG_ALLOW_DEGRADED_READINESS', 'false').strip().lower() == 'true')
