"""Minimal bookmarks service used by the SDD context demo."""

from datetime import datetime, timezone

_NEXT_ID = 1
_ITEMS = []


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_bookmark(url, title, tags=None):
    global _NEXT_ID
    if not url:
        raise ValueError("url is required")
    clean_tags = []
    for tag in tags or []:
        value = str(tag).strip()
        if value and value not in clean_tags:
            clean_tags.append(value)
    item = {
        "id": _NEXT_ID,
        "url": url,
        "title": title,
        "tags": clean_tags,
        "created_at": _now(),
    }
    _NEXT_ID += 1
    _ITEMS.append(item)
    return item["id"]


def list_bookmarks(tag=None, limit=10):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    selected = list(reversed(_ITEMS))
    if tag:
        normalized_tag = str(tag).strip().casefold()
        selected = [
            item
            for item in selected
            if any(
                str(item_tag).casefold() == normalized_tag
                for item_tag in item["tags"]
            )
        ]
    return [
        {
            "id": item["id"],
            "url": item["url"],
            "title": item["title"],
            "tags": list(item["tags"]),
        }
        for item in selected[:limit]
    ]


def delete_bookmark(bookmark_id):
    for index, item in enumerate(_ITEMS):
        if item["id"] == bookmark_id:
            del _ITEMS[index]
            return True
    return False
