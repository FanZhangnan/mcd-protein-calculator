"""Shared-logic regressions (migrated out of the deleted day-mode suite).

These cases never belonged to the three-meal planner: cache context, baseline
binding, the quote signature and the purchase-quantity contract are what the
single-meal product is built on.

CACHE   store/time change invalidates the cache; files are per context
BASE    a baseline from another store/date/time is never served (409)
QUOTE   quote the plan on screen, never re-solve; stale signature 409
SOLVE   feasible first; unknown critical nutrient is never feasible
INPUT   400-level validation
QTY     purchase counts are positive integers, never rounded
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
import quantity as quantity_mod  # noqa: E402
from quantity import InvalidQuantity  # noqa: E402
from server import InputError, validate_meal_request  # noqa: E402
from service import Service  # noqa: E402
from helpers import (meal_plan, new_service, new_svc, item, one_meal,  # noqa: E402
                     REMOVABLE)


class CacheContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_store_and_time_change_invalidates(self):
        first = self.svc.ensure_cache()["cache_key"]
        self.svc.store.save_state({"meal_times": {"breakfast": "09:00"}})
        self.svc.invalidate()
        second = self.svc.ensure_cache()["cache_key"]
        self.assertNotEqual(first, second)

        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        third = self.svc.ensure_cache()["cache_key"]
        self.assertNotEqual(second, third)

    def test_cache_file_is_per_context(self):
        self.svc.ensure_cache()
        files = list((Path(self.tmp.name) / "cache").glob("*.json"))
        self.assertEqual(len(files), 1)
        name = files[0].name
        self.assertIn("2026-10-19", name)
        self.assertIn("1440054", name)

    def test_offline_is_never_labelled_live(self):
        self.assertNotEqual(self.svc.provider.source_name, "live")
        self.assertEqual(self.svc.refresh()["source"], "offline")
        reloaded = Service(self.svc.provider, base=Path(self.tmp.name))
        self.assertEqual(reloaded.ensure_cache()["source"], "cache")
        self.assertNotEqual(self.svc.cache_info()["source"], "live")


class BaselineContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name)
        plan = planner_mod.PlanResult(
            feasible=True, meals=one_meal([item("LU1", "合成午餐", "main", 500, 25, 20, 22)]),
            totals={}, unknowns=[], is_estimate=False, cost=22.0, violations=[], score=0.0)
        self.svc.set_baseline(plan, extra={"mode": "single", "meal": "lunch",
                                           "targets": {"protein_min": 20, "budget": 30,
                                                       "fat_max": 25, "kcal_min": None,
                                                       "kcal_max": None}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_baseline_is_accepted(self):
        self.assertIsNotNone(self.svc.get_baseline())
        payload = self.svc.recompute()
        self.assertNotIn("error", payload)
        self.assertEqual(payload["cache"]["store_code"], "1440054")

    def test_store_change_retires_disk_baseline(self):
        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        self.assertTrue((Path(self.tmp.name) / "baseline.json").exists())
        self.assertIsNone(self.svc.get_baseline())
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")
        self.assertIn("门店", out.get("error", ""))

    def test_time_change_retires_baseline(self):
        self.svc.store.save_state({"meal_times": {"breakfast": "09:00"}})
        self.svc.invalidate()
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")

    def test_date_change_retires_baseline_and_quote(self):
        self.svc.set_working(one_meal([item("LU1", "合成午餐", "main", 500, 25, 20, 22)]))
        signature = self.svc.working_signature()
        self.svc.store.save_state({"date": "2026-10-21"})
        self.svc.invalidate()
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")
        quoted, status = self.svc.quote_working(signature)
        self.assertEqual(status, 409)
        self.assertEqual(quoted.get("kind"), "stale_context")


class QuoteCurrentPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name, amount=24.0)
        self.meals = [meal_plan("合成午餐", "LU1", 500, 25, 22, qty=2,
                                modification=[{"code": "100136", "key": "0-1", "quantity": 1}])]
        self.svc.set_working(self.meals, planner_mod.Targets(
            protein_min=20, budget=60, fat_max=40))
        self.signature = self.svc.working_signature()

    def tearDown(self):
        self.tmp.cleanup()

    def test_quote_never_runs_the_solver(self):
        with mock.patch.object(planner_mod, "plan_open",
                               side_effect=AssertionError("quote must not solve")):
            payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 200)
        sent = self.svc.provider.price_calls[0]["items"]
        self.assertEqual(sent[0]["quantity"], 2)
        self.assertEqual(sent[0]["modification"]["values"],
                         [{"code": "100136", "key": "0-1", "quantity": 1}])
        self.assertEqual(payload["items"][0]["quantity"], 2)
        self.assertEqual(payload["items"][0]["modification"],
                         [{"code": "100136", "key": "0-1", "quantity": 1}])
        self.assertEqual(payload["money_basis"], "quoted")
        self.assertEqual(payload["quotes"][payload["meal"]]["amount"], 24.0)

    def test_quote_has_server_timestamp_only(self):
        payload, _ = self.svc.quote_working(self.signature)
        quote = payload["quotes"]["lunch"]
        self.assertTrue(quote.get("queried_at"))
        self.assertNotIn("quoted_at", quote)

    def test_wrong_signature_is_rejected(self):
        payload, status = self.svc.quote_working("deadbeefdeadbeef")
        self.assertEqual(status, 409)
        self.assertEqual(payload["kind"], "stale_signature")
        self.assertEqual(self.svc.provider.price_calls, [])

    def test_missing_signature_is_rejected(self):
        payload, status = self.svc.quote_working("")
        self.assertEqual(status, 400)
        self.assertEqual(payload["kind"], "missing_signature")

    def test_context_change_blocks_quote(self):
        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 409)
        self.assertEqual(payload["kind"], "stale_context")
        self.assertEqual(self.svc.provider.price_calls, [])

    def test_recompute_refreshes_the_quotable_signature(self):
        base = planner_mod.PlanResult(
            feasible=True, meals=self.meals, totals={}, unknowns=[],
            is_estimate=False, cost=44.0, violations=[], score=0.0)
        self.svc.set_baseline(base, extra={"mode": "single", "meal": "lunch",
                                           "targets": {"protein_min": 20, "budget": 60,
                                                       "fat_max": 40, "kcal_min": None,
                                                       "kcal_max": None}})
        out = self.svc.recompute({"lunch": {"LU1": 3}}, {})
        self.assertNotIn("error", out)
        self.assertEqual(self.svc.working_signature(), out["signature"])
        payload, status = self.svc.quote_working(out["signature"])
        self.assertEqual(status, 200)
        self.assertEqual(self.svc.provider.price_calls[-1]["items"][0]["quantity"], 3)

    def test_quote_failure_keeps_reference_money(self):
        self.svc.provider.fail = True
        payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 200)
        self.assertEqual(payload["budget_status"], "not_evaluated")
        self.assertEqual(payload["money_basis"], "reference")
        self.assertEqual(payload["effective_cost"], 44.0)
        self.assertIn("参考价", payload["note"])


class SolverSelectionTest(unittest.TestCase):
    def test_capped_candidate_beats_cheaper_infeasible_one(self):
        cheap_fatty = item("A", "便宜高脂堡", "main", 600, 30, 30, 20)
        pricey_lean = item("B", "稍贵低脂堡", "main", 450, 25, 10, 40)
        pools = {"lunch": {"main": [cheap_fatty, pricey_lean], "side": [], "drink": []}}
        targets = planner_mod.Targets(protein_min=20, budget=45, fat_max=20)
        result = planner_mod.plan_open(pools, targets, ["lunch"], max_mains=1)
        self.assertTrue(result.feasible)
        self.assertEqual([i.code for i in result.meals[0].items], ["B"])

    def test_unknown_critical_nutrient_is_not_feasible(self):
        broken = item("X", "无蛋白数据堡", "main", 500, 0, 20, 20)
        broken.nutrition["protein"] = None
        pools = {"lunch": {"main": [broken], "side": [], "drink": []}}
        targets = planner_mod.Targets(protein_min=20, budget=45, fat_max=30)
        result = planner_mod.plan_open(pools, targets, ["lunch"], max_mains=1)
        self.assertFalse(result.feasible)
        self.assertTrue(any(v["constraint"] == "unknown" for v in result.violations))


class InputValidationTest(unittest.TestCase):
    def _ok(self, **over):
        body = {"meal": "lunch", "protein_min": 30, "fat_max": 20, "budget": 30}
        body.update(over)
        return body

    def test_rejects_inverted_kcal_range(self):
        with self.assertRaises(InputError):
            validate_meal_request(self._ok(kcal_min=800, kcal_max=600))

    def test_rejects_zero_and_negative_budget(self):
        for value in (0, -1):
            with self.assertRaises(InputError):
                validate_meal_request(self._ok(budget=value))

    def test_accepts_valid_request(self):
        meal, targets, allow_side, max_mains = validate_meal_request(self._ok())
        self.assertEqual(meal, "lunch")
        self.assertEqual(targets.protein_min, 30.0)
        self.assertEqual(targets.budget, 30.0)
        self.assertFalse(allow_side)
        self.assertEqual(max_mains, 2)


class PurchaseQuantityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name)
        self.plan = self.svc.plan_meal(
            "lunch", planner_mod.Targets(protein_min=20, budget=50, fat_max=40))
        self.item = next(i for i in self.plan["items"] if i["kind"] == "main")
        self.code = self.item["code"]
        self.unit_kcal = self.item["nutrition"]["energyKcal"]
        self.unit_price = self.item["price_ref"]
        self.provider = self.svc.provider
        self.provider.price_calls.clear()
        self.sig_before = self.svc.working_signature()

    def tearDown(self):
        self.tmp.cleanup()

    def test_positive_integers_accepted(self):
        for value in (1, 2, 3, "4", "5.0", 6.0):
            self.assertEqual(quantity_mod.purchase_quantity(value), int(float(value)))

    def test_fractions_and_junk_rejected(self):
        for value in (0.5, 1.5, 2.5, 0, -1, "", "  ", "abc", None,
                      float("nan"), float("inf"), True, False, [], {}):
            with self.assertRaises(InvalidQuantity, msg=repr(value)):
                quantity_mod.purchase_quantity(value)

    def test_quote_meal_refuses_fraction_without_calling_mcp(self):
        before = len(self.provider.price_calls)
        bad = meal_plan("半份测试堡", "SYN-HALF", 500, 20, 18.5, qty=0.5)
        with self.assertRaises(InvalidQuantity):
            self.svc.quote_meal(bad)
        self.assertEqual(len(self.provider.price_calls), before)

    def test_signature_normalises_two_and_two_point_zero(self):
        a = meal_plan("甲", "A", 400, 20, 10, qty=2)
        b = meal_plan("甲", "A", 400, 20, 10, qty=2.0)
        cache = self.svc.ensure_cache()
        self.assertEqual(self.svc.plan_signature(cache, [a]),
                         self.svc.plan_signature(cache, [b]))

    def test_purchase_two_reaches_nutrition_and_mcp(self):
        base = planner_mod.PlanResult(
            feasible=True, meals=[self.svc._meal_from_dict({"meal": "lunch",
                                                           "items": self.plan["items"]})],
            totals={}, unknowns=[], is_estimate=False, cost=0.0, violations=[], score=0.0)
        self.svc.set_baseline(base, extra={"mode": "single", "meal": "lunch",
                                           "targets": self.plan["targets"]})
        out = self.svc.recompute({"lunch": {self.code: 2}}, {})
        self.assertNotIn("error", out)
        current = next(i for i in out["items"] if str(i["code"]) == str(self.code))
        self.assertEqual(current["quantity"], 2)
        self.assertAlmostEqual(current["line_total_ref"], self.unit_price * 2, places=2)
        self.assertEqual(out["totals"]["energyKcal"],
                         round(self.plan["totals"]["energyKcal"] + self.unit_kcal, 1))

        payload, status = self.svc.quote_working(out["signature"])
        self.assertEqual(status, 200)
        sent = next(i for i in payload["quotes"]["lunch"]["requested_items"]
                    if i["productCode"] == self.code)
        self.assertEqual(sent["quantity"], 2)

    def test_removal_never_changes_nutrition(self):
        svc = new_svc(tempfile.mkdtemp(prefix="mai-shared-"))
        svc.removable_options = lambda code, meal=None: dict(REMOVABLE)  # type: ignore[assignment]
        base_plan = planner_mod.PlanResult(
            feasible=True, meals=one_meal([item("A", "合成牛肉堡", "main", 500, 25, 20, 25)]),
            totals={}, unknowns=[], is_estimate=False, cost=25.0, violations=[], score=0.0)
        svc.set_baseline(base_plan, extra={"mode": "single", "meal": "lunch",
                                           "targets": {"protein_min": 20, "budget": 30,
                                                       "fat_max": 25, "kcal_min": None,
                                                       "kcal_max": None}})
        out = svc.recompute({}, {"lunch": {"A": {"action": "suggest_remove",
                                                 "option_codes": ["C1"]}}})
        self.assertEqual(out["totals"]["energyKcal"], 500.0)
        self.assertEqual(out["totals"]["protein"], 25.0)
        self.assertEqual(out["items"][0]["nutrition_basis"], "standard_unadjusted")
        self.assertEqual(len(out["items"][0]["modification"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
