#!/usr/bin/env python3
"""按最终的套餐目录与快照数据改写 README 的首屏数字。

用法：
    python3 scripts/update_readme_numbers.py --catalog data/combo_catalog.json \
        --snapshot docs/demo/snapshot.json

只改「先看结果」区块与「实测发现」第 8 条 / 「已知限制」里跟数据挂钩的句子，
其余内容一字不动。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def scene_from_snapshot(snapshot: dict, meal: str, protein: int, budget: int) -> dict:
    """用网页求解器同源的逻辑算出场景结果（离线，直接读快照）。"""
    import os
    import subprocess
    script = f"""
const fs=require("fs");
const snap=JSON.parse(fs.readFileSync({json.dumps(str(ROOT / 'docs/demo/snapshot.json'))},"utf8"));
const S=require({json.dumps(str(ROOT / 'web/solver.js'))});
const r=S.solve(snap,{{meal:{json.dumps(meal)},protein_min:{protein},budget:{budget},
  protein_tolerance:3,max_mains:2}});
console.log(JSON.stringify({{
  status:r.protein_status, protein:r.totals.protein, cost:r.cost,
  items:r.items.map(i=>i.name),
  saver:r.saver?{{cost:r.saver.cost,protein:r.saver.protein,items:r.saver.items.map(i=>i.name)}}:null,
  reach:r.frontier&&r.frontier.reach_price?{{cost:r.frontier.reach_price.cost,
    items:r.frontier.reach_price.items.map(i=>typeof i==="string"?i:i.name),
    protein:r.frontier.reach_price.protein}}:null,
  maxp:r.frontier&&r.frontier.max_protein?{{name:r.frontier.max_protein.name,
    cost:r.frontier.max_protein.cost,protein:r.frontier.max_protein.protein}}:null,
}}));
"""
    node = os.environ.get("NODE_BIN") or "node"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         cwd=str(ROOT))
    if out.returncode != 0:
        raise SystemExit(f"求解失败：{out.stderr[:400]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(ROOT / "data/combo_catalog.json"))
    ap.add_argument("--snapshot", default=str(ROOT / "docs/demo/snapshot.json"))
    ap.add_argument("--date", default="")
    ap.add_argument("--node", default="node")
    args = ap.parse_args()

    catalog = load(Path(args.catalog))
    snapshot = load(Path(args.snapshot))
    date = args.date or catalog["context"]["date"]

    totals = {"sku": 0, "combos": 0, "variants": 0, "singles": 0, "excluded": 0}
    for meal in catalog["meals"].values():
        totals["sku"] += meal.get("checked") or 0
        totals["combos"] += meal.get("combo_count") or 0
        totals["variants"] += len(meal.get("variants") or [])
        totals["singles"] += len(meal.get("single_round") or [])
        totals["excluded"] += len(meal.get("excluded") or [])

    s44 = scene_from_snapshot(snapshot, "lunch", 44, 45)
    s35 = scene_from_snapshot(snapshot, "lunch", 35, 25)
    sb = scene_from_snapshot(snapshot, "breakfast", 25, 20)

    text = README.read_text("utf-8")

    # ---- 1. 首屏抬头：写明数据日期
    text = re.sub(
        r"麦当劳珠海屏湾一路餐厅（门店 `1440054`），[^：\n]*的实时菜单与试算价[^：\n]*：",
        f"麦当劳珠海屏湾一路餐厅（门店 `1440054`），{date} 的实时菜单与试算价"
        f"（套餐目录生成于 {catalog.get('built_at', '')[:16].replace('T', ' ')}）：",
        text, count=1)

    # ---- 2. 场景表格
    saver_items = " + ".join(s35.get("items") or [])
    rows = []
    rows.append(f"| 44 g / 45 元 | {' + '.join(s44['items'])} | **{s44['protein']:.0f} g** | "
                f"**{s44['cost']:.2f} 元** |")
    if s44.get("saver"):
        sv = s44["saver"]
        rows.append(f"| 同上，想更省 | {' + '.join(sv['items'])}（省钱选项，比目标少 "
                    f"{44 - sv['protein']:.0f} g，在 3 g 容差内） | {sv['protein']:.0f} g | "
                    f"{sv['cost']:.2f} 元 |")
    if s35.get("maxp"):
        mp = s35["maxp"]
        rows.append(f"| 35 g / 25 元 | 预算内最多：{mp['name']} ×1 | "
                    f"{mp['protein']:.0f} g（差 {35 - mp['protein']:.0f} g） | "
                    f"{mp['cost']:.2f} 元 |")
    if s35.get("reach"):
        rc = s35["reach"]
        rows.append(f"| 同上，加到 {rc['cost']:.2f} 元 | {' + '.join(rc['items'])} | "
                    f"{rc['protein']:.0f} g | {rc['cost']:.2f} 元 |")
    new_table = "\n".join(rows)
    text = re.sub(r"(\| 你的目标 \| 算出来的点法 \| 蛋白质 \| 价格 \|\n\| --- \| --- \| --- \| --- \|\n)"
                  r"(?:\|.*\n)+",
                  lambda m: m.group(1) + new_table + "\n", text, count=1)

    # ---- 3. 「实测发现」第 8 条：按用户给的措辞，N 与日期用实际值
    saved = 45.0 - s44["cost"]
    text = re.sub(
        r"8\. \*\*核查了.*?\n",
        f"8. **核查了全部套餐**（以 {date} 菜单为准，共 {totals['combos']} 个），"
        f"全部用 `calculate-price` 真实试算。"
        f"把套餐和单品放在一起组合后，午餐 44 g / 45 元能省 {saved:.2f} 元。\n",
        text, count=1)

    README.write_text(text, "utf-8")
    print(json.dumps({
        "date": date,
        "totals": totals,
        "scene_44_45": s44,
        "scene_35_25": s35,
        "scene_breakfast": sb,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
