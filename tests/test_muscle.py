"""Muscle-building calculator regressions (T1–T10).

T1  1..N mains, the same SKU twice merges into quantity 2
T2  the price cap is never broken — including the fallback card
T3  a user-set fat cap also holds on the fallback card
T4  no combo under the caps -> no product card at all
T5  among feasible combos, cheapest wins
T6  protein per yuan = protein / price, one decimal
T7  protein_risk: any name containing 酱 is False
T8  no single-patty -> single-patty ladder step; <5 g adds no per-yuan claim
T9  landscape only contains mains with official nutrition
T10 page and API copy contain none of 减脂/瘦/加肉饼/全天/三餐
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import planner as planner_mod  # noqa: E402
from planner import Item, PlanResult, Targets  # noqa: E402
from service import Service  # noqa: E402
from helpers import FakeProvider, new_svc, nutr, one_meal  # noqa: E402


def main_item(code, name, protein, fat, price, kcal=450, kind="main"):
    return Item(code, name, kind, nutr(kcal, protein, fat), "synthetic",
                "合成测试数据", price, 1, meal="lunch")


def pools_of(*mains, sides=(), drinks=()):
    return {"lunch": {"main": list(mains), "side": list(sides), "drink": list(drinks)}}


class StructureTest(unittest.TestCase):
    def test_t1_two_mains_allowed_and_same_sku_merges(self):
        a = main_item("A", "合成牛肉堡", 25, 20, 20)
        b = main_item("B", "合成鸡堡", 26, 18, 40)
        targets = Targets(protein_min=50, budget=42, fat_max=60)
        result = planner_mod.plan_open(pools_of(a, b), targets, ["lunch"], max_mains=2)
        self.assertTrue(result.feasible)
        items = result.meals[0].items
        mains = [i for i in items if i.kind == "main"]
        self.assertEqual(len(mains), 1, "同一款两个应合并为一行")
        self.assertEqual(mains[0].code, "A")
        self.assertEqual(mains[0].quantity, 2)
        self.assertAlmostEqual(result.totals["protein"], 50.0)

    def test_t1b_n_equals_one_never_two_mains(self):
        a = main_item("A", "合成牛肉堡", 25, 20, 20)
        b = main_item("B", "合成鸡堡", 26, 18, 40)
        targets = Targets(protein_min=40, budget=42)
        result = planner_mod.plan_open(pools_of(a, b), targets, ["lunch"], max_mains=1)
        self.assertFalse(result.feasible)
        self.assertLessEqual(len([i for i in result.meals[0].items if i.kind == "main"]), 1)


class CapsTest(unittest.TestCase):
    def test_t2_fallback_never_breaks_price_cap(self):
        cheap = main_item("A", "合成小鸡堡", 10, 10, 10)
        pricey = main_item("B", "合成大牛堡", 40, 20, 50)
        targets = Targets(protein_min=45, budget=30, fat_max=50)
        result = planner_mod.plan_open(pools_of(cheap, pricey), targets, ["lunch"],
                                       max_mains=2)
        self.assertFalse(result.feasible)
        self.assertTrue(result.meals)
        cost = planner_mod.sum_reference_cost(result.meals[0].items)
        self.assertLessEqual(cost, 30)
        self.assertNotIn("B", [i.code for i in result.meals[0].items])

    def test_t3_fallback_respects_fat_cap(self):
        lean = main_item("A", "合成低脂堡", 20, 15, 20)
        fatty = main_item("B", "合成高脂堡", 30, 40, 25)
        targets = Targets(protein_min=40, budget=30, fat_max=20)
        result = planner_mod.plan_open(pools_of(lean, fatty), targets, ["lunch"],
                                       max_mains=2)
        self.assertFalse(result.feasible)
        items = result.meals[0].items
        self.assertLessEqual(planner_mod.sum_nutrition(items)[0]["fat"], 20)
        self.assertEqual([i.code for i in items], ["A"])

    def test_t4_no_capped_combo_returns_no_card(self):
        pricey = main_item("A", "合成大牛堡", 40, 20, 50)
        targets = Targets(protein_min=10, budget=5)
        result = planner_mod.plan_open(pools_of(pricey), targets, ["lunch"], max_mains=2)
        self.assertFalse(result.feasible)
        self.assertEqual(result.meals, [])
        self.assertIn("没有任何组合", result.note)


class OrderingTest(unittest.TestCase):
    def test_t5_cheapest_feasible_wins(self):
        a = main_item("A", "合成甲堡", 20, 10, 20)
        b = main_item("B", "合成乙堡", 22, 10, 18)
        targets = Targets(protein_min=20, budget=30)
        result = planner_mod.plan_open(pools_of(a, b), targets, ["lunch"], max_mains=2)
        self.assertTrue(result.feasible)
        self.assertEqual([i.code for i in result.meals[0].items], ["B"])

    def test_t6_protein_per_yuan_one_decimal(self):
        svc = new_svc(tempfile.mkdtemp(prefix="mai-t6-"))
        meal = planner_mod.MealPlan(
            meal="lunch",
            items=[main_item("A", "合成甲堡", 28, 10, 24)],
            nutrition=planner_mod.sum_nutrition([main_item("A", "合成甲堡", 28, 10, 24)])[0],
            price_ref=24.0,
        )
        payload = svc.compose_payload(meal, Targets(protein_min=20, budget=30))
        self.assertEqual(payload["protein_per_yuan"], 1.2)
        self.assertEqual(len(str(payload["protein_per_yuan"]).split(".")[1]), 1)


class RemovalRuleTest(unittest.TestCase):
    def test_t7_sauce_is_never_protein_risk(self):
        payload = {"success": True, "code": 200, "message": "请求成功",
                   "data": {"code": "X", "name": "合成堡", "supportModify": True,
                            "modification": {"items": [{"name": None, "values": [
                                {"code": "C1", "name": "醇香芝士风味酱",
                                 "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
                                {"code": "C2", "name": "高达干酪片",
                                 "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
                                {"code": "C3", "name": "培根",
                                 "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
                            ]}]}}}
        provider = FakeProvider()
        provider.details[("X", "2026-10-19 12:15")] = payload
        svc = new_svc(tempfile.mkdtemp(prefix="mai-t7-"), provider)
        result = svc.removable_options("X", "lunch")
        by_name = {o["name"]: o for o in result["options"]}
        self.assertFalse(by_name["醇香芝士风味酱"]["protein_risk"])
        self.assertTrue(by_name["高达干酪片"]["protein_risk"])
        self.assertTrue(by_name["培根"]["protein_risk"])
        self.assertTrue(by_name["高达干酪片"]["muscle_warning"])
        self.assertIn("增肌时不建议去掉", by_name["高达干酪片"]["muscle_note"])


class LadderTest(unittest.TestCase):
    def test_t8_ladder_has_no_single_to_single_step(self):
        raw = json.loads((ROOT / "data" / "upgrade_ladder.json").read_text("utf-8"))
        pairs = {(str(p["from"]), str(p["to"])) for p in raw["pairs"]}
        # 芝士安格斯 -> 培根安格斯 (加培根，不是加肉) and 猪柳 -> 猪柳蛋 (加蛋)
        self.assertNotIn(("511782", "511781"), pairs)
        self.assertNotIn(("5024", "5022"), pairs)
        for pair in raw["pairs"]:
            self.assertTrue(pair.get("evidence"), pair)

    def test_t8b_small_delta_has_no_per_yuan_claim(self):
        svc = new_svc(tempfile.mkdtemp(prefix="mai-t8-"))
        svc._ladder = [{"from": "A", "to": "B", "label": "A → B", "evidence": "合成"}]
        a = main_item("A", "合成甲堡", 20, 10, 10)
        b = main_item("B", "合成乙堡", 23, 10, 12)
        upgrade = svc.upgrade_for("A", "lunch", {"main": [a, b]})
        self.assertEqual(upgrade["delta_protein"], 3.0)
        self.assertFalse(upgrade["show_conversion"])
        self.assertIsNone(upgrade["protein_per_extra_yuan"])


class LandscapeTest(unittest.TestCase):
    def test_t9_only_official_mains(self):
        official = main_item("A", "合成甲堡", 25, 10, 20)
        unknown = Item("B", "无营养堡", "main", None, "not_found",
                       "营养表中无此名称", 18, 1, meal="lunch")
        no_price = main_item("C", "无价堡", 20, 10, 20)
        no_price.price_ref = None
        svc = new_svc(tempfile.mkdtemp(prefix="mai-t9-"))
        svc.pools = lambda meal: pools_of(official, unknown, no_price)[meal]  # type: ignore[assignment]
        data = svc.landscape("lunch", 2)
        self.assertEqual([row["code"] for row in data["items"]], ["A"])
        self.assertEqual(data["basis"], "official_standard")


class CopyTest(unittest.TestCase):
    FORBIDDEN = ("减脂", "瘦", "加肉饼", "全天", "三餐")

    def test_t10_page_and_api_copy(self):
        for name in ("index.html", "app.js"):
            text = (ROOT / "web" / name).read_text("utf-8")
            for word in self.FORBIDDEN:
                self.assertNotIn(word, text, f"{name} 不得出现「{word}」")

        svc = new_svc(tempfile.mkdtemp(prefix="mai-t10-"))
        svc.pools = lambda meal: pools_of(main_item("A", "合成甲堡", 25, 10, 20))[meal]  # type: ignore[assignment]
        svc.removable_options = lambda code, meal=None: {"ok": False, "options": [],
                                                         "groups": [], "reason": "无"}  # type: ignore[assignment]
        payload = svc.plan_meal("lunch", Targets(protein_min=20, budget=30))
        blob = repr(payload)
        for word in self.FORBIDDEN:
            self.assertNotIn(word, blob, f"API 文案不得出现「{word}」")


if __name__ == "__main__":
    unittest.main(verbosity=2)
