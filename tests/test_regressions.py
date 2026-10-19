"""Regression tests for the six defects reported after the first build.

Every case is synthetic or fixture-based and clearly labelled; none of them
consumes live MCP quota except the cached fixtures.

Run: python3 tests/test_regressions.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from service import LiveProvider, OfflineProvider, Service  # noqa: E402
from store import Store  # noqa: E402

FIX = ROOT / "data" / "fixtures"


def make_service(tmp: str, targets: dict | None = None) -> Service:
    svc = Service(OfflineProvider(FIX), Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({"date": "2026-10-19",
                          "targets": targets or {"kcal_min": 1500, "kcal_max": 1900,
                                                 "protein_min": 80, "fat_max": 80,
                                                 "carb_max": 260, "budget": 110}})
    svc.ensure_cache("2026-10-19")
    return svc


def item(code: str, name: str, kcal: float, protein: float, fat: float = 10.0,
         carb: float = 30.0, sodium: float = 300.0, price: float = 10.0,
         kind: str = "main", qty: float = 1.0) -> planner_mod.Item:
    return planner_mod.Item(
        code=code, name=name, kind=kind,
        nutrition={"energyKj": kcal * 4.184, "energyKcal": kcal, "protein": protein,
                   "fat": fat, "carbohydrate": carb, "sodium": sodium, "calcium": 50.0},
        nutrition_status="synthetic", price_ref=price, quantity=qty,
    )


# ---------------------------------------------------------------- 1. replan math
class ReplanMathTests(unittest.TestCase):
    """Defect 1: eaten nutrition was subtracted, then counted again by the solver."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name, {"kcal_min": 1500, "kcal_max": 1900,
                                                "protein_min": 80, "fat_max": 80,
                                                "carb_max": 260, "budget": 110})
        self.targets = self.svc.build_targets()

    def tearDown(self):
        self.tmp.cleanup()

    def test_r1_eaten_is_subtracted_exactly_once(self):
        breakfast = [item("BF1", "合成早餐", 565, 30, price=20)]
        record = self.svc.record_meal("breakfast", [i.as_dict() for i in breakfast],
                                      day="2026-10-19", amount_actual=20.0)
        self.assertAlmostEqual(record["totals"]["energyKcal"], 565.0)

        result = self.svc.plan(self.targets)
        open_items = [i for m in result.meals if not m.locked for i in m.items]
        open_totals, _ = planner_mod.sum_nutrition(open_items)
        # remaining targets: 1500-565 = 935 .. 1900-565 = 1335
        self.assertGreaterEqual(open_totals["energyKcal"], 935.0)
        self.assertLessEqual(open_totals["energyKcal"], 1335.0)
        # day total is the sum of both, never compared against remaining targets
        self.assertAlmostEqual(result.totals["energyKcal"],
                               565.0 + open_totals["energyKcal"], places=1)
        self.assertGreaterEqual(result.totals["energyKcal"], 1500.0)

    def test_r2_remaining_keeps_fat_and_carb_lower_bounds(self):
        rem = self.svc.targets_after(
            planner_mod.Targets.from_dict({"kcal_min": 1500, "kcal_max": 1900, "protein_min": 80,
                                           "fat_min": 40, "fat_max": 80,
                                           "carb_min": 100, "carb_max": 260, "budget": 110}),
            {"breakfast": [item("BF1", "合成早餐", 500, 20, fat=30, carb=60, price=15)]},
        )
        self.assertAlmostEqual(rem.fat_min, 10.0)
        self.assertAlmostEqual(rem.fat_max, 50.0)
        self.assertAlmostEqual(rem.carb_min, 40.0)
        self.assertAlmostEqual(rem.carb_max, 200.0)


