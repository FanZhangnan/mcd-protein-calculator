#!/usr/bin/env python3
"""Copy the shared UI files into docs/demo/ so GitHub Pages can serve them.

The demo must not fork the renderer: docs/demo/app.js and docs/demo/style.css
are byte-for-byte copies of web/app.js and web/style.css, and the solver is the
same file the parity test loads. `tests/test_demo_assets.py` re-checks the hashes
so the two copies cannot drift unnoticed.

Run after editing anything under web/:
    python3 scripts/sync_demo_assets.py
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = [
    ("web/app.js", "docs/demo/app.js"),
    ("web/style.css", "docs/demo/style.css"),
    ("web/solver.js", "docs/demo/solver.js"),
]

FONT_DIRS = [("web/fonts", "docs/demo/fonts")]

FONT_DIRS = [("web/fonts", "docs/demo/fonts")]


def main() -> int:
    for src_rel, dst_rel in PAIRS:
        src, dst = ROOT / src_rel, ROOT / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
        print(f"{src_rel} -> {dst_rel}  sha256:{digest}")
    for src_rel, dst_rel in FONT_DIRS:
        src_dir, dst_dir = ROOT / src_rel, ROOT / dst_rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.iterdir()):
            if src.is_file():
                shutil.copy2(src, dst_dir / src.name)
                print(f"{src_rel}/{src.name} -> {dst_rel}/{src.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
