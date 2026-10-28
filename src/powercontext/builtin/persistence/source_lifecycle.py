# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable registry for evidence Source lifecycle bindings and availability events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

SOURCE_NODE = "source"
MEMORY_NODE = "memory"
TOPIC_NODE = "topic"
POLICY_ALL = "all"
POLICY_ANY = "any"

_REFERENCE_MODELS: dict[str, Any] = {
    SOURCE_NODE: SourceRef,
    MEMORY_NODE: MemoryCitation,
    TOPIC_NODE: ArtifactRef,
}


def canonical_json(values: Mapping[str, Any]) -> str:
    """Render one deterministic JSON document so repeated bindings compare byte-exactly."""

    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def encode_reference(kind: str, reference: Any) -> str:
    """Serialize one registered reference together with the kind that owns it."""

    model = _REFERENCE_MODELS.get(kind)
    if model is None:
        raise ValueError("unsupported lifecycle reference kind")
    if not isinstance(reference, model):
        raise ValueError("lifecycle reference does not match kind")
    return canonical_json({"kind": kind, "reference": reference.model_dump(mode="json")})


def decode_reference(payload: str) -> Any:
    """Rebuild the exact reference object registered for one node."""

    document = json.loads(payload)
    model = _REFERENCE_MODELS[str(document["kind"])]
    return model.model_validate(document["reference"])


def node_binding(
    kind: str,
    reference: Any,
    parents: Iterable[str],
    policy: str | None,
    revision: int | None,
) -> str:
    """Freeze the identity of one registration so a repeat can be proven idempotent."""

    return canonical_json(
        {
            "kind": kind,
            "reference": json.loads(encode_reference(kind, reference)),
            "parents": sorted(parents),
            "policy": policy,
            "revision": revision,
        }
    )


@dataclass(frozen=True, slots=True)
class SourceLifecycleNode:
    """One registered evidence node, its declared dependencies, and current availability."""

    node_id: str
    kind: str
    reference: Any
    parents: tuple[str, ...]
    policy: str | None
    revision: int | None
    order: int
    available: bool
    binding: str

    @property
    def is_source(self) -> bool:
        return self.kind == SOURCE_NODE

    @property
    def memory_entry_id(self) -> str | None:
        return None if self.kind != MEMORY_NODE else str(self.reference.entry_id)

    @property
    def topic_artifact_id(self) -> str | None:
        return None if self.kind != TOPIC_NODE else str(self.reference.artifact_id)

    def with_availability(self, available: bool) -> SourceLifecycleNode:
        return SourceLifecycleNode(
            node_id=self.node_id,
            kind=self.kind,
            reference=self.reference,
            parents=self.parents,
            policy=self.policy,
            revision=self.revision,
            order=self.order,
            available=available,
            binding=self.binding,
        )


def derive_availability(nodes: Iterable[SourceLifecycleNode]) -> dict[str, bool]:
    """Evaluate every node once in registration order.

    Registration requires parents to exist first, so insertion order is already a topological
    order of the graph. A derived node with no support is unavailable, and a Source keeps the
    availability recorded by its own events.
    """

    availability: dict[str, bool] = {}
    for node in sorted(nodes, key=lambda item: (item.order, item.node_id)):
        if node.is_source:
            availability[node.node_id] = node.available
            continue
        support = [availability.get(parent, False) for parent in node.parents]
        if not support:
            availability[node.node_id] = False
        elif node.policy == POLICY_ALL:
            availability[node.node_id] = all(support)
        else:
            availability[node.node_id] = any(support)
    return availability


