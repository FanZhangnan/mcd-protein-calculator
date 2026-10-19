"""Cross-step flow regressions (round 3).

Four reported defects, each reproduced in isolation first:

G1  ``plan_open([])`` used to fall back to three meals -> 3 extra meals after
    every meal was already eaten.
G2  quotes for the open meals were treated as the whole day's cost, and the
    quote roll-up wiped the booked amount of the eaten meal.
G3  ``invalidate()`` only cleared memory; ``get_baseline()`` then reloaded the
    old plan from disk and recomputed against the wrong store.
G4  „quote“ went through ``/api/plan`` and therefore re-ran the solver, losing
    the user's quantity / customisation edits.

Everything here is synthetic or fixture-based and clearly labelled. No network.

Run: python3 tests/test_flows.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from service import MEALS, OfflineProvider, Service  # noqa: E402
from store import Store  # noqa: E402

FIX = ROOT / "data" / "fixtures"


class RecordingProvider(OfflineProvider):
    """Offline menu/nutrition, deterministic quote, and a call log."""

    def __init__(self, fixture_dir: Path, amount: float = 23.0):
        super().__init__(fixture_dir)
        self.amount = amount
        self.price_calls: list[dict] = []
        self.fail = False

    def price(self, store_code, be_type, order_type, items, need_tableware=False, when=None):
        self.price_calls.append({"items": items, "when": when,
                                 "store": store_code})
        if self.fail:
            return {"success": False, "code": 60006, "message": "商品已售罄或不可售",
                    "datetime": "2026-10-19 00:00:00", "data": None}
        return {"success": True, "code": 200, "message": "请求成功（离线桩）",
                "datetime": "2026-10-19 00:00:00",
                "data": {"price": int(round(self.amount * 100)),
                         "originalPrice": int(round(self.amount * 100)), "discount": 0,
                         "takeWayList": [{"code": "eat-in", "title": "堂食"}]}}


def new_service(tmp: str, targets: dict | None = None,
                amount: float = 23.0) -> Service:
    provider = RecordingProvider(FIX, amount=amount)
    svc = Service(provider, Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({
        "date": "2026-10-19",
        "store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅",
                  "beType": 1, "orderType": 1},
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
        "targets": targets or {"kcal_min": 1700, "kcal_max": 1900,
                               "protein_min": 80, "fat_max": 90,
                               "carb_max": 260, "budget": 120},
    })
    svc.ensure_cache("2026-10-19")
    return svc


def entry(code: str, name: str, kcal: float, protein: float, price: float = 20.0,
          qty: float = 1.0) -> dict:
    return {
        "code": code, "name": name, "kind": "main", "quantity": qty,
        "nutrition": {"energyKj": round(kcal * 4.184, 1), "energyKcal": kcal,
                      "protein": protein, "fat": 20.0, "carbohydrate": 60.0,
                      "sodium": 500.0, "calcium": 100.0},
        "nutrition_status": "synthetic", "nutrition_note": "合成测试数据",
        "price_ref": price, "line_total_ref": round(price * qty, 2),
        "is_estimate": False, "estimate": None, "modification": [],
    }


def meal_plan(name: str, code: str, kcal: float, protein: float, price: float,
              *, locked: bool = False, amount: float | None = None,
              qty: float = 1.0, modification: list | None = None) -> planner_mod.MealPlan:
    item = planner_mod.Item(
        code=code, name=name, kind="main",
        nutrition={"energyKj": round(kcal * 4.184, 1), "energyKcal": kcal,
                   "protein": protein, "fat": 20.0, "carbohydrate": 60.0,
                   "sodium": 500.0, "calcium": 100.0},
        nutrition_status="synthetic", price_ref=price, quantity=qty,
        modification=modification or [],
    )
    nutrition, unknown = planner_mod.sum_nutrition([item])
    return planner_mod.MealPlan(
        meal=name, items=[item], nutrition=nutrition, unknown=unknown,
        price_ref=round(price * qty, 2), amount_actual=amount,
        amount_source="manual" if amount is not None else "unknown",
        locked=locked,
    )


# ------------------------------------------------- 1. nothing open means nothing
class NoOpenMealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name, {"kcal_min": 1500, "kcal_max": 1900,
                                               "protein_min": 80, "fat_max": 90,
                                               "carb_max": 260, "budget": 120})

    def tearDown(self):
        self.tmp.cleanup()

    def test_g1_empty_open_list_is_not_all_meals(self):
        result = planner_mod.plan_open({}, planner_mod.Targets.from_dict(
            {"kcal_min": 0, "kcal_max": 0, "protein_min": 0}), [])
        self.assertEqual(result.meals, [])
        self.assertEqual(result.cost, 0.0)

    def test_g2_three_meals_eaten_returns_history_only(self):
        for name in MEALS:
            self.svc.record_meal(name, [entry(f"{name}-1", f"合成{name}", 600, 30)],
                                 day="2026-10-19", amount_actual=20.0)
        with mock.patch.object(Service, "pools",
                               side_effect=AssertionError("solver must not run")):
            result = self.svc.plan(self.svc.build_targets())
        self.assertEqual(len(result.meals), 3)
        self.assertTrue(all(m.locked for m in result.meals))
        self.assertAlmostEqual(result.totals["energyKcal"], 1800.0)
        self.assertTrue(result.feasible)

    def test_g3_day_over_target_still_reported(self):
        for name in MEALS:
            self.svc.record_meal(name, [entry(f"{name}-1", f"合成{name}", 700, 30)],
                                 day="2026-10-19", amount_actual=20.0)
        result = self.svc.plan(self.svc.build_targets())
        self.assertAlmostEqual(result.totals["energyKcal"], 2100.0)
        self.assertFalse(result.feasible)
        kcal = [v for v in result.violations if v["constraint"] == "kcal"]
        self.assertEqual(len(kcal), 1)
        self.assertAlmostEqual(kcal[0]["gap"], 200.0)

    def test_g4_day_under_target_still_reported(self):
        for name in MEALS:
            self.svc.record_meal(name, [entry(f"{name}-1", f"合成{name}", 400, 20)],
                                 day="2026-10-19", amount_actual=20.0)
        result = self.svc.plan(self.svc.build_targets())
        self.assertAlmostEqual(result.totals["energyKcal"], 1200.0)
        self.assertFalse(result.feasible)
        kcal = [v for v in result.violations if v["constraint"] == "kcal"]
        self.assertAlmostEqual(kcal[0]["gap"], -300.0)

    def test_g5_deleting_one_record_reopens_only_that_meal(self):
        ids = {}
        for name in MEALS:
            rec = self.svc.record_meal(name, [entry(f"{name}-1", f"合成{name}", 600, 30)],
                                       day="2026-10-19", amount_actual=20.0)
            ids[name] = rec["id"]
        self.svc.store.delete_intake(ids["dinner"])
        result = self.svc.plan(self.svc.build_targets())
        open_meals = [m.meal for m in result.meals if not m.locked]
        self.assertEqual(open_meals, ["dinner"])
        self.assertEqual([m.meal for m in result.meals if m.locked], ["breakfast", "lunch"])


# ---------------------------------------------------- 2. the day's money, split
class DayMoneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name, {"kcal_min": 1700, "kcal_max": 1900,
                                               "protein_min": 80, "fat_max": 90,
                                               "carb_max": 260, "budget": 100})
        self.targets = self.svc.build_targets()

    def tearDown(self):
        self.tmp.cleanup()

    def _meals(self, booked=60.0):
        return [
            meal_plan("breakfast", "BF1", 600, 30, 20, locked=True, amount=booked),
            meal_plan("lunch", "LU1", 600, 30, 22),
            meal_plan("dinner", "DI1", 600, 30, 22),
        ]

    def test_g6_booked_plus_quotes_is_the_day_total(self):
        quotes = {"lunch": {"status": "ok", "amount": 25.0},
                  "dinner": {"status": "ok", "amount": 25.0}}
        payload = self.svc.compose_payload(self._meals(), self.targets,
                                           quotes=quotes, quote_attempted=True)
        self.assertEqual(payload["budget_status"], "quoted")
        self.assertAlmostEqual(payload["booked_total"], 60.0)
        self.assertAlmostEqual(payload["quote_total"], 50.0)
        self.assertAlmostEqual(payload["cost"], 110.0)
        self.assertFalse(payload["feasible"])
        budget = [v for v in payload["violations"] if v["constraint"] == "budget"]
        self.assertEqual(len(budget), 1)
        self.assertAlmostEqual(budget[0]["gap"], 10.0)

    def test_g7_locked_amount_is_never_overwritten_by_a_quote(self):
        quotes = {"lunch": {"status": "ok", "amount": 25.0},
                  "dinner": {"status": "ok", "amount": 25.0}}
        payload = self.svc.compose_payload(self._meals(), self.targets,
                                           quotes=quotes, quote_attempted=True)
        breakfast = payload["meals"][0]
        self.assertAlmostEqual(breakfast["amount_actual"], 60.0)
        self.assertEqual(breakfast["amount_source"], "manual")
        self.assertIsNone(breakfast["quote"])
        self.assertIsNone(payload["meals"][1]["amount_actual"])

    def test_g8_unknown_booked_amount_marks_budget_not_evaluated(self):
        quotes = {"lunch": {"status": "ok", "amount": 25.0},
                  "dinner": {"status": "ok", "amount": 25.0}}
        payload = self.svc.compose_payload(self._meals(booked=None), self.targets,
                                           quotes=quotes, quote_attempted=True)
        self.assertEqual(payload["budget_status"], "not_evaluated")
        self.assertIsNone(payload["cost"])
        self.assertFalse(payload["feasible"])
        self.assertTrue(any("记账金额" in (v.get("detail") or "")
                            for v in payload["violations"]))

    def test_g9_failed_quote_marks_budget_not_evaluated(self):
        quotes = {"lunch": {"status": "ok", "amount": 25.0},
                  "dinner": {"status": "failed", "amount": None, "message": "售罄"}}
        payload = self.svc.compose_payload(self._meals(), self.targets,
                                           quotes=quotes, quote_attempted=True)
        self.assertEqual(payload["budget_status"], "not_evaluated")
        self.assertIsNone(payload["cost"])
        self.assertFalse(payload["feasible"])

    def test_g10_replan_keeps_booked_money_and_only_solves_open_meals(self):
        self.svc.record_meal("breakfast", [entry("BF1", "合成早餐", 600, 30, price=20)],
                             day="2026-10-19", amount_actual=60.0)
        payload = self.svc.compose_payload(
            self.svc.plan(self.targets).meals, self.targets)
        self.assertAlmostEqual(payload["booked_total"], 60.0)
        # no quotes requested -> reference basis, booked money included
        self.assertEqual(payload["budget_status"], "reference")
        self.assertAlmostEqual(payload["cost"],
                               round(60.0 + (payload["open_reference_total"] or 0.0), 2))

    def test_g11_apply_quotes_agrees_with_day_evaluation(self):
        result = planner_mod.PlanResult(
            feasible=True, meals=[], totals={"energyKcal": 1800.0, "protein": 90.0,
                                             "fat": 60.0, "carbohydrate": 180.0,
                                             "sodium": 1200.0, "calcium": 300.0},
            unknowns=[], is_estimate=False, cost=66.0, violations=[], score=0.0)
        quotes = {"lunch": {"status": "ok", "amount": 25.0},
                  "dinner": {"status": "ok", "amount": 25.0}}
        rolled = Service.apply_quotes(result, self.targets, quotes, booked_total=60.0)
        self.assertAlmostEqual(rolled["effective_cost"], 110.0)
        self.assertFalse(rolled["feasible"])
        rolled_unknown = Service.apply_quotes(result, self.targets, quotes, booked_total=None)
        self.assertEqual(rolled_unknown["status"], "not_evaluated")
        self.assertIsNone(rolled_unknown["effective_cost"])

    def test_g23_quantity_edit_without_amount_invalidates_the_amount(self):
        rec = self.svc.record_meal("lunch", [entry("LU1", "合成午餐", 500, 25, price=22)],
                                   day="2026-10-19", amount_actual=30.5,
                                   amount_source="quoted")
        self.assertEqual(rec["amount_source"], "quoted")
        updated = self.svc.update_record(rec["id"], {
            "items": [{**entry("LU1", "合成午餐", 500, 25, price=22), "quantity": 2}],
        })
        self.assertIsNone(updated["amount_actual"])
        self.assertEqual(updated["amount_source"], "unknown")
        self.assertAlmostEqual(updated["totals"]["energyKcal"], 1000.0)

    def test_g24_quote_amount_is_recorded_as_adopted(self):
        rec = self.svc.record_meal(
            "lunch", [entry("LU1", "合成午餐", 500, 25, price=22)],
            day="2026-10-19", quote={"status": "ok", "amount": 22.0})
        self.assertAlmostEqual(rec["amount_actual"], 22.0)
        self.assertEqual(rec["amount_source"], "quoted")
        manual = self.svc.record_meal(
            "dinner", [entry("DI1", "合成晚餐", 600, 30, price=25)],
            day="2026-10-19", amount_actual=27.0,
            quote={"status": "ok", "amount": 25.0})
        self.assertEqual(manual["amount_source"], "manual")


# ------------------------------------------------------ 3. baseline is contextual
class BaselineContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name)
        self.svc.record_meal("lunch", [entry("H1", "历史午餐", 500, 25)],
                             day="2026-10-19", amount_actual=18.0)
        self.result = self.svc.plan(self.svc.build_targets())
        self.svc.set_baseline(self.result)
        self.signature = self.result.as_dict()["signature"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_g12_valid_baseline_is_accepted(self):
        self.assertIsNotNone(self.svc.get_baseline())
        payload = self.svc.recompute()
        self.assertNotIn("error", payload)
        self.assertEqual(payload["cache"]["store_code"], "1440054")

    def test_g13_store_change_retires_disk_baseline(self):
        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        # the file is still there (evidence), but it must not be served
        self.assertTrue((Path(self.tmp.name) / "baseline.json").exists())
        self.assertIsNone(self.svc.get_baseline())
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")
        self.assertIn("门店", out.get("error", ""))

    def test_g14_time_change_also_retires_baseline(self):
        self.svc.store.save_state({"meal_times": {"breakfast": "09:00",
                                                 "lunch": "12:15",
                                                 "dinner": "18:30"}})
        self.svc.invalidate()
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")

    def test_g15_history_survives_baseline_retirement(self):
        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        self.svc.recompute()
        records = self.svc.store.list_intake("2026-10-19")
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["amount_actual"], 18.0)

    def test_g25_date_change_retires_baseline_and_quote(self):
        self.svc.set_working([meal_plan("lunch", "LU1", 500, 25, 22)])
        signature = self.svc.working_signature()
        self.svc.store.save_state({"date": "2026-10-21"})
        self.svc.invalidate()
        out = self.svc.recompute()
        self.assertEqual(out.get("kind"), "stale_baseline")
        quoted, status = self.svc.quote_working(signature)
        self.assertEqual(status, 409)
        self.assertEqual(quoted.get("kind"), "stale_context")


# ------------------------------------------------- 4. quote the current plan only
class QuoteCurrentPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = new_service(self.tmp.name, {"kcal_min": 1500, "kcal_max": 2400,
                                               "protein_min": 60, "fat_max": 120,
                                               "carb_max": 300, "budget": 100})
        self.meals = [
            meal_plan("breakfast", "BF1", 600, 30, 20, qty=2,
                      modification=[{"code": "100136", "key": "0-1", "quantity": 1}]),
            meal_plan("lunch", "LU1", 500, 25, 22),
        ]
        self.svc.set_working(self.meals)
        self.signature = self.svc.working_signature()

    def tearDown(self):
        self.tmp.cleanup()

    def test_g16_quote_keeps_quantity_and_customisation(self):
        with mock.patch.object(planner_mod, "plan_open",
                               side_effect=AssertionError("quote must not solve")), \
             mock.patch.object(Service, "plan",
                               side_effect=AssertionError("quote must not solve")):
            payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 200)
        sent = self.svc.provider.price_calls[0]["items"]
        self.assertEqual(sent[0]["quantity"], 2)
        self.assertEqual(sent[0]["modification"]["values"],
                         [{"code": "100136", "key": "0-1", "quantity": 1}])
        self.assertEqual(payload["meals"][0]["items"][0]["quantity"], 2)
        self.assertEqual(payload["meals"][0]["items"][0]["modification"],
                         [{"code": "100136", "key": "0-1", "quantity": 1}])
        self.assertEqual(payload["budget_status"], "quoted")

    def test_g17_quote_has_a_server_timestamp(self):
        payload, _ = self.svc.quote_working(self.signature)
        quote = payload["quotes"]["breakfast"]
        self.assertTrue(quote.get("queried_at"))
        self.assertNotIn("quoted_at", quote)

    def test_g18_wrong_signature_is_rejected(self):
        payload, status = self.svc.quote_working("deadbeefdeadbeef")
        self.assertEqual(status, 409)
        self.assertEqual(payload["kind"], "stale_signature")
        self.assertEqual(self.svc.provider.price_calls, [])

    def test_g19_missing_signature_is_rejected(self):
        payload, status = self.svc.quote_working("")
        self.assertEqual(status, 400)
        self.assertEqual(payload["kind"], "missing_signature")

    def test_g20_context_change_blocks_quote(self):
        self.svc.store.save_state({"store": {"code": "1450713"}})
        self.svc.invalidate()
        payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 409)
        self.assertEqual(payload["kind"], "stale_context")
        self.assertEqual(self.svc.provider.price_calls, [])

    def test_g21_recompute_refreshes_the_quotable_signature(self):
        base = planner_mod.PlanResult(
            feasible=True, meals=self.meals, totals={}, unknowns=[],
            is_estimate=False, cost=62.0, violations=[], score=0.0)
        self.svc.set_baseline(base)
        out = self.svc.recompute({"breakfast": {"BF1": 3}}, {}, "official_only")
        self.assertNotIn("error", out)
        self.assertEqual(self.svc.working_signature(), out["signature"])
        payload, status = self.svc.quote_working(out["signature"])
        self.assertEqual(status, 200)
        sent = next(call for call in self.svc.provider.price_calls
                    if call["items"][0]["productCode"] == "BF1")
        self.assertEqual(sent["items"][0]["quantity"], 3)

    def test_g22_quote_failure_is_not_a_zero_yuan_day(self):
        self.svc.provider.fail = True
        payload, status = self.svc.quote_working(self.signature)
        self.assertEqual(status, 200)
        self.assertEqual(payload["budget_status"], "not_evaluated")
        self.assertIsNone(payload["cost"])
        self.assertFalse(payload["feasible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
