#!/usr/bin/env python3
"""Build the verified combo catalog.

For every SKU on the menu this script asks ``query-meal-detail`` whether the POS
returns rounds. Only that answer decides "combo" — never the product name.

For each combo it builds at most two variants (POS default + muscle), sums the
official nutrition of the selected choices, computes the reference price
(``menu price + Σ diffPrice``) and then asks ``calculate-price`` for the same
``roundList``. A variant whose two prices disagree, or whose nutrition is not
fully official, is excluded and recorded with its reason.

Usage:
    python3 scripts/build_combo_catalog.py [--date 2026-10-22] [--workers 5]
                                          [--meals breakfast,lunch]

Output: data/combo_catalog.json (public menu data only — no token, no account).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import catalog as catalog_mod  # noqa: E402
import combos as combos_mod  # noqa: E402
import planner as planner_mod  # noqa: E402
from mcp_client import McpClient, McpError  # noqa: E402
from service import LiveProvider, Service  # noqa: E402


class Worker:
    """One MCP session per thread; the transport is plain HTTP."""

    def __init__(self) -> None:
        self._local = threading.local()

    def client(self) -> McpClient:
        if not hasattr(self._local, "client"):
            self._local.client = McpClient()
        return self._local.client

    def call(self, tool: str, arguments: dict, tries: int = 3) -> dict:
        last: Exception | None = None
        for attempt in range(tries):
            try:
                return self.client().call(tool, arguments)
            except McpError as exc:
                last = exc
                time.sleep(1.5 + attempt * 2)
        raise last  # type: ignore[misc]


WORKER = Worker()


def category_map(menu_payload: dict) -> tuple[dict[str, list[str]], dict[str, list[str]],
                                             list[str]]:
    data = (menu_payload.get("data") or {})
    by_code: dict[str, list[str]] = {}
    tags: dict[str, list[str]] = {}
    excluded: set[str] = set()
    for cat in data.get("categories") or []:
        name = (cat.get("name") or "").replace("\n", "")
        membership = any(h in name for h in combos_mod.MEMBERSHIP_CATEGORY_HINTS)
        if membership:
            excluded.add(name)
        for entry in cat.get("meals") or []:
            code = str(entry.get("code"))
            by_code.setdefault(code, []).append(name)
            for tag in entry.get("tags") or []:
                tags.setdefault(code, []).append(tag)
    return by_code, tags, sorted(excluded)


def price_variant(combo_code: str, variant: dict, ctx: dict, when: str) -> dict:
    items = [{
        "productCode": str(combo_code), "quantity": 1,
        "roundList": variant["round_list"],
    }]
    try:
        payload = WORKER.call("calculate-price", {
            "storeCode": ctx["store_code"], "orderType": ctx["order_type"],
            "beType": ctx["be_type"], "reservationDate": when, "items": items,
        })
    except McpError as exc:
        return {**variant, "status": "excluded", "reason": "price_failed",
                "detail": f"试算失败：{exc}"}
    if not payload.get("success") or (payload.get("data") or {}).get("price") is None:
        return {**variant, "status": "excluded", "reason": "price_failed",
                "detail": f"试算失败：{payload.get('code')} {payload.get('message')}"}
    quoted = round(float(payload["data"]["price"]) / 100, 2)
    if abs(quoted - float(variant["reference_price"])) > 0.005:
        return {**variant, "status": "excluded", "reason": "price_mismatch",
                "detail": f"参考价 {variant['reference_price']:.2f} / 试算 {quoted:.2f}",
                "quoted_price": quoted}
    return {**variant, "status": "ok", "quoted_price": quoted,
            "quoted_at": payload.get("datetime")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--meals", default="breakfast,lunch")
    parser.add_argument("--out", default=str(ROOT / "data/combo_catalog.json"))
    parser.add_argument("--limit", type=int, default=0, help="debug: only N SKUs per meal")
    args = parser.parse_args()
    meals = [m.strip() for m in args.meals.split(",") if m.strip()]

    import shutil
    import tempfile

    tmp_base = Path(tempfile.mkdtemp(prefix="mai-combo-build-"))
    for name in ("aliases.json", "upgrade_ladder.json"):
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy2(src, tmp_base / name)
    state = {
        "store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅",
                  "beType": 1, "orderType": 1},
        "date": args.date or None,
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    }
    (tmp_base / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")

    service = Service(LiveProvider(), base=tmp_base)
    service.refresh()
    cache = service._cache or {}
    if cache.get("source") != "live":
        print(f"refusing to build from non-live data: {cache.get('source')}", file=sys.stderr)
        return 2
    store = service.store.get_state()["store"]
    meal_times = service.store.get_state()["meal_times"]
    ctx = {
        "store_code": store["code"], "date": cache["date"],
        "be_type": int(store.get("beType", 1)), "order_type": int(store.get("orderType", 1)),
        "meal_times": meal_times,
    }
    nutrition = service.catalog()
    print(f"context {ctx['date']} store {ctx['store_code']} meals {meals}", flush=True)

    out: dict = {
        "version": 1,
        "context": ctx,
        "source": "live",
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "store_name": store.get("name"),
        "meals": {},
    }
    stats = {"sku": 0, "combos": 0, "variants": 0, "excluded": 0, "reasons": {}}

    for meal in meals:
        menu_payload = cache["menus"][meal]
        meals_map: dict[str, dict] = ((menu_payload.get("data") or {}).get("meals") or {})
        by_cat, tags_by_code, excluded_cats = category_map(menu_payload)
        codes = sorted(meals_map)
        if args.limit:
            codes = codes[: args.limit]
        when = f"{ctx['date']} {meal_times[meal]}"
        print(f"\n== {meal}: {len(codes)} SKU, membership categories {excluded_cats}", flush=True)

        def detail_of(code: str) -> tuple[str, dict | None]:
            try:
                payload = WORKER.call("query-meal-detail", {
                    "storeCode": ctx["store_code"], "orderType": ctx["order_type"],
                    "beType": ctx["be_type"], "code": code, "reservationDate": when,
                })
            except McpError as exc:
                print(f"  detail failed {code}: {exc}", file=sys.stderr)
                return code, None
            return code, payload

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            details = dict(pool.map(detail_of, codes))

        combos_found: list[dict] = []
        not_combo: list[str] = []
        for code in codes:
            payload = details.get(code)
            info = meals_map.get(code) or {}
            name = info.get("name") or ""
            if not combos_mod.is_combo_detail(payload):
                not_combo.append(code)
                continue
            rounds = combos_mod.rounds_of(payload)
            combos_found.append({
                "code": code, "name": name,
                "menu_price": float(info.get("currentPrice") or 0),
                "rounds": rounds,
                "categories": by_cat.get(code, []),
                "tags": tags_by_code.get(code, []),
                "info": info,
            })
        stats["sku"] += len(codes)
        stats["combos"] += len(combos_found)
        print(f"  combos (rounds-based): {len(combos_found)} / {len(codes)} SKU", flush=True)

        variants: list[dict] = []
        excluded: list[dict] = []
        priced: list[dict] = []
        for combo in combos_found:
            markers = combos_mod.membership_markers(
                combo["categories"], combo["tags"], combo["info"])
            if markers:
                excluded.append({
                    "combo_code": combo["code"], "combo_name": combo["name"],
                    "reason": "membership", "detail": "；".join(markers),
                    "declared_price": combo["menu_price"],
                })
                continue
            built, dropped = combos_mod.build_variants(combo, nutrition)
            excluded.extend(dropped)
            priced.extend(built)

        def price_of(variant: dict) -> dict:
            return price_variant(variant["combo_code"], variant, ctx, when)

        if priced:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                for result in pool.map(price_of, priced):
                    if result["status"] == "ok":
                        variants.append(result)
                    else:
                        excluded.append(result)

        # de-duplicate: same combo + same positive nutrition + same price = one row
        unique: dict[tuple, dict] = {}
        for variant in variants:
            key = (variant["combo_code"], variant["quoted_price"],
                   tuple(sorted(variant["nutrition"].items())))
            unique.setdefault(key, variant)
        variants = list(unique.values())

        for item in excluded:
            reason = item.get("reason") or "other"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
        stats["variants"] += len(variants)
        stats["excluded"] += len(excluded)
        out["meals"][meal] = {
            "checked": len(codes),
            "combo_count": len(combos_found),
            "no_rounds": not_combo,
            "excluded_categories": excluded_cats,
            "variants": variants,
            "excluded": excluded,
        }
        print(f"  variants kept {len(variants)}, excluded {len(excluded)}", flush=True)

    out["stats"] = stats
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"\nwrote {path} — {path.stat().st_size/1024:.0f} KB")
    print("stats", json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
