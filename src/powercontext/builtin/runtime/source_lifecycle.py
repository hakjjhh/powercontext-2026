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

"""Propagate evidence Source availability through Memory and Topic Memory.

The lifecycle registry is an explicit dependency graph supplied by the caller: Sources support
Memory entries, and Memory supports Topic Memory. A withdrawal therefore has to reach every
descendant, while the immutable bodies behind the registered references stay readable for audit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.source_lifecycle import (
    MEMORY_NODE,
    POLICY_ALL,
    POLICY_ANY,
    SOURCE_NODE,
    TOPIC_NODE,
    SourceLifecycleNode,
    SourceLifecycleStore,
    canonical_json,
    derive_availability,
    node_binding,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

GENERATION_PURPOSE = "generation"
AUDIT_PURPOSE = "audit"

MAX_LIFECYCLE_NODE_ID_LENGTH = 255
_CHANGE_REASON_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;/_-]{0,119}$")
_FALLBACK_CHANGE_REASON = "source lifecycle"


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle registration, event, or resolution breaks the contract."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


class SourceLifecycleEvent(BaseModel):
    """One caller-supplied availability transition for a registered Source node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=MAX_LIFECYCLE_NODE_ID_LENGTH)
    revision: int = Field(gt=0, strict=True)
    sequence: int = Field(gt=0, strict=True)
    available: bool = Field(strict=True)
    reason: str = Field(min_length=1)

    @field_validator("node_id", "reason")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def _reject_boolean_ordinals(cls, value: int) -> int:
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        return value


