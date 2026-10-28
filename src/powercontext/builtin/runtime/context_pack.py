# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Content-addressed context packs and their incremental wire deltas.

A prepared Context is transferred between an Agent runtime and a context service
over and over again, and most of the time only a small part of it changes. A
:class:`ContextPack` freezes one snapshot of that content as an ordered list of
byte items with a digest computed over the canonical wire form, so the receiver
can tell whether anything it already holds is still valid. A
:class:`ContextPackDelta` then carries only the changed suffix: the number of
leading items the receiver may reuse plus the replacement items.

Both types own their serialisation. The canonical JSON form is byte-for-byte
stable (sorted keys, compact separators, base64 items), the digest covers the
payload without its own accounting fields, and every decoder re-derives the
digest instead of trusting the sender, so a tampered or truncated message is
rejected rather than silently applied.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
SHA256_PREFIX = "sha256:"
DEFAULT_CHUNK_BYTES = 512

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContextPackError(ValueError):
    """Raised when a context pack or delta is malformed, truncated, or tampered with."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_item(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_item(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise ContextPackError(f"{field} must be a base64 string")
    try:
        item = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ContextPackError(f"{field} is not valid base64") from error
    if _encode_item(item) != value:
        raise ContextPackError(f"{field} is not canonically encoded")
    return item


def _require_items(value: Any, field: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field} must be a list of base64 strings")
    return tuple(_decode_item(entry, field) for entry in value)


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ContextPackError(f"{field} must be a lowercase sha256 digest")
    return value


def _require_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContextPackError(f"{field} must be an integer")
    if value < 0:
        raise ContextPackError(f"{field} must not be negative")
    return value


def _require_byte_count(value: Any, expected: int, field: str = "byte_count") -> int:
    size = _require_integer(value, field)
    if size != expected:
        raise ContextPackError(f"{field} does not match the encoded message")
    return size


def _digest_of(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key not in {"digest", "byte_count"}}
    return SHA256_PREFIX + hashlib.sha256(_canonical(body)).hexdigest()


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, (bytes, bytearray)):
        raise ContextPackError(f"{label} must be bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(f"{label} is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise ContextPackError(f"{label} must be a JSON object")
    return decoded


def _split_utf8(text: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")
    encoded = text.encode("utf-8")
    if not encoded:
        return ()
    items: list[bytes] = []
    cursor = 0
    while cursor < len(encoded):
        limit = min(cursor + chunk_bytes, len(encoded))
        if limit < len(encoded):
            # Never cut a multi-byte character in half: back off to the last boundary.
            while limit > cursor and (encoded[limit] & 0xC0) == 0x80:
                limit -= 1
            if limit == cursor:
                raise ContextPackError("chunk_bytes is too small to hold one UTF-8 character")
        items.append(encoded[cursor:limit])
        cursor = limit
    return tuple(items)


def _common_prefix(base: tuple[bytes, ...], target: tuple[bytes, ...]) -> int:
    shared = 0
    for left, right in zip(base, target, strict=False):
        if left != right:
            break
        shared += 1
    return shared


class ContextPack:
    """An immutable, content-addressed snapshot of prepared context bytes."""

    __slots__ = ("_items", "_digest", "_byte_count")

    def __init__(self, items: tuple[bytes, ...], digest: str, byte_count: int) -> None:
        self._items = tuple(items)
        self._digest = digest
        self._byte_count = byte_count

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Freeze an ordered batch of byte items into one pack."""

        if isinstance(items, (str, bytes, bytearray)):
            raise ContextPackError("items must be a tuple of bytes")
        normalised = tuple(_require_item(item) for item in items)
        payload = {"schema": CONTEXT_PACK_SCHEMA, "items": [_encode_item(item) for item in normalised]}
        digest = SHA256_PREFIX + hashlib.sha256(_canonical(payload)).hexdigest()
        pack = cls(normalised, digest, 0)
        pack.to_bytes()
        return pack

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Build one pack by splitting UTF-8 content into whole-character chunks."""

        if not isinstance(content, str):
            raise ContextPackError("content must be a string")
        return cls.build(_split_utf8(content, chunk_bytes))

    @classmethod
    def from_bytes(cls, raw: bytes, /) -> ContextPack:
        """Decode and verify the canonical wire form of one pack."""

        decoded = _parse_json(raw, "context pack")
        if set(decoded) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("context pack fields do not match the schema")
        if decoded["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        items = _require_items(decoded["items"], "items")
        claimed = _require_digest(decoded["digest"], "digest")
        recomputed = _digest_of({"schema": decoded["schema"], "items": decoded["items"], "digest": claimed})
        if recomputed != claimed:
            raise ContextPackError("context pack digest does not match its payload")
        byte_count = _require_byte_count(decoded["byte_count"], len(raw))
        return cls(items, claimed, byte_count)

    @property
    def schema(self) -> str:
        return CONTEXT_PACK_SCHEMA

    @property
    def items(self) -> tuple[bytes, ...]:
        """Return the ordered byte items held by this pack."""

        return self._items

    @property
    def digest(self) -> str:
        """Return the ``sha256:`` digest of the canonical payload."""

        return self._digest

    @property
    def byte_count(self) -> int:
        """Return the exact size of :meth:`to_bytes`."""

        return self._byte_count

    @property
    def content(self) -> str:
        """Return the decoded UTF-8 content, rejecting partial characters."""

        return b"".join(self._items).decode("utf-8")

    def to_bytes(self) -> bytes:
        """Render the canonical, byte-stable wire form."""

        payload = {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": [_encode_item(item) for item in self._items],
            "digest": self._digest,
        }
        for _ in range(8):
            rendered = _canonical({**payload, "byte_count": self._byte_count})
            if len(rendered) == self._byte_count:
                return rendered
            self._byte_count = len(rendered)
        raise ContextPackError("context pack byte count did not stabilise")

    def delta_to(self, target: ContextPack, /) -> ContextPackDelta:
        """Return the smallest delta that turns this pack into ``target``."""

        return ContextPackDelta.between(self, target)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ContextPack) and self.to_bytes() == other.to_bytes()

    def __hash__(self) -> int:
        return hash(self._digest)

    def __repr__(self) -> str:
        return f"ContextPack(items={len(self._items)}, digest={self._digest!r}, byte_count={self._byte_count})"


class ContextPackDelta:
    """The changed suffix between two context packs."""

    __slots__ = ("_base_digest", "_target_digest", "_prefix_items", "_appended", "_byte_count")

    def __init__(
        self,
        base_digest: str,
        target_digest: str,
        prefix_items: int,
        appended: tuple[bytes, ...],
        byte_count: int = 0,
    ) -> None:
        self._base_digest = _require_digest(base_digest, "base_digest")
        self._target_digest = _require_digest(target_digest, "target_digest")
        self._prefix_items = _require_integer(prefix_items, "prefix_items")
        self._appended = tuple(appended)
        self._byte_count = byte_count

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Compute the delta whose reused prefix is the longest common prefix."""

        prefix = _common_prefix(base.items, target.items)
        delta = cls(base.digest, target.digest, prefix, target.items[prefix:])
        delta.to_bytes()
        return delta

    @classmethod
    def from_bytes(cls, raw: bytes, /) -> ContextPackDelta:
        """Decode and verify the canonical wire form of one delta."""

        decoded = _parse_json(raw, "context pack delta")
        required = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if set(decoded) != required:
            raise ContextPackError("context pack delta fields do not match the schema")
        if decoded["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        base_digest = _require_digest(decoded["base_digest"], "base_digest")
        target_digest = _require_digest(decoded["target_digest"], "target_digest")
        prefix_items = _require_integer(decoded["prefix_items"], "prefix_items")
        appended = _require_items(decoded["appended"], "appended")
        delta = cls(base_digest, target_digest, prefix_items, appended)
        delta.to_bytes()
        return delta

    @property
    def schema(self) -> str:
        return CONTEXT_PACK_DELTA_SCHEMA

    @property
    def base_digest(self) -> str:
        return self._base_digest

    @property
    def target_digest(self) -> str:
        return self._target_digest

    @property
    def prefix_items(self) -> int:
        """Return how many leading base items the receiver may reuse."""

        return self._prefix_items

    @property
    def appended(self) -> tuple[bytes, ...]:
        """Return the replacement items that follow the reused prefix."""

        return self._appended

    @property
    def byte_count(self) -> int:
        """Return the exact size of :meth:`to_bytes`."""

        return self._byte_count

    def to_bytes(self) -> bytes:
        """Render the canonical, byte-stable wire form."""

        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self._base_digest,
            "target_digest": self._target_digest,
            "prefix_items": self._prefix_items,
            "appended": [_encode_item(item) for item in self._appended],
        }
        rendered = _canonical(payload)
        self._byte_count = len(rendered)
        return rendered

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ContextPackDelta) and self.to_bytes() == other.to_bytes()

    def __hash__(self) -> int:
        return hash((self._base_digest, self._target_digest, self._prefix_items, self._appended))

    def __repr__(self) -> str:
        return (
            f"ContextPackDelta(base_digest={self._base_digest!r}, target_digest={self._target_digest!r}, "
            f"prefix_items={self._prefix_items}, appended={len(self._appended)})"
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack described by ``base`` plus ``delta``."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta does not apply to the supplied base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta reuses more items than the base pack holds")
    rebuilt = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("delta does not reconstruct the announced target pack")
    return rebuilt


def _require_item(item: Any) -> bytes:
    if isinstance(item, memoryview):
        item = item.tobytes()
    if not isinstance(item, bytes):
        raise ContextPackError("context pack items must be bytes")
    return item


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]