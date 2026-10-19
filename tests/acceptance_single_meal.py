"""Single-meal HTTP acceptance (real MCP, isolated data dir).

Run:
    MAI_MAI_DATA=/tmp/mai-single-accept python3 src/server.py --port 8773
    python3 tests/acceptance_single_meal.py http://127.0.0.1:8773

Every request/response pair is written to ``data/fixtures/single_meal/`` (no
token, no personal data) so the report can cite the raw payloads.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fixtures" / "single_meal"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8773"

RESULTS: list[tuple[str, bool, str]] = []


def call(path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ""))


def save(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------- case 1: lunch 30/20/30
    st, c1 = call("/api/meal", {"meal": "lunch", "protein_min": 30,
                                "fat_max": 20, "budget": 30}, "POST")
    save("case1_lunch_30_20_30", c1)
    check("用例1 接口 200", st == 200, str(c1.get("error", "")))
    check("用例1 结构锁定：恰好 1 个主食、数量 1",
          len([i for i in c1["meals"][0]["items"] if i["kind"] == "main"]) == 1
          and all(i["quantity"] == 1 for i in c1["meals"][0]["items"]))
    check("用例1 无解时给出可达上限", c1["feasible"] is False
          and (c1.get("frontier") or {}).get("max_protein") is not None,
          json.dumps((c1.get("frontier") or {}).get("max_protein"), ensure_ascii=False)[:120])

    # ---------------------------------------------------- case 2: lunch 40/20/35
    st, c2 = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                "fat_max": 20, "budget": 35}, "POST")
    save("case2_lunch_40_20_35", c2)
    f2 = c2.get("frontier") or {}
    check("用例2 无解且明确说达不到 40 g", c2["feasible"] is False
          and f2.get("reachable") is False and "达不到" in (f2.get("message") or ""),
          f2.get("message", ""))
    check("用例2 可达上限不超脂肪/价格",
          f2["max_protein"]["fat"] <= 20 and f2["max_protein"]["cost"] <= 35,
          f'{f2["max_protein"]["fat"]}g / {f2["max_protein"]["cost"]}y')

    # ---------------------------------------------------- case 3: breakfast 25/15/20
    st, c3 = call("/api/meal", {"meal": "breakfast", "protein_min": 25,
                                "fat_max": 15, "budget": 20}, "POST")
    save("case3_breakfast_25_15_20", c3)
    check("用例3 接口 200", st == 200, str(c3.get("error", "")))
    check("用例3 无解但给出放宽方案",
          c3["feasible"] is False and (c3.get("frontier") or {}).get("relax_fat") is not None,
          json.dumps((c3.get("frontier") or {}).get("relax_fat"), ensure_ascii=False)[:120])

    # bug 2: 514700 only exists at breakfast
    st, rem_bf = call("/api/removable?code=514700&meal=breakfast")
    save("bug2_514700_breakfast", rem_bf)
    check("bug2 514700 早餐特调可查", st == 200 and rem_bf.get("ok") is True
          and rem_bf.get("meal") == "breakfast", str(rem_bf.get("reason")))
    st, rem_lunch = call("/api/removable?code=514700&meal=lunch")
    save("bug2_514700_lunch", rem_lunch)
    check("bug2 514700 午餐不可查（证明餐次隔离）",
          st == 200 and rem_lunch.get("ok") is False, str(rem_lunch.get("reason"))[:60])

    # ---------------------------------------------------- case 4: cheese removal
    st, c4 = call("/api/meal", {"meal": "lunch", "protein_min": 30,
                                "fat_max": 60, "budget": 40}, "POST")
    save("case4_base", c4)
    main = next(i for i in c4["meals"][0]["items"] if i["kind"] == "main")
    st, rem = call(f"/api/removable?code={main['code']}&meal=lunch")
    save("case4_removable", rem)
    cheese = next((o for o in rem.get("options", []) if "干酪" in o["name"]), None) \
        or next((o for o in rem.get("options", []) if "芝士" in o["name"]), None)
    check("用例4 找到芝士类去除项", cheese is not None,
          str([o["name"] for o in rem.get("options", [])]))

    if cheese:
        st, rec = call("/api/recompute", {
            "customizations": {"lunch": {main["code"]: {
                "action": "suggest_remove", "option_codes": [cheese["code"]]}}}}, "POST")
        save("case4_recompute", rec)
        item = rec["meals"][0]["items"][0]
        check("用例4 营养与标准版完全相同",
              rec["totals"] == c4["totals"], f'{rec["totals"].get("energyKcal")}')
        check("用例4 nutrition_basis=standard_unadjusted",
              item["nutrition_basis"] == "standard_unadjusted" and item["is_estimate"] is False)
        check("用例4 两条严格文案",
              sum(1 for n in item["removal_notes"] if "标准版数值" in n) >= 1
              and sum(1 for n in item["removal_notes"] if "蛋白质可能低于显示值" in n) >= 1,
              str(item["removal_notes"]))
        st, q4 = call("/api/quote", {"signature": rec["signature"]}, "POST")
        save("case4_quote", q4)
        quote = q4["quotes"]["lunch"]
        mod = quote["requested_items"][0].get("modification", {}).get("values", [])
        check("用例4 报价成功且价差 0", quote["status"] == "ok"
              and abs(quote["amount"] - c4["cost"]) < 0.01,
              f'{quote["amount"]} vs {c4["cost"]}')
        check("用例4 报价请求带 modification", len(mod) >= 1
              and any(m["code"] == cheese["code"] and m["key"] == cheese["unselectedKey"]
                      for m in mod), json.dumps(mod, ensure_ascii=False)[:160])
        check("用例4 报价后营养不变", q4["totals"] == c4["totals"])

    # ---------------------------------------------------- case 5: add_patty -> 400
    st, bad = call("/api/recompute", {"customizations": {"lunch": {
        main["code"]: {"action": "add_patty"}}}}, "POST")
    save("case5_add_patty", {"status": st, "body": bad})
    check("用例5 add_patty 返回 400 unsupported_customization",
          st == 400 and bad.get("kind") == "unsupported_customization",
          f'{st} {bad.get("kind")}')
    check("用例5 说明支持的动作",
          set(bad.get("supported") or []) == {"remove_sauce", "suggest_remove"},
          str(bad.get("supported")))
    check("用例5 文案不出现「加肉饼」",
          "加肉饼" not in (bad.get("error") or ""), bad.get("error", "")[:80])

    # ---------------------------------------------------- bug 1: no budget
    st, nb = call("/api/plan", {"targets": {
        "kcal_min": 1700, "kcal_max": 2000, "protein_min": 85,
        "fat_max": 85, "carb_max": 250, "budget": None}, "quote": True}, "POST")
    save("bug1_no_budget", nb)
    check("bug1 无预算+报价成功 -> no_budget", nb.get("budget_status") == "no_budget",
          str(nb.get("budget_status")))
    check("bug1 不显示预算未评估", not any(v["label"] == "预算" for v in nb.get("violations", [])))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
