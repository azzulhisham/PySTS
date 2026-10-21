"""Unit tests for STS review pagination, notifications, and notified_at stamping."""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RESTAPI = Path(__file__).resolve().parents[1]
if str(RESTAPI) not in sys.path:
    sys.path.insert(0, str(RESTAPI))

from sts_review import (  # noqa: E402
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MIN_SUSPICION_SCORE,
    NOTIFY_LIMIT,
    PAGE_ORDER,
    UNREVIEWED_SQL,
    _check_page,
    _page_fields,
    is_notifiable_case,
    list_notifications,
    list_unreviewed,
    notified_at_on_upsert,
)


class TestPaginationBounds:
    def test_default_page_size_is_bounded(self):
        assert DEFAULT_PAGE_SIZE == 100
        assert MAX_PAGE_SIZE == 1000
        assert DEFAULT_PAGE_SIZE <= MAX_PAGE_SIZE

    def test_unreviewed_sql_always_limits(self):
        sql = str(UNREVIEWED_SQL)
        assert "LIMIT :limit" in sql
        assert "OFFSET :offset" in sql
        assert "LIMIT" in PAGE_ORDER

    def test_list_unreviewed_defaults_to_page_one_and_default_size(self):
        sig = inspect.signature(list_unreviewed)
        assert sig.parameters["page"].default == 1
        assert sig.parameters["page_size"].default == DEFAULT_PAGE_SIZE

    def test_check_page_rejects_unbounded_and_zero(self):
        with pytest.raises(ValueError):
            _check_page(0, DEFAULT_PAGE_SIZE)
        with pytest.raises(ValueError):
            _check_page(1, 0)
        with pytest.raises(ValueError):
            _check_page(1, MAX_PAGE_SIZE + 1)
        _check_page(1, DEFAULT_PAGE_SIZE)
        _check_page(1, MAX_PAGE_SIZE)

    def test_page_fields_math(self):
        fields = _page_fields(page=2, page_size=100, total=322_000, returned=100)
        assert fields["page"] == 2
        assert fields["page_size"] == 100
        assert fields["total_pages"] == 3220
        assert fields["returned_count"] == 100


class TestNotificationsApiSurface:
    def test_list_notifications_exported_and_capped(self):
        assert callable(list_notifications)
        assert NOTIFY_LIMIT == 200
        assert MIN_SUSPICION_SCORE == 4.5


class TestNotifiedAtStampOnce:
    def test_notifiable_queue_rule(self):
        base = {
            "suspicion_score": 5.0,
            "dredge_licence_match": False,
            "sanctions_match": False,
            "outside_anchorage": True,
            "dark_overlap": False,
            "illegal_anchor_overlap": False,
        }
        assert is_notifiable_case(base) is True
        assert is_notifiable_case({**base, "suspicion_score": 4.0}) is False
        assert is_notifiable_case({**base, "dredge_licence_match": True}) is False
        assert is_notifiable_case({
            **base,
            "outside_anchorage": False,
            "sanctions_match": False,
            "dark_overlap": False,
            "illegal_anchor_overlap": False,
        }) is False
        assert is_notifiable_case({**base, "in_port_area": True}) is False
        assert is_notifiable_case({**base, "at_berth": True}) is False

    def test_stamp_once_never_overwrites(self):
        now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        earlier = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
        assert notified_at_on_upsert(
            existing_notified_at=earlier, should_notify=True, now=now,
        ) == earlier
        assert notified_at_on_upsert(
            existing_notified_at=None, should_notify=True, now=now,
        ) == now
        assert notified_at_on_upsert(
            existing_notified_at=None, should_notify=False, now=now,
        ) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
