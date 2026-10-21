"""The Pages demo must not fork the renderer or the styles.

`docs/demo/{app.js,style.css,solver.js}` are copies of their `web/` originals,
kept in sync by `scripts/sync_demo_assets.py`. This test fails as soon as one
copy is edited on its own.

Run: python3 tests/test_demo_assets.py
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = [
    ("web/app.js", "docs/demo/app.js"),
    ("web/style.css", "docs/demo/style.css"),
    ("web/solver.js", "docs/demo/solver.js"),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DemoAssetsTest(unittest.TestCase):
    def test_copies_are_identical(self) -> None:
        for src_rel, dst_rel in PAIRS:
            src, dst = ROOT / src_rel, ROOT / dst_rel
            self.assertTrue(dst.exists(), f"{dst_rel} missing — run scripts/sync_demo_assets.py")
            self.assertEqual(digest(src), digest(dst),
                             f"{dst_rel} drifted from {src_rel} — run scripts/sync_demo_assets.py")

    def test_index_uses_relative_paths(self) -> None:
        html = (ROOT / "docs/index.html").read_text("utf-8")
        self.assertIn('href="demo/style.css"', html)
        self.assertIn('src="demo/app.js"', html)
        self.assertNotIn('src="/app.js"', html)
        self.assertNotIn("127.0.0.1", html)
        self.assertNotIn("localhost", html)

    def test_snapshot_is_small_and_clean(self) -> None:
        path = ROOT / "docs/demo/snapshot.json"
        self.assertTrue(path.exists(), "run scripts/export_demo_snapshot.py first")
        self.assertLess(path.stat().st_size, 2 * 1024 * 1024)
        text = path.read_text("utf-8")
        for needle in ("Bearer ", "eyJ", "/Users/", "token"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
