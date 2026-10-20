"""Live user-story acceptance for 麦当劳增肌计算器 (Zhuhai 1440054).

Usage: python3 tests/stories_live.py [base_url]

The server must run against the *live* MCP provider and an isolated data dir:

    MAI_MAI_DATA=/tmp/mai-stories python3 src/server.py --port 8780

Every story writes its raw responses to ``data/fixtures/stories/`` so the doc
can quote numbers without re-running anything.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8780"


def future_day() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")


DAY = os.environ.get("MAI_TEST_DATE") or future_day()
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fixtures" / "stories"
RESULTS: list[tuple[str, bool, str]] = []
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path: str, payload=None, method="GET") -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with OPENER.open(req, timeout=240) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ""))


def save(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def show(payload: dict) -> str:
    return " + ".join(f"{i['name']}×{int(i['quantity'])}" for i in payload.get("items", []))


def combos(land: dict) -> list[tuple[float, float, float, str]]:
    """1..N main-only combos, same structure rule as the solver."""
    items = [i for i in land["items"] if i["price"] and i["protein"] is not None]
    out = []
    for size in range(1, int(land.get("max_mains") or 2) + 1):
        for pick in itertools.combinations_with_replacement(items, size):
            out.append((round(sum(i["price"] for i in pick), 2),
                        round(sum(i["protein"] for i in pick), 1),
                        round(sum(i["fat"] or 0 for i in pick), 1),
                        " + ".join(i["name"] for i in pick)))
    return out


# ---------------------------------------------------------------- story 1
def story1() -> None:
    body = {"meal": "lunch", "protein_min": 35, "budget": 25, "max_mains": 2}
    status, plan = call("/api/meal", body, "POST")
    status_land, land = call("/api/meal/landscape?meal=lunch&max_mains=2")
    save("story1_plan", plan)
    save("story1_landscape", land)

    rows = combos(land)
    under = [r for r in rows if r[0] <= 25]
    best = max(under, key=lambda r: (r[1], -r[0]))
    reach = min([r for r in rows if r[1] >= 35], key=lambda r: (r[0], r[2]))

    check("故事1 返回 200", status == 200, str(status))
    check("故事1 无解", plan.get("feasible") is False, show(plan))
    check("故事1 主卡片不超 25 元", bool(plan.get("items")) and plan["cost"] <= 25,
          f"¥{plan.get('cost')}")
    check("故事1 主卡片=预算内蛋白质最高",
          abs(plan["totals"]["protein"] - best[1]) < 0.05
          and abs(plan["cost"] - best[0]) < 0.05,
          f"接口 {plan['totals']['protein']}g/¥{plan['cost']} vs 枚举 {best[1]}g/¥{best[0]}（{best[3]}）")

    frontier = plan.get("frontier") or {}
    cap = frontier.get("max_protein") or {}
    check("故事1 缺口=目标-实际",
          cap.get("protein_gap") == round(35 - plan["totals"]["protein"], 1),
          f"gap {cap.get('protein_gap')}")

    reach_price = frontier.get("reach_price") or {}
    check("故事1 放宽价格与 landscape 一致",
          abs((reach_price.get("cost") or -1) - reach[0]) < 0.01
          and abs((reach_price.get("protein") or -1) - reach[1]) < 0.05,
          f"接口 ¥{reach_price.get('cost')}/{reach_price.get('protein')}g vs 枚举 ¥{reach[0]}/{reach[1]}g（{reach[3]}）")

    steps = [p for p in land.get("frontier", []) if p["price"] <= 39.0]
    check("故事1 39 元档在 landscape 阶梯上",
          bool(steps) and abs(steps[-1]["protein"] - reach[1]) < 0.05,
          str(steps[-1] if steps else None))


# ---------------------------------------------------------------- story 2
def story2() -> None:
    # 男 28 / 178 cm / 80 kg / 3 餐 / 增肌 → 80 × 2.0 ÷ 3 = 53.33 → 53
    body = {"meal": "dinner", "protein_min": 53, "budget": 45, "max_mains": 2}
    status, plan = call("/api/meal", body, "POST")
    _, land = call("/api/meal/landscape?meal=dinner&max_mains=2")
    save("story2_plan", plan)
    save("story2_landscape", land)

    hint = [p for p in land.get("frontier", []) if p["price"] <= 45]
    cap = hint[-1] if hint else None
    rows = combos(land)
    best = max([r for r in rows if r[0] <= 45], key=lambda r: (r[1], -r[0]))

    check("故事2 晚餐 P≥53 / ¥≤45 有解",
          status == 200 and plan.get("feasible") is True, show(plan))
    check("故事2 达标且不超价",
          plan["totals"]["protein"] >= 53 and plan["cost"] <= 45,
          f"P{plan['totals']['protein']} ¥{plan['cost']}")
    check("故事2 上限提示与枚举一致",
          bool(cap) and abs(cap["protein"] - best[1]) < 0.05,
          f"提示 {cap and cap['protein']}g vs 枚举 {best[1]}g（{best[3]}）")
    check("故事2 提示上限不低于预填目标（因此有解）",
          bool(cap) and cap["protein"] >= 53, str(cap and cap["protein"]))
    check("故事2 热量未被当成约束",
          plan["targets"]["kcal_min"] is None and plan["targets"]["kcal_max"] is None,
          json.dumps(plan["targets"], ensure_ascii=False))

    # same story, two *different* mains: suggestions must cover both
    body2 = {"meal": "lunch", "protein_min": 40, "budget": 45, "max_mains": 2}
    _, two = call("/api/meal", body2, "POST")
    save("story2_two_mains", two)
    targets = {str(s["target_code"]) for s in two.get("suggestions", [])}
    mains = [str(i["code"]) for i in two["items"] if i["kind"] == "main"]
    remo = (two.get("removables") or {})
    with_options = {c for c in mains if (remo.get(c) or {}).get("options")}
    check("故事2 每个主食都查了可去项",
          set(remo.keys()) == set(mains), f"mains={mains} removables={sorted(remo)}")
    check("故事2 点单建议只挂在卡片上的主食",
          targets <= set(mains), f"targets={sorted(targets)} mains={mains}")
    check("故事2 两个可去主食时建议覆盖两个",
          len(with_options) < 2 or targets == with_options,
          f"有可去项的主食 {sorted(with_options)} / 建议来源 {sorted(targets)}")


# ---------------------------------------------------------------- story 3
def story3() -> None:
    body = {"meal": "breakfast", "protein_min": 25, "budget": 20, "max_mains": 2}
    status, plan = call("/api/meal", body, "POST")
    _, land = call("/api/meal/landscape?meal=breakfast&max_mains=2")
    save("story3_plan", plan)
    save("story3_landscape", land)

    breakfast_codes = {str(i["code"]) for i in land["items"]}
    # every *main* on the card must be sold at breakfast (the landscape is the
    # breakfast main pool, so an unknown code would mean a wrong menu)
    mains = [i for i in plan.get("items", []) if i.get("kind") == "main"]
    check("故事3 只出现早餐主食",
          all(str(m["code"]) in breakfast_codes for m in mains),
          str([(m["name"], m["code"]) for m in mains]))
    check("故事3 结论与金额/营养一致",
          plan.get("feasible") is True and plan["totals"]["protein"] >= 25
          and plan["cost"] <= 20,
          f"feasible={plan.get('feasible')} P{plan.get('totals', {}).get('protein')} ¥{plan.get('cost')}")

    removable = plan.get("removable") or {}
    check("故事3 特调查询用早餐时间",
          removable.get("meal") == "breakfast", str(removable.get("meal")))
    check("故事3 特调没有 60001",
          removable.get("ok") is True and "60001" not in json.dumps(removable, ensure_ascii=False),
          str(removable.get("reason") or "ok"))

    _, quote = call("/api/quote", {"signature": plan["signature"]}, "POST")
    save("story3_quote", quote)
    q = quote["quotes"]["breakfast"]
    check("故事3 报价使用早餐时点",
          str(q.get("reservation_date", "")).endswith("08:15"), str(q.get("reservation_date")))
    check("故事3 试算成功且金额=参考价",
          q.get("status") == "ok" and abs(q["amount"] - plan["cost"]) < 0.01,
          f"{q.get('amount')} / {plan.get('cost')}")


# ---------------------------------------------------------------- story 4
def story4() -> None:
    body = {"meal": "lunch", "protein_min": 40, "budget": 45, "fat_max": 30,
            "max_mains": 2}
    status, plan = call("/api/meal", body, "POST")
    _, land = call("/api/meal/landscape?meal=lunch&max_mains=2")
    save("story4_plan", plan)

    rows = combos(land)
    capped = [r for r in rows if r[0] <= 45 and r[2] <= 30]
    best = max(capped, key=lambda r: (r[1], -r[0]))
    # only fat may be relaxed here: price still has to hold
    reach = [r for r in rows if r[1] >= 40 and r[0] <= 45]
    min_fat = min(reach, key=lambda r: (r[2], r[0]))

    check("故事4 无解（脂肪卡住）", plan.get("feasible") is False, show(plan))
    check("故事4 主卡片脂肪 ≤30", plan["totals"].get("fat", 99) <= 30,
          f"F{plan['totals'].get('fat')}")
    check("故事4 主卡片=预算与脂肪内蛋白质最高",
          abs(plan["totals"]["protein"] - best[1]) < 0.05
          and abs(plan["cost"] - best[0]) < 0.05,
          f"{plan['totals']['protein']}g/¥{plan['cost']} vs {best[1]}g/¥{best[0]}（{best[3]}）")

    frontier = plan.get("frontier") or {}
    relax = frontier.get("relax_fat") or {}
    check("故事4 放宽脂肪到枚举最小值",
          abs((relax.get("fat") or -1) - min_fat[2]) < 0.05,
          f"接口 {relax.get('fat')}g vs 枚举 {min_fat[2]}g（{min_fat[3]}）")
    check("故事4 只放宽价格达不到目标",
          frontier.get("reach_price") is None, str(frontier.get("reach_price")))

    main = next(i for i in plan["items"] if i["kind"] == "main")
    sugg = [s for s in plan.get("suggestions", []) if str(s["target_code"]) == str(main["code"])]
    cheese = next((s for s in sugg if "吉士" in s["name"] or "芝士" in s["name"]), None)
    check("故事4 芝士项标注增肌不建议去掉",
          bool(cheese) and cheese.get("muscle_warning") is True
          and cheese.get("protein_risk") is True,
          str(cheese and cheese.get("name")))

    before = dict(plan["totals"])
    _, after = call("/api/recompute", {"customizations": {"lunch": {
        str(main["code"]): {"action": "suggest_remove",
                            "option_codes": [cheese["code"]]}}}}, "POST")
    save("story4_removed", after)
    check("故事4 去芝士后营养不变",
          all(after["totals"].get(k) == v for k, v in before.items()),
          f"{before} → {after['totals']}")
    notes = after["items"][0].get("removal_notes") or []
    check("故事4 严格模式文案",
          any("标准版数值" in n for n in notes), json.dumps(notes, ensure_ascii=False))
    check("故事4 蛋白质风险文案",
          any("蛋白质可能低于显示值" in n for n in notes),
          json.dumps(notes, ensure_ascii=False))


# --------------------------------------------------------------- boundaries
def boundaries() -> None:
    bad = [
        ("蛋白 0", {"meal": "lunch", "protein_min": 0, "budget": 45}),
        ("价格 0", {"meal": "lunch", "protein_min": 30, "budget": 0}),
        ("蛋白 -5", {"meal": "lunch", "protein_min": -5, "budget": 45}),
        ("价格 -1", {"meal": "lunch", "protein_min": 30, "budget": -1}),
        ("蛋白非数字", {"meal": "lunch", "protein_min": "abc", "budget": 45}),
        ("价格非数字", {"meal": "lunch", "protein_min": 30, "budget": "四十五"}),
        ("价格为空", {"meal": "lunch", "protein_min": 30}),
        ("主食上限 0", {"meal": "lunch", "protein_min": 30, "budget": 45, "max_mains": 0}),
        ("主食上限 4", {"meal": "lunch", "protein_min": 30, "budget": 45, "max_mains": 4}),
    ]
    responses = {}
    for label, body in bad:
        status, payload = call("/api/meal", body, "POST")
        responses[label] = {"status": status, "body": payload}
        check(f"边界 {label} → 400 且中文提示",
              status == 400 and any("\u4e00" <= ch <= "\u9fff" for ch in payload.get("error", "")),
              f"{status} {payload.get('error')}")
    save("boundary_invalid", responses)

    status, big = call("/api/meal", {"meal": "lunch", "protein_min": 50,
                                     "budget": 500, "max_mains": 2}, "POST")
    save("boundary_budget_500", big)
    check("边界 价格 500 → 200 且不超价",
          status == 200 and big.get("cost", 999) <= 500,
          f"¥{big.get('cost')} P{big.get('totals', {}).get('protein')}")


def main() -> int:
    status, state = call("/api/settings", {
        "store": {"code": "1440054"},
        "date": DAY,
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    }, "POST")
    check("门店切换为珠海 1440054", status == 200, str(state.get("state", {}).get("store")))

    status, refreshed = call("/api/refresh", {}, "POST")
    check("刷新菜单成功", status == 200, str(refreshed))
    check("数据来源为实时", refreshed.get("source") == "live", str(refreshed.get("source")))
    _, boot = call("/api/bootstrap")
    check("bootstrap 来源实时", (boot.get("cache") or {}).get("source") == "live",
          str((boot.get("cache") or {}).get("source")))
    check("正常时段无菜单错位提示",
          not (boot.get("cache") or {}).get("menu_warnings"),
          json.dumps((boot.get("cache") or {}).get("menu_warnings"), ensure_ascii=False))

    story1()
    story2()
    story3()
    story4()
    boundaries()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
