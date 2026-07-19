"""Tests for lazy auto-indexing (_maybe_lazy_index) in the API routes."""
from __future__ import annotations

import pytest

from repograph.api import routes


@pytest.fixture(autouse=True)
def _reset_throttle():
    routes._LAZY_INDEX_LAST.clear()
    yield
    routes._LAZY_INDEX_LAST.clear()


@pytest.fixture
def calls(monkeypatch):
    import repograph.autoindex as autoindex

    recorded: list[tuple] = []
    monkeypatch.setattr(autoindex, "ensure_indexed", lambda *a, **k: recorded.append((a, k)))
    return recorded


def test_disabled_by_default(monkeypatch, calls):
    monkeypatch.delenv("REPOGRAPH_AUTOINDEX", raising=False)
    routes._maybe_lazy_index("/some/repo", None)
    assert calls == []


def test_lazy_mode_indexes_once_then_throttles(monkeypatch, calls):
    monkeypatch.setenv("REPOGRAPH_AUTOINDEX", "lazy")
    routes._maybe_lazy_index("/some/repo", None)
    routes._maybe_lazy_index("/some/repo", None)
    assert len(calls) == 1


def test_distinct_tenants_not_throttled_together(monkeypatch, calls):
    monkeypatch.setenv("REPOGRAPH_AUTOINDEX", "lazy")
    routes._maybe_lazy_index("/some/repo", "tenant-a")
    routes._maybe_lazy_index("/some/repo", "tenant-b")
    assert len(calls) == 2


def test_missing_repo_path_is_noop(monkeypatch, calls):
    monkeypatch.setenv("REPOGRAPH_AUTOINDEX", "lazy")
    routes._maybe_lazy_index(None, "tenant-a")
    routes._maybe_lazy_index("", "tenant-a")
    assert calls == []


def test_indexing_failure_never_raises(monkeypatch):
    import repograph.autoindex as autoindex

    monkeypatch.setenv("REPOGRAPH_AUTOINDEX", "lazy")

    def boom(*a, **k):
        raise RuntimeError("index failed")

    monkeypatch.setattr(autoindex, "ensure_indexed", boom)
    routes._maybe_lazy_index("/some/repo", None)  # must not raise