def _require_identifier(label: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("invalid-identifier", label)
    if len(value) > MAX_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError("identifier-too-long", label)
    return value


def _require_positive_int(label: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError("invalid-ordinal", label)
    return value


def _validate_parents(node_id: str, parents: Any) -> tuple[str, ...]:
    if isinstance(parents, (str, bytes)) or not isinstance(parents, (tuple, list)):
        raise SourceLifecycleError("parents-must-be-sequence", node_id)
    identities = tuple(_require_identifier("parents", parent) for parent in parents)
    if not identities:
        raise SourceLifecycleError("parents-required", node_id)
    if len(set(identities)) != len(identities):
        raise SourceLifecycleError("parents-duplicated", node_id)
    if node_id in identities:
        raise SourceLifecycleError("parents-self-reference", node_id)
    return identities


def _validate_policy(policy: Any) -> str:
    if policy not in (POLICY_ALL, POLICY_ANY):
        raise SourceLifecycleError("invalid-policy", str(policy))
    return str(policy)


def _change_reason(reason: str | None) -> str:
    if reason is None:
        return _FALLBACK_CHANGE_REASON
    normalized = reason.strip()
    return normalized if _CHANGE_REASON_PATTERN.match(normalized) else _FALLBACK_CHANGE_REASON


class ScopedSourceLifecycleApplication:
    """Own one Scope's evidence graph and drive native retrieval state from it."""

    def __init__(self, runtime: Any, store: SourceLifecycleStore, scope_id: str) -> None:
        self._runtime = runtime
        self._store = store
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, revision: int) -> bool:
        """Bind one captured Source and start it as available.

        Availability before the first event is deliberately permissive: the caller has not
        withdrawn anything, and the recorded events replay monotonically from that point.
        """

        identity = _require_identifier("node_id", node_id)
        ordinal = _require_positive_int("revision", revision)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-reference-type", identity)
        async with self._store.scope_lock(self.scope_id):
            async with self._store.transaction() as lifecycle:
                if not await lifecycle.has_source(self.scope_id, source_ref):
                    raise SourceLifecycleError("source-reference-unreadable", identity)
                registered = await self._registered(lifecycle)
                binding = node_binding(SOURCE_NODE, source_ref, (), None, ordinal)
                existing = registered.get(identity)
                if existing is not None:
                    if existing.binding != binding:
                        raise SourceLifecycleError("registration-conflict", identity)
                    return existing.available
                await lifecycle.insert_node(
                    self.scope_id,
                    SourceLifecycleNode(
                        node_id=identity,
                        kind=SOURCE_NODE,
                        reference=source_ref,
                        parents=(),
                        policy=None,
                        revision=ordinal,
                        order=await lifecycle.next_order(self.scope_id),
                        available=True,
                        binding=binding,
                    ),
                )
                return True

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Iterable[str],
        policy: str,
    ) -> bool:
        """Bind one Memory entry to its supporting nodes and apply the result immediately."""

        identity = _require_identifier("node_id", node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-citation-type", identity)
        dependencies = _validate_parents(identity, parents)
        selected = _validate_policy(policy)
        async with self._store.scope_lock(self.scope_id):
            await self._require_memory_readable(citation)
            async with self._store.transaction() as lifecycle:
                node = await self._insert_derivative(
                    lifecycle,
                    identity,
                    MEMORY_NODE,
                    citation,
                    dependencies,
                    selected,
                )
            await self._sync_memory((await self._graph()).values())
        return node.available

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Iterable[str],
        policy: str,
    ) -> bool:
        """Bind one Topic Memory revision and gate its discovery by the computed state."""

        identity = _require_identifier("node_id", node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("topic-reference-type", identity)
        dependencies = _validate_parents(identity, parents)
        selected = _validate_policy(policy)
        async with self._store.scope_lock(self.scope_id):
            await self._require_topic_readable(artifact_ref)
            async with self._store.transaction() as lifecycle:
                node = await self._insert_derivative(
                    lifecycle,
                    identity,
                    TOPIC_NODE,
                    artifact_ref,
                    dependencies,
                    selected,
                )
        return node.available

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Record one Source transition, propagate it, and return every available node."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event-type")
        async with self._store.scope_lock(self.scope_id):
            async with self._store.transaction() as lifecycle:
                persisted = list(await lifecycle.nodes(self.scope_id))
                registered = {node.node_id: node for node in persisted}
                target = registered.get(event.node_id)
                if target is None or not target.is_source:
                    raise SourceLifecycleError("unknown-source", event.node_id)
                if target.revision != event.revision:
                    raise SourceLifecycleError("revision-mismatch", event.node_id)
                content = canonical_json(
                    {"revision": event.revision, "available": event.available, "reason": event.reason}
                )
                recorded = await lifecycle.event_content(self.scope_id, event.node_id, event.sequence)
                if recorded is not None:
                    if recorded != content:
                        raise SourceLifecycleError("sequence-conflict", event.node_id)
                elif event.sequence < await lifecycle.max_sequence(self.scope_id, event.node_id):
                    raise SourceLifecycleError("sequence-regression", event.node_id)
                else:
                    await lifecycle.insert_event(
                        self.scope_id,
                        event.node_id,
                        event.sequence,
                        content,
                        event.available,
                    )
                graph = [
                    node.with_availability(event.available) if node.node_id == event.node_id else node
                    for node in persisted
                ]
                availability = derive_availability(graph)
                changes = {
                    node.node_id: availability[node.node_id]
                    for node in persisted
                    if node.available != availability[node.node_id]
                }
                await lifecycle.set_availability(self.scope_id, changes)
                graph = [node.with_availability(availability[node.node_id]) for node in graph]
            await self._sync_memory(graph, reason=event.reason)
        return sorted(node.node_id for node in graph if node.available)

    async def resolve(self, node_id: str, *, purpose: str = GENERATION_PURPOSE) -> Any:
        """Return the registered reference when the node may serve the requested purpose."""

        identity = _require_identifier("node_id", node_id)
        if purpose not in (GENERATION_PURPOSE, AUDIT_PURPOSE):
            raise SourceLifecycleError("unknown-purpose", str(purpose))
        node = (await self._graph()).get(identity)
        if node is None:
            raise SourceLifecycleError("unknown-node", identity)
        if purpose == GENERATION_PURPOSE and not node.available:
            raise SourceLifecycleError("node-unavailable", identity)
        return node.reference

    async def availability(self) -> dict[str, bool]:
        """Expose the computed graph state so callers can inspect one Scope directly."""

        return {node.node_id: node.available for node in (await self._graph()).values()}

    async def _registered(self, lifecycle: Any) -> dict[str, SourceLifecycleNode]:
        return {node.node_id: node for node in await lifecycle.nodes(self.scope_id)}

    async def _graph(self) -> dict[str, SourceLifecycleNode]:
        return {node.node_id: node for node in await self._store.nodes(self.scope_id)}

    async def _insert_derivative(
        self,
        lifecycle: Any,
        node_id: str,
        kind: str,
        reference: Any,
        parents: tuple[str, ...],
        policy: str,
    ) -> SourceLifecycleNode:
        registered = await self._registered(lifecycle)
        binding = node_binding(kind, reference, parents, policy, None)
        existing = registered.get(node_id)
        if existing is not None:
            if existing.binding != binding:
                raise SourceLifecycleError("registration-conflict", node_id)
            return existing
        for parent in parents:
            if parent == node_id:
                raise SourceLifecycleError("parents-self-reference", node_id)
            if parent not in registered:
                raise SourceLifecycleError("parent-unregistered", parent)
        draft = SourceLifecycleNode(
            node_id=node_id,
            kind=kind,
            reference=reference,
            parents=parents,
            policy=policy,
            revision=None,
            order=await lifecycle.next_order(self.scope_id),
            available=False,
            binding=binding,
        )
        availability = derive_availability([*registered.values(), draft])
        node = draft.with_availability(availability[node_id])
        await lifecycle.insert_node(self.scope_id, node)
        return node

    async def _require_memory_readable(self, citation: MemoryCitation) -> None:
        try:
            async with self._runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                await service.revision(citation.memory_ref)
                await service.validate_citation(citation)
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("memory-citation-unreadable", citation.entry_id) from error

    async def _require_topic_readable(self, artifact_ref: ArtifactRef) -> None:
        try:
            application = self._runtime.records.for_scope(self.scope_id)
            record = await application.get_artifact_revision(
                artifact_ref.family,
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
            if not isinstance(record.content, dict):
                raise SourceLifecycleError("topic-reference-unreadable", artifact_ref.artifact_id)
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("topic-reference-unreadable", artifact_ref.artifact_id) from error

    async def _sync_memory(
        self,
        graph: Iterable[SourceLifecycleNode] | Mapping[str, SourceLifecycleNode],
        *,
        reason: str | None = None,
    ) -> None:
        """Align native Memory entry state with the graph, leaving bodies and citations untouched."""

        nodes = graph.values() if isinstance(graph, Mapping) else graph
        wanted = [node for node in nodes if node.kind == MEMORY_NODE]
        if not wanted:
            return
        change_reason = _change_reason(reason)
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            service = context.artifacts.memory
            async with self._runtime._locked(self.scope_id):
                try:
                    head = await service.head(context.artifacts.memory_artifact_id)
                except ArtifactNotFoundError:
                    return
                versions = {entry.entry_id: entry for entry in await service.entries(head)}
                states = {entry.entry_id: entry.state for entry in head.content.manifest.entries}
                deactivated: list[Any] = []
                reactivated: list[Any] = []
                for node in wanted:
                    entry_id = node.memory_entry_id
                    version = versions.get(entry_id)
                    state = states.get(entry_id)
                    if version is None or state is None:
                        raise SourceLifecycleError("memory-entry-unreadable", node.node_id)
                    if node.available and state != "active":
                        reactivated.append(version)
                    elif not node.available and state != "inactive":
                        deactivated.append(version)
                if not deactivated and not reactivated:
                    return
                current = head
                if deactivated:
                    current = await service.forget(
                        current,
                        entries=tuple(deactivated),
                        reason=change_reason,
                    )
                if reactivated:
                    await service.reactivate(
                        current,
                        entries=tuple(reactivated),
                        reason=change_reason,
                    )


class SourceLifecycleApplication:
    """Select the evidence-lifecycle service for one Scope."""

    def __init__(self, runtime: Any, store: SourceLifecycleStore | None) -> None:
        self._runtime = runtime
        self._store = store

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleApplication:
        if self._store is None:
            raise SourceLifecycleError("source-lifecycle-unavailable")
        return ScopedSourceLifecycleApplication(self._runtime, self._store, scope_id)


__all__ = [
    "AUDIT_PURPOSE",
    "GENERATION_PURPOSE",
    "ScopedSourceLifecycleApplication",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
