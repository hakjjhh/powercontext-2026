"""Apply the tag-filter block produced by MiniMax to sample_project/src/bookmarks.py.

The model's unified diff had correct content but wrong hunk coordinates, so the
block is inserted at the spec-mandated anchor instead of relying on `git apply`.
"""

import pathlib

BLOCK = '''    if tag:
        normalized_tag = str(tag).strip().casefold()
        selected = [
            item
            for item in selected
            if any(
                str(item_tag).casefold() == normalized_tag
                for item_tag in item["tags"]
            )
        ]
'''

path = pathlib.Path(__file__).resolve().parents[1] / "sample_project" / "src" / "bookmarks.py"
text = path.read_text(encoding="utf-8")
anchor = "    selected = list(reversed(_ITEMS))\n"
if BLOCK in text:
    print("already applied")
    raise SystemExit(0)
if anchor not in text:
    raise SystemExit("anchor not found")
path.write_text(text.replace(anchor, anchor + BLOCK, 1), encoding="utf-8")
print("applied model block")
