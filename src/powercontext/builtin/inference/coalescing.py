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

"""Request coalescing and bounded vector caching for embedding models.

``CoalescingEmbeddingModel`` wraps any embedding port and adds the two behaviours
the memory and topic-memory pipelines need when many writers embed the same or
overlapping texts at once:

* concurrent calls arriving inside one coalescing window are merged, their texts
  are deduplicated, and the remaining unique texts are embedded in provider-sized
  batches instead of one request per caller;
* results are kept in a least-recently-used vector cache, so repeated writes and
  repeated retrieval queries cost no further provider requests.

Usage is attributed per caller: a caller is charged only for the provider batches
that embedded at least one of its own texts, and a fully cached call reports zero
requests. Failed batches are never cached, so a later call retries them.
"""

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
class _CacheEntry:
    """One cached vector whose provider result may still be in flight."""

    vector: EmbeddingVector | None = None
    failed: bool = False
    settled: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_settled(self) -> bool:
        return self.settled.is_set()


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
    """Coalesce concurrent embedding calls and cache vectors with an LRU bound."""

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
        if isinstance(cache_size, bool) or cache_size < 1:
            raise ValueError("cache_size must be a positive integer")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        self._delegate = delegate
        self._max_batch_size = int(max_batch_size)
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = int(cache_size)
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
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
        """Return how many distinct texts currently hold a vector."""

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
        # In-flight entries must settle first, otherwise a waiter could read a vector
        # for a text whose provider request has not finished yet.
        inflight = [entry.settled.wait() for entry in self._cache.values() if not entry.is_settled]
        if inflight:
            await asyncio.gather(*inflight)
        missing: list[str] = []
        seen: set[str] = set()
        for request in window:
            for text in request.texts:
                if text in seen:
                    continue
                seen.add(text)
                entry = self._cache.get(text)
                if entry is None:
                    missing.append(text)
                else:
                    # Every read refreshes recency: a text keeps its slot only while callers
                    # keep asking for it, and a failed slot is retried by the next window.
                    self._cache.move_to_end(text)
                    if entry.vector is None or entry.failed:
                        missing.append(text)
        batches: list[_Batch] = []
        for index in range(0, len(missing), self._max_batch_size):
            chunk = tuple(missing[index : index + self._max_batch_size])
            for text in chunk:
                self._reserve(text)
            batches.append(_Batch(texts=chunk, task=asyncio.ensure_future(self._run_batch(chunk, mode))))
        for batch in batches:
            for request in window:
                if any(text in request.texts for text in batch.texts):
                    request.charged.add(batch.task)
        results = await asyncio.gather(*(batch.task for batch in batches), return_exceptions=True)
        self._settle(batches, results)
        for request in window:
            if request.future.done():
                continue
            self._deliver(request, results, batches)

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

    def _deliver(self, request: _Request, results: tuple[Any, ...], batches: list[_Batch]) -> None:
        requests = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        vectors: list[EmbeddingVector] = []
        pending: dict[str, EmbeddingVector] = {}
        for batch, outcome in zip(batches, results, strict=True):
            if isinstance(outcome, BaseException):
                if batch.task in request.charged:
                    request.future.set_exception(outcome)
                    return
                continue
            for text, vector in zip(batch.texts, outcome.vectors, strict=True):
                pending.setdefault(text, vector)
            if batch.task not in request.charged:
                continue
            usage = outcome.usage
            requests += usage.requests
            input_tokens = (input_tokens or 0) + (usage.input_tokens or 0)
            output_tokens = (output_tokens or 0) + (usage.output_tokens or 0)
        for text in request.texts:
            entry = self._cache.get(text)
            if entry is not None and entry.vector is not None and not entry.failed:
                vectors.append(entry.vector)
            elif text in pending:
                vectors.append(pending[text])
            else:
                request.future.set_exception(
                    RuntimeError(f"embedding for {text!r} produced no vector"),  # noqa: TRY003
                )
                return
        if request.charged:
            usage = InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)
        else:
            usage = InferenceUsage(requests=0)
        request.future.set_result(EmbeddingResult(vectors=tuple(vectors), usage=usage))

    def _reserve(self, text: str) -> _CacheEntry:
        entry = self._cache.get(text)
        if entry is None:
            while len(self._cache) >= self._cache_size:
                self._cache.popitem(last=False)
            entry = _CacheEntry()
            self._cache[text] = entry
        else:
            entry.vector = None
            entry.failed = False
            entry.settled = asyncio.Event()
            self._cache.move_to_end(text)
        return entry

    def _settle(self, batches: list[_Batch], results: tuple[Any, ...]) -> None:
        for batch, outcome in zip(batches, results, strict=True):
            for position, text in enumerate(batch.texts):
                entry = self._cache.get(text)
                if entry is None:
                    continue
                if isinstance(outcome, BaseException):
                    entry.failed = True
                    entry.settled.set()
                    if self._cache.get(text) is entry:
                        del self._cache[text]
                    continue
                entry.vector = outcome.vectors[position]
                entry.settled.set()
                self._cache.move_to_end(text)


__all__ = ["CoalescingEmbeddingModel"]