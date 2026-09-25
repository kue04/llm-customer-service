from __future__ import annotations

from typing import Any

from services.query_resolution import (
    DEFAULT_QUERY_REWRITE_PROVIDER,
    QueryResolutionPlan,
    QueryRewriteProvider,
    resolve_query,
)


class QueryRewriteError(RuntimeError):
    """Raised by optional providers; the resolver converts it to original-query fallback."""


class DeterministicRewriteProvider:
    """Compatibility provider name for callers that want an explicit default."""

    def rewrite(self, query: str, intent_analysis: dict, context: dict | None = None) -> QueryResolutionPlan:
        return DEFAULT_QUERY_REWRITE_PROVIDER.rewrite(query, intent_analysis, context)


def rewrite_query(
    query: str,
    *,
    intent_analysis: dict | None = None,
    context: dict | None = None,
    provider: QueryRewriteProvider | None = None,
) -> dict[str, Any]:
    """Return a serializable resolution plan with fail-safe original fallback."""

    return resolve_query(query, intent_analysis, context, provider).to_dict()


__all__ = [
    "DeterministicRewriteProvider",
    "QueryRewriteError",
    "rewrite_query",
]
