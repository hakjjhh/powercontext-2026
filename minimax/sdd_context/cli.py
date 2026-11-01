"""SDD context module CLI.

Commands:
  index       index spec/src/tests into seekdb
  context     build the task context from seekdb
  state-save  persist task state into PowerContext
  state-load  read it back after a restart
  generate    ask MiniMax to produce a patch from the context
  verify      run the sample project's tests
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

from . import diffutil, minimax_client, powercontext, store


def _root(value):
    return pathlib.Path(value).resolve()


def cmd_index(args):
    store.ensure_schema()
    rows = store.index_dir(args.root)
    print(json.dumps({"indexed": len(rows), "paths": [r[1] for r in rows]}, ensure_ascii=False, indent=2))


def _context_payload(args):
    rows = store.search(args.task, limit=args.limit)
    return {
        "task": args.task,
        "specs": [r for r in rows if r["kind"] == "spec"],
        "code": [r for r in rows if r["kind"] == "code"],
        "tests": [r for r in rows if r["kind"] == "test"],
    }


def cmd_context(args):
    print(json.dumps(_context_payload(args), ensure_ascii=False, indent=2))


def cmd_state_save(args):
    payload = _context_payload(args)
    text = json.dumps(
        {
            "task": args.task,
            "status": args.status,
            "selected": {
                "specs": [r["path"] for r in payload["specs"]],
                "code": [r["path"] for r in payload["code"]],
                "tests": [r["path"] for r in payload["tests"]],
            },
        },
        ensure_ascii=False,
    )
    result = powercontext.remember("task_state", text)
    print(json.dumps({"saved": True, "result": result}, ensure_ascii=False, indent=2)[:2000])


def cmd_state_load(args):
    entries = powercontext.search(args.task, limit=5)
    print(json.dumps(entries, ensure_ascii=False, indent=2)[:4000])


def cmd_verify(args):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(_root(args.root)),
        capture_output=True,
        text=True,
    )
    print(proc.stdout)
    print(proc.stderr)
    raise SystemExit(proc.returncode)


def cmd_generate(args):
    payload = _context_payload(args)
    context_text = "\n\n".join(
        f"### {row['kind']} :: {row['path']}\n```\n{row['content']}\n```"
        for row in payload["specs"] + payload["code"] + payload["tests"]
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior engineer working under spec-driven development. "
                "Return ONLY a unified diff (git apply compatible) that implements the task. "
                "Do not rename files. Keep existing behavior unless the spec says otherwise."
            ),
        },
        {
            "role": "user",
            "content": f"Task:\n{args.task}\n\nSelected context:\n{context_text}",
        },
    ]
    answer = minimax_client.chat(messages)
    raw = pathlib.Path(args.out).with_suffix(".raw.txt")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(answer, encoding="utf-8")
    # Reasoning models may prepend a <think>...</think> block to the content.
    cleaned = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    cleaned = re.sub(r"^.*?</think>", "", cleaned, flags=re.S)
    diff = ""
    match = re.search(r"```(?:diff|patch)?\n(.*?)```", cleaned, re.S)
    if match:
        diff = match.group(1)
    else:
        for marker in ("diff --git ", "--- a/"):
            pos = cleaned.find(marker)
            if pos >= 0:
                diff = cleaned[pos:]
                break
    if not diff.strip():
        raise SystemExit(f"MiniMax did not return a diff; raw answer saved to {raw}")
    diff = diffutil.recount_hunks(diff)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff, encoding="utf-8")
    print(json.dumps({"patch": str(out), "bytes": len(diff)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(prog="sdd_context")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index")
    p.add_argument("--root", default="sample_project")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("context")
    p.add_argument("--task", required=True)
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("state-save")
    p.add_argument("--task", required=True)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--status", default="in_progress")
    p.set_defaults(func=cmd_state_save)

    p = sub.add_parser("state-load")
    p.add_argument("--task", required=True)
    p.set_defaults(func=cmd_state_load)

    p = sub.add_parser("generate")
    p.add_argument("--task", required=True)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--out", default="evidence/patch.diff")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("verify")
    p.add_argument("--root", default="sample_project")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
