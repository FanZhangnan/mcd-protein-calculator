"""Shared test helpers (synthetic + offline fixtures)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from planner import Item, MealPlan  # noqa: E402
from service import OfflineProvider, Service  # noqa: E402
from store import Store  # noqa: E402

FIX = ROOT / "data" / "fixtures"

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
         price: float, *, quantity: float = 1) -> Item:
    return Item(code=code, name=name, kind=kind, nutrition=nutr(kcal, protein, fat),
                nutrition_status="synthetic", nutrition_note="合成测试数据",
                price_ref=price, quantity=quantity, meal="lunch")


def meal_plan(name: str, code: str, kcal: float, protein: float, price: float,
              *, qty: float = 1.0, modification: list | None = None) -> MealPlan:
    entry = item(code, name, "main", kcal, protein, 20.0, price, quantity=qty)
    entry.modification = modification or []
    nutrition, unknown = planner_mod.sum_nutrition([entry])
    return MealPlan(meal="lunch", items=[entry], nutrition=nutrition, unknown=unknown,
                    price_ref=round(price * qty, 2))


def one_meal(items: list[Item]) -> list[MealPlan]:
    nutrition, unknown = planner_mod.sum_nutrition(items)
    return [MealPlan(meal="lunch", items=items, nutrition=nutrition, unknown=unknown,
                     price_ref=planner_mod.sum_reference_cost(items) or 0.0)]


class FakeProvider:
    """Records detail calls; returns whatever was registered."""

    source_name = "offline"

    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []
        self.details: dict[tuple[str, str | None], dict] = {}

    def now(self):
        return {"success": True, "data": {"date": "2026-10-19", "formatted": "2026-10-19 08:00:00"}}

    def detail(self, store_code, be_type, order_type, code, when=None):
        self.calls.append((code, when))
        return self.details.get((code, when), {"success": False, "code": 1,
                                               "message": "未匹配到商品"})


class RecordingProvider(OfflineProvider):
    """Offline menus/nutrition + deterministic quote amount + call log."""

    def __init__(self, fixture_dir: Path, amount: float = 23.0):
        super().__init__(fixture_dir)
        self.amount = amount
        self.price_calls: list[dict] = []
        self.fail = False

    def price(self, store_code, be_type, order_type, items, need_tableware=False, when=None):
        self.price_calls.append({"items": items, "when": when, "store": store_code})
        if self.fail:
            return {"success": False, "code": 60006, "message": "商品已售罄或不可售",
                    "datetime": "2026-10-19 00:00:00", "data": None}
        return {"success": True, "code": 200, "message": "请求成功（离线桩）",
                "datetime": "2026-10-19 00:00:00",
                "data": {"price": int(round(self.amount * 100)),
                         "originalPrice": int(round(self.amount * 100)), "discount": 0,
                         "takeWayList": [{"code": "eat-in", "title": "堂食"}]}}


def new_service(tmp: str, amount: float = 23.0) -> Service:
    provider = RecordingProvider(FIX, amount=amount)
    svc = Service(provider, Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({
        "date": "2026-10-19",
        "store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅",
                  "beType": 1, "orderType": 1},
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    })
    svc.ensure_cache("2026-10-19")
    return svc


REMOVABLE = {
    "ok": True,
    "sku": {"code": "A", "name": "合成牛肉堡"},
    "supportModify": True,
    "groups": [{"index": 0, "name": None, "values": [
        {"code": "C1", "name": "高达干酪片", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
        {"code": "C2", "name": "番茄片", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
        {"code": "C3", "name": "培根", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
        {"code": "C4", "name": "醇香芝士风味酱", "selectedKey": "0-1", "unselectedKey": "0-0", "price": 0},
    ]}],
    "options": [
        {"code": "C1", "name": "高达干酪片", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": True, "default_suggestion": True},
        {"code": "C2", "name": "番茄片", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": False, "default_suggestion": False},
        {"code": "C3", "name": "培根", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": True, "default_suggestion": True},
        {"code": "C4", "name": "醇香芝士风味酱", "selectedKey": "0-1", "unselectedKey": "0-0",
         "price": 0, "group": 0, "protein_risk": False, "default_suggestion": True},
    ],
}


def new_svc(tmp: str, provider=None) -> Service:
    provider = provider or FakeProvider()
    svc = Service(provider, Store(Path(tmp)), base=Path(tmp))
    svc.store.save_state({"date": "2026-10-19",
                          "store": {"code": "1440054", "name": "合成门店",
                                    "beType": 1, "orderType": 1},
                          "meal_times": CACHE["meal_times"]})
    svc.ensure_cache = lambda day=None: dict(CACHE)  # type: ignore[assignment]
    return svc
