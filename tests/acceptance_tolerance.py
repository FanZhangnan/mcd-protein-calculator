"""Live acceptance for the server-side protein tolerance (Zhuhai 1440054).

Usage: python3 tests/acceptance_tolerance.py [base_url] [date]

The date defaults to *tomorrow* in China, because lunch/breakfast of the current
day become un-orderable (600053) once their slot has passed. Raw responses are
written to ``data/fixtures/tolerance/``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8792"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fixtures" / "tolerance"
RESULTS: list[tuple[str, bool, str]] = []
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def china_tomorrow() -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")


DAY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MAI_TEST_DATE") or china_tomorrow()


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


def combo(payload: dict) -> str:
    return " + ".join(f"{i['name']}×{int(i['quantity'])}" for i in payload.get("items", []))


def main() -> int:
    status, state = call("/api/settings", {
        "store": {"code": "1440054"}, "date": DAY,
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    }, "POST")
    check("设置门店与日期", status == 200, f"{DAY}")
    status, refreshed = call("/api/refresh", {}, "POST")
    check("刷新菜单", status == 200, str(refreshed.get("cache_key")))
    check("数据来源为实时", refreshed.get("source") == "live", str(refreshed.get("source")))

    # ---------------------------------------------------------------- T1
    status, t1 = call("/api/meal", {"meal": "lunch", "protein_min": 44,
                                    "budget": 45}, "POST")
    save("t1_strict_with_saver", t1)
    check("T1 主方案严格达标", status == 200 and t1.get("protein_status") == "met",
          f"{t1.get('protein_status')} {combo(t1)}")
    check("T1 主方案为两层吉士 54 g / 45 元",
          t1.get("totals", {}).get("protein") == 54.0 and t1.get("cost") == 45.0,
          f"P{t1.get('totals', {}).get('protein')} ¥{t1.get('cost')}")
    saver = t1.get("saver") or {}
    check("T1 省钱选项为 42 g / 39 元",
          saver.get("protein") == 42.0 and saver.get("cost") == 39.0,
          f"{saver.get('items')} P{saver.get('protein')} ¥{saver.get('cost')}")
    check("T1 省钱选项写明少 12 g、省 6 元",
          saver.get("protein_less") == 12.0 and saver.get("money_saved") == 6.0,
          f"less {saver.get('protein_less')} saved {saver.get('money_saved')}")

    # ---------------------------------------------------------------- T2
    status, t2 = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                    "budget": 35}, "POST")
    save("t2_under", t2)
    check("T2 原目标与 37 g 都无解", t2.get("protein_status") == "under",
          str(t2.get("protein_status")))
    check("T2 主卡为预算内最多", 0 < (t2.get("totals", {}).get("protein") or 0) < 40
          and (t2.get("cost") or 99) <= 35,
          f"P{t2.get('totals', {}).get('protein')} ¥{t2.get('cost')}")
    check("T2 放宽方案按 40 g 计算（缺口 6 g）",
          ((t2.get("frontier") or {}).get("max_protein") or {}).get("protein_gap") == 6.0,
          str(((t2.get("frontier") or {}).get("max_protein") or {}).get("protein_gap")))
    reach = (t2.get("frontier") or {}).get("reach_price") or {}
    check("T2 放宽价格仍能到 40 g",
          (reach.get("protein") or 0) >= 40.0, str(reach.get("protein")))

    # ---------------------------------------------------------------- T3
    status, t3 = call("/api/meal", {"meal": "lunch", "protein_min": 44,
                                    "budget": 41, "protein_tolerance": 0}, "POST")
    save("t3_tolerance_zero", t3)
    check("T3 tolerance=0 时原目标无解", t3.get("protein_status") == "under",
          str(t3.get("protein_status")))
    check("T3 tolerance=0 不返回省钱选项", t3.get("saver") is None, str(t3.get("saver")))

    status, t3n = call("/api/meal", {"meal": "lunch", "protein_min": 44,
                                     "budget": 41, "protein_tolerance": 3}, "POST")
    save("t3_near", t3n)
    check("T3 放宽 3 g 后接近达标", t3n.get("protein_status") == "near",
          str(t3n.get("protein_status")))
    check("T3 接近达标显示真实 42 g（不是 44）",
          t3n.get("totals", {}).get("protein") == 42.0 and t3n.get("protein_gap") == 2.0,
          f"P{t3n.get('totals', {}).get('protein')} gap {t3n.get('protein_gap')}")

    # ---------------------------------------------------------------- T4
    status, t4a = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                     "budget": 45}, "POST")
    status, t4b = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                     "budget": 45, "protein_tolerance": 0}, "POST")
    save("t4_tolerance_3", t4a)
    save("t4_tolerance_0", t4b)
    same = ([(i["code"], i["quantity"]) for i in t4a.get("items", [])]
            == [(i["code"], i["quantity"]) for i in t4b.get("items", [])]
            and t4a.get("cost") == t4b.get("cost")
            and t4a.get("totals") == t4b.get("totals"))
    check("T4 tolerance=0 与改动前主方案一致", same,
          f"{t4a.get('cost')} vs {t4b.get('cost')}")

    # ---------------------------------------------------------------- saver
    status, sv = call("/api/meal", {"meal": "lunch", "protein_min": 44,
                                    "budget": 45, "select": "saver"}, "POST")
    save("t5_select_saver", sv)
    check("选择省钱选项后成为主方案",
          sv.get("selected") == "saver" and sv.get("cost") == 39.0
          and sv.get("protein_status") == "near",
          f"{sv.get('cost')} {sv.get('protein_status')}")
    check("省钱方案不冒充达标", sv.get("protein_gap") == 2.0, str(sv.get("protein_gap")))

    # ---------------------------------------------------------------- T5
    for label, value in [("负", -1), ("超范围", 11), ("非数字", "abc")]:
        status, bad = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                         "budget": 45, "protein_tolerance": value}, "POST")
        check(f"T5 {label}容差 400", status == 400, f"{status} {bad.get('error')}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
