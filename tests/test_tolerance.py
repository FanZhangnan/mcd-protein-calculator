"""Protein-tolerance regressions (server-side, shared by page and Skill).

Rules under test
----------------
TOL1  strict plan wins: the real target is solved first, so a cheaper tolerated
      combo never replaces a plan that actually meets the goal
TOL2  the cheapest tolerated combo is offered as ``saver`` (one only), with the
      protein it really has and the money it really saves
TOL3  strict infeasible + tolerated feasible -> ``near``, reporting the real
      protein number, never the target
TOL4  both infeasible -> ``under``, fallback card and relaxations still use the
      *original* target
TOL5  tolerance = 0 reproduces the pre-tolerance behaviour exactly
TOL6  choosing the saver makes it the main plan (and flips status to near)
TOL7  hard caps are never relaxed by the tolerance
TOL8  the protein number is never rewritten (42 g stays 42 g)

Synthetic pools only. Run: python3 tests/test_tolerance.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import planner as planner_mod  # noqa: E402
from planner import Item, Targets  # noqa: E402
from service import Service  # noqa: E402
from helpers import nutr, new_svc  # noqa: E402

# prices/protein deliberately mirror the live 双层吉士 / 麦香鸡 pair
BEEF = Item("X", "合成双层吉士", "main", nutr(798, 27, 39), "synthetic", "合成", 22.5, 1, meal="lunch")
CHICKEN = Item("Y", "合成麦香鸡", "main", nutr(370, 15, 21), "synthetic", "合成", 16.5, 1, meal="lunch")
FATTY = Item("Z", "合成高脂堡", "main", nutr(900, 34, 50), "synthetic", "合成", 30.0, 1, meal="lunch")

POOLS = {"lunch": {"main": [BEEF, CHICKEN, FATTY], "side": [], "drink": []}}


def make_service() -> Service:
    svc = new_svc(tempfile.mkdtemp(prefix="mai-tol-"))
    svc.pools = lambda meal: {k: list(v) for k, v in POOLS.get(meal, {}).items()}  # type: ignore[assignment]
    svc.removable_options = lambda code, meal=None: {  # type: ignore[assignment]
        "ok": False, "options": [], "groups": [], "reason": "合成测试"}
    svc.upgrade_for = lambda code, meal, pool: None  # type: ignore[assignment]
    return svc


class ToleranceTest(unittest.TestCase):
    def setUp(self):
        self.svc = make_service()

    def plan(self, protein, budget=45, tolerance=3, **kw):
        targets = Targets(protein_min=protein, budget=budget,
                          protein_tolerance=tolerance, **kw)
        return self.svc.plan_meal("lunch", targets)

    # ------------------------------------------------------------------ TOL1
    def test_strict_plan_is_not_replaced_by_a_cheaper_tolerated_one(self):
        payload = self.plan(44, 45)
        self.assertEqual(payload["protein_status"], "met")
        self.assertTrue(payload["feasible"])
        self.assertEqual([(i["code"], i["quantity"]) for i in payload["items"]],
                         [("X", 2.0)])
        self.assertEqual(payload["totals"]["protein"], 54.0)
        self.assertEqual(payload["cost"], 45.0)

    # ------------------------------------------------------------------ TOL2
    def test_saver_is_the_cheapest_tolerated_combo(self):
        payload = self.plan(44, 45)
        saver = payload["saver"]
        self.assertIsNotNone(saver)
        self.assertEqual(saver["items"], ["合成双层吉士", "合成麦香鸡"])
        self.assertEqual(saver["protein"], 42.0)
        self.assertEqual(saver["cost"], 39.0)
        self.assertEqual(saver["protein_less"], 12.0)
        self.assertEqual(saver["money_saved"], 6.0)

    def test_no_saver_when_tolerance_is_zero(self):
        self.assertIsNone(self.plan(44, 45, tolerance=0)["saver"])

    def test_no_saver_when_nothing_is_cheaper(self):
        # 2 个高脂堡 = 68 g / 60 元 is the only way past 44 g under 70 元
        payload = self.plan(60, 70)
        self.assertEqual(payload["protein_status"], "met")
        self.assertIsNone(payload["saver"])

    # ------------------------------------------------------------------ TOL3
    def test_near_reports_the_real_protein_number(self):
        payload = self.plan(44, 41)  # strict 44 元? no: budget 41, strict needs 45
        self.assertEqual(payload["protein_status"], "near")
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["totals"]["protein"], 42.0)
        self.assertNotEqual(payload["totals"]["protein"], 44.0)
        self.assertEqual(payload["protein_gap"], 2.0)
        self.assertEqual(payload["protein_accept_min"], 41.0)
        self.assertIsNone(payload["frontier"])

    def test_near_never_claims_the_goal_was_met(self):
        payload = self.plan(44, 41)
        self.assertFalse(payload["protein_status"] == "met")
        self.assertIn("protein", {v["constraint"] for v in payload["violations"]})

    # ------------------------------------------------------------------ TOL4
    def test_under_keeps_the_original_target_for_relaxations(self):
        payload = self.plan(40, 35)
        self.assertEqual(payload["protein_status"], "under")
        self.assertFalse(payload["feasible"])
        self.assertEqual(payload["totals"]["protein"], 34.0)  # 高脂堡 34 g / 30 元? capped
        self.assertEqual(payload["protein_accept_min"], 37.0)
        self.assertEqual(payload["targets"]["protein_min"], 40.0)
        # relaxations are computed against 40 g, not 37 g
        self.assertIsNotNone(payload["frontier"])
        self.assertEqual(payload["frontier"]["max_protein"]["protein_gap"],
                         round(40 - payload["totals"]["protein"], 1))

    def test_under_fallback_still_respects_caps(self):
        payload = self.plan(40, 35, fat_max=40)
        self.assertEqual(payload["protein_status"], "under")
        self.assertLessEqual(payload["cost"], 35)
        self.assertLessEqual(payload["totals"]["fat"], 40)

    # ------------------------------------------------------------------ TOL5
    def test_tolerance_zero_matches_the_legacy_answer(self):
        payload = self.plan(40, 45, tolerance=0)
        self.assertEqual(payload["protein_status"], "met")
        self.assertEqual(payload["totals"]["protein"], 42.0)
        self.assertEqual(payload["cost"], 39.0)
        self.assertIsNone(payload["saver"])

    # ------------------------------------------------------------------ TOL6
    def test_selecting_the_saver_makes_it_the_main_plan(self):
        payload = self.svc.plan_meal(
            "lunch", Targets(protein_min=44, budget=45, protein_tolerance=3),
            select="saver")
        self.assertEqual(payload["selected"], "saver")
        self.assertEqual(payload["protein_status"], "near")
        self.assertEqual(payload["cost"], 39.0)
        self.assertEqual(payload["totals"]["protein"], 42.0)
        self.assertIsNone(payload["saver"])

    # ------------------------------------------------------------------ TOL7
    def test_tolerance_does_not_relax_price_or_fat(self):
        payload = self.plan(44, 45, tolerance=3, fat_max=30)
        self.assertLessEqual(payload["cost"], 45)
        self.assertLessEqual(payload["totals"]["fat"], 30)
        if payload["saver"]:
            self.assertLessEqual(payload["saver"]["cost"], 45)

    # ------------------------------------------------------------------ TOL8
    def test_payload_keeps_the_official_protein_value(self):
        payload = self.plan(44, 41)
        item_protein = sum(i["nutrition"]["protein"] * i["quantity"] for i in payload["items"])
        self.assertEqual(payload["totals"]["protein"], item_protein)


class SelectValidationTest(unittest.TestCase):
    def test_unknown_select_is_rejected(self):
        from server import InputError, validate_select
        with self.assertRaises(InputError):
            validate_select("cheapest")
        self.assertEqual(validate_select(None), "main")
        self.assertEqual(validate_select("saver"), "saver")

    def test_tolerance_is_an_integer_in_range(self):
        from server import InputError, validate_meal_request
        for value in (-1, 11, "abc", 3.5, True, float("nan")):
            with self.assertRaises(InputError, msg=repr(value)):
                validate_meal_request({"meal": "lunch", "protein_min": 40,
                                       "budget": 45, "protein_tolerance": value})
        for value in (0, 3, 10, "3"):
            _, targets, _, _ = validate_meal_request(
                {"meal": "lunch", "protein_min": 40, "budget": 45,
                 "protein_tolerance": value})
            self.assertEqual(targets.protein_tolerance, float(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
