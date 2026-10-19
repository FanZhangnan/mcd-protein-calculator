"""Quantity contract regressions (round 4).

Purchase counts are whole servings; intake counts may be fractional. These
tests pin the boundary so a UI hint can never be the only guard.

Q1-Q4  purchase ``0.5 / 1.5 / 2.5`` used to be rounded inside ``quote_meal``:
       nutrition said half a burger, ``calculate-price`` charged a whole one.
Q5-Q8  ``0`` was accepted by ``recompute`` and then turned back into ``1`` by
       ``_items_from_dicts``, so the returned signature went stale immediately.
Q9-Q11 purchase ``2`` must reach both nutrition and the MCP request unchanged.
Q12-Q15 eaten ``0.5`` stays ``0.5``; the booked amount is never halved.

Everything here is synthetic and offline (fixtures + a recording stub).
Run: python3 tests/test_quantity_contract.py
"""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import planner as planner_mod  # noqa: E402
import quantity as quantity_mod  # noqa: E402
from quantity import InvalidQuantity  # noqa: E402
from test_flows import entry, meal_plan, new_service  # noqa: E402

BAD_PURCHASE = [0.5, 1.5, 2.5, 0, -1, -0.5, "", "  ", "abc", None,
                float("nan"), float("inf"), float("-inf"), True, False, [], {}]


class PurchaseQuantityUnitTest(unittest.TestCase):
    def test_q1_positive_integers_accepted(self):
        for value in (1, 2, 3, "4", "5.0", 6.0):
            self.assertEqual(quantity_mod.purchase_quantity(value), int(float(value)))

    def test_q2_fractions_rejected_not_rounded(self):
        for value in (0.5, 1.5, 2.5, 0.1, 2.999):
            with self.assertRaises(InvalidQuantity, msg=str(value)):
                quantity_mod.purchase_quantity(value)

    def test_q3_zero_negative_and_junk_rejected(self):
        for value in (0, -1, -0.5, "", "  ", "abc", None, float("nan"),
                      float("inf"), float("-inf"), True, False, [], {}):
            with self.assertRaises(InvalidQuantity, msg=repr(value)):
                quantity_mod.purchase_quantity(value)

    def test_q4_intake_allows_fractions_but_not_zero(self):
        self.assertEqual(quantity_mod.intake_quantity(0.5), 0.5)
        self.assertEqual(quantity_mod.intake_quantity("0.5"), 0.5)
        self.assertEqual(quantity_mod.intake_quantity(1), 1.0)
        for value in (0, -0.5, "", "abc", None, float("nan"), float("inf"), True):
            with self.assertRaises(InvalidQuantity, msg=repr(value)):
                quantity_mod.intake_quantity(value)


class ServiceQuantityContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mai-qty-")
        self.svc = new_service(self.tmp)
        self.plan = self.svc.plan(self.svc.build_targets())
        self.svc.set_baseline(self.plan)
        self.svc.set_working(self.plan.meals)
        self.open_meal = next(m for m in self.plan.meals if not m.locked)
        self.meal = self.open_meal.meal
        self.item = self.open_meal.items[0]
        self.code = self.item.code
        self.unit_kcal = self.item.nutrition["energyKcal"]
        self.unit_price = self.item.price_ref
        self.sig_before = self.svc.working_signature()
        self.provider = self.svc.provider
        self.provider.price_calls.clear()

    # ---------------------------------------------------------- purchase edits
    def test_q5_recompute_rejects_fractional_purchase(self):
        for bad in (0.5, 1.5, 2.5):
            with self.assertRaises(InvalidQuantity, msg=str(bad)):
                self.svc.recompute({self.meal: {self.code: bad}}, {})
            self.assertEqual(self.provider.price_calls, [], "拒绝时不得发起价格查询")
            self.assertEqual(self.svc.working_signature(), self.sig_before,
                             "无效输入不得改变工作快照签名")

    def test_q6_recompute_rejects_zero_negative_and_junk(self):
        for bad in BAD_PURCHASE:
            with self.assertRaises(InvalidQuantity, msg=repr(bad)):
                self.svc.recompute({self.meal: {self.code: bad}}, {})
        self.assertEqual(self.provider.price_calls, [])
        self.assertEqual(self.svc.working_signature(), self.sig_before)

    def test_q7_rejected_edit_keeps_old_signature_quotable(self):
        ok = self.svc.recompute({self.meal: {self.code: 2}}, {})
        sig2 = ok["signature"]
        self.assertEqual(self.svc.working_signature(), sig2)

        with self.assertRaises(InvalidQuantity):
            self.svc.recompute({self.meal: {self.code: 0.5}}, {})
        self.assertEqual(self.svc.working_signature(), sig2,
                         "被拒后工作快照必须仍是最后一次有效方案")

        payload, status = self.svc.quote_working(sig2)
        self.assertEqual(status, 200)
        requested = payload["quotes"][self.meal]["requested_items"]
        sent = next(i for i in requested if i["productCode"] == self.code)
        self.assertEqual(sent["quantity"], 2)
        meal = next(m for m in payload["meals"] if m["meal"] == self.meal)
        self.assertEqual(next(i for i in meal["items"]
                              if str(i["code"]) == str(self.code))["quantity"], 2)

    def test_q8_quote_meal_refuses_fraction_without_calling_mcp(self):
        before = len(self.provider.price_calls)
        bad = meal_plan("半份测试堡", "SYN-HALF", 500, 20, 18.5, qty=0.5)
        with self.assertRaises(InvalidQuantity):
            self.svc.quote_meal(bad)
        self.assertEqual(len(self.provider.price_calls), before)

    # ------------------------------------------------- purchase 2 end to end
    def test_q9_purchase_two_reaches_nutrition_and_mcp_request(self):
        before_kcal = next(m for m in self.plan.meals
                           if m.meal == self.meal).nutrition["energyKcal"]
        out = self.svc.recompute({self.meal: {self.code: 2}}, {})
        meal = next(m for m in out["meals"] if m["meal"] == self.meal)
        item = next(i for i in meal["items"] if str(i["code"]) == str(self.code))
        self.assertEqual(item["quantity"], 2)
        self.assertAlmostEqual(meal["nutrition"]["energyKcal"],
                               before_kcal + self.unit_kcal, places=1)
        self.assertAlmostEqual(item["line_total_ref"], self.unit_price * 2, places=2)

        payload, status = self.svc.quote_working(out["signature"])
        self.assertEqual(status, 200)
        requested = payload["quotes"][self.meal]["requested_items"]
        sent = next(i for i in requested if i["productCode"] == self.code)
        self.assertEqual(sent["quantity"], 2)
        meal_after = next(m for m in payload["meals"] if m["meal"] == self.meal)
        item_after = next(i for i in meal_after["items"]
                          if str(i["code"]) == str(self.code))
        self.assertEqual(item_after["quantity"], 2, "报价不得改写数量")

    def test_q10_quote_keeps_modification_and_product(self):
        base = self.svc.recompute({self.meal: {self.code: 2}}, {})
        # attach a real POS modification and sign it
        meals = [self.svc._meal_from_dict(d) for d in base["meals"]]  # noqa: SLF001
        target = next(m for m in meals if m.meal == self.meal)
        target_item = next(i for i in target.items if str(i.code) == str(self.code))
        target_item.modification = [
            {"code": "100136", "key": "0-0", "quantity": 1}]
        sig = self.svc.plan_signature(self.svc.ensure_cache(), meals)
        self.svc.set_working(meals)

        payload, status = self.svc.quote_working(sig)
        self.assertEqual(status, 200)
        requested = payload["quotes"][self.meal]["requested_items"]
        sent = next(i for i in requested if i["productCode"] == self.code)
        self.assertEqual(sent["quantity"], 2)
        self.assertEqual(sent["modification"]["values"][0]["code"], "100136")
        meal_after = next(m for m in payload["meals"] if m["meal"] == self.meal)
        item_after = next(i for i in meal_after["items"]
                          if str(i["code"]) == str(self.code))
        self.assertEqual(item_after["quantity"], 2)
        self.assertEqual(item_after["modification"][0]["code"], "100136")

    def test_q11_signature_normalises_two_and_two_point_zero(self):
        a = meal_plan("甲", "A", 400, 20, 10, qty=2)
        b = meal_plan("甲", "A", 400, 20, 10, qty=2.0)
        cache = self.svc.ensure_cache()
        self.assertEqual(self.svc.plan_signature(cache, [a]),
                         self.svc.plan_signature(cache, [b]))

    # ------------------------------------------------------------ intake side
    def test_q12_half_serving_counts_half_nutrition(self):
        record = self.svc.record_meal(
            "breakfast", [entry("SYN-B", "合成早餐堡", 400, 20, 18.5, qty=0.5)],
            amount_actual=18.5)
        self.assertAlmostEqual(record["totals"]["energyKcal"], 200.0, places=1)
        self.assertAlmostEqual(record["totals"]["protein"], 10.0, places=1)
        self.assertAlmostEqual(record["amount_reference"], 9.25, places=2)
        self.assertEqual(record["items"][0]["quantity"], 0.5)

    def test_q13_half_serving_does_not_halve_booked_amount(self):
        record = self.svc.record_meal(
            "breakfast", [entry("SYN-B", "合成早餐堡", 400, 20, 18.5, qty=0.5)],
            amount_actual=18.5, amount_source="manual")
        self.assertEqual(record["amount_actual"], 18.5)
        self.assertEqual(record["amount_source"], "manual")
        # the reference price follows the half serving, the money paid does not
        self.assertAlmostEqual(record["amount_reference"], 9.25, places=2)

    def test_q14_intake_rejects_zero_negative_and_junk(self):
        for bad in (0, -1, "", "abc", None, float("nan"), float("inf"), True):
            item = entry("SYN-B", "合成早餐堡", 400, 20, 18.5, qty=1)
            item["quantity"] = bad
            with self.assertRaises(InvalidQuantity, msg=repr(bad)):
                self.svc.record_meal("breakfast", [item])
        self.assertEqual(self.svc.store.list_intake("2026-10-19"), [])

    def test_q15_intake_edit_keeps_fraction(self):
        record = self.svc.record_meal(
            "breakfast", [entry("SYN-B", "合成早餐堡", 400, 20, 18.5, qty=1)],
            amount_actual=18.5)
        patched = self.svc.update_record(
            record["id"], {"items": [entry("SYN-B", "合成早餐堡", 400, 20, 18.5, qty=0.5)]})
        self.assertAlmostEqual(patched["totals"]["energyKcal"], 200.0, places=1)
        self.assertEqual(patched["items"][0]["quantity"], 0.5)

    def test_q16_intake_never_triggers_a_price_call(self):
        before = len(self.provider.price_calls)
        self.svc.record_meal(
            "lunch", [entry("SYN-L", "合成午餐堡", 500, 25, 20, qty=0.5)],
            amount_actual=20.0)
        self.assertEqual(len(self.provider.price_calls), before,
                         "已吃记录不应重新报价")


if __name__ == "__main__":
    unittest.main(verbosity=2)
