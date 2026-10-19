"""Live acceptance for the muscle calculator (Zhuhai 1440054).

Usage: python3 tests/acceptance_muscle.py [base_url]

The server must run against a *live* MCP provider and an isolated data dir:

    MAI_MAI_DATA=/tmp/mai-live python3 src/server.py --port 8775

Every case writes its raw response to data/fixtures/muscle/.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8775"
OUT = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "muscle"
RESULTS: list[tuple[str, bool, str]] = []


def call(path: str, payload=None, method="GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ""))


def save(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


def show(payload: dict) -> str:
    return " + ".join(f"{i['name']}×{int(i['quantity'])}" for i in payload.get("items", []))


def main() -> int:
    call("/api/settings", {
        "date": "2026-10-20",
        "store": {"code": "1440054"},
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    }, "POST")

    status, refreshed = call("/api/refresh", {}, "POST")
    check("刷新菜单 200", status == 200, str(status))
    check("数据来源为实时", refreshed.get("source") == "live", str(refreshed.get("source")))
    status, boot = call("/api/bootstrap")
    check("bootstrap 缓存来源实时", (boot.get("cache") or {}).get("source") == "live",
          str((boot.get("cache") or {}).get("source")))

    # ---------------------------------------------------------------- case 1
    case1 = {"meal": "lunch", "protein_min": 40, "budget": 45, "max_mains": 2}
    status, p1 = call("/api/meal", case1, "POST")
    save("case1_lunch_40_45", p1)
    check("用例1 午餐 P≥40 / ¥≤45 有解",
          status == 200 and p1.get("feasible") is True, show(p1))
    check("用例1 蛋白质达标且不超价",
          (p1.get("totals") or {}).get("protein", 0) >= 40 and (p1.get("cost") or 99) <= 45,
          f"P{p1.get('totals', {}).get('protein')} / ¥{p1.get('cost')}")
    mains = [i for i in p1.get("items", []) if i.get("kind") == "main"]
    check("用例1 主食不超过 2 份", sum(i["quantity"] for i in mains) <= 2,
          str([(i["name"], i["quantity"]) for i in mains]))
    check("用例1 给出每元蛋白质",
          isinstance(p1.get("protein_per_yuan"), (int, float)), str(p1.get("protein_per_yuan")))

    # ---------------------------------------------------------------- case 2
    case2 = {"meal": "lunch", "protein_min": 40, "budget": 25, "max_mains": 2}
    status, p2 = call("/api/meal", case2, "POST")
    save("case2_lunch_40_25", p2)
    cap = (p2.get("frontier") or {}).get("max_protein")
    check("用例2 午餐 P≥40 / ¥≤25 无解", status == 200 and p2.get("feasible") is False,
          show(p2))
    check("用例2 主卡片仍在预算内", bool(p2.get("items")) and (p2.get("cost") or 99) <= 25,
          f"¥{p2.get('cost')} / P{(p2.get('totals') or {}).get('protein')}")
    check("用例2 给出蛋白质缺口",
          bool(cap) and cap.get("protein_gap", 0) > 0,
          f"最多 {cap.get('protein')} g，差 {cap.get('protein_gap')} g")

    # ---------------------------------------------------------------- case 3
    case3 = {"meal": "breakfast", "protein_min": 30, "budget": 30, "max_mains": 2}
    status, p3 = call("/api/meal", case3, "POST")
    save("case3_breakfast_30_30", p3)
    check("用例3 早餐 P≥30 / ¥≤30 返回 200", status == 200, show(p3))
    if p3.get("feasible"):
        ok = (p3["totals"]["protein"] >= 30 and p3["cost"] <= 30)
    else:
        ok = p3["cost"] <= 30 and p3["totals"]["protein"] < 30
    check("用例3 结论与金额/营养一致", ok,
          f"feasible={p3.get('feasible')} P{p3.get('totals', {}).get('protein')} ¥{p3.get('cost')}")

    # ---------------------------------------------------------------- case 4
    case4 = {"meal": "lunch", "protein_min": 40, "budget": 45,
             "fat_max": 40, "max_mains": 2}
    status, p4 = call("/api/meal", case4, "POST")
    save("case4_lunch_40_45_fat40", p4)
    check("用例4 午餐 P≥40 / ¥≤45 / F≤40 有解",
          status == 200 and p4.get("feasible") is True, show(p4))
    check("用例4 脂肪不超 40 g",
          (p4.get("totals") or {}).get("fat", 99) <= 40,
          f"F{p4.get('totals', {}).get('fat')}")

    # ---------------------------------------------------------------- copy
    blob = json.dumps([p1, p2, p3, p4], ensure_ascii=False)
    check("接口文案不含禁用词",
          all(word not in blob for word in ("减脂", "瘦", "加肉饼", "全天", "三餐")),
          "checked 5 words")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
