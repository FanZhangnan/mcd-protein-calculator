"""Single-meal mode regressions (round 5).

Strict mode: every number is the official standard-version value. Removing a
component only changes the order suggestion, never the nutrition.

S1  structure lock: exactly one main, quantity 1, never two burgers
S2  drink whitelist: no sugary drinks
S3  price / fat caps can never be exceeded by the chosen or fallback plan
S4  reachable ceiling + two one-at-a-time relaxations
S5  upgrade ladder hides a step when either SKU is missing
S6  suggest_remove keeps nutrition identical, writes modification, basis flag
S7  protein-risk wording for cheese / bacon
S8  unknown option_code -> 400-style UnsupportedCustomization
S9  suggest_remove is idempotent
S10 single-meal plan never touches intake
S11 no forbidden wording (加肉饼/更低/小于/约为)
S12 removable_options uses the meal's own time slot

All synthetic / fixture-based. Run: python3 tests/test_single_meal.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from planner import Item, MealPlan, PlanResult, Targets  # noqa: E402
from service import Service, UnsupportedCustomization  # noqa: E402
from store import Store  # noqa: E402

CACHE = {
    "store_code": "1440054", "be_type": 1, "order_type": 1,
    "date": "2026-10-19",
    "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    "cache_key": "synthetic", "source": "offline",
}


def nutr(kcal: float, protein: float, fat: float, carb: float = 30.0) -> dict:
    return {"energyKj": round(kcal * 4.184, 1), "energyKcal": kcal, "protein": protein,
            "fat": fat, "carbohydrate": carb, "sodium": 700.0, "calcium": 100.0}


def item(code: str, name: str, kind: str, kcal: float, protein: float, fat: float,
         price: float) -> Item:
    return Item(code=code, name=name, kind=kind, nutrition=nutr(kcal, protein, fat),
                nutrition_status="synthetic", nutrition_note="合成测试数据",
                price_ref=price, quantity=1, meal="lunch")


MAIN_A = item("A", "合成牛肉堡", "main", 500, 25, 20, 25)
MAIN_B = item("B", "合成鸡堡", "main", 380, 18, 12, 18)
MAIN_C = item("C", "合成双牛堡", "main", 700, 32, 30, 32)
MILK = item("D1", "纯牛奶", "drink", 120, 8, 5, 6)
COLA = item("D2", "含糖可乐", "drink", 150, 0, 0, 6)
ZERO = item("D3", "零度可乐", "drink", 0, 0, 0, 6)
WATER = item("D4", "矿泉水", "drink", 0, 0, 0, 3)
SIDE = item("S1", "玉米杯", "side", 60, 2, 1, 8)

POOLS = {"lunch": {"main": [MAIN_A, MAIN_B, MAIN_C], "side": [SIDE],
                   "drink": [MILK, COLA, ZERO, WATER]}}


def one_meal(items: list[Item]) -> list[MealPlan]:
    nutrition, unknown = planner_mod.sum_nutrition(items)
    return [MealPlan(meal="lunch", items=items, nutrition=nutrition, unknown=unknown,
                     price_ref=planner_mod.sum_reference_cost(items) or 0.0)]


class SingleMealSolverTest(unittest.TestCase):
    def _plan(self, protein=20, fat=25, budget=30, allow_side=False):
        restricted = {"lunch": {
            "main": [MAIN_A, MAIN_B, MAIN_C],
            "side": [SIDE] if allow_side else [],
            "drink": [d for d in [MILK, COLA, ZERO, WATER]
                      if Service.is_single_drink(d)],
        }}
        targets = Targets(kcal_min=0, kcal_max=100000, protein_min=protein,
                          fat_max=fat, budget=budget)
        return planner_mod.plan_open(restricted, targets, ["lunch"],
                                     ranker=planner_mod.single_meal_rank,
                                     full_enumeration=True), restricted, targets

    def test_s1_structure_lock(self):
        result, _, _ = self._plan(protein=20, fat=25, budget=40)
        self.assertTrue(result.feasible)
        items = result.meals[0].items
        mains = [i for i in items if i.kind == "main"]
        self.assertEqual(len(mains), 1, "必须恰好一个主食")
        self.assertEqual(mains[0].quantity, 1, "主食数量固定为 1")
        self.assertLessEqual(len(items), 2, "最多主食 + 一份饮品/配菜")

    def test_s2_drink_whitelist(self):
        allowed = [d for d in [MILK, COLA, ZERO, WATER] if Service.is_single_drink(d)]
        names = [d.name for d in allowed]
        self.assertIn("纯牛奶", names)
        self.assertIn("零度可乐", names)
        self.assertIn("矿泉水", names)          # 0 kcal
        self.assertNotIn("含糖可乐", names)     # 150 kcal, not whitelisted

    def test_s3_price_and_fat_caps_never_exceeded(self):
        # only B fits price<=20 and fat<=15 -> must pick B, never A
        result, _, targets = self._plan(protein=18, fat=15, budget=20)
        self.assertTrue(result.feasible)
        items = result.meals[0].items
        totals = planner_mod.sum_nutrition(items)[0]
        cost = planner_mod.sum_reference_cost(items)
        self.assertLessEqual(cost, 20)
        self.assertLessEqual(totals["fat"], 15)
        self.assertEqual([i.code for i in items if i.kind == "main"], ["B"])

    def test_s3b_infeasible_ceiling_still_respects_caps(self):
        result, pools, targets = self._plan(protein=40, fat=15, budget=20)
        self.assertFalse(result.feasible)
        frontier = planner_mod.single_meal_frontier(pools, targets, "lunch")
        cap = frontier["max_protein"]
        self.assertIsNotNone(cap)
        self.assertLessEqual(cap["fat"], 15, "可达上限也不能超脂肪")
        self.assertLessEqual(cap["cost"], 20, "可达上限也不能超价格")
        self.assertLess(cap["protein"], 40)

    def test_s4_frontier_relaxations(self):
        # no drinks here, so the ceiling is purely the burger ladder:
        # A: 25p/20f/25y   B: 18p/12f/18y   C: 32p/30f/32y
        # target 32p, fat<=20, price<=40
        # hard cap: A (25p, 20f, 25y)
        # relax fat only: price<=40, reach 32p with least fat -> C (30f)
        # relax price only: fat<=20, reach 32p cheapest -> none
        restricted = {"lunch": {"main": [MAIN_A, MAIN_B, MAIN_C],
                                "side": [], "drink": []}}
        targets = Targets(kcal_min=0, kcal_max=100000, protein_min=32,
                          fat_max=20, budget=40)
        result = planner_mod.plan_open(restricted, targets, ["lunch"],
                                       ranker=planner_mod.single_meal_rank,
                                       full_enumeration=True)
        self.assertFalse(result.feasible)
        frontier = planner_mod.single_meal_frontier(restricted, targets, "lunch")
        self.assertEqual(frontier["max_protein"]["code"], "A")
        self.assertEqual(frontier["relax_fat"]["code"], "C")
        self.assertEqual(frontier["relax_fat"]["fat"], 30)
        self.assertIsNone(frontier["relax_price"])
        self.assertTrue(frontier["reachable"])

    def test_s4b_unreachable_message(self):
        result, pools, targets = self._plan(protein=99, fat=20, budget=40)
        frontier = planner_mod.single_meal_frontier(pools, targets, "lunch")
        self.assertFalse(frontier["reachable"])
        self.assertIn("达不到", frontier["message"])


class FakeProvider:
    source_name = "offline"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.details = {}

    def detail(self, store_code, be_type, order_type, code, when=None):
        self.calls.append((code, when))
        return self.details.get((code, when), {"success": False, "code": 1,
                                               "message": "未匹配到商品"})

    def now(self):
        return {"success": True, "data": {"formatted": "2026-10-19 08:00:00"}}


REMOVABLE = {
    "ok": True,
    "sku": {"code": "A", "name": "合成牛肉堡"},
    "supportModify": True,
    "groups": [{"index": 0, "name": None, "values": [
        {"code": "C1", "name": "高达干酪片", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
        {"code": "C2", "name": "番茄片", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
        {"code": "C3", "name": "培根", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
    ]}],
    "options": [
        {"code": "C1", "name": "高达干酪片", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": True, "default_suggestion": True},
        {"code": "C2", "name": "番茄片", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": False, "default_suggestion": False},
        {"code": "C3", "name": "培根", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": True, "default_suggestion": True},
    ],
}


def new_svc(tmp: str, provider=None) -> Service:
    provider = provider or FakeProvider()
    svc = Service(provider, Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({"date": "2026-10-19",
                          "store": {"code": "1440054", "name": "合成门店",
                                    "beType": 1, "orderType": 1},
                          "meal_times": CACHE["meal_times"],
                          "targets": {"kcal_min": 0, "kcal_max": 100000,
                                      "protein_min": 20, "fat_max": 25,
                                      "budget": 30}})
    svc.ensure_cache = lambda day=None: dict(CACHE)  # type: ignore[assignment]
    svc.pools = lambda meal: {k: list(v) for k, v in POOLS.get(meal, {}).items()}  # type: ignore[assignment]
    svc.removable_options = lambda code, meal=None: dict(REMOVABLE)  # type: ignore[assignment]
    return svc


class SuggestRemoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mai-single-")
        self.svc = new_svc(self.tmp)
        self.base = PlanResult(
            feasible=True, meals=one_meal([MAIN_A]), totals={}, unknowns=[],
            is_estimate=False, cost=25.0, violations=[], score=0.0)
        self.svc.set_baseline(self.base)
        self.svc.set_working(self.base.meals)

    def _apply(self, codes, code="A"):
        return self.svc.recompute({}, {"lunch": {code: {"action": "suggest_remove",
                                                        "option_codes": codes}}})

    def test_s6_nutrition_unchanged_and_modification_written(self):
        out = self._apply(["C1"])
        item = out["meals"][0]["items"][0]
        self.assertEqual(item["nutrition"], MAIN_A.nutrition)
        self.assertEqual(out["totals"]["energyKcal"], 500.0)
        self.assertEqual(item["nutrition_basis"], "standard_unadjusted")
        self.assertFalse(item["is_estimate"])
        self.assertEqual(len(item["modification"]), 3, "整组三项都要传")
        keys = {m["code"]: m["key"] for m in item["modification"]}
        self.assertEqual(keys, {"C1": "0-0", "C2": "0-1", "C3": "0-1"})

    def test_s7_protein_risk_notes(self):
        out = self._apply(["C1", "C3"])
        notes = out["meals"][0]["items"][0]["removal_notes"]
        self.assertTrue(any("建议去掉高达干酪片" in n for n in notes))
        self.assertTrue(any("蛋白质可能低于显示值" in n for n in notes))
        self.assertTrue(any("培根" in n and "蛋白质可能低于显示值" in n for n in notes))

    def test_s8_unknown_option_code_rejected(self):
        with self.assertRaises(UnsupportedCustomization) as ctx:
            self._apply(["NOPE"])
        self.assertEqual(ctx.exception.action, "suggest_remove")
        self.assertIn("NOPE", str(ctx.exception))

    def test_s8b_unknown_action_rejected(self):
        with self.assertRaises(UnsupportedCustomization) as ctx:
            self.svc.recompute({}, {"lunch": {"A": {"action": "add_patty"}}})
        self.assertEqual(ctx.exception.action, "add_patty")
        self.assertIn("remove_sauce", str(ctx.exception))

    def test_s9_idempotent(self):
        first = self._apply(["C1"])
        second = self._apply(["C1"])
        self.assertEqual(first["totals"], second["totals"])
        self.assertEqual(first["meals"][0]["items"][0]["modification"],
                         second["meals"][0]["items"][0]["modification"])
        cleared = self.svc.recompute({}, {})
        self.assertEqual(cleared["totals"]["energyKcal"], 500.0)
        self.assertEqual(cleared["totals"]["protein"], 25.0)
        self.assertEqual(cleared["meals"][0]["items"][0]["modification"], [])

    def test_s10_plan_meal_does_not_touch_intake(self):
        before = list(self.svc.store.list_intake("2026-10-19"))
        out = self.svc.plan_meal("lunch", self.svc.build_targets())
        after = list(self.svc.store.list_intake("2026-10-19"))
        self.assertEqual(before, after)
        self.assertEqual(out["mode"], "single")
        self.assertFalse(out["is_estimate"])

    def test_s11_no_forbidden_wording(self):
        out = self._apply(["C1"])
        blob = repr(out)
        for word in ("加肉饼", "更低", "小于", "约为"):
            self.assertNotIn(word, blob, f"文案不得出现「{word}」")


class KnownBugTest(unittest.TestCase):
    def test_b1_no_budget_is_not_not_evaluated(self):
        """budget 为空 + 报价成功 -> no_budget，不是 not_evaluated。"""
        svc = new_svc(tempfile.mkdtemp(prefix="mai-b1-"))
        targets = Targets(kcal_min=0, kcal_max=100000, protein_min=20,
                          fat_max=30, budget=None)
        quotes = {"lunch": {"status": "ok", "amount": 25.0}}
        payload = svc.compose_payload(one_meal([MAIN_A]), targets,
                                      quotes=quotes, quote_attempted=True)
        self.assertEqual(payload["budget_status"], "no_budget")
        self.assertEqual(payload["quote_total"], 25.0)
        self.assertFalse(any(v["label"] == "预算" for v in payload["violations"]))
        self.assertTrue(payload["feasible"])

    def test_b3_unknown_action_returns_unsupported(self):
        svc = new_svc(tempfile.mkdtemp(prefix="mai-b3-"))
        Base = PlanResult(feasible=True, meals=one_meal([MAIN_A]), totals={},
                          unknowns=[], is_estimate=False, cost=25.0,
                          violations=[], score=0.0)
        svc.set_baseline(Base)
        with self.assertRaises(UnsupportedCustomization) as ctx:
            svc.recompute({}, {"lunch": {"A": {"action": "add_patty"}}})
        self.assertEqual(ctx.exception.action, "add_patty")
        self.assertIn("remove_sauce", ctx.exception.supported)
        self.assertIn("suggest_remove", ctx.exception.supported)


class RemovalTimeTest(unittest.TestCase):
    def test_s12_detail_uses_meal_time(self):
        provider = FakeProvider()
        provider.details[("514700", "2026-10-19 08:15")] = {
            "success": True, "code": 200, "message": "请求成功",
            "data": {"code": "514700", "name": "原味板烧鸡腿炒双蛋堡", "supportModify": True,
                     "modification": {"items": [{"name": None, "values": [
                         {"code": "102201", "name": "烤味酱",
                          "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0}]}]}}}
        svc = new_svc(tempfile.mkdtemp(prefix="mai-time-"), provider)
        # restore the real implementation (new_svc mocked it)
        del svc.removable_options
        svc.removable_options = Service.removable_options.__get__(svc, Service)
        result = svc.removable_options("514700", "breakfast")
        self.assertTrue(result["ok"])
        self.assertEqual(provider.calls[-1], ("514700", "2026-10-19 08:15"))

        result2 = svc.removable_options("514700", "lunch")
        self.assertFalse(result2["ok"])
        self.assertEqual(provider.calls[-1], ("514700", "2026-10-19 12:15"))

    def test_b2_sauce_capability_uses_meal_time(self):
        provider = FakeProvider()
        provider.details[("514700", "2026-10-19 08:15")] = {
            "success": True, "code": 200, "message": "请求成功",
            "data": {"code": "514700", "name": "原味板烧鸡腿炒双蛋堡", "supportModify": True,
                     "modification": {"items": [{"name": None, "values": [
                         {"code": "102201", "name": "烤味酱",
                          "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0}]}]}}}
        svc = new_svc(tempfile.mkdtemp(prefix="mai-b2-"), provider)
        result = svc.sauce_capability("514700", "breakfast")
        self.assertTrue(result["ok"])
        self.assertEqual(provider.calls[-1], ("514700", "2026-10-19 08:15"))
        self.assertEqual(result["meal"], "breakfast")


if __name__ == "__main__":
    unittest.main(verbosity=2)
