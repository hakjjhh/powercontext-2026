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

"""HTTP scoring-service adapter for listwise Memory reranking."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score one coarse candidate pool through a batched HTTP score service.

    Ranks are the one-based positions of candidates in the *full* pool, so a
    batched call still reports scores for the whole pool and the final ordering
    is one global selection instead of a per-batch concatenation.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty string")  # noqa: TRY003
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(client, httpx.AsyncClient):
            raise ValueError("rerank client must be an httpx.AsyncClient")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("rerank policy_id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return the top ``limit`` global ranks ordered by service score."""

        candidate_count = len(candidates)
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("rerank limit must be an integer")  # noqa: TRY003
        if limit < 1 or limit > candidate_count:
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, candidate_count, self._batch_size):
            batch = tuple(candidates[start : start + self._batch_size])
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            payload: dict[str, Any] = {
                "query": query,
                "candidates": [{"rank": rank, "text": hit.text} for rank, hit in zip(ranks, batch, strict=True)],
            }
            observed = self._read_scores(await self._post(payload), set(ranks))
            overlap = set(observed) & set(scores)
            if overlap:
                raise ValueError(f"rerank service repeated ranks across batches: {sorted(overlap)}")  # noqa: TRY003
            scores.update(observed)
            requests += 1

        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
            discarded_rank_count=0,
        )

    async def _post(self, payload: dict[str, Any]) -> Any:
        """Send one batch, keeping httpx failures untouched."""

        response = await self._client.post(self._endpoint, json=payload)
        response.raise_for_status()
        content = response.content
        if isinstance(content, str):
            raw = content
        else:
            raw = bytes(content).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError as exc:  # json.JSONDecodeError is a ValueError.
            raise ValueError(f"rerank service returned invalid JSON: {exc}") from exc

    @staticmethod
    def _read_scores(payload: Any, expected: set[int]) -> dict[int, float]:
        """Validate that one response covers exactly the ranks of its batch."""

        if not isinstance(payload, dict) or set(payload) != {"scores"}:
            raise ValueError("rerank response must contain exactly a scores field")  # noqa: TRY003
        records = payload["scores"]
        if not isinstance(records, list):
            raise ValueError("rerank scores must be a list")  # noqa: TRY003
        observed: dict[int, float] = {}
        for record in records:
            if not isinstance(record, dict) or set(record) != {"rank", "score"}:
                raise ValueError("rerank score records must contain exactly rank and score")  # noqa: TRY003
            rank = record["rank"]
            if isinstance(rank, bool) or not isinstance(rank, int):
                raise ValueError("rerank score rank must be an integer")  # noqa: TRY003
            if rank not in expected:
                raise ValueError(f"rerank service returned unknown rank {rank}")  # noqa: TRY003
            if rank in observed:
                raise ValueError(f"rerank service returned rank {rank} more than once")  # noqa: TRY003
            score = record["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError("rerank score must be numeric")  # noqa: TRY003
            value = float(score)
            if not math.isfinite(value):
                raise ValueError("rerank score must be finite")  # noqa: TRY003
            observed[rank] = value
        if set(observed) != expected:
            missing = sorted(expected - set(observed))
            raise ValueError(f"rerank service omitted ranks: {missing}")  # noqa: TRY003
        return observed


__all__ = ["HTTPMemoryReranker", "HTTP_RERANK_POLICY_ID"]