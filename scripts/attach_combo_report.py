#!/usr/bin/env python3
"""Copy the combo catalog's per-meal counts into the static demo snapshot.

``docs/demo/snapshot.json`` is what GitHub Pages serves; the page's
「套餐核查」 row quotes the numbers it finds there. Those numbers must come from
``data/combo_catalog.json`` — not from counting the variants that survived,
which is how the row ended up saying "识别 17 个套餐 … 排除 0 个" for lunch while
the catalog says 50 combos and 45 exclusions.

A full ``scripts/export_demo_snapshot.py`` run writes ``comboReport`` itself.
Use this script when only the catalog was rebuilt and the snapshot's menudata
must stay untouched.

Usage:
    python3 scripts/attach_combo_report.py [--snapshot ...] [--catalog ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import combos as combos_mod  # noqa: E402

MEALS = ("breakfast", "lunch", "dinner")


def menu_codes(block: dict) -> set[str]:
    return {str(i.get("code")) for key in ("mains", "drinks", "sides")
            for i in block.get(key) or []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(ROOT / "docs/demo/snapshot.json"))
    parser.add_argument("--catalog", default=str(ROOT / "data/combo_catalog.json"))
    args = parser.parse_args()

    snap_path, cat_path = Path(args.snapshot), Path(args.catalog)
    snap = json.loads(snap_path.read_text("utf-8"))
    catalog = json.loads(cat_path.read_text("utf-8"))
    ctx = catalog.get("context") or {}
    meta = snap.get("meta") or {}
    store = meta.get("store") or {}

    # The catalog is only valid for the store / date / meal times it was built
    # for; refusing here beats publishing numbers for another day's menu.
    if (str(ctx.get("store_code")) != str(store.get("code"))
            or ctx.get("date") != meta.get("date")
            or ctx.get("meal_times") != meta.get("meal_times")):
        print("catalog and snapshot describe a different menu — rebuild the snapshot",
              file=sys.stderr)
        return 2

    meals = snap.get("meals") or {}
    lunch_codes, dinner_codes = menu_codes(meals.get("lunch") or {}), menu_codes(meals.get("dinner") or {})
    for meal in MEALS:
        block = meals.get(meal)
        if block is None:
            continue
        entry = (catalog.get("meals") or {}).get(meal)
        if not entry and meal == "dinner" and lunch_codes and lunch_codes == dinner_codes:
            # same SKU set as lunch (documented in README) → lunch's catalog holds
            entry = (catalog.get("meals") or {}).get("lunch")
        if not entry:
            print(f"{meal}: no catalog entry, leaving the snapshot alone", file=sys.stderr)
            continue
        report = combos_mod.report_from_meal(
            entry, source=catalog.get("source"), built_at=catalog.get("built_at"), limit=200)
        kept = len(block.get("comboVariants") or [])
        if kept != report["variants_used"]:
            print(f"{meal}: snapshot has {kept} variants but the catalog has "
                  f"{report['variants_used']} usable ones — rebuild the snapshot",
                  file=sys.stderr)
            return 3
        block["comboReport"] = report
        reasons = "、".join(f"{k} {v}" for k, v in report["reasons"].items()) or "—"
        print(f"{meal}: 已查 {report['checked']} 个 SKU，识别 {report['combos_found']} 个套餐，"
              f"纳入 {report['variants_used']} 个变体；排除 {report['excluded']} 个（{reasons}）")

    snap_path.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"wrote {snap_path} — {snap_path.stat().st_size/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
