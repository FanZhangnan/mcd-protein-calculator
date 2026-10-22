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
ALIASES = ROOT / "data" / "aliases.json"
SUPPLEMENT = ROOT / "data" / "combo_nutrition_map.json"


def load_catalog(supplement: bool = True) -> catalog_mod.NutritionCatalog:
    return catalog_mod.NutritionCatalog.from_files(
        FIX / "nutrition.json", ALIASES,
        SUPPLEMENT if supplement else None)


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

    def test_alias_targets_exist_in_the_nutrition_table(self):
        """Every code->name alias must point at a row that really exists."""
        rows = catalog_mod.parse_nutrition_payload(
            json.loads((FIX / "nutrition.json").read_text("utf-8")))
        names = {r["productName"] for r in rows}
        aliases = json.loads(ALIASES.read_text("utf-8"))["codes"]
        missing = {code: entry["name"] for code, entry in aliases.items()
                   if entry["name"] not in names}
        self.assertEqual(missing, {}, f"aliases point at absent rows: {missing}")

    def test_alias_failure_falls_back_to_the_menu_name(self):
        """A stale alias must not hide a perfectly valid exact name."""
        cat = catalog_mod.NutritionCatalog(
            catalog_mod.parse_nutrition_payload(
                json.loads((FIX / "nutrition.json").read_text("utf-8"))),
            {"9999": "表里没有的名字"})
        match = cat.lookup("9999", "麦香鸡")
        self.assertTrue(match.usable)
        self.assertEqual(match.status, catalog_mod.STATUS_OFFICIAL)
        self.assertIn("别名", match.note)

    def test_supplemental_map_resolves_verified_spellings(self):
        match = self.cat.lookup("1700", "麦辣鸡翅2块")
        self.assertTrue(match.usable)
        self.assertEqual(match.status, catalog_mod.STATUS_SUPPLEMENTAL)
        self.assertAlmostEqual(match.nutrition["protein"], 13.0)
        self.assertIn("连字符", match.note)

    def test_duplicate_rows_do_not_make_a_normalised_name_ambiguous(self):
        # 纯牛奶（盒装）appears twice with identical values; the bracket-width
        # path must still resolve it instead of reporting two specs.
        match = self.cat.lookup("3755", "纯牛奶(盒装)")
        self.assertTrue(match.usable)
        self.assertEqual(match.status, catalog_mod.STATUS_OFFICIAL)
        self.assertAlmostEqual(match.nutrition["protein"], 7.0)

    def test_unverified_spelling_stays_unknown(self):
        # 6102 苹果片一袋(60g): the official table has no gram field, so the
        # map deliberately rejects it instead of guessing the serving size.
        match = self.cat.lookup("6102", "苹果片一袋(60g)")
        self.assertFalse(match.usable)
        self.assertEqual(match.status, catalog_mod.STATUS_NOT_FOUND)


if __name__ == "__main__":
    unittest.main(verbosity=2)
