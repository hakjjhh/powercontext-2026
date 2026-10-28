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

"""Count-triggered admission for Memory and Topic Memory processing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from types import TracebackType
from typing import Any

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

DEFAULT_SOURCE_COUNT_POLL_SECONDS = 0.2
DEFAULT_SOURCE_COUNT_RETRY_SECONDS = 1.5
SOURCE_COUNT_BINDING_NAMES = {
    "memory": SOURCE_WINDOW_TRIGGER_NAME,
    "topic-memory": TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
}

logger = logging.getLogger(__name__)


class SourceCountDriver:
    """Trigger the unchanged processing call once a scope and family reach their count.

    Pending work is measured the same way the processing binding measures it, as the Source
    journal high watermark minus the cursor for that binding. A duplicated capture therefore
    reuses one journal identity, a receipt trust upgrade keeps the position it already had,
    and a lineage-only journal identity counts once for admission without ever becoming
    generation evidence. Because each scope and each family owns a separate cursor, one of
    them never consumes the other's admission, and a restart recomputes both from durable
    state instead of trusting in-process memory.
    """

    def __init__(
        self,
        runtime: Any,
        contexts: Any,
        thresholds: Mapping[str, int],
        /,
        *,
        poll_seconds: float = DEFAULT_SOURCE_COUNT_POLL_SECONDS,
        retry_seconds: float = DEFAULT_SOURCE_COUNT_RETRY_SECONDS,
    ) -> None:
        unknown = sorted(set(thresholds) - set(SOURCE_COUNT_BINDING_NAMES))
        if unknown:
            raise ValueError("unsupported count-triggered families: " + ", ".join(unknown))  # noqa: TRY003
        self._runtime = runtime
        self._contexts = contexts
        self._thresholds = {family: int(thresholds[family]) for family in thresholds}
        self._poll_seconds = poll_seconds
        self._retry_seconds = retry_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._retry_after: dict[tuple[str, str], float] = {}

    async def __aenter__(self) -> SourceCountDriver:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Begin watching durable progress; no threshold means nothing to watch."""

        if self._task is not None or self._stopping or not self._thresholds:
            return
        self._task = asyncio.create_task(self._run(), name="powercontext.source_count")

    async def aclose(self) -> None:
        self._stopping = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    @property
    def thresholds(self) -> Mapping[str, int]:
        return dict(self._thresholds)

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log_safely(
                    logger,
                    logging.WARNING,
                    "Source count sweep failed",
                    exc_info=error,
                    extra={"event": "source_count.sweep_failed", "outcome": "retry"},
                )
            await asyncio.sleep(self._poll_seconds)

    async def sweep_once(self) -> None:
        """Inspect every scope with a Source journal and advance each qualifying family."""

        if self._stopping:
            return
        for scope_id in await self._contexts.scope_ids():
            if self._stopping:
                return
            for family, threshold in self._thresholds.items():
                await self._drive(scope_id, family, threshold)

    async def _drive(self, scope_id: str, family: str, threshold: int) -> None:
        binding = SOURCE_COUNT_BINDING_NAMES[family]
        key = (scope_id, family)
        while not self._stopping:
            pending = await self._pending(scope_id, binding)
            if pending < threshold:
                self._retry_after.pop(key, None)
                return
            retry_at = self._retry_after.get(key)
            if retry_at is not None and asyncio.get_running_loop().time() < retry_at:
                return
            if self._is_closing():
                return
            if not await self._process(scope_id, family):
                self._retry_after[key] = asyncio.get_running_loop().time() + self._retry_seconds
                return

    async def _process(self, scope_id: str, family: str) -> bool:
        """Run one original processing call and report whether durable progress is visible."""

        try:
            if family == "memory":
                result = await self._runtime.memory.for_scope(scope_id).flush()
                return bool(result.processed)
            if await self._wave_in_flight(scope_id):
                return False
            await self._runtime.topic_memory.for_scope(scope_id).flush()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._retry_after[(scope_id, family)] = asyncio.get_running_loop().time() + self._retry_seconds
            log_safely(
                logger,
                logging.WARNING,
                "Count-triggered processing call failed",
                exc_info=error,
                extra={
                    "event": "source_count.processing_failed",
                    "outcome": "retry",
                    "unit": family,
                    "scope_id": scope_id,
                },
            )
            return False
        # Topic Memory consumes the accepted wave inside its own worker: the wave keeps its
        # original window plan, so this driver never re-adds the threshold to later windows
        # and paces the next admission by the retry window instead of by this poll.
        return False

    async def _wave_in_flight(self, scope_id: str) -> bool:
        """Report whether an accepted Topic Memory wave still owns this scope's admission."""

        binding = SOURCE_COUNT_BINDING_NAMES["topic-memory"]
        async with self._contexts.database.transaction() as connection:
            pending = await self._contexts.repositories.processing_pending.load(connection, scope_id, binding)
        if pending is None:
            return False
        return int(pending.flush_generation) > int(pending.handled_flush_generation)

    async def _pending(self, scope_id: str, binding: str) -> int:
        async with self._contexts.database.transaction() as connection:
            high = await self._contexts.repositories.sources.journal_position(connection, scope_id)
            stored = await self._contexts.repositories.cursors.load(connection, scope_id, binding)
        return int(high) - (0 if stored is None else int(stored.cursor.sequence))

    def _is_closing(self) -> bool:
        return bool(
            self._stopping or getattr(self._runtime, "_closing", False) or getattr(self._runtime, "_closed", False)
        )


__all__ = [
    "DEFAULT_SOURCE_COUNT_POLL_SECONDS",
    "DEFAULT_SOURCE_COUNT_RETRY_SECONDS",
    "SOURCE_COUNT_BINDING_NAMES",
    "SourceCountDriver",
]
