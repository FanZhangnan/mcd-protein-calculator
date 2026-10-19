"""Catalog matching regressions (migrated from the day-mode suite).

The single-meal solver is only as honest as this layer: a menu SKU may only
inherit a nutrition row through an evidenced alias or an exact (bracket-width
normalised) name. Sizes are never merged.

C1  evidenced alias gives the official row
C2  exact name match
C3  different specs of the same product are not merged
C4  duplicate names are reported instead of silently collapsed
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import catalog as catalog_mod  # noqa: E402

FIX = ROOT / "data" / "fixtures"


def load_catalog() -> catalog_mod.NutritionCatalog:
    return catalog_mod.NutritionCatalog.from_files(
        FIX / "nutrition.json", ROOT / "data" / "aliases.json")


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()

    def test_c1_alias_gives_official_value(self):
        match = self.cat.lookup("1406", "板烧鸡腿堡")
        self.assertEqual(match.status, catalog_mod.STATUS_ALIAS)
        self.assertTrue(match.usable)
        self.assertAlmostEqual(match.nutrition["protein"], 23.0)
        self.assertIn("板烧鸡腿堡", match.note)

    def test_c2_exact_name_match(self):
        match = self.cat.lookup("1440", "麦辣鸡腿汉堡")
        self.assertEqual(match.status, catalog_mod.STATUS_OFFICIAL)
        self.assertAlmostEqual(match.nutrition["energyKcal"], 485.0)

    def test_c3_same_name_different_spec_not_merged(self):
        # 可乐中杯 / 大杯 / 小杯 are three rows; the bare name must not pick one
        match = self.cat.lookup("NOPE", "可乐")
        self.assertFalse(match.usable)
        self.assertNotEqual(match.status, catalog_mod.STATUS_OFFICIAL)

        medium = self.cat.lookup("NOPE", "可乐中杯")
        self.assertEqual(medium.status, catalog_mod.STATUS_OFFICIAL)

    def test_c4_duplicate_names_are_reported(self):
        duplicates = self.cat.describe()["duplicates"]
        self.assertIn("小杯玉米杯", duplicates)
        self.assertEqual(duplicates["小杯玉米杯"]["count"], 2)
        self.assertTrue(duplicates["小杯玉米杯"]["identical"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
