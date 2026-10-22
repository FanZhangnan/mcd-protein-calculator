"""Combo (套餐) rules: detection, variants, prices, structure and end-to-end.

The combo catalog is *built* by ``scripts/build_combo_catalog.py``; these tests
never call MCP. They use (a) the real detail payloads kept as fixtures and
(b) the checked-in catalog that the build produced.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import catalog as catalog_mod  # noqa: E402
import combos as combos_mod  # noqa: E402
import planner as planner_mod  # noqa: E402
from helpers import FIX, new_service  # noqa: E402

LIVE_CATALOG = ROOT / "data/combo_catalog.json"
DEMO_CACHE = ROOT / "tests/fixtures/demo-cache.json"
CRISPY = "9900011126"


def nutrition_catalog() -> catalog_mod.NutritionCatalog:
    rows = catalog_mod.parse_nutrition_payload(
        json.loads((FIX / "nutrition.json").read_text("utf-8")))
    aliases = {str(c): e["name"]
               for c, e in json.loads((ROOT / "data/aliases.json").read_text("utf-8"))["codes"].items()}
    supplemental = json.loads(
        (ROOT / "data/combo_nutrition_map.json").read_text("utf-8"))["codes"]
    return catalog_mod.NutritionCatalog(rows, aliases, {}, supplemental)


def detail(code: str) -> dict:
    return json.loads((FIX / f"combo_{code}_detail.json").read_text("utf-8"))


def combo_input(code: str, name: str, price: float) -> dict:
    return {"code": code, "name": name, "menu_price": price,
            "rounds": combos_mod.rounds_of(detail(code))}


def combo_item(protein: float, price: float = 10.0, code: str = "C") -> planner_mod.Item:
    nutrition = {k: 0.0 for k in planner_mod.NUTRI_KEYS}
    nutrition["protein"] = protein
    return planner_mod.Item(code=code, name=f"套餐{code}", kind="combo", nutrition=nutrition,
                            nutrition_status="official", price_ref=price)


def main_item(protein: float, price: float = 10.0, code: str = "M") -> planner_mod.Item:
    nutrition = {k: 0.0 for k in planner_mod.NUTRI_KEYS}
    nutrition["protein"] = protein
    return planner_mod.Item(code=code, name=f"主食{code}", kind="main", nutrition=nutrition,
                            nutrition_status="official", price_ref=price)


def service_with_fixture_catalog(tmp: str):
    svc = new_service(tmp)
    shutil.copy2(FIX / "combo_catalog.json", Path(tmp) / "combo_catalog.json")
    svc.invalidate()
    return svc


def load_builder():
    path = ROOT / "scripts/build_combo_catalog.py"
    spec = importlib.util.spec_from_file_location("combo_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class DetectionTest(unittest.TestCase):
    def test_two_rounds_define_a_combo(self):
        payload = {"data": {"rounds": [
            {"id": 1, "name": "选择主食", "choices": [
                {"code": "1", "name": "板烧鸡腿堡", "isDefault": 1}]},
            {"id": 2, "name": "选择饮料", "choices": [
                {"code": "2", "name": "无糖可口可乐中杯", "isDefault": 1}]},
        ]}}
        self.assertTrue(combos_mod.is_combo_detail(payload))

    def test_one_round_is_a_configurable_single_item(self):
        # 新地 / 麦旋风 / 麦麦脆汁鸡1块 return one round: flavour or size choice
        payload = {"data": {"rounds": [{"id": 1, "name": "选择口味", "choices": [
            {"code": "1", "name": "朱古力新地", "isDefault": 1},
            {"code": "2", "name": "草莓新地", "isDefault": 0}]}]}}
        self.assertFalse(combos_mod.is_combo_detail(payload))

    def test_combo_word_without_rounds_is_not_a_combo(self):
        payload = {"data": {"name": "假装是套餐", "rounds": []}}
        self.assertFalse(combos_mod.is_combo_detail(payload))
        self.assertTrue(catalog_mod.is_combo_name("假装是套餐"))  # the old heuristic

    def test_single_item_fixture_has_no_rounds(self):
        single = json.loads((FIX / "combo_1406_detail.json").read_text("utf-8"))
        self.assertFalse(combos_mod.is_combo_detail(single))

    def test_round_roles(self):
        rounds = combos_mod.rounds_of(detail(CRISPY))
        roles = [combos_mod.round_role(r) for r in rounds]
        self.assertEqual(roles, ["side", "side", "drink"])


class SingleRoundTest(unittest.TestCase):
    """只有一轮选项的商品（麦麦脆汁鸡1块、新地、麦旋风）按单品处理。"""

    def test_single_round_is_not_a_combo(self):
        payload = {"data": {"rounds": [{"id": 1, "name": "选择口味", "choices": [
            {"code": "9900008754", "name": "奥利奥麦旋风", "isDefault": 1}]}]}}
        self.assertTrue(combos_mod.is_single_round_detail(payload))
        self.assertFalse(combos_mod.is_combo_detail(payload))
        self.assertEqual(combos_mod.single_round_choice(payload)["name"], "奥利奥麦旋风")

    def test_single_round_enters_the_pool_by_kind(self):
        catalog = json.loads((FIX / "combo_catalog.json").read_text("utf-8"))
        catalog["meals"]["lunch"]["single_round"] = [{
            "code": "9900008747", "name": "牛肉堡单品精选",
            "choice_code": "1440", "choice_name": "汉堡包", "display_name": "汉堡包",
            "kind": "main", "reference_price": 14.0, "quoted_price": 14.0,
            "status": "ok",
            "nutrition": {"energyKj": 0, "energyKcal": 250, "protein": 13.0, "fat": 9.0,
                          "carbohydrate": 30.0, "sodium": 700.0, "calcium": 100.0},
        }]
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp)
            (Path(tmp) / "combo_catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False), "utf-8")
            svc.invalidate()
            pool = svc.pools("lunch")
            names = {i.name for i in pool["main"]}
            self.assertIn("汉堡包", names)
            item = next(i for i in pool["main"] if i.name == "汉堡包")
            self.assertEqual(item.price_ref, 14.0)
            self.assertEqual(item.nutrition["protein"], 13.0)
            # a single main is selectable
            self.assertTrue(any(i.name == "汉堡包" for i in svc.single_pools("lunch")["lunch"]["main"]))

    def test_dessert_is_visible_but_not_selectable(self):
        catalog = json.loads((FIX / "combo_catalog.json").read_text("utf-8"))
        catalog["meals"]["lunch"]["single_round"] = [{
            "code": "9900008754", "name": "经典麦旋风",
            "choice_code": "1", "choice_name": "奥利奥麦旋风", "display_name": "奥利奥麦旋风",
            "kind": "dessert", "reference_price": 14.5, "quoted_price": 14.5,
            "status": "ok",
            "nutrition": {"energyKj": 0, "energyKcal": 300, "protein": 5.0, "fat": 12.0,
                          "carbohydrate": 40.0, "sodium": 200.0, "calcium": 150.0},
        }]
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp)
            (Path(tmp) / "combo_catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False), "utf-8")
            svc.invalidate()
            desserts = svc.dessert_items("lunch")
            self.assertEqual([d["name"] for d in desserts], ["奥利奥麦旋风"])
            selectable = {i.name for i in svc.single_pools("lunch")["lunch"]["main"]}
            self.assertNotIn("奥利奥麦旋风", selectable)

    def test_unknown_nutrition_is_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp)
            catalog = json.loads((FIX / "combo_catalog.json").read_text("utf-8"))
            catalog["meals"]["lunch"]["single_round_excluded"] = [{
                "code": "9900008746", "name": "鸡肉堡单品精选", "reason": "nutrition_unknown",
                "detail": "缺官方营养：双层脆鸡堡"}]
            (Path(tmp) / "combo_catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False), "utf-8")
            svc.invalidate()
            missing = svc.unknown_items("lunch")
        self.assertTrue([m for m in missing if m["code"] == "9900008746"])


class CatalogStatusTest(unittest.TestCase):
    """套餐目录状态必须能被前端读到，缺失时如实报告。"""

    def test_missing_catalog_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp)   # no catalog in the data dir
            report = svc.combo_report("lunch")
        self.assertFalse(report["available"])

    def test_loaded_catalog_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            report = svc.combo_report("lunch")
        self.assertTrue(report["available"])
        self.assertGreaterEqual(report["combos_found"], 1)
        self.assertTrue(report["built_at"])

    def test_plan_payload_carries_the_catalog_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                max_mains=2)
            self.assertTrue(payload["combos"]["catalog"]["available"])
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                max_mains=2)
            self.assertFalse(payload["combos"]["catalog"]["available"])


class MembershipTest(unittest.TestCase):
    def test_markers(self):
        hits = combos_mod.membership_markers(
            ["6折起早餐卡专区"], ["早餐卡专享价"],
            {"withOrder": {"cardId": "x"}, "discountType": "随单购早餐卡优惠"})
        self.assertGreaterEqual(len(hits), 4)

    def test_plain_category_has_no_marker(self):
        self.assertEqual(combos_mod.membership_markers(
            ["500大卡套餐"], [], {"canWithOrder": False}), [])

    def test_membership_combo_never_becomes_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            catalog = json.loads((FIX / "combo_catalog.json").read_text("utf-8"))
            catalog["meals"]["lunch"]["excluded"].append({
                "combo_code": "9900003424", "combo_name": "会员价套餐",
                "reason": "membership", "detail": "分类「6折起早餐卡专区」",
            })
            (Path(tmp) / "combo_catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False), "utf-8")
            svc.invalidate()
            self.assertIn("9900003424", svc.combo_codes("lunch"))
            self.assertNotIn("9900003424", [i.code for i in svc.combo_items("lunch")])
            self.assertNotIn("9900003424", [i.code for i in svc.pools("lunch")["main"]])


class VariantTest(unittest.TestCase):
    def test_nutrition_is_the_sum_of_rounds(self):
        cat = nutrition_catalog()
        variants, _ = combos_mod.build_variants(combo_input(CRISPY, "酥脆无双套餐", 20.5), cat)
        default = next(v for v in variants if v["variant"] == "default")
        expected = 0.0
        for part in default["rounds"]:
            match = cat.lookup(part["code"], part["name"])
            self.assertTrue(match.usable, part["name"])
            expected += float(match.nutrition["protein"])
        self.assertAlmostEqual(default["nutrition"]["protein"], round(expected, 1))
        self.assertEqual(default["nutrition"]["protein"], 23.0)

    def test_variant_records_round_list_and_price(self):
        cat = nutrition_catalog()
        variants, _ = combos_mod.build_variants(combo_input(CRISPY, "酥脆无双套餐", 20.5), cat)
        muscle = next(v for v in variants if v["variant"] == "muscle")
        self.assertTrue(muscle["round_list"])
        self.assertEqual(muscle["round_list"][0]["round"], "3")
        self.assertEqual(muscle["reference_price"], round(20.5 + sum(
            r["diff_price"] for r in muscle["rounds"]), 2))

    def test_unknown_component_excludes_the_variant(self):
        empty = catalog_mod.NutritionCatalog([], {}, {})
        variants, excluded = combos_mod.build_variants(
            combo_input(CRISPY, "酥脆无双套餐", 20.5), empty)
        self.assertEqual(variants, [])
        self.assertTrue(excluded)
        self.assertTrue(all(e["reason"] == "nutrition_unknown" for e in excluded))
        self.assertTrue(all("缺官方营养" in e["detail"] for e in excluded))

    def test_muscle_drink_never_loses_protein(self):
        cat = nutrition_catalog()
        variants, _ = combos_mod.build_variants(
            combo_input("9900003728", "脆香油条豆浆组合", 17.5), cat)
        default = next(v for v in variants if v["variant"] == "default")
        # 这轮要 2 根油条（quantity=2）+ 1 杯豆浆：2×5 + 8 = 18 g。
        # 旧模型按 1 根算成 13 g，是漏乘轮次数量的错。
        self.assertEqual(len(variants), 1)
        self.assertEqual(default["nutrition"]["protein"], 18.0)

    def test_parse_diff_price(self):
        self.assertEqual(combos_mod.parse_diff_price("+ ¥2.5"), 2.5)
        self.assertEqual(combos_mod.parse_diff_price("- ¥1"), -1.0)
        self.assertEqual(combos_mod.parse_diff_price(""), 0.0)


class PriceTest(unittest.TestCase):
    def test_reference_equals_quote_in_catalog(self):
        catalog = json.loads((FIX / "combo_catalog.json").read_text("utf-8"))
        checked = 0
        for meal in catalog["meals"].values():
            for v in meal["variants"]:
                self.assertEqual(v["status"], "ok")
                self.assertAlmostEqual(v["quoted_price"], v["reference_price"], places=2)
                checked += 1
        self.assertGreater(checked, 0)

    def test_mismatched_quote_is_excluded(self):
        builder = load_builder()
        cat = nutrition_catalog()
        variants, _ = combos_mod.build_variants(combo_input(CRISPY, "酥脆无双套餐", 20.5), cat)

        class FakeWorker:
            @staticmethod
            def call(tool, args):
                self.assertEqual(tool, "calculate-price")
                self.assertIn("roundList", args["items"][0])
                return {"success": True, "code": 200, "message": "ok", "datetime": "t",
                        "data": {"price": 9900, "originalPrice": 9900, "discount": 0}}

        original = builder.WORKER
        builder.WORKER = FakeWorker()
        try:
            result = builder.price_variant(CRISPY, variants[0],
                                           {"store_code": "1", "be_type": 1, "order_type": 1},
                                           "2026-10-22 12:15")
        finally:
            builder.WORKER = original
        self.assertEqual(result["status"], "excluded")
        self.assertEqual(result["reason"], "price_mismatch")
        self.assertIn("参考价", result["detail"])

    def test_matching_quote_is_kept(self):
        builder = load_builder()
        cat = nutrition_catalog()
        variants, _ = combos_mod.build_variants(combo_input(CRISPY, "酥脆无双套餐", 20.5), cat)
        target = variants[0]

        class FakeWorker:
            @staticmethod
            def call(tool, args):
                return {"success": True, "code": 200, "message": "ok", "datetime": "t",
                        "data": {"price": int(round(target["reference_price"] * 100)),
                                 "originalPrice": 0, "discount": 0}}

        original = builder.WORKER
        builder.WORKER = FakeWorker()
        try:
            result = builder.price_variant(CRISPY, target,
                                           {"store_code": "1", "be_type": 1, "order_type": 1},
                                           "2026-10-22 12:15")
        finally:
            builder.WORKER = original
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["quoted_price"], target["reference_price"], places=2)


class StructureTest(unittest.TestCase):
    def test_combo_counts_as_one_main(self):
        combos = [combo_item(20)]
        mains = [main_item(10, code="M1"), main_item(12, code="M2")]
        rows = planner_mod.build_meal_options(mains, [], [], max_mains=1, combos=combos)
        combo_rows = [r for r in rows if any(i.kind == "combo" for i in r)]
        self.assertTrue(combo_rows)
        for row in combo_rows:
            self.assertEqual([i.kind for i in row], ["combo"])

        rows = planner_mod.build_meal_options(mains, [], [], max_mains=2, combos=combos)
        combo_rows = [r for r in rows if any(i.kind == "combo" for i in r)]
        widths = {len(r) for r in combo_rows}
        self.assertEqual(widths, {1, 2})

    def test_at_most_two_combo_portions_and_no_extra_side_or_drink(self):
        rows = planner_mod.build_meal_options(
            [main_item(10)], [main_item(2, code="S")], [main_item(1, code="D")],
            max_mains=3, combos=[combo_item(20, code="C1"), combo_item(22, code="C2")])
        for row in rows:
            combos = [i for i in row if i.kind == "combo"]
            self.assertLessEqual(len(combos), 2)
            self.assertLessEqual(sum(i.quantity for i in combos), 2)
            if combos:
                self.assertFalse([i for i in row if i.kind in ("side", "drink")])

    def test_same_combo_twice_needs_a_verified_two_portion_price(self):
        plain = combo_item(20, price=15, code="C1")
        plain.combo_x2_price = None          # never priced as two portions
        rows = planner_mod.build_meal_options([], [], [], max_mains=2, combos=[plain])
        self.assertFalse([r for r in rows if sum(i.quantity for i in r) >= 2])

        priced = combo_item(20, price=15, code="C1")
        priced.combo_x2_price = 30.0
        rows = planner_mod.build_meal_options([], [], [], max_mains=2, combos=[priced])
        doubled = [r for r in rows if r and r[0].quantity == 2]
        self.assertEqual(len(doubled), 1)
        self.assertEqual(planner_mod.sum_reference_cost(doubled[0]), 30.0)
        self.assertEqual(planner_mod.sum_nutrition(doubled[0])[0]["protein"], 40.0)

    def test_two_different_combos_are_two_lines(self):
        a = combo_item(20, price=15, code="C1")
        a.combo_variant = "default"
        b = combo_item(25, price=18, code="C2")
        b.combo_variant = "default"
        rows = planner_mod.build_meal_options([], [], [], max_mains=2, combos=[a, b])
        pair = next(r for r in rows if len(r) == 2 and all(i.kind == "combo" for i in r))
        self.assertEqual([i.quantity for i in pair], [1, 1])
        self.assertEqual(planner_mod.sum_reference_cost(pair), 33.0)
        self.assertEqual(planner_mod.sum_nutrition(pair)[0]["protein"], 45.0)

    def test_two_combos_do_not_fit_when_max_mains_is_one(self):
        a = combo_item(20, price=15, code="C1")
        a.combo_x2_price = 30.0
        b = combo_item(25, price=18, code="C2")
        rows = planner_mod.build_meal_options([], [], [], max_mains=1, combos=[a, b])
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if sum(i.quantity for i in r) > 1])

    def test_combo_totals_include_everything(self):
        combo = combo_item(20, price=18)
        combo.nutrition = {k: 1.0 for k in planner_mod.NUTRI_KEYS}
        combo.nutrition["protein"] = 20.0
        row = [combo, main_item(10, price=22)]
        totals, unknown = planner_mod.sum_nutrition(row)
        self.assertEqual(unknown, [])
        self.assertEqual(totals["protein"], 30.0)
        self.assertEqual(planner_mod.sum_reference_cost(row), 40.0)


class ServiceTest(unittest.TestCase):
    def test_lunch_44_45_becomes_combo_plus_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                max_mains=2)
        kinds = [i["kind"] for i in payload["items"]]
        self.assertIn("combo", kinds)
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["protein_status"], "met")
        self.assertGreaterEqual(payload["totals"]["protein"], 44)
        self.assertLessEqual(payload["cost"], 45)
        self.assertTrue(payload["combos"]["applied"])
        self.assertIn("套餐暂不支持", payload["combos"]["unsupported"])
        combo_codes = {i["code"] for i in payload["items"] if i["kind"] == "combo"}
        self.assertFalse([o for o in payload["suggestions"] if o.get("target_code") in combo_codes])

    def test_combo_only_plan_reports_removal_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=20, budget=25, protein_tolerance=0),
                max_mains=1)
        self.assertEqual([i["kind"] for i in payload["items"]], ["combo"])
        self.assertEqual(payload["removable"]["reason"], "套餐暂不支持去配料")

    def test_lunch_35_25_and_breakfast_25_20_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            lunch = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=35, budget=25, protein_tolerance=3),
                max_mains=2)
            breakfast = svc.plan_meal(
                "breakfast", planner_mod.Targets(protein_min=25, budget=20, protein_tolerance=3),
                max_mains=2)
        self.assertEqual([i["name"] for i in lunch["items"]], ["双层深海鳕鱼堡"])
        self.assertFalse(lunch["combos"]["applied"])
        self.assertEqual([i["name"] for i in breakfast["items"]], ["原味板烧鸡腿炒双蛋堡"])
        self.assertFalse(breakfast["combos"]["applied"])

    def test_quote_sends_the_round_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            svc.plan_meal("lunch",
                          planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                          max_mains=2, quote=True)
        calls = svc.provider.price_calls
        combo_lines = [line for call in calls for line in call["items"]
                       if str(line.get("productCode")) == CRISPY]
        self.assertTrue(combo_lines)
        self.assertTrue(any(line.get("roundList") for line in combo_lines))

    def test_coupon_is_unsupported_for_a_combo_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                max_mains=2)
            result, status = svc.quote_with_coupon("lunch", payload["signature"])
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "unsupported")
        self.assertIn("套餐暂不支持", result["note"])

    def test_signature_depends_on_the_variant(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            combo = next(i for i in svc.combo_items("lunch") if i.combo_variant == "default")
            other = next(i for i in svc.combo_items("lunch") if i.combo_variant == "muscle")
            cache = svc.ensure_cache()
            sig_a = svc.plan_signature(cache, [planner_mod.MealPlan(meal="lunch", items=[combo])])
            sig_b = svc.plan_signature(cache, [planner_mod.MealPlan(meal="lunch", items=[other])])
        self.assertNotEqual(sig_a, sig_b)

    def test_combo_report_lists_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_with_fixture_catalog(tmp)
            report = svc.combo_report("lunch")
        self.assertTrue(report["available"])
        self.assertGreaterEqual(report["variants_used"], 1)
        self.assertIn("note", report)


class QuantityRoundTest(unittest.TestCase):
    """堡堡随心拼 asks for 2 mains + 3 sides + 2 drinks in one round.

    ``calculate-price`` rejects a 2-drink round when the roundList carries only
    one drink (60006), so both the nutrition sum and the payload must use the
    round's real quantity.
    """

    CODE = "9900004066"

    def test_round_list_sends_the_round_quantity(self):
        combo = combo_input(self.CODE, "堡堡随心拼（7件任选）", 73.0)
        variants, excluded = combos_mod.build_variants(combo, nutrition_catalog())
        self.assertEqual(excluded, [])
        muscle = next(v for v in variants if v["variant"] == "muscle")
        self.assertEqual(
            muscle["round_list"],
            [{"round": "3", "comboItemList": [{"code": "507411", "quantity": 2}]}])
        self.assertEqual(muscle["reference_price"], 73.0)

    def test_quote_sends_two_drinks_and_matches_73(self):
        item = planner_mod.Item(
            code=self.CODE, name="堡堡随心拼（7件任选）", kind="combo",
            nutrition={k: 0.0 for k in planner_mod.NUTRI_KEYS},
            nutrition_status="official", price_ref=73.0, meal="lunch",
            round_list=[{"round": "3", "comboItemList": [{"code": "507411", "quantity": 2}]}])
        plan = planner_mod.MealPlan(meal="lunch", items=[item],
                                    nutrition=item.nutrition, price_ref=73.0)
        with tempfile.TemporaryDirectory() as tmp:
            svc = new_service(tmp, amount=73.0)
            result = svc.quote_meal(plan)
            line = svc.provider.price_calls[-1]["items"][0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["amount"], 73.0)
        self.assertEqual(line["roundList"][0]["comboItemList"][0]["quantity"], 2)


@unittest.skipUnless(LIVE_CATALOG.exists() and DEMO_CACHE.exists(),
                     "live combo catalog not built")
class LiveCatalogScenariosTest(unittest.TestCase):
    """The three acceptance scenarios on the checked-in live catalog."""

    def _service(self, tmp: str):
        cache = json.loads(DEMO_CACHE.read_text("utf-8"))
        (Path(tmp) / "state.json").write_text(json.dumps({
            "store": {"code": cache["store_code"], "name": "麦当劳珠海屏湾一路餐厅",
                      "beType": cache["be_type"], "orderType": cache["order_type"]},
            "date": cache["date"], "meal_times": cache["meal_times"],
        }, ensure_ascii=False), "utf-8")
        shutil.copy2(ROOT / "data/aliases.json", Path(tmp) / "aliases.json")
        from service import Service

        class _Null:
            source_name = "live"

            def now(self):
                raise AssertionError("date is fixed")

            def detail(self, *args, **kwargs):
                # removal options are not part of this scenario test
                return {"success": False, "code": 1, "message": "no detail in test"}

        svc = Service(_Null(), base=Path(tmp))
        ctx = svc.context()
        payload = dict(cache)
        payload.update({"date": ctx.day, "meal_times": ctx.meal_times,
                        "cache_key": ctx.key(), "store_code": ctx.store_code,
                        "be_type": ctx.be_type, "order_type": ctx.order_type,
                        "source": "live"})
        (Path(tmp) / "cache").mkdir(parents=True, exist_ok=True)
        (Path(tmp) / "cache" / f"{ctx.day}__{ctx.store_code}__{ctx.key()}.json").write_text(
            json.dumps(payload, ensure_ascii=False), "utf-8")
        return svc

    def test_lunch_44_45(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=44, budget=45, protein_tolerance=3),
                max_mains=2)
        self.assertTrue(payload["combos"]["applied"])
        self.assertEqual(payload["totals"]["protein"], 54.0)
        # 同一套餐 2 份：29.80 元 = 14.90 × 2，比「随心配 + 双层吉士」的 37.40 更便宜
        self.assertAlmostEqual(payload["cost"], 29.8)
        self.assertEqual([(i["name"], i["quantity"]) for i in payload["items"]],
                         [("人气经典随心配", 2)])
        combo = next(i for i in payload["items"] if i["kind"] == "combo")
        # 可乐麦炫酷没有官方营养，构建时换成无糖可口可乐中杯并验价
        self.assertEqual(combo["combo"]["replacements"][0]["to"], "无糖可口可乐中杯")

    def test_two_different_combos_in_one_meal(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=40, budget=45, protein_tolerance=3),
                max_mains=2)
        combos = [i for i in payload["items"] if i["kind"] == "combo"]
        self.assertEqual(len(combos), 2)
        self.assertEqual([i["quantity"] for i in combos], [1, 1])
        self.assertAlmostEqual(payload["cost"], 28.8)
        self.assertEqual(payload["totals"]["protein"], 42.0)

    def test_lunch_35_25(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            payload = svc.plan_meal(
                "lunch", planner_mod.Targets(protein_min=35, budget=25, protein_tolerance=3),
                max_mains=2)
        self.assertEqual([i["name"] for i in payload["items"]], ["双层深海鳕鱼堡"])
        self.assertAlmostEqual(payload["cost"], 25.0)
        # 加预算卡片：人气经典 + 精选超值 = 42 g，28.80 元
        reach = payload["frontier"]["reach_price"]
        self.assertAlmostEqual(reach["cost"], 28.8)
        self.assertEqual(reach["protein"], 42.0)

    def test_breakfast_25_20(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            payload = svc.plan_meal(
                "breakfast", planner_mod.Targets(protein_min=25, budget=20, protein_tolerance=3),
                max_mains=2)
        self.assertGreaterEqual(payload["totals"]["protein"], 25)
        self.assertLessEqual(payload["cost"], 20.0)
        self.assertFalse(payload["combos"]["applied"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
