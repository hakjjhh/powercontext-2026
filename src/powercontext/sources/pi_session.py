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

"""Pi session branch Sources with durable incremental collection.

The Pi assistant stores one session as JSONL: a ``session`` header followed by
records connected through ``parentId``.  The selected leaf and its ancestors
determine one history path, and every visible event on that path becomes an
immutable ``pi-event`` Source so later evidence recall can cite the original
record instead of a model-compressed view.
"""

# ruff: noqa: TRY003

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_TYPE = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_SESSION_TYPE = "session"
_SESSION_VERSION = 3
_SUMMARY_TYPES = frozenset({"compaction", "branch_summary"})
_CUSTOM_TYPE = "custom_message"
_CUSTOM_ROLE = "custom"
_MESSAGE_TYPE = "message"
_TOOL_RESULT_ROLE = "toolResult"
_BASH_ROLE = "bashExecution"
_CHECKPOINT_KEY = "sessions"


def _require_trimmed(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


def _require_non_blank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_trimmed(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_trimmed(value, field)


class PiSessionInput(BaseModel):
    """One complete Pi session archive plus its optional selected leaf."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None

    @field_validator("leaf_id")
    @classmethod
    def validate_leaf_id(cls, value: str | None) -> str | None:
        return _optional_trimmed(value, "leaf_id")


class PiEventInput(BaseModel):
    """The lossless representation of one visible event on a Pi history path."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @field_validator("session_id", "event_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _require_trimmed(value, "event identity")

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        return _optional_trimmed(value, "parent_id")

    @field_validator("event_type", "role")
    @classmethod
    def validate_classification(cls, value: str) -> str:
        return _require_trimmed(value, "event classification")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must carry visible text")
        return value


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity for one event of one session."""

    _require_trimmed(session_id, "session_id")
    _require_trimmed(event_id, "event_id")
    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pi-event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def _decode(content: bytes) -> str:
    if not isinstance(content, bytes):
        raise ValueError("Pi session content must be bytes")
    try:
        return content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error


def _read_records(content: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Parse the archive and validate the whole session graph atomically."""

    lines = [line for line in _decode(content).splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session archive must not be empty")
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError as error:
            raise ValueError("Pi session lines must be valid JSON objects") from error
        if not isinstance(parsed, dict):
            raise ValueError("Pi session records must be JSON objects")
        records.append(parsed)

    header, *nodes = records
    if header.get("type") != _SESSION_TYPE or header.get("version") != _SESSION_VERSION:
        raise ValueError("Pi session archive must start with a version 3 session record")
    session_id = _require_trimmed(header.get("id"), "session id")

    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in nodes:
        event_id = _require_trimmed(record.get("id"), "event id")
        if event_id in by_id:
            raise ValueError(f"Pi session event id {event_id!r} must be unique")
        parent_id = record.get("parentId")
        if parent_id is not None and parent_id not in by_id:
            raise ValueError(f"Pi session event {event_id!r} references an unknown parent")
        by_id[event_id] = record
        ordered.append(record)
    return session_id, ordered


def _ancestors(session_id: str, ordered: list[dict[str, Any]], leaf_id: str | None) -> list[dict[str, Any]]:
    if not ordered:
        if leaf_id is not None:
            raise ValueError(f"Pi session {session_id!r} does not contain leaf {leaf_id!r}")
        return []
    by_id = {record["id"]: record for record in ordered}
    selected = ordered[-1]["id"] if leaf_id is None else leaf_id
    record = by_id.get(selected)
    if record is None:
        raise ValueError(f"Pi session {session_id!r} does not contain leaf {selected!r}")

    path: list[dict[str, Any]] = []
    visited: set[str] = set()
    current: dict[str, Any] | None = record
    while current is not None:
        event_id = current["id"]
        if event_id in visited:
            raise ValueError(f"Pi session {session_id!r} contains a parent cycle at {event_id!r}")
        visited.add(event_id)
        path.append(current)
        parent_id = current.get("parentId")
        current = None if parent_id is None else by_id[parent_id]
    path.reverse()
    return path


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_values(content: object) -> list[str]:
    """Return visible string or text-block values without touching other blocks."""

    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        values = [block.get("text") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return [value for value in values if isinstance(value, str) and value]
    return []


def _compact_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _visible_values(content: object) -> list[str]:
    """Render message bodies; thinking and image blocks never become text evidence."""

    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        values: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                values.extend(_text_values([block]))
            elif kind == "toolCall":
                name = _string(block.get("name"))
                call_id = _string(block.get("id"))
                values.append(f"[tool_call {name} {call_id}] {_compact_json(block.get('arguments'))}")
        return [value for value in values if value]
    return []


def _role_for(event_type: str, message: object) -> str | None:
    if event_type in _SUMMARY_TYPES:
        return event_type
    if event_type == _CUSTOM_TYPE:
        return _CUSTOM_ROLE
    if event_type == _MESSAGE_TYPE and isinstance(message, dict):
        role = message.get("role")
        return role if isinstance(role, str) and role.strip() else None
    return None


def _body_for(event_type: str, role: str, record: dict[str, Any]) -> str:
    if event_type in _SUMMARY_TYPES:
        return _string(record.get("summary"))
    if event_type == _CUSTOM_TYPE:
        return "\n".join(_text_values(record.get("content")))

    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    if role == _TOOL_RESULT_ROLE:
        text = "\n".join(_text_values(message.get("content")))
        if not text:
            return ""
        name = _string(message.get("toolName"))
        call_id = _string(message.get("toolCallId"))
        return f"[tool_result {name} {call_id}] {text}"
    if role == _BASH_ROLE:
        command = _string(message.get("command"))
        output = message.get("output")
        if not isinstance(output, str) or not output:
            output = "\n".join(_text_values(message.get("content")))
        return f"$ {command}\n{output}" if output else f"$ {command}"
    return "\n".join(_visible_values(message.get("content")))


def _event_for(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    event_type = record.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    message = record.get("message")
    role = _role_for(event_type, message)
    if role is None:
        return None
    body = _body_for(event_type, role, record)
    if not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return root-to-leaf visible events for the selected branch of one archive."""

    if not isinstance(value, PiSessionInput):
        raise ValueError("parse_pi_session requires a PiSessionInput")
    session_id, ordered = _read_records(value.content)
    path = _ancestors(session_id, ordered, value.leaf_id)
    events = [_event_for(session_id, record) for record in path]
    return tuple(event for event in events if event is not None)


class PiEventSource(Source):
    """A captured Source preserving one complete visible Pi event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_source(self) -> PiEventSource:
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("Pi event Sources must be captured")
        expected_id = pi_event_source_id(self.session_id, self.event_id)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("Pi event source identity must match session_id and event_id")
        PiEventInput(
            session_id=self.session_id,
            event_id=self.event_id,
            parent_id=self.parent_id,
            event_type=self.event_type,
            role=self.role,
            content=self.content,
            record=self.record,
        )
        return self


class _PiEventAdapter:
    name = PI_EVENT_SOURCE_TYPE
    input_class = PiEventInput
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class _PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_TYPE,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    adapter=_PiEventAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(_PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, JsonValue]) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _checkpoint_imported(checkpoint: JsonValue | None) -> dict[str, dict[str, str]]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi session checkpoint must be a JSON object")
    sessions = checkpoint.get(_CHECKPOINT_KEY)
    if sessions is None:
        return {}
    if not isinstance(sessions, dict):
        raise ValueError("Pi session checkpoint sessions must be a JSON object")
    imported: dict[str, dict[str, str]] = {}
    for session_id, state in sessions.items():
        events = state.get("events") if isinstance(state, dict) else None
        if not isinstance(events, dict):
            raise ValueError("Pi session checkpoint events must be a JSON object")
        imported[str(session_id)] = {
            str(event_id): str(digest) for event_id, digest in events.items() if isinstance(digest, str)
        }
    return imported


class PiSessionConnector:
    """Collect the selected Pi branch, one durable event at a time."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_TYPE})

    def __init__(self, path: str | Path, leaf_id: str | None = None, /) -> None:
        self.path = Path(_require_non_blank(str(path), "path"))
        self.leaf_id = _optional_trimmed(leaf_id, "leaf_id")

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        try:
            content = self.path.read_bytes()
        except OSError as error:
            raise ValueError(f"Pi session file {self.path} cannot be read") from error
        events = parse_pi_session(PiSessionInput(content=content, leaf_id=self.leaf_id))

        imported = _checkpoint_imported(session.checkpoint)
        for event in events:
            digest = imported.get(event.session_id, {}).get(event.event_id)
            if digest is not None and digest != _record_digest(event.record):
                raise ValueError(f"Pi session event {event.event_id!r} was rewritten after import")

        state = {session_id: dict(events_map) for session_id, events_map in imported.items()}
        for event in events:
            bucket = state.setdefault(event.session_id, {})
            if event.event_id in bucket:
                continue
            outcome = await session.submit(event.event_id, PI_EVENT_SOURCE_TYPE, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                continue
            bucket[event.event_id] = _record_digest(event.record)

        checkpoint = {
            _CHECKPOINT_KEY: {
                session_id: {"events": {event_id: bucket[event_id] for event_id in sorted(bucket)}}
                for session_id, bucket in sorted(state.items())
            }
        }
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_TYPE",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]