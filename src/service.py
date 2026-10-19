"""Service layer: context-keyed cache, one-meal solving, quoting, recompute."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

import catalog as catalog_mod
import planner as planner_mod
import quantity as quantity_mod
from mcp_client import McpClient, McpError
from store import Store, data_dir

MEALS = planner_mod.MEALS
MEAL_LABEL = planner_mod.MEAL_LABEL

#: strict-mode rule 5 — a removal that may lower the protein number.
#: A name containing 酱 is always excluded: sauce carries fat, not protein.
PROTEIN_RISK_HINTS = ("芝士", "吉士", "干酪", "培根", "蛋")
#: muscle-building warning shown on the removal option itself
MUSCLE_WARNING_HINTS = ("芝士", "吉士", "干酪", "培根", "蛋")
MUSCLE_WARNING = "增肌时不建议去掉，会减少蛋白质"
#: default „point-order suggestion“ list
SUGGEST_REMOVE_HINTS = ("酱", "芝士", "吉士", "干酪", "培根", "黄油")
#: only one customisation action survives the day-mode removal
SUPPORTED_CUSTOMIZATIONS = ("suggest_remove",)
SINGLE_DRINK_HINTS = ("零度可乐", "美式", "纯牛奶")
SINGLE_DRINK_MAX_KCAL = 20.0
#: product default: two mains per meal (the same SKU twice is allowed)
DEFAULT_MAX_MAINS = 2
MAX_MAINS_LIMIT = 3


class UnsupportedCustomization(ValueError):
    """A customisation action (or option code) this version does not support."""

    def __init__(self, message: str, *, supported: list[str] | None = None,
                 action: str | None = None):
        super().__init__(message)
        self.supported = supported or list(SUPPORTED_CUSTOMIZATIONS)
        self.action = action


# --------------------------------------------------------------------- providers
class LiveProvider:
    """Real MCP calls. Token never leaves this object."""

    source_name = "live"

    def __init__(self, client: McpClient | None = None):
        self.client = client or McpClient()

    def now(self) -> dict:
        return self.client.call("now-time-info")

    def menu(self, store_code: str, be_type: int, order_type: int, when: str) -> dict:
        return self.client.call(
            "query-meals",
            {"storeCode": store_code, "orderType": order_type, "beType": be_type,
             "reservationDate": when},
        )

    def nutrition(self) -> dict:
        return self.client.call("list-nutrition-foods")

    def detail(self, store_code: str, be_type: int, order_type: int, code: str,
               when: str | None = None) -> dict:
        payload = {"storeCode": store_code, "orderType": order_type, "beType": be_type,
                   "code": code}
        if when:
            payload["reservationDate"] = when
        return self.client.call("query-meal-detail", payload)

    def price(self, store_code: str, be_type: int, order_type: int,
              items: list[dict], need_tableware: bool = False,
              when: str | None = None) -> dict:
        payload = {
            "storeCode": store_code, "orderType": order_type, "beType": be_type,
            "needTableware": need_tableware, "items": items,
        }
        if when:
            payload["reservationDate"] = when
        return self.client.call("calculate-price", payload)


class OfflineProvider:
    """Fixture-backed provider. Must never be advertised as live."""

    source_name = "offline"

    def __init__(self, fixture_dir: Path):
        self.dir = Path(fixture_dir)
        self.price_calls: list[dict] = []
        self._menus: dict[str, dict] = {}

    def _load(self, name: str) -> dict:
        path = self.dir / f"{name}.json"
        if not path.exists():
            raise McpError(f"fixture missing: {name}", stage="offline")
        return json.loads(path.read_text("utf-8"))

    def now(self) -> dict:
        return self._load("now_time_info")

    def menu(self, store_code: str, be_type: int, order_type: int, when: str) -> dict:
        meal = when[-5:]
        if meal <= "10:30":
            payload = self._load("menu_breakfast_0815")
        elif meal <= "16:00":
            payload = self._load("menu_lunch_1215")
        else:
            payload = self._load("menu_dinner_1830")
        self._menus.update(((payload.get("data") or {}).get("meals") or {}))
        return payload

    def nutrition(self) -> dict:
        return self._load("nutrition")

    def detail(self, store_code: str, be_type: int, order_type: int, code: str,
               when: str | None = None) -> dict:
        """Meal-time specific fixture first, then the SKU-level one.

        This mirrors the live POS: 514700 is on sale at 08:15 and gone at
        12:15, so the offline provider must not answer both from one file.
        """
        if when:
            slot = when[-5:].replace(":", "")
            specific = self.dir / f"combo_{code}_{slot}_detail.json"
            if specific.exists():
                return json.loads(specific.read_text("utf-8"))
        return self._load(f"combo_{code}_detail")

    def price(self, store_code: str, be_type: int, order_type: int,
              items: list[dict], need_tableware: bool = False,
              when: str | None = None) -> dict:
        """Deterministic offline quote.

        The real POS is never called; the amount is the fixture menu price, and
        every payload says so. Without this, ``--offline`` could render a plan
        but never quote it.
        """
        self.price_calls.append({"items": items, "when": when, "store": store_code})
        if os.environ.get("MAI_MAI_QUOTE_FAIL"):
            # test hook: prove the UI never turns a failed quote into ¥0
            return {"success": False, "code": 60006,
                    "message": "离线桩：模拟试算失败", "datetime": "offline", "data": None}
        total = 0.0
        for line in items:
            code = str(line.get("productCode"))
            info = self._menus.get(code) or {}
            unit = info.get("currentPrice")
            if unit in (None, ""):
                return {"success": False, "code": 60006,
                        "message": f"离线桩：菜单中没有 {code} 的价格",
                        "datetime": "offline", "data": None}
            total += float(unit) * int(line.get("quantity") or 1)
        fen = int(round(total * 100))
        return {"success": True, "code": 200, "message": "请求成功（离线桩，按菜单价）",
                "datetime": "offline",
                "data": {"price": fen, "originalPrice": fen, "discount": 0,
                         "takeWayList": [{"code": "eat-in", "title": "堂食"}]}}


# ------------------------------------------------------------ serving windows
def _menu_codes(payload: dict | None) -> set[str]:
    data = (payload or {}).get("data") or {}
    return set((data.get("meals") or {}).keys())


def menu_warnings(menus: dict[str, dict], meal_times: dict[str, str]) -> dict[str, str]:
    """Flag a meal slot that silently served another serving period's menu.

    The POS exposes no serving-window field, so the only evidence available is
    the menu itself. When the lunch slot returns exactly the breakfast menu
    (10:30 sits in the 10:15–10:44 gap), every number below would be correct
    for breakfast and wrong for the label on the card. Comparing the three
    menus we already fetch is enough to say so.

    Lunch and dinner legitimately share one menu, so only the
    breakfast↔(lunch|dinner) pairs are compared. Breakfast itself is never
    flagged: when its menu equals lunch's, the usual cause is a lunch slot that
    fell into the gap (10:30), and flagging both would be noise.
    """
    sets = {meal: _menu_codes(payload) for meal, payload in (menus or {}).items()}
    breakfast = sets.get("breakfast") or set()
    if not breakfast:
        return {}
    out: dict[str, str] = {}
    for meal in ("lunch", "dinner"):
        if sets.get(meal) and sets[meal] == breakfast:
            out[meal] = (
                f"{MEAL_LABEL[meal]}时段 {meal_times.get(meal, '')} 返回的菜单与早餐完全相同"
                f"（{len(breakfast)} 个 SKU），可能正处在两个供餐时段之间；"
                "以下结果按这份菜单的实际在售商品计算，请确认取餐时间。"
            )
    return out


# ---------------------------------------------------------------------- service
@dataclass
class CacheContext:
    store_code: str
    day: str
    be_type: int
    order_type: int
    meal_times: dict[str, str]

    def key(self) -> str:
        blob = json.dumps(
            {"s": self.store_code, "d": self.day, "b": self.be_type,
             "o": self.order_type, "t": self.meal_times},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def as_dict(self) -> dict[str, Any]:
        return {"store_code": self.store_code, "date": self.day, "be_type": self.be_type,
                "order_type": self.order_type, "meal_times": self.meal_times}


class Service:
    def __init__(self, provider, store: Store | None = None, base: Path | None = None):
        self.provider = provider
        self.store = store or Store(base)
        self.base = Path(base) if base else data_dir()
        self.cache_dir = self.base / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._catalog: catalog_mod.NutritionCatalog | None = None
        self._catalog_key = ""
        self._cache: dict[str, Any] | None = None
        self._cache_key = ""
        self._detail_cache: dict[str, Any] = {}
        self._load_detail_cache()
        self._baseline: dict[str, Any] | None = None
        self._working: dict[str, Any] | None = None
        self._ladder: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------- context/cache
    def context(self, day: str | None = None, state: dict[str, Any] | None = None) -> CacheContext:
        state = state or self.store.get_state()
        day = day or state.get("date")
        if not day:
            now_payload = self.provider.now()
            server_date = (now_payload.get("data") or {}).get("date")
            if not server_date:
                server_date = (date_cls.today() + timedelta(days=1)).isoformat()
            day = (date_cls.fromisoformat(server_date) + timedelta(days=1)).isoformat()
            self.store.save_state({"date": day})
        return CacheContext(
            store_code=str(state["store"]["code"]),
            day=day,
            be_type=int(state["store"].get("beType", 1)),
            order_type=int(state["store"].get("orderType", 1)),
            meal_times=dict(state["meal_times"]),
        )

    def _cache_path(self, ctx: CacheContext) -> Path:
        return self.cache_dir / f"{ctx.day}__{ctx.store_code}__{ctx.key()}.json"

    def _load_detail_cache(self) -> None:
        path = self.base / "detail_cache.json"
        if path.exists():
            try:
                self._detail_cache = json.loads(path.read_text("utf-8"))
            except Exception:
                self._detail_cache = {}

    def _save_detail_cache(self) -> None:
        (self.base / "detail_cache.json").write_text(
            json.dumps(self._detail_cache, ensure_ascii=False, indent=2), "utf-8")

    def refresh(self, day: str | None = None) -> dict[str, Any]:
        state = self.store.get_state()
        ctx = self.context(day, state)
        menus = {meal: self.provider.menu(ctx.store_code, ctx.be_type, ctx.order_type,
                                          f"{ctx.day} {ctx.meal_times[meal]}") for meal in MEALS}
        nutrition = self.provider.nutrition()
        payload = {
            **ctx.as_dict(),
            "cache_key": ctx.key(),
            "source": self.provider.source_name,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "server_time": (menus.get("lunch") or {}).get("datetime"),
            "menus": menus,
            "nutrition": nutrition,
            "menu_warnings": menu_warnings(menus, ctx.meal_times),
        }
        self._cache_path(ctx).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        self._cache = payload
        self._cache_key = ctx.key()
        self._catalog = None
        return {"date": ctx.day, "fetched_at": payload["fetched_at"],
                "source": payload["source"], "cache_key": ctx.key()}

    def ensure_cache(self, day: str | None = None) -> dict[str, Any]:
        """Return the cache matching the *current* context, refreshing if needed."""
        ctx = self.context(day)
        if self._cache and self._cache_key == ctx.key():
            return self._cache
        expected = {"cache_key": ctx.key(), "store_code": ctx.store_code, "date": ctx.day,
                    "be_type": ctx.be_type, "order_type": ctx.order_type,
                    "meal_times": ctx.meal_times}
        path = self._cache_path(ctx)
        if path.exists():
            try:
                payload = json.loads(path.read_text("utf-8"))
                if all(payload.get(k) == v for k, v in expected.items()):
                    payload["source"] = "cache"
                    self._cache = payload
                    self._cache_key = ctx.key()
                    return payload
            except Exception:
                pass
        self.refresh(ctx.day)
        return self._cache  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Drop everything tied to the previous context — memory *and* derived plan.

        The on-disk baseline is not deleted (it is evidence, not user data), but
        :meth:`get_baseline` refuses to serve it once the context changed.
        """
        self._cache = None
        self._cache_key = ""
        self._catalog = None
        self._baseline = None

    # ---------------------------------------------------------------- context id
    def _fingerprint(self, ctx: CacheContext | None = None) -> dict[str, Any]:
        ctx = ctx or self.context()
        return {
            "cache_key": ctx.key(),
            "store_code": ctx.store_code,
            "date": ctx.day,
            "be_type": ctx.be_type,
            "order_type": ctx.order_type,
            "meal_times": dict(ctx.meal_times),
        }

    def _context_matches(self, fingerprint: dict[str, Any] | None) -> bool:
        if not isinstance(fingerprint, dict) or not fingerprint:
            return False
        try:
            current = self._fingerprint()
        except Exception:
            return False
        keys = ("cache_key", "store_code", "date", "be_type", "order_type", "meal_times")
        return all(fingerprint.get(k) == current.get(k) for k in keys)

    def cache_info(self) -> dict[str, Any]:
        cache = self._cache or {}
        return {
            "source": cache.get("source") or "none",
            "fetched_at": cache.get("fetched_at"),
            "date": cache.get("date"),
            "store_code": cache.get("store_code"),
            "meal_times": cache.get("meal_times"),
            "cache_key": cache.get("cache_key"),
            "menu_warnings": cache.get("menu_warnings") or {},
        }

    # ------------------------------------------------------------------- catalog
    def catalog(self) -> catalog_mod.NutritionCatalog:
        cache = self.ensure_cache()
        key = cache.get("cache_key", "")
        if self._catalog is not None and self._catalog_key == key:
            return self._catalog
        rows = catalog_mod.parse_nutrition_payload(cache["nutrition"])
        alias_path = self.base / "aliases.json"
        if not alias_path.exists():
            alias_path = Path(__file__).resolve().parents[1] / "data" / "aliases.json"
        aliases: dict[str, str] = {}
        evidence: dict[str, str] = {}
        if alias_path.exists():
            raw = json.loads(alias_path.read_text("utf-8"))
            for code, entry in (raw.get("codes") or {}).items():
                aliases[str(code)] = entry["name"]
                evidence[str(code)] = entry.get("evidence", "")
        self._catalog = catalog_mod.NutritionCatalog(rows, aliases, evidence)
        self._catalog_key = key
        return self._catalog

    # --------------------------------------------------------------------- pools
    def pools(self, meal: str) -> dict[str, list[planner_mod.Item]]:
        cache = self.ensure_cache()
        data = (cache["menus"].get(meal) or {}).get("data") or {}
        meals_map: dict[str, Any] = data.get("meals") or {}
        cat = self.catalog()

        buckets: dict[str, list[planner_mod.Item]] = {"main": [], "side": [], "drink": [], "other": []}
        for code, info in meals_map.items():
            name = (info or {}).get("name") or ""
            if not name or catalog_mod.is_combo_name(name):
                continue
            kind = catalog_mod.classify(name)
            if kind == "other":
                continue
            match = cat.lookup(code, name)
            try:
                price = float(info.get("currentPrice")) if info.get("currentPrice") not in (None, "") else None
            except (TypeError, ValueError):
                price = None
            buckets[kind].append(planner_mod.Item(
                code=code, name=name, kind=kind,
                nutrition=match.nutrition if match.usable else None,
                nutrition_status=match.status, nutrition_note=match.note,
                price_ref=price, meal=meal,
            ))
        for kind in buckets:
            buckets[kind].sort(key=lambda i: (
                i.nutrition is None,
                -float((i.nutrition or {}).get("protein") or 0.0),
                i.code,
            ))
        return buckets

    def unknown_items(self, meal: str) -> list[dict[str, Any]]:
        buckets = self.pools(meal)
        return [{
            "code": item.code, "name": item.name, "kind": item.kind,
            "status": item.nutrition_status, "note": item.nutrition_note,
        } for kind in ("main", "side", "drink") for item in buckets[kind] if not item.nutrition]

    # ----------------------------------------------------------- detail / removal
    def _meal_for_code(self, code: str) -> str:
        """Which meal pool actually sells this SKU (breakfast vs lunch/dinner)."""
        target = str(code)
        for meal in MEALS:
            pool = self.pools(meal)
            for kind in ("main", "side", "drink"):
                if any(str(i.code) == target for i in pool[kind]):
                    return meal
        return "lunch"

    def _when_for(self, meal: str) -> str:
        ctx = self.context()
        return f"{ctx.day} {ctx.meal_times.get(meal, '12:15')}"

    def _detail(self, code: str, meal: str, kind: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Cached ``query-meal-detail`` for a specific meal slot.

        The cache key contains store / date / meal time on purpose: the same SKU
        can be on sale at 08:15 and gone at 12:15 (514700 is the canonical
        example), so a lunch key must never answer a breakfast question.
        """
        ctx = self.context()
        when = self._when_for(meal)
        key = (f"{kind}:{ctx.store_code}:{ctx.be_type}:{ctx.order_type}:"
               f"{ctx.day}:{when}:{code}")
        if key in self._detail_cache:
            cached = self._detail_cache[key]
            if isinstance(cached, dict) and cached.get("data") is not None:
                return cached, None
        try:
            payload = self.provider.detail(ctx.store_code, ctx.be_type, ctx.order_type,
                                           code, when=when)
        except McpError as exc:
            err = {"ok": False, "reason": f"未能获取该 SKU 的特调信息：{exc}", "options": []}
            self._detail_cache[key] = err
            self._save_detail_cache()
            return None, err
        if not payload.get("success"):
            err = {"ok": False,
                   "reason": f"接口返回 {payload.get('code')}：{payload.get('message')}",
                   "options": []}
            self._detail_cache[key] = err
            self._save_detail_cache()
            return None, err
        self._detail_cache[key] = payload
        self._save_detail_cache()
        return payload, None

    def removable_options(self, code: str, meal: str | None = None) -> dict[str, Any]:
        """Every removable component the POS exposes for one SKU.

        Each option carries the raw keys so a quote can send the whole group
        back; removing a component never changes the nutrition number.
        """
        meal = meal if meal in MEALS else self._meal_for_code(code)
        payload, err = self._detail(code, meal, "removable")
        if err:
            err["meal"] = meal
            return err
        data = payload.get("data") or {}
        groups: list[dict[str, Any]] = []
        options: list[dict[str, Any]] = []
        for gi, group in enumerate((data.get("modification") or {}).get("items", []) or []):
            values = []
            for value in group.get("values", []) or []:
                name = value.get("name") or ""
                selected = value.get("selectedKey")
                unselected = value.get("unselectedKey")
                if not unselected or selected in (None, ""):
                    continue
                entry = {
                    "code": value.get("code"), "name": name,
                    "selectedKey": selected, "unselectedKey": unselected,
                    "price": value.get("price", 0),
                }
                values.append(entry)
                options.append({
                    **entry,
                    "group": gi,
                    "protein_risk": self.protein_risk(name),
                    "muscle_warning": any(h in name for h in MUSCLE_WARNING_HINTS),
                    "muscle_note": (MUSCLE_WARNING if any(
                        h in name for h in MUSCLE_WARNING_HINTS) else ""),
                    "default_suggestion": any(h in name for h in SUGGEST_REMOVE_HINTS),
                })
            if values:
                groups.append({"index": gi, "name": group.get("name"), "values": values})
        return {
            "ok": bool(options),
            "sku": {"code": data.get("code"), "name": data.get("name")},
            "supportModify": data.get("supportModify"),
            "groups": groups,
            "options": options,
            "reason": "" if options else "该 SKU 没有可去除的配料",
            "meal": meal,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------ single meal
    @staticmethod
    def protein_risk(name: str) -> bool:
        """Could removing this component lower the displayed protein?

        A sauce (name contains 酱) never counts: it contributes fat and sugar,
        so 醇香芝士风味酱 is ``False`` while 高达干酪片 is ``True``.
        """
        if not name or "酱" in name:
            return False
        return any(hint in name for hint in PROTEIN_RISK_HINTS)

    @staticmethod
    def is_single_drink(item: planner_mod.Item) -> bool:
        """Whitelist: zero Coke / Americano / plain milk by name, or < 20 kcal."""
        name = item.name or ""
        if any(hint in name for hint in SINGLE_DRINK_HINTS):
            return True
        kcal = (item.nutrition or {}).get("energyKcal")
        return kcal is not None and float(kcal) < SINGLE_DRINK_MAX_KCAL

    def single_pools(self, meal: str, allow_side: bool = False) -> dict[str, dict[str, list[planner_mod.Item]]]:
        pool = self.pools(meal)
        return {meal: {
            "main": pool["main"],
            "side": pool["side"] if allow_side else [],
            "drink": [d for d in pool["drink"] if self.is_single_drink(d)],
        }}

    def landscape(self, meal: str, max_mains: int = DEFAULT_MAX_MAINS) -> dict[str, Any]:
        """Every main with official nutrition, plus the price→protein frontier.

        ``items`` is what the scatter plot draws (one dot per SKU);
        ``frontier`` is the "with ≤ B yuan, the most protein you can buy"
        curve used by the prefill hint. Only official standard values enter.
        """
        pool = self.pools(meal)
        rows: list[dict[str, Any]] = []
        for item in pool["main"]:
            nutrition = item.nutrition or {}
            protein = nutrition.get("protein")
            kcal = nutrition.get("energyKcal")
            if protein is None or item.price_ref in (None, 0) or kcal is None:
                continue
            price = float(item.price_ref)
            rows.append({
                "code": item.code,
                "name": item.name,
                "protein": round(float(protein), 1),
                "fat": None if nutrition.get("fat") is None else round(float(nutrition["fat"]), 1),
                "kcal": round(float(kcal), 1),
                "price": round(price, 2),
                "protein_per_yuan": round(float(protein) / price, 1),
            })
        rows.sort(key=lambda r: (-r["protein_per_yuan"], r["price"], r["code"]))

        combos = planner_mod.build_meal_options(pool["main"], [], [], max_mains=max_mains)
        points: list[tuple[float, float, list[str]]] = []
        for combo in combos:
            totals, _ = planner_mod.sum_nutrition(combo)
            cost = planner_mod.sum_reference_cost(combo)
            protein = totals.get("protein")
            if protein is None or cost is None:
                continue
            points.append((round(cost, 2), round(float(protein), 1),
                           [i.name if i.quantity == 1 else f"{i.name} ×{int(i.quantity)}"
                            for i in combo]))
        points.sort(key=lambda p: (p[0], -p[1]))
        frontier: list[dict[str, Any]] = []
        best = -1.0
        for cost, protein, names in points:
            if protein > best + 1e-9:
                best = protein
                frontier.append({"price": cost, "protein": protein, "items": names})

        return {
            "meal": meal,
            "meal_label": MEAL_LABEL.get(meal, meal),
            "items": rows,
            "frontier": frontier,
            "max_mains": max_mains,
            "basis": "official_standard",
            "note": "严格模式：仅官方标准版整款数据；未匹配到官方营养的主食不进入本图。",
        }

    def upgrade_ladder(self) -> list[dict[str, Any]]:
        if self._ladder is None:
            path = self.base / "upgrade_ladder.json"
            if not path.exists():
                path = Path(__file__).resolve().parents[1] / "data" / "upgrade_ladder.json"
            try:
                raw = json.loads(path.read_text("utf-8")) if path.exists() else {}
                self._ladder = list(raw.get("pairs") or [])
            except Exception:
                self._ladder = []
        return self._ladder

    def upgrade_for(self, code: str, meal: str,
                    pool: dict[str, list[planner_mod.Item]]) -> dict[str, Any] | None:
        """One step up the meat ladder, or ``None`` when either side is missing."""
        by_code = {str(i.code): i for i in pool.get("main", [])}
        current = by_code.get(str(code))
        if not current or not current.nutrition or current.price_ref is None:
            return None
        for pair in self.upgrade_ladder():
            if str(pair.get("from")) != str(code):
                continue
            target = by_code.get(str(pair.get("to")))
            if not target or not target.nutrition or target.price_ref is None:
                return None
            dp = float(target.nutrition.get("protein") or 0) - float(current.nutrition.get("protein") or 0)
            df = float(target.nutrition.get("fat") or 0) - float(current.nutrition.get("fat") or 0)
            dc = round(float(target.price_ref) - float(current.price_ref), 2)
            # "每多 1 元多多少 g 蛋白质" only makes sense when the step adds a
            # meaningful amount of protein and actually costs more.
            show_conversion = dp >= 5 and dc > 0
            return {
                "from": {"code": current.code, "name": current.name,
                         "protein": current.nutrition.get("protein"),
                         "fat": current.nutrition.get("fat"), "price": current.price_ref},
                "to": {"code": target.code, "name": target.name,
                       "protein": target.nutrition.get("protein"),
                       "fat": target.nutrition.get("fat"), "price": target.price_ref},
                "label": pair.get("label") or f"{current.name} → {target.name}",
                "evidence": pair.get("evidence", ""),
                "delta_protein": round(dp, 1),
                "delta_fat": round(df, 1),
                "delta_price": dc,
                "protein_per_extra_yuan": round(dp / dc, 1) if show_conversion else None,
                "show_conversion": show_conversion,
                "basis": "official_standard",
            }
        return None

    def plan_meal(self, meal: str, targets: planner_mod.Targets,
                  allow_side: bool = False, *, quote: bool = False,
                  max_mains: int = DEFAULT_MAX_MAINS) -> dict[str, Any]:
        """One meal, strict mode: 1..N mains, official standard values only."""
        pools = self.single_pools(meal, allow_side)
        result = planner_mod.plan_open(
            pools, targets, [meal],
            ranker=planner_mod.single_meal_rank,
            full_enumeration=True,
            max_mains=max_mains,
        )
        result.basis = "single_meal"

        meal_plan = result.meals[0] if result.meals else None
        mains = [i for i in (meal_plan.items if meal_plan else []) if i.kind == "main"]
        removables_map: dict[str, dict[str, Any]] = {}
        suggestions: list[dict[str, Any]] = []
        upgrades: list[dict[str, Any]] = []
        for main in mains:
            cap = self.removable_options(main.code, meal)
            removables_map[str(main.code)] = cap
            for option in cap.get("options", []):
                if option.get("default_suggestion"):
                    suggestions.append({**option, "target_code": main.code,
                                        "target_name": main.name})
            step = self.upgrade_for(main.code, meal, pools[meal])
            if step:
                upgrades.append(step)
        removable = (next(iter(removables_map.values()))
                     if removables_map else {"ok": False, "options": [], "groups": [],
                                        "reason": "没有主食"})
        upgrade = upgrades[0] if upgrades else None
        frontier = None
        if not result.feasible:
            frontier = planner_mod.single_meal_frontier(pools, targets, meal,
                                                        max_mains=max_mains)

        quotes: dict[str, dict[str, Any]] = {}
        if quote and meal_plan is not None:
            quotes = {meal: self.quote_meal(meal_plan)}
        payload = self.compose_payload(meal_plan, targets, quotes=quotes,
                                       quote_attempted=quote)
        payload.update({
            "mode": "single",
            "meal": meal,
            "meal_label": MEAL_LABEL.get(meal, meal),
            "allow_side": allow_side,
            "max_mains": max_mains,
            "strict_mode": True,
            "nutrition_basis": "official_standard",
            "context_warning": (self.ensure_cache().get("menu_warnings") or {}).get(meal, ""),
            "removable": removable,
            "removables": removables_map,
            "suggestions": suggestions,
            "upgrade": upgrade,
            "upgrades": upgrades,
            "frontier": frontier,
            "basis": "single_meal",
            "targets": targets.as_dict(),
        })
        self.set_working(result.meals, targets, mode="single")
        self.set_baseline(result, extra={"mode": "single", "targets": targets.as_dict(),
                                         "meal": meal, "max_mains": max_mains})
        return payload

    # -------------------------------------------------------------------- quoting
    @staticmethod
    def plan_signature(cache: dict[str, Any], meals: list[planner_mod.MealPlan]) -> str:
        """Fingerprint of the *user-visible* plan: context + items + qty + options."""
        blob = {
            "store": cache.get("store_code"), "date": cache.get("date"),
            "be": cache.get("be_type"), "order": cache.get("order_type"),
            "times": cache.get("meal_times"),
            "meals": [
                {"meal": m.meal, "items": [
                    {"c": i.code, "q": quantity_mod.signature_quantity(i.quantity),
                     "mod": i.modification or []} for i in m.items
                ]} for m in meals
            ],
        }
        return hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def quote_meal(self, meal_plan: planner_mod.MealPlan) -> dict[str, Any]:
        """Trial-quote one meal. Rejects fractional purchase counts outright."""
        cache = self.ensure_cache()
        items = []
        for item in meal_plan.items:
            qty = quantity_mod.purchase_quantity(
                item.quantity, f"「{item.name or item.code}」的待买数量")
            entry: dict[str, Any] = {"productCode": item.code, "quantity": qty}
            if item.modification:
                entry["modification"] = {"values": item.modification}
            items.append(entry)
        if not items:
            return {"status": "empty", "amount": None, "unit": "CNY", "message": "无商品"}

        meal_time = (cache.get("meal_times") or {}).get(meal_plan.meal, "")
        when = f"{cache['date']} {meal_time}".strip() if meal_time else None
        signature = self.plan_signature(cache, [meal_plan])
        try:
            payload = self.provider.price(
                cache["store_code"], cache["be_type"], cache["order_type"], items, when=when
            )
        except McpError as exc:
            return {"status": "failed", "amount": None, "unit": "CNY", "message": str(exc),
                    "stage": exc.stage, "signature": signature, "reservation_date": when}
        if not payload.get("success"):
            return {"status": "failed", "amount": None, "unit": "CNY",
                    "message": payload.get("message"), "code": payload.get("code"),
                    "signature": signature, "reservation_date": when}
        data = payload.get("data") or {}
        amount_fen = data.get("price")
        if amount_fen is None:
            return {"status": "failed", "amount": None, "unit": "CNY",
                    "message": "接口未返回金额", "signature": signature, "reservation_date": when}
        return {
            "status": "ok",
            "amount": round(amount_fen / 100, 2),
            "amount_fen": amount_fen,
            "original_fen": data.get("originalPrice"),
            "discount_fen": data.get("discount"),
            "unit": "CNY",
            "queried_at": payload.get("datetime"),
            "reservation_date": when,
            "quote_kind": "slot_quote",
            "take_way": [t.get("code") for t in (data.get("takeWayList") or [])],
            "message": payload.get("message"),
            "signature": signature,
            "source": getattr(self.provider, "source_name", "live"),
            "requested_items": items,
        }

    # ------------------------------------------------------- baseline & recompute
    def _read_baseline_file(self) -> dict[str, Any] | None:
        path = self.base / "baseline.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) and payload.get("plan") else None

    def baseline_status(self) -> dict[str, Any]:
        raw = self._read_baseline_file()
        if not raw:
            return {"exists": False, "valid": False, "reason": "尚无基线方案"}
        stored_context = raw.get("context")
        if not self._context_matches(stored_context):
            return {
                "exists": True, "valid": False,
                "reason": "基线属于其他门店/日期/时段，已失效，请重新规划",
                "context": stored_context, "created_at": raw.get("created_at"),
            }
        return {"exists": True, "valid": True, "created_at": raw.get("created_at"),
                "plan_version": raw.get("plan_version"), "context": stored_context}

    def set_baseline(self, result: planner_mod.PlanResult,
                     extra: dict[str, Any] | None = None) -> None:
        cache = self.ensure_cache()
        payload = {
            "version": 2,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "context": self._fingerprint(),
            "plan_version": self.plan_signature(cache, result.meals),
            "cache": self.cache_info(),
            "plan": result.as_dict(),
            "extra": extra or {},
        }
        self._baseline = payload
        (self.base / "baseline.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    def get_baseline(self) -> dict[str, Any] | None:
        """Load the baseline, but never serve one from a different context."""
        if self._baseline is None:
            self._baseline = self._read_baseline_file()
        if not self._baseline:
            return None
        if not self._context_matches(self._baseline.get("context")):
            self._baseline = None
            return None
        return self._baseline

    # ------------------------------------------------------ current working plan
    def set_working(self, meals: list[planner_mod.MealPlan],
                    targets: planner_mod.Targets | None = None,
                    *, mode: str = "single") -> dict[str, Any]:
        """Remember exactly what the user is looking at, for „quote this plan“."""
        cache = self.ensure_cache()
        self._working = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "context": self._fingerprint(),
            "signature": self.plan_signature(cache, meals),
            "meals": [m.as_dict() for m in meals],
            "mode": mode,
            "targets": (targets or self._fallback_targets()).as_dict(),
        }
        return self._working

    @staticmethod
    def _fallback_targets() -> planner_mod.Targets:
        return planner_mod.Targets(protein_min=0.0, budget=None)

    def working_signature(self) -> str:
        if not self._working:
            return ""
        if not self._context_matches(self._working.get("context")):
            return ""
        meals = [self._meal_from_dict(d) for d in self._working.get("meals", [])]
        return self.plan_signature(self.ensure_cache(), meals)

    @staticmethod
    def _meal_from_dict(payload: dict[str, Any]) -> planner_mod.MealPlan:
        meal = payload.get("meal")
        return planner_mod.MealPlan(
            meal=meal,
            items=Service._items_from_dicts(payload.get("items") or [], meal),
            nutrition=payload.get("nutrition") or {},
            unknown=payload.get("unknown") or [],
            price_ref=float(payload.get("price_ref") or 0.0),
            is_estimate=bool(payload.get("is_estimate")),
            quote=payload.get("quote"),
        )

    def compose_payload(self, meal_plan: planner_mod.MealPlan | None,
                        targets: planner_mod.Targets,
                        *, quotes: dict[str, dict[str, Any]] | None = None,
                        quote_attempted: bool = False,
                        signature: str | None = None) -> dict[str, Any]:
        """One payload builder for plan / recompute / quote-current."""
        cache = self.ensure_cache()
        quotes = quotes or {}
        meals = [meal_plan] if meal_plan is not None else []
        for plan in meals:
            plan.quote = quotes.get(plan.meal)

        items = [i for m in meals for i in m.items]
        totals, unknown = planner_mod.sum_nutrition(items)
        reference = planner_mod.sum_reference_cost(items) or 0.0
        quote = (quotes.get(meal_plan.meal) if meal_plan is not None else None) or {}
        quote_ok = quote.get("status") == "ok" and quote.get("amount") is not None
        if quote_ok:
            effective = round(float(quote["amount"]), 2)
            money_basis = "quoted"
        else:
            effective = round(reference, 2)
            money_basis = "reference"

        feasible = bool(meals) and planner_mod.is_feasible(totals, effective, targets)
        vio = planner_mod.violations(totals, effective, targets)
        protein = totals.get("protein")
        per_yuan = round(float(protein) / effective, 1) if (protein is not None and effective > 0) else None
        signature = signature or self.plan_signature(cache, meals)

        note = ""
        if quote_attempted and not quote_ok and quote:
            note = "试算未成功，金额与判定按菜单参考价；请以门店实际价格为准。"
        elif not feasible:
            note = "当前方案不满足全部硬约束，不视为达标。"
        return {
            "feasible": feasible,
            "meals": [m.as_dict() for m in meals],
            "items": [i.as_dict() for i in items],
            "totals": totals,
            "unknowns": unknown,
            "is_estimate": any(i.nutrition_basis == "estimate" for i in items),
            "cost": reference,
            "effective_cost": effective,
            "money_basis": money_basis,
            "budget_status": ("no_budget" if targets.budget is None
                              else ("not_evaluated" if quote_attempted and not quote_ok
                                    else money_basis)),
            "protein_per_yuan": per_yuan,
            "violations": vio,
            "targets": targets.as_dict(),
            "signature": signature,
            "quotes": {m.meal: m.quote for m in meals if m.quote},
            "quotes_valid": bool(quote_attempted),
            "cache": self.cache_info(),
            "note": note,
        }

    def quote_working(self, signature: str) -> tuple[dict[str, Any], int]:
        """Quote the plan the user is looking at — never re-runs the solver."""
        if not self._working:
            return {"error": "当前没有可报价的方案，请先生成方案", "kind": "no_plan"}, 409
        if not signature:
            return {"error": "缺少方案签名，拒绝为未知方案报价", "kind": "missing_signature"}, 400
        if not self._context_matches(self._working.get("context")):
            self._working = None
            return {"error": "门店/日期/时段已变化，旧方案已失效，请重新规划",
                    "kind": "stale_context"}, 409
        meals = [self._meal_from_dict(d) for d in self._working.get("meals", [])]
        current = self.working_signature()
        if not current or signature != current:
            return {"error": "方案已改变，报价请求与当前方案不一致，请重新报价",
                    "kind": "stale_signature", "expected": current}, 409
        quotes = {m.meal: self.quote_meal(m) for m in meals}
        raw_targets = self._working.get("targets")
        targets = (planner_mod.Targets(**raw_targets) if raw_targets
                   else self._fallback_targets())
        payload = self.compose_payload(meals[0] if meals else None, targets, quotes=quotes,
                                       quote_attempted=True, signature=current)
        payload["mode"] = self._working.get("mode", "single")
        if meals:
            payload["meal"] = meals[0].meal
            payload["meal_label"] = MEAL_LABEL.get(meals[0].meal, meals[0].meal)
        self.set_working(meals, targets, mode=payload["mode"])
        return payload, 200

    @staticmethod
    def _items_from_dicts(items: list[dict[str, Any]], meal: str) -> list[planner_mod.Item]:
        """Rebuild items from a payload; purchase counts must be integers."""
        out = []
        for i in items:
            raw = i.get("quantity", 1)
            label = f"「{i.get('name') or i.get('code')}」的待买数量"
            qty = quantity_mod.purchase_quantity(raw, label)
            out.append(planner_mod.Item(
                code=i.get("code"), name=i.get("name"), kind=i.get("kind", "other"),
                nutrition=i.get("nutrition"), nutrition_status=i.get("nutrition_status", ""),
                nutrition_note=i.get("nutrition_note", ""), price_ref=i.get("price_ref"),
                quantity=qty, meal=meal, modification=i.get("modification") or [],
                nutrition_basis=i.get("nutrition_basis") or "official",
                removal_notes=list(i.get("removal_notes") or []),
            ))
        return out

    def recompute(self, quantities: dict[str, dict[str, float]] | None = None,
                  customizations: dict[str, dict[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
        """Rebuild the plan from the immutable baseline. Never mutates the baseline."""
        baseline = self.get_baseline()
        if not baseline:
            status = self.baseline_status()
            return {"error": status.get("reason") or "尚无基线方案，请先规划",
                    "kind": "stale_baseline" if status.get("exists") else "no_baseline"}
        base = baseline["plan"]
        quantities = quantities or {}
        customizations = customizations or {}

        # Reject unknown actions *before* any rebuilding.
        for meal, rules in customizations.items():
            if not isinstance(rules, dict):
                raise UnsupportedCustomization(
                    f"{meal} 的定制必须是「商品代码: 规则」对象")
            for code, rule in rules.items():
                if not isinstance(rule, dict):
                    raise UnsupportedCustomization(f"商品 {code} 的定制规则必须是对象")
                action = rule.get("action")
                if action not in SUPPORTED_CUSTOMIZATIONS:
                    raise UnsupportedCustomization(
                        f"不支持的定制动作「{action}」。本版本只支持 "
                        f"{'、'.join(SUPPORTED_CUSTOMIZATIONS)}"
                        "（suggest_remove = 点单建议：只写点单参数，不改营养数值）；"
                        "不支持在标准款之外追加配料。",
                        action=str(action) if action is not None else None,
                    )

        # Validate every incoming quantity *before* touching the plan.
        requested: dict[str, dict[str, int]] = {}
        for meal, entries in quantities.items():
            if entries in (None, {}):
                continue
            if not isinstance(entries, dict):
                raise quantity_mod.InvalidQuantity(
                    f"{meal} 的数量必须是「商品代码: 数量」对象")
            for code, raw in entries.items():
                requested.setdefault(meal, {})[str(code)] = quantity_mod.purchase_quantity(
                    raw, f"「{meal}」中商品 {code} 的待买数量")

        meals: list[planner_mod.MealPlan] = []
        issues: list[dict[str, Any]] = []
        for meal_dict in base["meals"]:
            meal = meal_dict["meal"]
            items = self._items_from_dicts(meal_dict["items"], meal)
            known_codes = {str(i.code) for i in items}
            for code in requested.get(meal, {}):
                if code not in known_codes:
                    issues.append({"meal": meal, "code": code,
                                   "reason": "该商品不在当前方案中"})
            out_items: list[planner_mod.Item] = []
            for item in items:
                qty = requested.get(meal, {}).get(str(item.code))
                if qty is not None:
                    item.quantity = qty

                rule = (customizations.get(meal) or {}).get(str(item.code))
                if rule and rule.get("action") == "suggest_remove":
                    wanted = [str(c) for c in (rule.get("option_codes") or []) if str(c)]
                    if not wanted:
                        raise UnsupportedCustomization(
                            "suggest_remove 至少需要一个 option_code", action="suggest_remove")
                    cap = self.removable_options(item.code, meal)
                    by_code = {str(o["code"]): o for o in cap.get("options", [])}
                    missing = [c for c in wanted if c not in by_code]
                    if missing:
                        raise UnsupportedCustomization(
                            f"「{item.name}」不支持这些去除项：{'、'.join(missing)}；"
                            f"可选：{'、'.join(by_code) or '无'}",
                            action="suggest_remove")
                    selected = [by_code[c] for c in wanted]
                    # §3.5: send the whole group — removed items with
                    # unselectedKey, the rest with their selectedKey.
                    values: list[dict[str, Any]] = []
                    for group in cap.get("groups", []):
                        for value in group.get("values", []):
                            key = (value["unselectedKey"] if str(value["code"]) in wanted
                                   else value["selectedKey"])
                            values.append({"code": value["code"], "key": key, "quantity": 1})
                    item.modification = values
                    # strict mode: the number stays the official standard value
                    item.nutrition_basis = "standard_unadjusted"
                    notes: list[str] = []
                    for option in selected:
                        notes.append(
                            f"建议去掉{option['name']}。去掉后的营养官方未提供，此处显示标准版数值。")
                        if option.get("protein_risk"):
                            notes.append(
                                f"去掉{option['name']}后，蛋白质可能低于显示值。")
                    item.removal_notes = notes
                    item.removals = [{"component": o["name"], "component_code": o["code"],
                                      "scenario": "standard_unadjusted"} for o in selected]
                out_items.append(item)

            nutrition, unknown = planner_mod.sum_nutrition(out_items)
            meals.append(planner_mod.MealPlan(
                meal=meal, items=out_items, nutrition=nutrition, unknown=unknown,
                price_ref=planner_mod.sum_reference_cost(out_items) or 0.0,
            ))

        extra = baseline.get("extra") or {}
        targets = (planner_mod.Targets(**extra["targets"]) if extra.get("targets")
                   else self._fallback_targets())
        payload = self.compose_payload(meals[0] if meals else None, targets)
        payload["mode"] = extra.get("mode", "single")
        payload["baseline_created_at"] = baseline.get("created_at")
        payload["plan_version"] = baseline.get("plan_version")
        payload["issues"] = issues
        if meals:
            payload["meal"] = meals[0].meal
            payload["meal_label"] = MEAL_LABEL.get(meals[0].meal, meals[0].meal)
        self.set_working(meals, targets, mode=payload["mode"])
        return payload
