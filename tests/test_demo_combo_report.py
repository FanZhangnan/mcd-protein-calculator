"""The demo's 「套餐核查」 row must quote data/combo_catalog.json.

The row used to be assembled in the browser from the variants that survived:
lunch printed "识别 17 个套餐 … 排除 0 个" while the catalog says 50 combos,
33 usable variants and 45 exclusions. These tests pin the snapshot's numbers to
the catalog so a re-export cannot drift again.

Run: python3 tests/test_demo_combo_report.py
"""

from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/demo/snapshot.json"
CATALOG = ROOT / "data/combo_catalog.json"


class DemoComboReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = json.loads(SNAPSHOT.read_text("utf-8"))
        cls.catalog = json.loads(CATALOG.read_text("utf-8"))

    def report(self, meal: str) -> dict:
        block = (self.snapshot.get("meals") or {}).get(meal) or {}
        report = block.get("comboReport")
        self.assertIsNotNone(
            report, f"{meal} 缺少 comboReport —— 运行 scripts/attach_combo_report.py")
        return report

    def catalog_meal(self, meal: str) -> dict:
        entry = (self.catalog.get("meals") or {}).get(meal)
        if entry is None and meal == "dinner":
            # the POS serves the same SKU set at lunch and dinner; the build
            # stores one entry and the service reuses it under that evidence.
            entry = (self.catalog.get("meals") or {}).get("lunch")
        self.assertIsNotNone(entry, f"catalog has no {meal} entry")
        return entry

    def test_counts_match_the_catalog(self) -> None:
        for meal in ("breakfast", "lunch", "dinner"):
            with self.subTest(meal=meal):
                report, entry = self.report(meal), self.catalog_meal(meal)
                self.assertEqual(report["checked"], entry["checked"], "已查 SKU 数")
                self.assertEqual(report["combos_found"], entry["combo_count"], "识别套餐数")
                self.assertEqual(report["variants_used"], len(entry["variants"]), "纳入变体数")
                self.assertEqual(report["variants_excluded"], len(entry["excluded"]), "排除变体数")
                self.assertEqual(report["excluded"], len(entry["excluded"]), "排除记录数")
                self.assertEqual(report["reasons"],
                                 dict(Counter(e.get("reason") or "other"
                                              for e in entry["excluded"])),
                                 "排除原因（变体级）")

    def test_available_plus_excluded_equals_found(self) -> None:
        for meal in ("breakfast", "lunch", "dinner"):
            with self.subTest(meal=meal):
                report = self.report(meal)
                self.assertTrue(report["combos_consistent"], meal)
                self.assertEqual(report["combos_available"] + report["combos_excluded"],
                                 report["combos_found"])
                self.assertEqual(sum(report["combo_reasons"].values()),
                                 report["combos_excluded"])
                # 可用变体来自 combos_available 个套餐，不可能来自更多套餐
                self.assertLessEqual(report["combos_available"], report["variants_used"])

    def test_exclusions_add_up(self) -> None:
        for meal in ("breakfast", "lunch", "dinner"):
            with self.subTest(meal=meal):
                report = self.report(meal)
                self.assertEqual(sum(report["reasons"].values()), report["excluded"])
                # "识别 N 个套餐，纳入 M 个变体" — M counts variants, so it can
                # exceed N (one combo yields a default and a muscle variant),
                # but the exclusion list can never outnumber the combos found.
                self.assertGreaterEqual(report["combos_found"], report["excluded"])


if __name__ == "__main__":
    unittest.main()
