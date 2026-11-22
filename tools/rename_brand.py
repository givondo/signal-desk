"""Rename the product from "Northchart" to "Northcharts" in everything people read.

Changes the capitalised word only, so code names stay as they are: module names (northchart_*.py),
settings (NORTHCHART_*), token prefixes, storage keys and the NorthchartBridge add-on file names.
Safe to run more than once, and again after merging a branch that still says "Northchart".

    python tools/rename_brand.py          # rename in place
    python tools/rename_brand.py --check  # list files that still need it (exit 1 if any)
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OLD, NEW = "Northchart", "Northcharts"
# whole word only: leaves NorthchartBridge, northchart_ and NORTHCHART_ alone, and never makes "Northchartss"
WORD = re.compile(r"\b" + OLD + r"\b")
SUFFIXES = {".py", ".html", ".md", ".mq4", ".mq5", ".ps1", ".sh", ".json", ".webmanifest", ".txt", ".js"}
SKIP_DIRS = {".git", "static", "node_modules", "__pycache__", "data"}
SKIP_FILES = {"rename_brand.py"}


def files():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in SUFFIXES and p.name not in SKIP_FILES \
                and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
            yield p


def main(check: bool) -> int:
    todo = []
    for p in files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        n = len(WORD.findall(text))
        if n:
            todo.append((p.relative_to(ROOT), n))
            if not check:
                p.write_text(WORD.sub(NEW, text).replace(NEW + "'s", NEW + "'"), encoding="utf-8")
    for path, n in todo:
        print(f"{'needs' if check else 'renamed'} {n:3d}  {path}")
    if not todo:
        print(f"Nothing says {OLD} any more.")
    return 1 if check and todo else 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
