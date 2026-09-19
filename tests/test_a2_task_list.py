"""Task list pagination: keyset cursors survive concurrent inserts."""

from __future__ import annotations

from pathlib import Path

import pytest

from scholartrace.delivery.models import DemoMode, TaskStatus
from scholartrace.delivery.store import DeliveryStore, InvalidCursorError


def _seed(store: DeliveryStore, count: int, *, prefix: str = "task") -> None:
    for index in range(count):
        store.create_task(
            task_id=f"{prefix}:{index:03d}",
            thread_id=f"thread:{prefix}:{index:03d}",
            title=f"Task {index}",
            question=f"Question number {index}",
            demo_mode=DemoMode.SUCCESS,
        )


def test_pages_cover_every_task_exactly_once(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 25)
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            page = store.list_tasks(cursor=cursor, limit=10)
            seen.extend(item["task_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 25
        assert len(set(seen)) == 25
        assert page["total_known"] == 25
    finally:
        store.close()


def test_insert_during_paging_does_not_skip_or_repeat_earlier_rows(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 12)
        first = store.list_tasks(limit=5)
        # A new task arrives mid-page-walk. Keyset paging means the rows we have
        # not reached yet are unaffected; OFFSET would have shifted them.
        _seed(store, 1, prefix="late")
        second = store.list_tasks(cursor=first["next_cursor"], limit=5)
        overlap = {i["task_id"] for i in first["items"]} & {i["task_id"] for i in second["items"]}
        assert overlap == set()
    finally:
        store.close()


def test_status_filter_narrows_both_items_and_total(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 6)
        store.update_task("task:000", status=TaskStatus.COMPLETED)
        store.update_task("task:001", status=TaskStatus.COMPLETED)
        page = store.list_tasks(status=TaskStatus.COMPLETED, limit=10)
        assert {item["task_id"] for item in page["items"]} == {"task:000", "task:001"}
        assert page["total_known"] == 2
    finally:
        store.close()


def test_last_page_has_no_next_cursor(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 3)
        page = store.list_tasks(limit=10)
        assert page["next_cursor"] is None
        assert len(page["items"]) == 3
    finally:
        store.close()


def test_exact_multiple_of_limit_terminates_cleanly(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 10)
        first = store.list_tasks(limit=5)
        assert first["next_cursor"] is not None
        second = store.list_tasks(cursor=first["next_cursor"], limit=5)
        assert len(second["items"]) == 5
        assert second["next_cursor"] is None
    finally:
        store.close()


def test_empty_database_returns_an_empty_page(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        page = store.list_tasks(limit=20)
        assert page == {"items": [], "next_cursor": None, "total_known": 0}
    finally:
        store.close()


@pytest.mark.parametrize("cursor", ["not-base64!", "", "e30=", "YWJj"])
def test_malformed_cursor_is_rejected(tmp_path: Path, cursor: str) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 2)
        with pytest.raises(InvalidCursorError):
            store.list_tasks(cursor=cursor, limit=5)
    finally:
        store.close()


@pytest.mark.parametrize("limit", [0, -1, 101, 1000])
def test_limit_outside_the_allowed_range_is_refused(tmp_path: Path, limit: int) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        with pytest.raises(ValueError):
            store.list_tasks(limit=limit)
    finally:
        store.close()


def test_cursor_round_trip_is_stable(tmp_path: Path) -> None:
    encoded = DeliveryStore.encode_cursor("2026-09-16T10:00:00Z", "task:abc")
    assert DeliveryStore.decode_cursor(encoded) == ("2026-09-16T10:00:00Z", "task:abc")


def test_listed_rows_expose_execution_mode(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        _seed(store, 1)
        item = store.list_tasks(limit=1)["items"][0]
        assert item["execution_mode"] == "demo"
        assert "metrics" in item and "degradations" in item
    finally:
        store.close()
