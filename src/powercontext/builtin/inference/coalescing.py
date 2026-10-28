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

"""Request coalescing and bounded vector caching for embedding models."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, embed_query

DOCUMENT_MODE = "document"
QUERY_MODE = "query"


@dataclass(slots=True)
class _PendingText:
    """One text whose provider batch is still in flight for at least one caller."""

    task: asyncio.Future
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    vector: EmbeddingVector | None = None
    usage: InferenceUsage | None = None
    failed: bool = False
    error: BaseException | None = None


@dataclass(slots=True)
class _Request:
    """One waiting caller and the provider batches it must be charged for."""

    texts: tuple[str, ...]
    future: asyncio.Future
    charged: set[asyncio.Future] = field(default_factory=set)


@dataclass(slots=True)
class _Batch:
    """One provider request over deduplicated, previously uncached texts."""

    texts: tuple[str, ...]
    task: asyncio.Future


class CoalescingEmbeddingModel:
    """Coalesce concurrent embedding calls and cache vectors with an LRU bound.

    Settled vectors live in a bounded LRU map and in-flight provider work lives in a
    separate registry, so a late caller shares a running request without the cache
    capacity ever evicting work that others are still awaiting. A cache size of zero
    disables reuse across calls while in-flight sharing keeps working. Usage is
    attributed to every caller that waited on a batch, once per distinct batch, and a
    fully cached call reports zero provider requests.
    """

    def __init__(
        self,
        delegate: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        self._delegate = delegate
        self._max_batch_size = int(max_batch_size)
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = int(cache_size)
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, _PendingText] = {}
        self._windows: dict[str, list[_Request]] = {}
        self._flushers: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    @property
    def delegate(self) -> EmbeddingModel:
        """Return the wrapped provider model."""

        return self._delegate

    @property
    def profile(self) -> Any:
        """Expose the deployment-fixed profile of the wrapped model."""

        return self._delegate.profile

    @property
    def cached_texts(self) -> int:
        """Return how many distinct texts currently hold a cached vector."""

        return len(self._cache)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed an ordered batch, sharing provider work across concurrent callers."""

        return await self._submit(texts, DOCUMENT_MODE)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries through the same coalescing and cache path."""

        return await self._submit(texts, QUERY_MODE)

    async def _submit(self, texts: tuple[str, ...], mode: str) -> EmbeddingResult:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        if not texts:
            future.set_result(EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0)))
            return await future
        request = _Request(texts=tuple(texts), future=future)
        async with self._lock:
            self._windows.setdefault(mode, []).append(request)
            if mode not in self._flushers:
                self._flushers[mode] = loop.create_task(self._flush_window(mode))
        return await future

    async def _flush_window(self, mode: str) -> None:
        if self._coalesce_seconds:
            await asyncio.sleep(self._coalesce_seconds)
        else:
            await asyncio.sleep(0)
        async with self._lock:
            window = self._windows.pop(mode, [])
            self._flushers.pop(mode, None)
        if not window:
            return
        try:
            await self._serve(window, mode)
        except Exception as error:  # noqa: BLE001 - every waiter observes the same failure
            for request in window:
                if not request.future.done():
                    request.future.set_exception(error)

    async def _serve(self, window: list[_Request], mode: str) -> None:
        shared: dict[str, _PendingText] = {}
        events: list[asyncio.Future] = []
        seen_wanted: set[str] = set()
        for request in window:
            for text in request.texts:
                if text in seen_wanted:
                    continue
                seen_wanted.add(text)
                pending = self._inflight.get(text)
                if pending is not None:
                    shared[text] = pending
                    events.append(pending.settled.wait())
        if events:
            await asyncio.gather(*events)

        missing: list[str] = []
        hit_vectors: dict[str, EmbeddingVector] = {}
        seen: set[str] = set()
        for request in window:
            for text in request.texts:
                if text in seen:
                    continue
                seen.add(text)
                pending = shared.get(text)
                if pending is not None:
                    # A late caller reuses the running request and is charged for it exactly
                    # once, the same as the callers that started that batch.
                    request.charged.add(pending.task)
                    continue
                cached = self._cache.get(text)
                if cached is None:
                    missing.append(text)
                else:
                    # Every read refreshes recency without ever displacing in-flight work.
                    self._cache.move_to_end(text)
                    hit_vectors[text] = cached

        batches: list[_Batch] = []
        for index in range(0, len(missing), self._max_batch_size):
            chunk = tuple(missing[index : index + self._max_batch_size])
            task = asyncio.ensure_future(self._run_batch(chunk, mode))
            for text in chunk:
                self._inflight[text] = _PendingText(task=task)
            batches.append(_Batch(texts=chunk, task=task))
        for batch in batches:
            for request in window:
                if any(text in request.texts for text in batch.texts):
                    request.charged.add(batch.task)

        own_results = await asyncio.gather(*(batch.task for batch in batches), return_exceptions=True)
        task_usage: dict[asyncio.Future, InferenceUsage] = {}
        task_error: dict[asyncio.Future, BaseException] = {}
        pending_vectors: dict[str, EmbeddingVector] = {}
        for batch, outcome in zip(batches, own_results, strict=True):
            if isinstance(outcome, BaseException):
                task_error[batch.task] = outcome
                for text in batch.texts:
                    entry = self._inflight.get(text)
                    if entry is not None and entry.task is batch.task:
                        entry.failed = True
                        entry.error = outcome
                        self._inflight.pop(text, None)
                        entry.settled.set()
                continue
            usage = outcome.usage if outcome.usage is not None else InferenceUsage(requests=1)
            task_usage[batch.task] = usage
            for text, vector in zip(batch.texts, outcome.vectors, strict=True):
                entry = self._inflight[text]
                entry.vector = vector
                entry.usage = usage
                self._inflight.pop(text, None)
                if self._cache_size:
                    self._put_cached(text, vector)
                entry.settled.set()
                pending_vectors[text] = vector
        for entry in {pending.task: pending for pending in shared.values()}.values():
            if entry.failed:
                task_error.setdefault(
                    entry.task, entry.error if entry.error is not None else RuntimeError("embedding batch failed")
                )
            elif entry.usage is not None:
                task_usage.setdefault(entry.task, entry.usage)

        for request in window:
            if request.future.done():
                continue
            self._deliver(request, pending_vectors, hit_vectors, shared, task_usage, task_error)

    def _deliver(
        self,
        request: _Request,
        pending_vectors: dict[str, EmbeddingVector],
        hit_vectors: dict[str, EmbeddingVector],
        shared: dict[str, _PendingText],
        task_usage: dict[asyncio.Future, InferenceUsage],
        task_error: dict[asyncio.Future, BaseException],
    ) -> None:
        requests_total = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        for task in request.charged:
            if task in task_error:
                # The failure reaches every caller that depends on this batch, and the
                # failed result stays uncached so a later call may retry it.
                request.future.set_exception(task_error[task])
                return
            usage = task_usage.get(task)
            if usage is None:
                continue
            requests_total += usage.requests
            input_tokens = (input_tokens or 0) + (usage.input_tokens or 0)
            output_tokens = (output_tokens or 0) + (usage.output_tokens or 0)
        vectors: list[EmbeddingVector] = []
        for text in request.texts:
            if text in pending_vectors:
                vectors.append(pending_vectors[text])
                continue
            if text in hit_vectors:
                vectors.append(hit_vectors[text])
                continue
            entry = shared.get(text)
            if entry is not None and entry.vector is not None:
                vectors.append(entry.vector)
                continue
            request.future.set_exception(
                RuntimeError(f"embedding for {text!r} produced no vector"),  # noqa: TRY003
            )
            return
        if request.charged:
            usage = InferenceUsage(requests=requests_total, input_tokens=input_tokens, output_tokens=output_tokens)
        else:
            usage = InferenceUsage(requests=0)
        request.future.set_result(EmbeddingResult(vectors=tuple(vectors), usage=usage))

    def _put_cached(self, text: str, vector: EmbeddingVector) -> None:
        while len(self._cache) >= self._cache_size:
            self._cache.popitem(last=False)
        self._cache[text] = vector
        self._cache.move_to_end(text)

    async def _run_batch(self, texts: tuple[str, ...], mode: str) -> EmbeddingResult:
        if mode == QUERY_MODE:
            result = await embed_query(self._delegate, texts)
        else:
            result = await self._delegate.embed(texts)
        vectors = tuple(result.vectors)
        if len(vectors) != len(texts):
            raise ValueError(  # noqa: TRY003
                f"Embedding model returned {len(vectors)} vectors for {len(texts)} texts"
            )
        return EmbeddingResult(vectors=vectors, usage=result.usage)


__all__ = ["CoalescingEmbeddingModel"]