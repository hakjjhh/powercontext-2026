from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="inspector-recall-") as directory:
        database = Path(directory) / "powercontext.sqlite3"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Inspector Recall verification",
                    summary="PowerContext memory round-trip",
                    idempotency_key="inspector-recall-runtime-verification",
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            await memory.remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="observation",
                            text="The player promised to return the golden compass before midnight.",
                            reason="verification seed",
                        ),
                    )
                )
            )
            first = await memory.search(SearchMemoryRequest(query="unrelated query", limit=5, mode="fts"))
            second = await memory.search(SearchMemoryRequest(query="golden compass promise", limit=5, mode="fts"))
            result = {
                "scope_id": scope.scope_id,
                "first_hit_count": len(first.hits),
                "second_hit_count": len(second.hits),
                "second_texts": [hit.text for hit in second.hits],
                "assertions": {
                    "scope_created": bool(scope.scope_id),
                    "memory_written": bool(second.hits),
                    "recall_uses_real_powercontext": True,
                },
            }
            print(json.dumps(result, indent=2))
            if not result["assertions"]["memory_written"]:
                raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())