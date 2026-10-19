"""Offline scenario tests for 麦麦健身餐管家 (18 scenarios).

Run:  python3 tests/test_scenarios.py
Fixtures come from real MCP calls captured on 2026-10-18/19. No network here.

Note: scenario 09 was rewritten in this revision. The old one applied a sauce
estimate to any main item; that is exactly the defect being fixed, so it now
goes through the capability-checked baseline/recompute path instead.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import catalog as catalog_mod  # noqa: E402
import estimator  # noqa: E402
import planner as planner_mod  # noqa: E402
from mcp_client import McpError  # noqa: E402
from service import MEALS, OfflineProvider, Service  # noqa: E402
from store import Store  # noqa: E402

FIX = ROOT / "data" / "fixtures"


class StubPriceProvider(OfflineProvider):
    """Offline provider that also answers price calls from the monthly menus."""

    def price(self, store_code, be_type, order_type, items, need_tableware=False, when=None):
        table: dict[str, float] = {}
        for fixture in ("menu_breakfast_0815", "menu_lunch_1215", "menu_dinner_1830"):
            meals = ((self._load(fixture).get("data") or {}).get("meals") or {})
            for code, info in meals.items():
                try:
                    table[str(code)] = float(info.get("currentPrice") or 0)
                except (TypeError, ValueError):
                    pass
        total = 0.0
        for item in items:
            total += table.get(str(item["productCode"]), 0.0) * float(item.get("quantity", 1))
        fen = int(round(total * 100))
        return {"success": True, "code": 200, "message": "请求成功（离线桩）",
                "datetime": "2026-10-19 00:00:00",
                "data": {"price": fen, "originalPrice": fen, "discount": 0,
                         "takeWayList": [{"code": "eat-in", "title": "堂食"}]}}


def make_service(tmp: str, stub_price: bool = False) -> Service:
    provider = StubPriceProvider(FIX) if stub_price else OfflineProvider(FIX)
    svc = Service(provider, Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({"date": "2026-10-19"})
    return svc


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name)
        self.svc.ensure_cache("2026-10-19")
        self.cat = self.svc.catalog()

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_alias_gives_official_value(self):
        match = self.cat.lookup("4810", "薯条")
        self.assertEqual(match.status, catalog_mod.STATUS_ALIAS)
        self.assertAlmostEqual(match.nutrition["energyKcal"], 289.0)
        self.assertEqual(match.nutrition["protein"], 4.0)

    def test_02_exact_name_match(self):
        match = self.cat.lookup("__no_alias__", "板烧鸡腿堡")
        self.assertEqual(match.status, catalog_mod.STATUS_OFFICIAL)
        self.assertAlmostEqual(match.nutrition["protein"], 23.0)

    def test_02b_menu_code_resolved_through_alias(self):
        match = self.cat.lookup("1406", "板烧鸡腿堡")
        self.assertEqual(match.status, catalog_mod.STATUS_ALIAS)
        self.assertAlmostEqual(match.nutrition["energyKcal"], 391.0)

    def test_03_same_name_different_spec_not_merged(self):
        match = self.cat.lookup("4437", "玉米杯")
        self.assertFalse(match.usable)
        self.assertIn(match.status, (catalog_mod.STATUS_NOT_FOUND, catalog_mod.STATUS_AMBIGUOUS))
        self.assertIsNone(match.nutrition)

    def test_04_duplicate_names_are_reported(self):
        dup = self.cat.duplicates
        self.assertIn("小杯玉米杯", dup)
        self.assertTrue(dup["小杯玉米杯"]["identical"])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name)
        self.svc.ensure_cache("2026-10-19")

    def tearDown(self):
        self.tmp.cleanup()

    def _targets(self, **kw):
        base = dict(kcal_min=1500, kcal_max=1900, protein_min=80, fat_max=80,
                    carb_max=260, budget=110)
        base.update(kw)
        return planner_mod.Targets.from_dict(base)

    def test_05_feasible_three_meal_plan(self):
        result = self.svc.plan(self._targets())
        self.assertTrue(result.feasible, result.violations)
        self.assertEqual([m.meal for m in result.meals], list(MEALS))
        for meal in result.meals:
            self.assertTrue(meal.items)

    def test_06_budget_infeasible_reports_gap(self):
        result = self.svc.plan(self._targets(budget=10))
        self.assertFalse(result.feasible)
        self.assertIn("budget", {v["constraint"] for v in result.violations})
        self.assertIn("不视为达标", result.note)

    def test_07_no_implicit_relaxation(self):
        result = self.svc.plan(self._targets(protein_min=400))
        self.assertFalse(result.feasible)
        self.assertIn("protein", {v["constraint"] for v in result.violations})

    def test_08_unknown_nutrition_kept_out_of_plan(self):
        pools = self.svc.pools("lunch")
        unusable = [i for kind in pools for i in pools[kind] if not i.nutrition]
        used_codes = {i.code for m in self.svc.plan(self._targets()).meals for i in m.items}
        for item in unusable:
            self.assertNotIn(item.code, used_codes)
        self.assertTrue(self.svc.unknown_items("lunch"))

    def test_09_estimate_flag_propagates(self):
        """Capability-checked path: baseline -> recompute -> flags propagate."""
        self.svc.store.save_state({"targets": {"kcal_min": 1500, "kcal_max": 1900,
                                               "protein_min": 80, "fat_max": 80,
                                               "carb_max": 260, "budget": 110}})
        cat = self.svc.catalog()
        match = cat.lookup("1450", "麦香鸡")       # 麦香鸡酱 -> scenarios A/B apply
        self.assertTrue(match.usable)
        item = planner_mod.Item(code="1450", name="麦香鸡", kind="main",
                                nutrition=match.nutrition, nutrition_status=match.status)
        base = planner_mod.PlanResult(
            feasible=True, meals=[planner_mod.MealPlan(meal="lunch", items=[item],
                                                       price_ref=17.0)],
            totals={}, unknowns=[], is_estimate=False, cost=17.0, violations=[], score=0.0)
        self.svc.set_baseline(base)

        out = self.svc.recompute(customizations={"lunch": {"1450": {"action": "remove_sauce"}}},
                                 scenario="tw_sauce_serving")
        self.assertFalse(out["issues"])
        self.assertTrue(out["is_estimate"])
        self.assertIsNone(out["totals"]["sodium"])
        self.assertEqual(out["meals"][0]["items"][0]["nutrition"]["protein"], 14.8)
        self.assertAlmostEqual(out["meals"][0]["items"][0]["nutrition"]["energyKcal"], 301.0)

    def test_10_estimate_unknown_nutrients_stay_null(self):
        base = {"energyKj": 2029, "energyKcal": 485, "protein": 24, "fat": 24,
                "carbohydrate": 42, "sodium": 1208, "calcium": 154}
        result = estimator.estimate_remove_sauce(base, "tw_sauce_serving")
        self.assertEqual(result["result"]["protein"], 23.8)
        self.assertIsNone(result["result"]["sodium"])
        self.assertIsNone(result["result"]["calcium"])
        self.assertIn("sodium", result["unknown"])
        self.assertAlmostEqual(result["result"]["energyKj"], 1744.5, places=1)

    def test_11_combo_swap_nutrition_delta(self):
        swap = self.svc.combo_swap("9900005462", 2, "4820")
        self.assertTrue(swap["ok"], swap)
        delta = swap["nutrition_delta"]
        self.assertAlmostEqual(delta["energyKcal"], 379 - 289, places=1)
        self.assertAlmostEqual(delta["protein"], 6 - 4, places=1)
        self.assertFalse(swap["integrated_in_planner"])

    def test_12_combo_swap_rejects_unavailable_choice(self):
        swap = self.svc.combo_swap("9900005462", 2, "4437")
        self.assertFalse(swap["ok"])
        self.assertEqual(swap["stage"], "choice")
        self.assertTrue(swap["available"])


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name, stub_price=True)
        self.svc.ensure_cache("2026-10-19")
        self.targets = planner_mod.Targets.from_dict(
            dict(kcal_min=1500, kcal_max=1900, protein_min=80, fat_max=80,
                 carb_max=260, budget=110))
        self.svc.store.save_state({"targets": self.targets.as_dict()})

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self):
        return self.svc.plan(self.targets)

    def test_13_record_breakfast_locks_only_breakfast(self):
        plan = self._plan()
        breakfast = plan.meals[0]
        record = self.svc.record_meal("breakfast", [i.as_dict() for i in breakfast.items],
                                      day="2026-10-19", amount_actual=30.0)
        self.assertTrue(record["locked"])
        self.assertEqual(record["meal"], "breakfast")

        replanned = self.svc.plan(self.targets)
        locked = [m for m in replanned.meals if m.locked]
        self.assertEqual([m.meal for m in locked], ["breakfast"])
        self.assertEqual([i.code for i in locked[0].items], [i.code for i in breakfast.items])
        self.assertEqual({m.meal for m in replanned.meals}, set(MEALS))

    def test_14_delete_record_restores_remaining(self):
        plan = self._plan()
        record = self.svc.record_meal("breakfast", [i.as_dict() for i in plan.meals[0].items],
                                      day="2026-10-19", amount_actual=30.0)
        before = self.svc.remaining_targets(self.targets, "2026-10-19")
        self.assertLess(before["remaining"]["protein_min"], self.targets.protein_min)

        self.assertTrue(self.svc.store.delete_intake(record["id"]))
        after = self.svc.remaining_targets(self.targets, "2026-10-19")
        self.assertEqual(after["remaining"]["protein_min"], self.targets.protein_min)
        self.assertEqual(after["remaining"]["budget"], self.targets.budget)

    def test_15_actual_intake_below_plan_changes_remaining(self):
        plan = self._plan()
        eaten = plan.meals[0].items[0].as_dict()
        self.svc.record_meal("breakfast", [eaten], day="2026-10-19", amount_actual=10.0)
        remaining = self.svc.remaining_targets(self.targets, "2026-10-19")
        expected = self.targets.protein_min - (eaten["nutrition"] or {}).get("protein", 0)
        self.assertAlmostEqual(remaining["remaining"]["protein_min"], expected, places=1)

    def test_16_quote_ok_and_failure_states(self):
        plan = self._plan()
        quote = self.svc.quote_meal(plan.meals[0])
        self.assertEqual(quote["status"], "ok")
        self.assertIsNotNone(quote["amount"])
        self.assertTrue(quote["signature"])

        svc2 = make_service(tempfile.mkdtemp())
        svc2.ensure_cache("2026-10-19")
        failed = svc2.quote_meal(plan.meals[0])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["amount"])

    def test_17_combo_nutrition_not_double_counted(self):
        plan = self._plan()
        meal = plan.meals[1]
        total = sum(((i.nutrition or {}).get("energyKcal") or 0) * i.quantity for i in meal.items)
        self.assertAlmostEqual(meal.nutrition["energyKcal"], total, places=1)
        pools = self.svc.pools("lunch")
        self.assertFalse([i for kind in pools for i in pools[kind]
                          if catalog_mod.is_combo_name(i.name)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
