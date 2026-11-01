import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import bookmarks  # noqa: E402


@pytest.fixture(autouse=True)
def reset_store():
    bookmarks._ITEMS.clear()
    bookmarks._NEXT_ID = 1
    yield


def test_add_and_list_newest_first():
    first = bookmarks.add_bookmark("https://a.example", "A", ["db"])
    second = bookmarks.add_bookmark("https://b.example", "B", ["ai", "db"])
    items = bookmarks.list_bookmarks()
    assert [item["id"] for item in items] == [second, first]
    assert "created_at" not in items[0]


def test_filter_by_tag_is_case_insensitive():
    bookmarks.add_bookmark("https://a.example", "A", ["Database"])
    bookmarks.add_bookmark("https://b.example", "B", ["AI"])
    items = bookmarks.list_bookmarks(tag="database")
    assert [item["title"] for item in items] == ["A"]


def test_unknown_tag_returns_empty_list():
    bookmarks.add_bookmark("https://a.example", "A", ["db"])
    assert bookmarks.list_bookmarks(tag="missing") == []


def test_limit_and_invalid_limit():
    for index in range(3):
        bookmarks.add_bookmark(f"https://{index}.example", str(index), ["db"])
    assert len(bookmarks.list_bookmarks(limit=2)) == 2
    with pytest.raises(ValueError):
        bookmarks.list_bookmarks(limit=0)


def test_delete_bookmark():
    bookmark_id = bookmarks.add_bookmark("https://a.example", "A", ["db"])
    assert bookmarks.delete_bookmark(bookmark_id) is True
    assert bookmarks.delete_bookmark(bookmark_id) is False
