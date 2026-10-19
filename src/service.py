"""Service layer: context-keyed cache, planning, quoting, intake, recompute."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

import catalog as catalog_mod
import estimator
import planner as planner_mod
import quantity as quantity_mod
from mcp_client import McpClient, McpError
from store import Store, data_dir

MEALS = planner_mod.MEALS
MEAL_LABEL = planner_mod.MEAL_LABEL

SAUCE_HINTS = ("酱",)
#: strict-mode rule 5 — a removal that may lower the protein number
PROTEIN_RISK_HINTS = ("芝士", "吉士", "干酪", "培根", "蛋")
#: default „point-order suggestion“ list in single-meal mode
SUGGEST_REMOVE_HINTS = ("酱", "芝士", "吉士", "干酪", "培根", "黄油")
SUPPORTED_CUSTOMIZATIONS = ("remove_sauce", "suggest_remove")
SINGLE_DRINK_HINTS = ("零度可乐", "美式", "纯牛奶")
SINGLE_DRINK_MAX_KCAL = 20.0


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
            {"storeCode": store_code, "orderType": order_type, "beType": be_type, "reservationDate": when},
        )

    def nutrition(self) -> dict:
        return self.client.call("list-nutrition-foods")

    def detail(self, store_code: str, be_type: int, order_type: int, code: str,
               when: str | None = None) -> dict:
        payload = {"storeCode": store_code, "orderType": order_type, "beType": be_type, "code": code}
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
            return self._load("menu_breakfast_0815")
        if meal <= "16:00":
            return self._load("menu_lunch_1215")
        return self._load("menu_dinner_1830")

    def nutrition(self) -> dict:
        return self._load("nutrition")

    def detail(self, store_code: str, be_type: int, order_type: int, code: str,
               when: str | None = None) -> dict:
        return self._load(f"combo_{code}_detail")

    def price(self, store_code: str, be_type: int, order_type: int,
              items: list[dict], need_tableware: bool = False,
              when: str | None = None) -> dict:
        self.price_calls.append({"items": items, "when": when})
        code = str(items[0]["productCode"]) if items else ""
        mapping = {
            "9900005462": "price_combo_5462_default",
            "9900005456": "price_combo_default",
        }
        name = mapping.get(code)
        if not name:
            raise McpError(f"offline: no price fixture for {code}", stage="offline:price")
        return self._load(name)


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
        :meth:`get_baseline` refuses to serve it once the context changed, so it
        can never be resurrected by a later read.

        ``_working`` is kept on purpose: :meth:`quote_working` needs it to tell
        the user *why* the quote was refused (context changed vs. never planned)
        instead of silently reporting "no plan".
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
        avoid = [a for a in (self.store.get_state().get("prefs", {}).get("avoid") or []) if a]

        buckets: dict[str, list[planner_mod.Item]] = {"main": [], "side": [], "drink": [], "other": []}
        for code, info in meals_map.items():
            name = (info or {}).get("name") or ""
            if not name or catalog_mod.is_combo_name(name):
                continue
            if any(word in name for word in avoid):
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

    # ----------------------------------------------------------- sauce / removal
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

        Returns ``(payload, error)``. The cache key contains store / date / meal
        time on purpose: the same SKU can be on sale at 08:15 and gone at 12:15
        (514700 is the canonical example), so a lunch key must never answer a
        breakfast question.
        """
        ctx = self.context()
        when = self._when_for(meal)
        key = (f"{kind}:{ctx.store_code}:{ctx.be_type}:{ctx.order_type}:"
               f"{ctx.day}:{when}:{code}")
        if key in self._detail_cache:
            cached = self._detail_cache[key]
            # older cache entries stored the *processed* capability, not the raw
            # detail payload; ignore them instead of mis-reading `data`
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

    def sauce_capability(self, code: str, meal: str | None = None) -> dict[str, Any]:
        """Real, per-SKU check that the POS exposes a removable sauce option.

        ``meal`` selects the reservation time; when omitted it is inferred from
        the pools so a breakfast SKU is never queried at 12:15.
        """
        meal = meal if meal in MEALS else self._meal_for_code(code)
        payload, err = self._detail(code, meal, "sauce")
        if err:
            return err
        data = payload.get("data") or {}
        options = []
        for group in (data.get("modification") or {}).get("items", []) or []:
            for value in group.get("values", []) or []:
                name = value.get("name") or ""
                if any(h in name for h in SAUCE_HINTS):
                    options.append({
                        "code": value.get("code"), "name": name,
                        "selectedKey": value.get("selectedKey"),
                        "unselectedKey": value.get("unselectedKey"),
                        "price": value.get("price", 0),
                        "scenarios": estimator.scenarios_for(name),
                    })
        return {
            "ok": bool(options),
            "sku": {"code": data.get("code"), "name": data.get("name")},
            "supportModify": data.get("supportModify"),
            "options": options,
            "reason": "" if options else "该 SKU 的特调项中没有可去除的酱料",
            "meal": meal,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

    def removable_options(self, code: str, meal: str | None = None) -> dict[str, Any]:
        """Every removable component the POS exposes for one SKU.

        Unlike :meth:`sauce_capability` this is not limited to sauces: cheese,
        bacon, butter and tomato slices are returned too. Each option carries
        the raw keys so a quote can send the whole group back (§3.5).
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
                    "protein_risk": any(h in name for h in PROTEIN_RISK_HINTS),
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

    # -------------------------------------------------------------------- targets
    def build_targets(self, override: dict[str, Any] | None = None) -> planner_mod.Targets:
        raw = dict(self.store.get_state().get("targets") or {})
        if override:
            raw.update({k: v for k, v in override.items() if v is not None})
        return planner_mod.Targets.from_dict(raw)

    # ----------------------------------------------------------------------- plan
    def targets_after(self, targets: planner_mod.Targets,
                      locked: dict[str, list[planner_mod.Item]],
                      day: str | None = None) -> planner_mod.Targets:
        """Remaining targets handed to the solver — the *only* subtraction point.

        Uses :meth:`remaining_targets` so the displayed remainder and the solved
        remainder can never diverge.
        """
        day = day or self.ensure_cache()["date"]
        remaining = self.remaining_targets(targets, day, locked=locked)["remaining"]

        def value(key: str, fallback: float | None, floor_zero: bool = False) -> float | None:
            current = remaining.get(key)
            if current is None:
                return fallback
            return max(0.0, current) if floor_zero else current

        return planner_mod.Targets(
            kcal_min=value("kcal_min", targets.kcal_min, True) or 0.0,
            kcal_max=value("kcal_max", targets.kcal_max, True) or 0.0,
            protein_min=value("protein_min", targets.protein_min, True) or 0.0,
            fat_min=value("fat_min", targets.fat_min),
            fat_max=value("fat_max", targets.fat_max),
            carb_min=value("carb_min", targets.carb_min),
            carb_max=value("carb_max", targets.carb_max),
            budget=value("budget", targets.budget),
        )

    def _locked_meal_plans(self, locked: dict[str, list[planner_mod.Item]],
                           day: str | None = None) -> list[planner_mod.MealPlan]:
        records = {r.get("meal"): r for r in self.store.list_intake(day or self.ensure_cache()["date"])}
        out = []
        for meal, items in locked.items():
            nutrition, unknown = planner_mod.sum_nutrition(items)
            record = records.get(meal, {})
            out.append(planner_mod.MealPlan(
                meal=meal, items=items, nutrition=nutrition, unknown=unknown,
                price_ref=planner_mod.sum_reference_cost(items) or 0.0,
                amount_actual=record.get("amount_actual"),
                amount_source=record.get("amount_source", "unknown"),
                is_estimate=any(i.is_estimate for i in items), locked=True,
            ))
        return out

    def plan(self, targets: planner_mod.Targets,
             locked: dict[str, list[planner_mod.Item]] | None = None,
             open_meals: list[str] | None = None) -> planner_mod.PlanResult:
        if locked is None:
            locked = self.locked_items(self.ensure_cache()["date"])
        if locked:
            remaining = self.targets_after(targets, locked)
            open_list = [m for m in MEALS if m not in locked]
            locked_plans = self._locked_meal_plans(locked)
            if not open_list:
                # nothing left to solve: never run the search, judge the day only
                return planner_mod.merge_locked(
                    planner_mod.plan_open({}, remaining, []), locked_plans, targets)
            pools = {meal: self.pools(meal) for meal in open_list}
            open_result = planner_mod.plan_open(pools, remaining, open_list)
            return planner_mod.merge_locked(open_result, locked_plans, targets)

        meals = list(MEALS) if open_meals is None else [m for m in open_meals if m in MEALS]
        if not meals:
            return planner_mod.plan_open({}, targets, [])
        pools = {meal: self.pools(meal) for meal in meals}
        return planner_mod.plan_open(pools, targets, meals)

    # ------------------------------------------------------------------ single meal
    @staticmethod
    def is_single_drink(item: planner_mod.Item) -> bool:
        """Whitelist for the single-meal mode.

        Zero Coke / Americano / plain milk are allowed by name; any other drink
        qualifies only if the official table says it is under 20 kcal.
        """
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
        """One step up the meat ladder, or ``None`` when either side is missing.

        Both SKUs must be on sale in this meal *and* carry official nutrition —
        otherwise the step is hidden rather than computed from stale data.
        """
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
            per_10 = None
            if dp > 0:
                per_10 = {"extra_price": round(dc / dp * 10, 2),
                          "extra_fat": round(df / dp * 10, 1)}
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
                "per_10g_protein": per_10,
                "basis": "official_standard",
            }
        return None

    def plan_meal(self, meal: str, targets: planner_mod.Targets,
                  allow_side: bool = False, *, quote: bool = False) -> dict[str, Any]:
        """One meal, strict mode: exactly one main, quantity 1.

        Reuses the same ``Item`` / ``Targets`` / ``sum_nutrition`` / ``plan_open``
        machinery as the day mode, with a lexicographic ranker and full
        enumeration (the candidate space is a few thousand rows at most).

        Never reads intake and never touches the replan path.
        """
        pools = self.single_pools(meal, allow_side)
        result = planner_mod.plan_open(
            pools, targets, [meal],
            ranker=planner_mod.single_meal_rank,
            full_enumeration=True,
        )
        result.basis = "single_meal"

        meal_plan = result.meals[0] if result.meals else None
        main = next((i for i in (meal_plan.items if meal_plan else []) if i.kind == "main"), None)
        removable: dict[str, Any] = {"ok": False, "options": [], "groups": [], "reason": "没有主食"}
        if main:
            removable = self.removable_options(main.code, meal)
        suggestions = [o for o in removable.get("options", []) if o.get("default_suggestion")]
        upgrade = self.upgrade_for(main.code, meal, pools[meal]) if main else None
        frontier = None
        if not result.feasible:
            frontier = planner_mod.single_meal_frontier(pools, targets, meal)

        quotes: dict[str, dict[str, Any]] = {}
        if quote and meal_plan is not None:
            quotes = {meal: self.quote_meal(meal_plan)}
        payload = self.compose_payload(result.meals, targets, quotes=quotes,
                                       quote_attempted=quote)
        payload.update({
            "mode": "single",
            "meal": meal,
            "meal_label": MEAL_LABEL.get(meal, meal),
            "allow_side": allow_side,
            "strict_mode": True,
            "nutrition_basis": "official_standard",
            "removable": removable,
            "suggestions": suggestions,
            "upgrade": upgrade,
            "frontier": frontier,
            "basis": "single_meal",
            "targets": targets.as_dict(),
        })
        self.set_working(result.meals, targets, mode="single")
        self.set_baseline(result, extra={"mode": "single", "targets": targets.as_dict(),
                                         "meal": meal})
        return payload

    # --------------------------------------------------------------------- intake
    def locked_items(self, day: str, records: list[dict[str, Any]] | None = None) -> dict[str, list[planner_mod.Item]]:
        records = records if records is not None else self.store.list_intake(day)
        out: dict[str, list[planner_mod.Item]] = {}
        for record in records:
            items = []
            for entry in record.get("items", []):
                items.append(planner_mod.Item(
                    code=entry.get("code", ""), name=entry.get("name", ""),
                    kind=entry.get("kind", "other"), nutrition=entry.get("nutrition"),
                    nutrition_status=entry.get("nutrition_status", "recorded"),
                    nutrition_note=entry.get("nutrition_note", "历史记录快照"),
                    price_ref=entry.get("price_ref"),
                    quantity=float(entry.get("quantity", 1) or 0),
                    is_estimate=bool(entry.get("is_estimate")),
                    estimate=entry.get("estimate"), meal=record.get("meal", ""),
                    modification=entry.get("modification"),
                ))
            out[record["meal"]] = items
        return out

    @staticmethod
    def _normalise_items(item_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalise an *intake* line: fractions are fine, junk is not.

        This is the eaten-record path, so ``0.5`` must survive as ``0.5``.
        ``0``, negatives, ``NaN`` and non-numeric strings raise instead of
        silently becoming one serving.
        """
        out = []
        for entry in item_dicts:
            qty = quantity_mod.intake_quantity(
                entry.get("quantity", 1), f"「{entry.get('name') or entry.get('code')}」的已吃数量")
            unit = entry.get("price_ref")
            out.append({
                "code": str(entry.get("code")),
                "name": entry.get("name"),
                "kind": entry.get("kind"),
                "quantity": qty,
                "nutrition": entry.get("nutrition"),
                "nutrition_status": entry.get("nutrition_status"),
                "nutrition_note": entry.get("nutrition_note"),
                "price_ref": None if unit in ("", None) else float(unit),
                "line_total_ref": None if unit in ("", None) else round(float(unit) * qty, 2),
                "is_estimate": bool(entry.get("is_estimate")),
                "estimate": entry.get("estimate"),
                "modification": entry.get("modification") or [],
            })
        return out

    @staticmethod
    def _items_nutrition(items: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        objs = [planner_mod.Item(
            code=i["code"], name=i["name"] or "", kind=i.get("kind") or "other",
            nutrition=i.get("nutrition"), nutrition_status=i.get("nutrition_status") or "recorded",
            price_ref=i.get("price_ref"), quantity=i.get("quantity", 1),
            is_estimate=bool(i.get("is_estimate")),
        ) for i in items]
        return planner_mod.sum_nutrition(objs)

    def record_meal(self, meal: str, item_dicts: list[dict[str, Any]], day: str | None = None,
                    quote: dict[str, Any] | None = None,
                    amount_actual: float | None = None,
                    amount_source: str | None = None) -> dict[str, Any]:
        cache = self.ensure_cache()
        day = day or cache["date"]
        items = self._normalise_items(item_dicts)
        totals, unknown = self._items_nutrition(items)

        if amount_actual is None and quote and quote.get("status") == "ok":
            amount_actual = quote.get("amount")
            amount_source = amount_source or "quoted"
        if amount_actual is None:
            amount_source = "unknown"
        elif not amount_source:
            # money came from a successful trial quote -> say so; otherwise the
            # caller typed it. Never leave the source blank / null.
            adopted = (quote and quote.get("status") == "ok"
                       and abs(float(amount_actual) - float(quote.get("amount") or -1)) < 0.005)
            amount_source = "quoted" if adopted else "manual"

        reference = None
        if all(i["line_total_ref"] is not None for i in items):
            reference = round(sum(i["line_total_ref"] for i in items), 2)

        record = {
            "date": day, "meal": meal, "items": items,
            "totals": totals, "unknown": unknown,
            "is_estimate": any(i["is_estimate"] for i in items),
            "price_status": (quote or {}).get("status", "not_quoted"),
            "amount_actual": amount_actual,
            "amount_source": amount_source,
            "amount_reference": reference,
            "quote": quote or None,
            "scenario": self.store.get_state().get("estimate_scenario"),
            "store_code": cache.get("store_code"),
            "menu_snapshot_at": cache.get("fetched_at"),
            "data_source": cache.get("source"),
        }
        return self.store.add_intake(record)

    def update_record(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        records = self.store.list_intake()
        existing = next((r for r in records if r.get("id") == record_id), None)
        if not existing:
            return None
        updated = dict(existing)
        if "items" in patch and patch["items"] is not None:
            items = self._normalise_items(patch["items"])
            totals, unknown = self._items_nutrition(items)
            updated["items"] = items
            updated["totals"] = totals
            updated["unknown"] = unknown
            updated["is_estimate"] = any(i["is_estimate"] for i in items)
            updated["amount_reference"] = (
                round(sum(i["line_total_ref"] for i in items), 2)
                if all(i["line_total_ref"] is not None for i in items) else None
            )
            if patch.get("amount_actual") is None:
                # quantity changed without a new amount -> the old amount is stale
                updated["amount_actual"] = None
                updated["amount_source"] = "unknown"
            # the old trial quote described a different composition
            updated["quote"] = None
        if "amount_actual" in patch:
            amount = patch["amount_actual"]
            updated["amount_actual"] = None if amount in ("", None) else round(float(amount), 2)
            updated["amount_source"] = "manual" if updated["amount_actual"] is not None else "unknown"
        return self.store.replace_intake(record_id, updated)

    def remaining_targets(self, targets: planner_mod.Targets, day: str | None = None,
                          locked: dict[str, list[planner_mod.Item]] | None = None) -> dict[str, Any]:
        cache = self.ensure_cache()
        day = day or cache["date"]
        records = self.store.list_intake(day)
        locked = locked if locked is not None else self.locked_items(day, records)
        eaten_items = [i for group in locked.values() for i in group]
        eaten_totals, eaten_unknown = planner_mod.sum_nutrition(eaten_items) if eaten_items else ({}, [])

        amount_known = [r.get("amount_actual") for r in records]
        budget_unknown = any(a is None for a in amount_known) and bool(records)
        spent = round(sum(a for a in amount_known if a is not None), 2)

        def minus(target: float | None, key: str, floor_zero: bool = False) -> float | None:
            if target is None:
                return None
            eaten = eaten_totals.get(key)
            if eaten is None:
                return None if eaten_unknown and eaten_items else target
            value = target - eaten
            return max(0.0, value) if floor_zero else value

        remaining = {
            "kcal_min": minus(targets.kcal_min, "energyKcal", True),
            "kcal_max": minus(targets.kcal_max, "energyKcal", True),
            "protein_min": minus(targets.protein_min, "protein", True),
            "fat_min": minus(targets.fat_min, "fat"),
            "fat_max": minus(targets.fat_max, "fat"),
            "carb_min": minus(targets.carb_min, "carbohydrate"),
            "carb_max": minus(targets.carb_max, "carbohydrate"),
            "budget": None if targets.budget is None else (None if budget_unknown else round(targets.budget - spent, 2)),
        }
        return {
            "eaten_totals": eaten_totals,
            "eaten_unknown": eaten_unknown,
            "eaten_cost": spent,
            "budget_unknown": budget_unknown,
            "record_count": len(records),
            "remaining": remaining,
        }

    # -------------------------------------------------------------------- quoting
    @staticmethod
    def plan_signature(cache: dict[str, Any], meals: list[planner_mod.MealPlan]) -> str:
        """Fingerprint of the *user-visible* plan: context + items + qty + options."""
        blob = {
            "store": cache.get("store_code"), "date": cache.get("date"),
            "be": cache.get("be_type"), "order": cache.get("order_type"),
            "times": cache.get("meal_times"),
            "meals": [
                {"meal": m.meal, "locked": bool(m.locked), "items": [
                    {"c": i.code, "q": quantity_mod.signature_quantity(i.quantity),
                     "mod": i.modification or []} for i in m.items
                ]} for m in meals
            ],
        }
        return hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def quote_meal(self, meal_plan: planner_mod.MealPlan) -> dict[str, Any]:
        """Trial-quote one meal. Rejects fractional purchase counts outright.

        The POS sells whole items, so the quantity sent to ``calculate-price``
        is exactly the integer the plan shows. No rounding happens here: that
        mismatch (0.5 shown, 1 charged) is what this guard exists to prevent.
        """
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

    # ------------------------------------------------------------ quote roll-up
    @staticmethod
    def apply_quotes(result: planner_mod.PlanResult, targets: planner_mod.Targets,
                     quotes: dict[str, dict[str, Any]],
                     booked_total: float | None = 0.0) -> dict[str, Any]:
        """Legacy adapter: roll trial quotes into the verdict.

        ``booked_total`` is money already recorded for eaten meals; the day total
        is ``booked + quotes``. Pass ``None`` when a booked amount is unknown —
        the budget then becomes ``not_evaluated`` instead of silently dropping
        the eaten money.

        The product path uses :func:`planner.evaluate_day`; this helper exists for
        callers that only have totals (and for the regression tests).
        """
        failed = [meal for meal, q in quotes.items() if q.get("status") != "ok" or q.get("amount") is None]
        if failed:
            status = "not_evaluated"
            effective: float | None = None
            note = "有餐次报价失败或缺少金额，预算尚未完成评估"
        elif targets.budget is None:
            # a successful quote with no budget set is „no budget“, not
            # „budget not evaluated“ — there is nothing to evaluate against
            status = "no_budget"
            effective = round(sum(float(q["amount"]) for q in quotes.values()), 2)
            note = ""
        elif booked_total is None:
            status = "not_evaluated"
            effective = None
            note = "已吃记录缺少记账金额，完整日预算尚未评估"
        else:
            status = "quoted"
            effective = round(sum(float(q["amount"]) for q in quotes.values()) + float(booked_total), 2)
            note = ""

        if status in ("quoted", "no_budget"):
            vio = planner_mod.violations(result.totals, effective, targets)
            feasible = planner_mod.is_feasible(result.totals, effective, targets)
        else:
            vio = planner_mod.violations(result.totals, None, targets)
            if targets.budget is not None:
                for entry in vio:
                    if entry["constraint"] == "unknown" and entry["label"] == "预算":
                        entry["detail"] = note
                feasible = False
            else:
                feasible = result.feasible
        return {
            "status": status,
            "failed_meals": failed,
            "effective_cost": effective,
            "reference_cost": round(result.cost, 2),
            "feasible": feasible,
            "violations": vio,
            "note": note,
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

    def set_baseline(self, result: planner_mod.PlanResult, extra: dict[str, Any] | None = None) -> None:
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
        """Load the baseline, but never serve one from a different context.

        Clearing only ``self._baseline`` in :meth:`invalidate` would let the next
        read pull the old plan straight back from disk; the context check is what
        actually retires it.
        """
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
                    *, mode: str = "day") -> dict[str, Any]:
        """Remember exactly what the user is looking at, for „quote this plan“."""
        cache = self.ensure_cache()
        self._working = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "context": self._fingerprint(),
            "signature": self.plan_signature(cache, meals),
            "meals": [m.as_dict() for m in meals],
            "mode": mode,
            "targets": (targets or self.build_targets()).as_dict(),
        }
        return self._working

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
            amount_actual=payload.get("amount_actual"),
            amount_source=payload.get("amount_source") or "reference",
            is_estimate=bool(payload.get("is_estimate")),
            locked=bool(payload.get("locked")),
            quote=payload.get("quote"),
        )

    def compose_payload(self, meals: list[planner_mod.MealPlan], targets: planner_mod.Targets,
                        *, quotes: dict[str, dict[str, Any]] | None = None,
                        quote_attempted: bool = False,
                        signature: str | None = None) -> dict[str, Any]:
        """One payload builder for plan / recompute / replan / quote-current."""
        cache = self.ensure_cache()
        quotes = quotes or {}
        for meal_plan in meals:
            if not meal_plan.locked:
                meal_plan.quote = quotes.get(meal_plan.meal)

        evaluation = planner_mod.evaluate_day(meals, targets, quotes, quote_attempted)
        signature = signature or self.plan_signature(cache, meals)
        payload = {
            "feasible": evaluation["feasible"],
            "meals": [m.as_dict() for m in meals],
            "totals": evaluation["totals"],
            "unknowns": evaluation["unknown"],
            "is_estimate": evaluation["is_estimate"],
            "cost": evaluation["day_cost"],
            "reference_total": evaluation["reference_total"],
            "open_reference_total": evaluation["open_reference_total"],
            "booked_total": evaluation["booked_total"],
            "booked_known": evaluation["booked_known"],
            "quote_total": evaluation["quote_total"],
            "budget_status": evaluation["budget_status"],
            "money_basis": evaluation["money_basis"],
            "violations": evaluation["violations"],
            "summary": {
                "gaps": evaluation["gaps"],
                "budget": targets.budget,
                "cost": evaluation["day_cost"],
                "reference_total": evaluation["reference_total"],
                "quote_total": evaluation["quote_total"],
                "booked_total": evaluation["booked_total"],
            },
            "targets": targets.as_dict(),
            "signature": signature,
            "quotes": {m.meal: m.quote for m in meals if m.quote},
            "quotes_valid": bool(evaluation["quote_attempted"]),
            "cache": self.cache_info(),
            "note": "",
        }
        if not evaluation["feasible"]:
            payload["note"] = ("当前方案不满足全部硬约束（含未评估项），"
                              "不视为达标，也不代表全局无解。")
        return payload

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
        quotes = {m.meal: self.quote_meal(m) for m in meals if not m.locked}
        raw_targets = self._working.get("targets")
        targets = (planner_mod.Targets(**raw_targets) if raw_targets
                   else self.build_targets())
        payload = self.compose_payload(meals, targets, quotes=quotes,
                                       quote_attempted=True, signature=current)
        payload["mode"] = self._working.get("mode", "day")
        self.set_working(meals, targets, mode=payload["mode"])
        return payload, 200

    @staticmethod
    def _items_from_dicts(items: list[dict[str, Any]], meal: str,
                          *, quantity_mode: str = "recorded") -> list[planner_mod.Item]:
        """Rebuild items from a payload.

        ``quantity_mode="recorded"`` keeps fractions (history / locked meals),
        ``"purchase"`` demands a positive integer (what will be bought).
        A missing key means one serving; an explicit empty value is rejected.
        """
        out = []
        for i in items:
            raw = i.get("quantity", 1)
            label = f"「{i.get('name') or i.get('code')}」的{'待买' if quantity_mode == 'purchase' else '已吃'}数量"
            qty = (quantity_mod.purchase_quantity(raw, label)
                   if quantity_mode == "purchase"
                   else quantity_mod.intake_quantity(raw, label))
            out.append(planner_mod.Item(
                code=i.get("code"), name=i.get("name"), kind=i.get("kind", "other"),
                nutrition=i.get("nutrition"), nutrition_status=i.get("nutrition_status", ""),
                nutrition_note=i.get("nutrition_note", ""), price_ref=i.get("price_ref"),
                quantity=qty, is_estimate=bool(i.get("is_estimate")),
                estimate=i.get("estimate"), meal=meal, modification=i.get("modification") or [],
                can_remove_sauce=bool(i.get("can_remove_sauce")),
                nutrition_basis=i.get("nutrition_basis") or "official",
                removal_notes=list(i.get("removal_notes") or []),
            ))
        return out

    def recompute(self, quantities: dict[str, dict[str, float]] | None = None,
                  customizations: dict[str, dict[str, dict[str, Any]]] | None = None,
                  scenario: str = "official_only") -> dict[str, Any]:
        """Rebuild the plan from the immutable baseline. Never mutates the baseline."""
        baseline = self.get_baseline()
        if not baseline:
            status = self.baseline_status()
            return {"error": status.get("reason") or "尚无基线方案，请先规划",
                    "kind": "stale_baseline" if status.get("exists") else "no_baseline"}
        base = baseline["plan"]
        quantities = quantities or {}
        customizations = customizations or {}

        # Reject unknown actions *before* any rebuilding. Silently ignoring
        # `add_patty` used to return 200 with an unchanged plan, which looks like
        # the customisation was applied.
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
                        "（remove_sauce = 去酱估算，suggest_remove = 单餐点单建议）；"
                        "不支持在标准款之外追加配料。",
                        action=str(action) if action is not None else None,
                    )

        # Validate every incoming quantity *before* touching the plan. A rejected
        # request must leave the working snapshot / baseline / signature intact
        # and must never reach calculate-price.
        locked_meals = {m["meal"] for m in base["meals"] if m.get("locked")}
        requested: dict[str, dict[str, int]] = {}
        for meal, entries in quantities.items():
            if entries in (None, {}):
                continue
            if not isinstance(entries, dict):
                raise quantity_mod.InvalidQuantity(
                    f"{meal} 的数量必须是「商品代码: 数量」对象")
            for code, raw in entries.items():
                if meal in locked_meals:
                    # eaten quantities may be fractional; they are edited in the
                    # intake record, not here
                    quantity_mod.intake_quantity(raw, f"已吃餐次「{meal}」的数量")
                    continue
                requested.setdefault(meal, {})[str(code)] = quantity_mod.purchase_quantity(
                    raw, f"「{meal}」中商品 {code} 的待买数量")

        meals: list[planner_mod.MealPlan] = []
        issues: list[dict[str, Any]] = []
        for meal_dict in base["meals"]:
            meal = meal_dict["meal"]
            locked = bool(meal_dict.get("locked"))
            items = self._items_from_dicts(meal_dict["items"], meal)
            out_items: list[planner_mod.Item] = []
            for item in items:
                qty = requested.get(meal, {}).get(str(item.code))
                if qty is not None and not locked:
                    item.quantity = qty
                elif qty is None and locked and str(item.code) in (quantities.get(meal) or {}):
                    issues.append({"meal": meal, "code": item.code,
                                   "reason": "已锁定餐次的数量请在「已吃记录」中修改"})

                rule = (customizations.get(meal) or {}).get(str(item.code))
                if rule and rule.get("action") == "remove_sauce":
                    if locked:
                        issues.append({"meal": meal, "code": item.code,
                                       "reason": "已锁定餐次不可再定制"})
                        out_items.append(item)
                        continue
                    cap = self.sauce_capability(item.code, meal)
                    option = next((o for o in cap.get("options", [])
                                   if o.get("code") == str(rule.get("option_code") or o.get("code"))), None)
                    if not cap.get("ok") or not option:
                        issues.append({"meal": meal, "code": item.code,
                                       "reason": cap.get("reason") or "该 SKU 无可用酱料特调项"})
                        out_items.append(item)
                        continue
                    scenario_key = rule.get("scenario") or scenario
                    if scenario_key not in (option.get("scenarios") or []):
                        issues.append({"meal": meal, "code": item.code,
                                       "reason": f"情景 {scenario_key} 不适用于酱料「{option['name']}」"})
                        out_items.append(item)
                        continue
                    est = estimator.estimate_remove_sauce(item.nutrition or {}, scenario_key)
                    item.nutrition = est["result"]
                    item.nutrition_status = "estimate"
                    item.nutrition_note = est["display"]
                    item.is_estimate = True
                    item.estimate = {
                        "action": "remove_sauce",
                        "component": option["name"],
                        "scenario": est["meta"],
                        "removed": est["removed"],
                        "unknown": est["unknown"],
                        "display": est["display"],
                    }
                    item.removals = [{"component": option["name"], "component_code": option["code"],
                                      "scenario": scenario_key}]
                    item.modification = [
                        {"code": option["code"], "key": option["unselectedKey"], "quantity": 1}
                    ]
                elif rule and rule.get("action") == "suggest_remove":
                    if locked:
                        issues.append({"meal": meal, "code": item.code,
                                       "reason": "已锁定餐次不可再定制"})
                        out_items.append(item)
                        continue
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
                    item.is_estimate = False
                out_items.append(item)

            nutrition, unknown = planner_mod.sum_nutrition(out_items)
            meals.append(planner_mod.MealPlan(
                meal=meal, items=out_items, nutrition=nutrition, unknown=unknown,
                price_ref=planner_mod.sum_reference_cost(out_items) or 0.0,
                amount_actual=meal_dict.get("amount_actual"),
                amount_source=meal_dict.get("amount_source", "reference"),
                is_estimate=any(i.is_estimate for i in out_items),
                locked=locked,
            ))

        extra = baseline.get("extra") or {}
        mode = extra.get("mode") or "day"
        if mode == "single" and extra.get("targets"):
            # the baseline was produced by /api/meal — judge against that meal's
            # targets, not the day targets
            targets = planner_mod.Targets(**extra["targets"])
        else:
            targets = self.build_targets()
        # same builder as plan / replan / quote-current -> no口径 drift
        payload = self.compose_payload(meals, targets)
        payload["mode"] = mode
        payload["baseline_created_at"] = baseline.get("created_at")
        payload["plan_version"] = baseline.get("plan_version")
        payload["issues"] = issues
        payload["open_targets"] = self._open_targets(targets, meals).as_dict()
        self.set_working(meals, targets, mode=mode)
        return payload

    def _open_targets(self, targets: planner_mod.Targets,
                      meals: list[planner_mod.MealPlan]) -> planner_mod.Targets:
        locked = [i for m in meals if m.locked for i in m.items]
        if not locked:
            return targets
        eaten, _ = planner_mod.sum_nutrition(locked)
        locked_meals = [m for m in meals if m.locked]
        money_known = all(m.amount_actual is not None for m in locked_meals)
        spent = sum(m.amount_actual for m in locked_meals if m.amount_actual is not None)
        return planner_mod.Targets(
            kcal_min=max(0.0, targets.kcal_min - (eaten.get("energyKcal") or 0)),
            kcal_max=max(0.0, targets.kcal_max - (eaten.get("energyKcal") or 0)),
            protein_min=max(0.0, targets.protein_min - (eaten.get("protein") or 0)),
            fat_min=None if targets.fat_min is None else targets.fat_min - (eaten.get("fat") or 0),
            fat_max=None if targets.fat_max is None else targets.fat_max - (eaten.get("fat") or 0),
            carb_min=None if targets.carb_min is None else targets.carb_min - (eaten.get("carbohydrate") or 0),
            carb_max=None if targets.carb_max is None else targets.carb_max - (eaten.get("carbohydrate") or 0),
            budget=None if (targets.budget is None or not money_known) else round(targets.budget - spent, 2),
        )

    # ---------------------------------------------------------------- combo swap
    def combo_swap(self, combo_code: str, round_id: int, replace_with: str) -> dict[str, Any]:
        cache = self.ensure_cache()
        cat = self.catalog()
        try:
            detail = self.provider.detail(cache["store_code"], cache["be_type"], cache["order_type"], combo_code)
        except McpError as exc:
            return {"ok": False, "stage": "detail", "message": str(exc)}
        if not detail.get("success"):
            return {"ok": False, "stage": "detail", "code": detail.get("code"),
                    "message": detail.get("message")}
        data = detail.get("data") or {}
        rounds = data.get("rounds") or []
        target_round = next((r for r in rounds if r.get("id") == round_id), None)
        if not target_round:
            return {"ok": False, "stage": "detail", "message": f"套餐无 round {round_id}"}
        default_choice = next((c for c in target_round["choices"] if c.get("isDefault")), None)
        swap_choice = next((c for c in target_round["choices"] if c.get("code") == replace_with), None)
        if not swap_choice:
            return {"ok": False, "stage": "choice",
                    "message": f"套餐未提供 {replace_with} 作为换配选项",
                    "available": [{"code": c["code"], "name": c["name"], "diffPrice": c.get("diffPrice")}
                                  for c in target_round["choices"]]}

        def nutr(code: str, name: str) -> dict[str, Any]:
            match = cat.lookup(code, name)
            return {"code": code, "name": name, "status": match.status,
                    "nutrition": match.nutrition, "note": match.note}

        base_item = nutr(default_choice["code"], default_choice["name"])
        new_item = nutr(swap_choice["code"], swap_choice["name"])
        diff = None
        if base_item["nutrition"] and new_item["nutrition"]:
            diff = {k: None if (base_item["nutrition"].get(k) is None or new_item["nutrition"].get(k) is None)
                    else round(new_item["nutrition"][k] - base_item["nutrition"][k], 1)
                    for k in estimator.NUTRI_KEYS}
        return {
            "ok": True,
            "combo": {"code": data.get("code"), "name": data.get("name")},
            "round": {"id": round_id, "name": target_round.get("name")},
            "default": {"code": default_choice["code"], "name": default_choice["name"],
                        "diffPrice": default_choice.get("diffPrice"), "nutrition": base_item},
            "replacement": {"code": swap_choice["code"], "name": swap_choice["name"],
                            "diffPrice": swap_choice.get("diffPrice"), "nutrition": new_item},
            "nutrition_delta": diff,
            "integrated_in_planner": False,
            "note": "换配目前是独立接口，未接入三餐求解，也不参与全天合计。",
        }