# ------------------------------------------------------- 2. quantity and amounts
class QuantityAmountTests(unittest.TestCase):
    """Defect 2: quantity ignored; budget used reference price instead of actual."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name)
        self.targets = self.svc.build_targets()

    def tearDown(self):
        self.tmp.cleanup()

    def test_r3_quantity_multiplies_nutrition(self):
        rec = self.svc.record_meal(
            "lunch", [item("X1", "合成单品", 400, 20, price=10, qty=2).as_dict()],
            day="2026-10-19", amount_actual=20.0)
        self.assertAlmostEqual(rec["totals"]["energyKcal"], 800.0)
        self.assertAlmostEqual(rec["totals"]["protein"], 40.0)

    def test_r4_actual_amount_drives_remaining_budget(self):
        self.svc.record_meal("lunch", [item("X1", "合成单品", 400, 20, price=10).as_dict()],
                             day="2026-10-19", amount_actual=50.0)
        rem = self.svc.remaining_targets(self.targets, "2026-10-19")
        self.assertAlmostEqual(rem["eaten_cost"], 50.0)
        self.assertAlmostEqual(rem["remaining"]["budget"], 110.0 - 50.0)

    def test_r5_unknown_amount_is_not_zero(self):
        self.svc.record_meal("lunch", [item("X1", "合成单品", 400, 20, price=10).as_dict()],
                             day="2026-10-19")
        rem = self.svc.remaining_targets(self.targets, "2026-10-19")
        self.assertTrue(rem["budget_unknown"])
        self.assertIsNone(rem["remaining"]["budget"])

    def test_r6_edit_record_updates_totals_and_amount(self):
        rec = self.svc.record_meal("lunch", [item("X1", "合成单品", 400, 20, price=10).as_dict()],
                                   day="2026-10-19", amount_actual=10.0)
        updated = self.svc.update_record(rec["id"], {
            "items": [item("X1", "合成单品", 400, 20, price=10, qty=3).as_dict()],
            "amount_actual": 28.5,
        })
        self.assertAlmostEqual(updated["totals"]["energyKcal"], 1200.0)
        self.assertAlmostEqual(updated["amount_actual"], 28.5)
        self.assertEqual(updated["amount_source"], "manual")

    def test_r7_delete_restores_budget(self):
        rec = self.svc.record_meal("lunch", [item("X1", "合成单品", 400, 20, price=10).as_dict()],
                                   day="2026-10-19", amount_actual=50.0)
        self.svc.store.delete_intake(rec["id"])
        rem = self.svc.remaining_targets(self.targets, "2026-10-19")
        self.assertAlmostEqual(rem["remaining"]["budget"], 110.0)


# ------------------------------------------------------------- 3. customisation
class CustomisationTests(unittest.TestCase):
    """Defect 3: no capability check, baseline mutated, repeated application."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name)
        self.svc.store.save_state({"targets": {"kcal_min": 1500, "kcal_max": 2200,
                                               "protein_min": 60, "fat_max": 90,
                                               "carb_max": 300, "budget": 110}})
        cat = self.svc.catalog()
        match = cat.lookup("1450", "麦香鸡")
        self.item = planner_mod.Item(code="1450", name="麦香鸡", kind="main",
                                     nutrition=match.nutrition, nutrition_status=match.status,
                                     price_ref=17.0)
        self.base = planner_mod.PlanResult(
            feasible=True, meals=[planner_mod.MealPlan(meal="lunch", items=[self.item],
                                                       price_ref=17.0)],
            totals={}, unknowns=[], is_estimate=False, cost=17.0, violations=[], score=0.0)
        self.svc.set_baseline(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def _custom(self):
        return {"lunch": {"1450": {"action": "remove_sauce"}}}

    def test_r8_apply_then_cancel_restores_baseline(self):
        on = self.svc.recompute(customizations=self._custom(), scenario="tw_sauce_serving")
        self.assertAlmostEqual(on["totals"]["energyKcal"], 301.0)

        off = self.svc.recompute(customizations={}, scenario="tw_sauce_serving")
        self.assertAlmostEqual(off["totals"]["energyKcal"], 369.0)
        self.assertFalse(off["is_estimate"])

    def test_r9_repeated_apply_does_not_accumulate(self):
        first = self.svc.recompute(customizations=self._custom(), scenario="tw_sauce_serving")
        second = self.svc.recompute(customizations=self._custom(), scenario="tw_sauce_serving")
        third = self.svc.recompute(customizations=self._custom(), scenario="tw_sauce_serving")
        self.assertAlmostEqual(first["totals"]["energyKcal"], second["totals"]["energyKcal"])
        self.assertAlmostEqual(second["totals"]["energyKcal"], third["totals"]["energyKcal"])
        self.assertNotAlmostEqual(third["totals"]["energyKcal"], 233.0)

    def test_r10_unsupported_sku_is_rejected(self):
        cat = self.svc.catalog()
        match = cat.lookup("9900003586", "双层深海鳕鱼堡")
        fish = planner_mod.Item(code="9900003586", name="双层深海鳕鱼堡", kind="main",
                                nutrition=match.nutrition, nutrition_status=match.status)
        base = planner_mod.PlanResult(
            feasible=True, meals=[planner_mod.MealPlan(meal="lunch", items=[fish])],
            totals={}, unknowns=[], is_estimate=False, cost=24.0, violations=[], score=0.0)
        self.svc.set_baseline(base)
        out = self.svc.recompute(customizations={"lunch": {"9900003586": {"action": "remove_sauce"}}},
                                 scenario="tw_sauce_serving")
        self.assertTrue(out["issues"])
        self.assertFalse(out["is_estimate"])
        self.assertIsNotNone(out["totals"]["sodium"])

    def test_r11_scenario_must_match_component(self):
        cat = self.svc.catalog()
        match = cat.lookup("1406", "板烧鸡腿堡")
        ban = planner_mod.Item(code="1406", name="板烧鸡腿堡", kind="main",
                               nutrition=match.nutrition, nutrition_status=match.status)
        base = planner_mod.PlanResult(
            feasible=True, meals=[planner_mod.MealPlan(meal="lunch", items=[ban])],
            totals={}, unknowns=[], is_estimate=False, cost=23.5, violations=[], score=0.0)
        self.svc.set_baseline(base)
        # 板烧's sauce is 烤味酱 -> the 麦香鸡酱 scenarios must not apply
        out = self.svc.recompute(customizations={"lunch": {"1406": {"action": "remove_sauce"}}},
                                 scenario="tw_sauce_serving")
        self.assertTrue(out["issues"])
        self.assertFalse(out["is_estimate"])

    def test_r12_recompute_updates_feasibility(self):
        self.svc.store.save_state({"targets": {"kcal_min": 2000, "kcal_max": 2200,
                                               "protein_min": 60, "fat_max": 90,
                                               "carb_max": 300, "budget": 110}})
        out = self.svc.recompute(customizations={}, scenario="official_only")
        self.assertFalse(out["feasible"])
        self.assertTrue(out["violations"])


# ------------------------------------------------------------------ 4. quotes
class QuoteRollupTests(unittest.TestCase):
    """Defect 4: quotes were attached but never rolled back into the verdict."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(self.tmp.name)
        self.targets = planner_mod.Targets.from_dict(
            {"kcal_min": 1500, "kcal_max": 1900, "protein_min": 80,
             "fat_max": 80, "carb_max": 260, "budget": 110})

    def tearDown(self):
        self.tmp.cleanup()

    def _result(self):
        return planner_mod.PlanResult(
            feasible=True, meals=[], totals={"energyKcal": 1800.0, "protein": 90.0,
                                             "fat": 70.0, "carbohydrate": 200.0,
                                             "sodium": 2000.0, "calcium": 300.0},
            unknowns=[], is_estimate=False, cost=110.0, violations=[], score=0.0)

    def test_r13_expensive_quotes_flip_feasible_to_false(self):
        quotes = {m: {"status": "ok", "amount": 500.0} for m in planner_mod.MEALS}
        rolled = Service.apply_quotes(self._result(), self.targets, quotes)
        self.assertEqual(rolled["status"], "quoted")
        self.assertAlmostEqual(rolled["effective_cost"], 1500.0)
        self.assertFalse(rolled["feasible"])
        self.assertIn("budget", {v["constraint"] for v in rolled["violations"]})

    def test_r14_failed_quote_marks_budget_not_evaluated(self):
        quotes = {"breakfast": {"status": "ok", "amount": 30.0},
                  "lunch": {"status": "failed", "amount": None},
                  "dinner": {"status": "ok", "amount": 40.0}}
        rolled = Service.apply_quotes(self._result(), self.targets, quotes)
        self.assertEqual(rolled["status"], "not_evaluated")
        self.assertIsNone(rolled["effective_cost"])
        self.assertFalse(rolled["feasible"])

    def test_r15_quote_signature_changes_with_items(self):
        self.svc.ensure_cache("2026-10-19")
        cache = self.svc.ensure_cache()
        a = planner_mod.MealPlan(meal="lunch", items=[item("A", "甲", 400, 20)])
        b = planner_mod.MealPlan(meal="lunch", items=[item("A", "甲", 400, 20, qty=2)])
        sig_a = self.svc.plan_signature(cache, [a])
        sig_b = self.svc.plan_signature(cache, [b])
        self.assertNotEqual(sig_a, sig_b)


# ------------------------------------------------------------------- 5. cache
class CacheContextTests(unittest.TestCase):
    """Defect 5: cache keyed by date only -> new store/times returned stale data."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = Service(OfflineProvider(FIX), Store(Path(self.tmp.name)),
                           base=Path(self.tmp.name))
        self.svc.store.save_state({
            "date": "2026-10-19",
            "store": {"code": "1450713", "beType": 1, "orderType": 1},
            "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_r16_store_and_time_change_invalidates_cache(self):
        first = self.svc.ensure_cache()
        self.assertEqual(first["store_code"], "1450713")
        self.assertEqual(first["meal_times"]["breakfast"], "08:15")

        self.svc.store.save_state({
            "store": {"code": "1440054", "beType": 1, "orderType": 1},
            "meal_times": {"breakfast": "09:00", "lunch": "12:15", "dinner": "18:30"},
        })
        second = self.svc.ensure_cache()
        self.assertEqual(second["store_code"], "1440054")
        self.assertEqual(second["meal_times"]["breakfast"], "09:00")
        self.assertNotEqual(first["cache_key"], second["cache_key"])

    def test_r17_cache_file_per_context(self):
        self.svc.ensure_cache()
        self.svc.store.save_state({"meal_times": {"breakfast": "07:30", "lunch": "12:15",
                                                  "dinner": "18:30"}})
        self.svc.ensure_cache()
        files = list((Path(self.tmp.name) / "cache").glob("*.json"))
        self.assertGreaterEqual(len(files), 2)

    def test_r18_offline_provider_is_never_labelled_live(self):
        cache = self.svc.ensure_cache()
        self.assertEqual(cache["source"], "offline")  # provider truth, never "live"
        # after a re-load from disk it must downgrade to "cache"
        svc2 = Service(OfflineProvider(FIX), Store(Path(self.tmp.name)),
                       base=Path(self.tmp.name))
        reloaded = svc2.ensure_cache()
        self.assertEqual(reloaded["source"], "cache")
        self.assertEqual(svc2.cache_info()["source"], "cache")
        self.assertEqual(svc2.provider.source_name, "offline")


# ------------------------------------------------------------------ 6. solver
class SolverSelectionTests(unittest.TestCase):
    """Defect 6: the minimum-score candidate was returned even when infeasible."""

    def test_r19_prefers_feasible_candidate_over_better_score(self):
        dinner_a = item("A", "晚餐A", 1200, 40, fat=20, carb=80, price=30)
        dinner_b = item("B", "晚餐B", 1000, 39, fat=20, carb=80, price=28)
        pools = {
            "lunch": {"main": [], "side": [], "drink": []},
            "dinner": {"main": [dinner_a, dinner_b], "side": [], "drink": []},
        }
        locked = {
            "breakfast": [item("L1", "已吃早", 500, 30, price=15)],
            "lunch": [item("L2", "已吃午", 500, 30, price=15)],
        }
        # plan()'s contract: when locked meals exist, ``targets`` is already the remainder
        remaining = planner_mod.Targets.from_dict(
            {"kcal_min": 800, "kcal_max": 1200, "protein_min": 40, "budget": 170})
        result = planner_mod.plan(pools, remaining, locked)
        dinner = next(m for m in result.meals if m.meal == "dinner")
        self.assertEqual([i.code for i in dinner.items], ["A"])
        self.assertTrue(result.feasible)
        self.assertAlmostEqual(result.totals["protein"], 100.0)

    def test_r20_unknown_critical_nutrient_cannot_be_feasible(self):
        dinner = item("A", "晚餐A", 1200, 40, price=30)
        dinner.nutrition = dict(dinner.nutrition or {})
        dinner.nutrition["protein"] = None
        pools = {"dinner": {"main": [dinner], "side": [], "drink": []}}
        targets = planner_mod.Targets.from_dict(
            {"kcal_min": 800, "kcal_max": 1300, "protein_min": 30, "budget": 50})
        result = planner_mod.plan_open(pools, targets, ["dinner"])
        self.assertFalse(result.feasible)
        self.assertIn("unknown", {v["constraint"] for v in result.violations})


# --------------------------------------------------------- 7. input validation
class InputValidationTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "src"))
        import server as server_mod
        self.server = server_mod

    def test_r21_rejects_inverted_range(self):
        with self.assertRaises(self.server.InputError):
            self.server.validate_targets({"kcal_min": 2200, "kcal_max": 1800,
                                          "protein_min": 80, "budget": 100})

    def test_r22_rejects_negative_and_zero_budget(self):
        with self.assertRaises(self.server.InputError):
            self.server.validate_targets({"kcal_min": -100, "kcal_max": 1800,
                                          "protein_min": 80, "budget": 100})
        with self.assertRaises(self.server.InputError):
            self.server.validate_targets({"kcal_min": 1500, "kcal_max": 1800,
                                          "protein_min": 80, "budget": 0})

    def test_r23_accepts_valid_targets(self):
        out = self.server.validate_targets({"kcal_min": 1500, "kcal_max": 1800,
                                            "protein_min": 80, "fat_max": 70,
                                            "carb_max": 250, "budget": 100})
        self.assertEqual(out["kcal_max"], 1800.0)
        self.assertIsNone(out["fat_min"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
