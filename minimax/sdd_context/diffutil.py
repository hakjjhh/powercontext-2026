"""Normalize unified diffs produced by LLMs (recompute hunk line counts)."""

import re


def recount_hunks(diff: str) -> str:
    lines = diff.splitlines()
    out = []
    index = 0
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    while index < len(lines):
        line = lines[index]
        match = hunk_re.match(line)
        if not match:
            out.append(line)
            index += 1
            continue
        old_start = match.group(1)
        new_start = match.group(3)
        suffix = match.group(5)
        body = []
        index += 1
        while index < len(lines) and not lines[index].startswith("@@"):
            body.append(lines[index])
            index += 1
        old_count = sum(1 for b in body if b[:1] in (" ", "-"))
        new_count = sum(1 for b in body if b[:1] in (" ", "+"))
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}")
        out.extend(body)
    return "\n".join(out) + ("\n" if diff.endswith("\n") else "")
