"""Markdown 里的图片引用必须指向仓库内真实存在的文件。

截图被删掉、路径写错、目录改名，都会让 README 和开发记录出现坏图。
`scripts/check_doc_links.py` 负责实际检查，这里把它挂进测试套件，
以后每次跑单测都会顺手验一遍。

Run: python3 tests/test_doc_links.py
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocLinksTest(unittest.TestCase):
    def test_no_broken_image_links(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_doc_links.py")],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0,
                         f"图片链接检查失败：\n{proc.stdout}\n{proc.stderr}")

    def test_checker_catches_a_missing_file(self) -> None:
        """检查脚本本身要能发现坏链接，否则上面的断言是空的。"""
        probe = ROOT / "docs" / "dev-log" / "_link_probe.md"
        probe.write_text("![x](screenshots/__does_not_exist__.png)\n", "utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_doc_links.py")],
                capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("__does_not_exist__", proc.stdout)
        finally:
            probe.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
