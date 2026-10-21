#!/usr/bin/env python3
"""Export a static snapshot for the GitHub Pages demo.

The demo must work without a token and without a server, so every legal
single-meal combo is enumerated here once, from the *live* MCP menu, and the
browser only filters/sorts that list.

Usage:
    python3 scripts/export_demo_snapshot.py [--date 2026-10-21] [--max-mains 3]

Outputs:
    docs/demo/snapshot.json       public: items, combos, landscape, ladder
    tests/fixtures/demo-cache.json  test-only: the raw cache the backend reads
                                    so the parity test solves the same numbers

Nothing here is written to the public snapshot except menu data: no token, no
coupon, no order, no personal data, no absolute path.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import combinations_with_replacement
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from service import LiveProvider, Service  # noqa: E402

MEALS = ("breakfast", "lunch", "dinner")
LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
NUTRI_KEYS = ("energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium")


def item_row(item: planner_mod.Item) -> dict:
    nutrition = item.nutrition or {}
    return {
        "code": str(item.code),
        "name": item.name,
        "price": None if item.price_ref is None else round(float(item.price_ref), 2),
        "nutrition": {k: nutrition.get(k) for k in NUTRI_KEYS},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="planning date (default: MCP server date)")
    parser.add_argument("--max-mains", type=int, default=3)
    parser.add_argument("--out", default=str(ROOT / "docs/demo/snapshot.json"))
    parser.add_argument("--cache-out", default=str(ROOT / "tests/fixtures/demo-cache.json"))
    args = parser.parse_args()

    import shutil
    import tempfile

    tmp_base = Path(tempfile.mkdtemp(prefix="mai-demo-export-"))
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
    info = service.refresh()
    cache = service._cache or {}
    if cache.get("source") != "live":
        print(f"refusing to export non-live data: source={cache.get('source')}", file=sys.stderr)
        return 2

    store = service.store.get_state()["store"]
    meal_times = service.store.get_state()["meal_times"]
    meta = {
        "store": {"code": store["code"], "name": store["name"]},
        "date": cache["date"],
        "meal_times": meal_times,
        "fetched_at": cache["fetched_at"],
        "source": "live",
        "note": "麦当劳官方 MCP 实时菜单与官方营养表的静态快照；价格是抓取时点的菜单参考价。",
    }

    meals: dict[str, dict] = {}
    total_combos = 0
    for meal in MEALS:
        pools = service.single_pools(meal, allow_side=True)[meal]
        mains = [i for i in pools["main"] if i.nutrition and i.price_ref is not None]
        drinks = [i for i in pools["drink"] if i.nutrition and i.price_ref is not None]
        sides = [i for i in pools["side"] if i.nutrition and i.price_ref is not None]

        combos: list[list] = []
        for size in range(1, max(1, args.max_mains) + 1):
            for idx in combinations_with_replacement(range(len(mains)), size):
                picks = [mains[i] for i in idx]
                for side_i in [-1] + list(range(len(sides))):
                    for drink_i in [-1] + list(range(len(drinks))):
                        chosen = list(picks)
                        if side_i >= 0:
                            chosen.append(sides[side_i])
                        if drink_i >= 0:
                            chosen.append(drinks[drink_i])
                        totals, _ = planner_mod.sum_nutrition(chosen)
                        cost = planner_mod.sum_reference_cost(chosen)
                        if totals.get("protein") is None or cost is None:
                            continue
                        padded = list(idx) + [-1] * (3 - size)
                        combos.append([
                            *padded, side_i, drink_i,
                            round(float(cost), 2),
                            round(float(totals.get("protein") or 0.0), 1),
                            round(float(totals.get("fat") or 0.0), 1),
                            round(float(totals.get("energyKcal") or 0.0), 1),
                        ])

        combo_items = service.combo_items(meal)
        combo_variants = [{
            "code": str(i.code), "name": i.name, "variant": i.combo_variant,
            "price": round(float(i.price_ref or 0), 2),
            "menu_price": i.combo_menu_price,
            "nutrition": {k: (i.nutrition or {}).get(k) for k in NUTRI_KEYS},
            "rounds": i.combo_rounds or [],
            "round_list": i.round_list or [],
        } for i in combo_items]

        combo_combos: list[list] = []
        for ci, item in enumerate(combo_items):
            base = [item]
            base_total, _ = planner_mod.sum_nutrition(base)
            combo_combos.append([
                ci, -1, -1,
                round(float(item.price_ref or 0), 2),
                round(float(base_total.get("protein") or 0.0), 1),
                round(float(base_total.get("fat") or 0.0), 1),
                round(float(base_total.get("energyKcal") or 0.0), 1),
            ])
            for size in range(1, max(1, args.max_mains)):
                for idx in combinations_with_replacement(range(len(mains)), size):
                    chosen = base + [mains[i] for i in idx]
                    totals, _ = planner_mod.sum_nutrition(chosen)
                    cost = planner_mod.sum_reference_cost(chosen)
                    if totals.get("protein") is None or cost is None:
                        continue
                    padded = list(idx) + [-1] * (2 - size)
                    combo_combos.append([
                        ci, *padded,
                        round(float(cost), 2),
                        round(float(totals.get("protein") or 0.0), 1),
                        round(float(totals.get("fat") or 0.0), 1),
                        round(float(totals.get("energyKcal") or 0.0), 1),
                    ])

        desserts = [item_row(i) for i in service.pools(meal)["dessert"]
                    if i.nutrition and i.price_ref is not None]

        removable: dict[str, list[str]] = {}
        for main in mains:
            try:
                cap = service.removable_options(main.code, meal)
            except Exception as exc:  # a detail failure must not kill the export
                print(f"  detail failed for {main.code}: {exc}", file=sys.stderr)
                cap = {"options": []}
            names = [o.get("name") for o in cap.get("options") or [] if o.get("name")]
            if names:
                removable[str(main.code)] = names

        rows = []
        for main in mains:
            row = item_row(main)
            row["removable"] = removable.get(str(main.code), [])
            rows.append(row)

        meals[meal] = {
            "label": LABEL[meal],
            "mains": rows,
            "drinks": [item_row(i) for i in drinks],
            "sides": [item_row(i) for i in sides],
            "combos": combos,
            "comboVariants": combo_variants,
            "comboCombos": combo_combos,
            "desserts": desserts,
        }
        total_combos += len(combos) + len(combo_combos)
        print(f"{meal}: {len(mains)} mains, {len(drinks)} drinks, {len(sides)} sides, "
              f"{len(combos)} single combos, {len(combo_items)} combo variants, "
              f"{len(combo_combos)} combo rows")

    snapshot = {
        "meta": meta,
        "meals": meals,
        "landscape": {meal: service.landscape(meal) for meal in MEALS},
        "ladder": service.upgrade_ladder(),
        "rules": {
            "max_mains": args.max_mains,
            "tolerance_default": 3,
            "tolerance_range": [0, 10],
            "drink_whitelist": "纯牛奶 / 零度可乐 / 美式咖啡 / 官方热量 < 20 kcal",
            "side_max": 1,
            "nutrition_basis": "official_standard",
            "combo_rules": ("一餐最多 1 个套餐；套餐计 1 个主食；有套餐时不额外加饮品/配菜；"
                            "套餐营养为各轮官方营养相加，价格必须与真实试算一致。"),
        },
        "counts": {"combos": total_combos},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    out.write_text(text, "utf-8")
    size = out.stat().st_size
    print(f"wrote {out} — {size/1024/1024:.2f} MB, {total_combos} combos")
    if size > 2 * 1024 * 1024:
        print("WARNING: snapshot exceeds 2 MB", file=sys.stderr)

    cache_out = Path(args.cache_out)
    cache_out.parent.mkdir(parents=True, exist_ok=True)
    trimmed = {
        key: cache.get(key) for key in
        ("cache_key", "store_code", "date", "be_type", "order_type", "meal_times",
         "menus", "nutrition", "fetched_at", "source")
    }
    cache_out.write_text(json.dumps(trimmed, ensure_ascii=False), "utf-8")
    print(f"wrote {cache_out} — {cache_out.stat().st_size/1024:.0f} KB (test only)")
    print("exported at", datetime.now().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