class _SourceLifecycleTransaction:
    """One atomic view of the registry for a single Scope."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def nodes(self, scope_id: str) -> tuple[SourceLifecycleNode, ...]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await self._connection.execute(
                select(table).where(table.c.scope_id == scope_id).order_by(table.c.node_order, table.c.node_id)
            )
        ).mappings()
        return tuple(
            SourceLifecycleNode(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
                reference=decode_reference(str(row["reference_payload"])),
                parents=tuple(json.loads(str(row["parents_payload"]))),
                policy=None if row["policy"] is None else str(row["policy"]),
                revision=None if row["revision"] is None else int(row["revision"]),
                order=int(row["node_order"]),
                available=bool(int(row["available_state"])),
                binding=str(row["binding_payload"]),
            )
            for row in rows
        )

    async def next_order(self, scope_id: str) -> int:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        current = await self._connection.scalar(
            select(table.c.node_order)
            .where(table.c.scope_id == scope_id)
            .order_by(table.c.node_order.desc())
            .limit(1)
        )
        return 1 if current is None else int(current) + 1

    async def insert_node(self, scope_id: str, node: SourceLifecycleNode) -> None:
        await self._connection.execute(
            insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                scope_id=scope_id,
                node_id=node.node_id,
                kind=node.kind,
                reference_payload=encode_reference(node.kind, node.reference),
                parents_payload=json.dumps(list(node.parents)),
                policy=node.policy,
                revision=node.revision,
                node_order=node.order,
                available_state=1 if node.available else 0,
                binding_payload=node.binding,
            )
        )

    async def set_availability(self, scope_id: str, updates: Mapping[str, bool]) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        for node_id, available in updates.items():
            await self._connection.execute(
                update(table)
                .where(table.c.scope_id == scope_id, table.c.node_id == node_id)
                .values(available_state=1 if available else 0)
            )

    async def max_sequence(self, scope_id: str, node_id: str) -> int:
        table = SOURCE_LIFECYCLE_EVENTS_TABLE
        current = await self._connection.scalar(
            select(table.c.event_sequence)
            .where(table.c.scope_id == scope_id, table.c.node_id == node_id)
            .order_by(table.c.event_sequence.desc())
            .limit(1)
        )
        return 0 if current is None else int(current)

    async def event_content(self, scope_id: str, node_id: str, sequence: int) -> str | None:
        table = SOURCE_LIFECYCLE_EVENTS_TABLE
        row = await self._connection.execute(
            select(table.c.content_payload).where(
                table.c.scope_id == scope_id,
                table.c.node_id == node_id,
                table.c.event_sequence == sequence,
            )
        )
        value = row.scalar()
        return None if value is None else str(value)

    async def insert_event(
        self,
        scope_id: str,
        node_id: str,
        sequence: int,
        content: str,
        available: bool,
    ) -> None:
        await self._connection.execute(
            insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                scope_id=scope_id,
                node_id=node_id,
                event_sequence=sequence,
                content_payload=content,
                available_state=1 if available else 0,
            )
        )

    async def has_source(self, scope_id: str, ref: SourceRef) -> bool:
        table = SOURCES_TABLE
        found = await self._connection.scalar(
            select(table.c.source_id).where(
                table.c.scope_id == scope_id,
                table.c.source_type == ref.source_type,
                table.c.source_id == ref.source_id,
            )
        )
        return found is not None


class SourceLifecycleStore:
    """Own the persistence seam for one deployment's evidence-lifecycle registries."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database
        self._locks: dict[str, asyncio.Lock] = {}

    def scope_lock(self, scope_id: str) -> asyncio.Lock:
        """Serialize registry mutation for one Scope without reusing the Memory write lock."""

        scope = validate_scope_id(scope_id)
        lock = self._locks.get(scope)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope] = lock
        return lock

    @asynccontextmanager
    async def transaction(self):
        async with self._database.transaction() as connection:
            yield _SourceLifecycleTransaction(connection)

    async def nodes(self, scope_id: str) -> tuple[SourceLifecycleNode, ...]:
        scope = validate_scope_id(scope_id)
        async with self.transaction() as lifecycle:
            return await lifecycle.nodes(scope)

    async def availability(self, scope_id: str) -> dict[str, bool]:
        return {node.node_id: node.available for node in await self.nodes(scope_id)}

    async def blocked_topic_artifact_ids(self, connection: AsyncConnection, scope_id: str) -> frozenset[str]:
        """Return Topic artifacts that currently have no surviving support.

        Only registered Topic nodes are gated, so every other Scope and family keeps the native
        retrieval behavior untouched.
        """

        scope = validate_scope_id(scope_id)
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await connection.execute(
                select(table.c.reference_payload).where(
                    table.c.scope_id == scope,
                    table.c.kind == TOPIC_NODE,
                    table.c.available_state == 0,
                )
            )
        ).mappings()
        return frozenset(str(decode_reference(str(row["reference_payload"])).artifact_id) for row in rows)


__all__ = [
    "MEMORY_NODE",
    "POLICY_ALL",
    "POLICY_ANY",
    "SOURCE_NODE",
    "TOPIC_NODE",
    "SourceLifecycleNode",
    "SourceLifecycleStore",
    "canonical_json",
    "decode_reference",
    "derive_availability",
    "encode_reference",
    "node_binding",
]
