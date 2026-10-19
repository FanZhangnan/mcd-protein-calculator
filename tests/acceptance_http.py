"""HTTP acceptance for the single-meal build.

Usage: python3 tests/acceptance_http.py [base_url]

The server must run with an isolated data directory, e.g.

    MAI_MAI_DATA=/tmp/mai-accept python3 src/server.py --offline --port 8771

so the real user state is never touched.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8771"
RESULTS: list[tuple[str, bool, str]] = []


def call(path: str, payload=None, method="GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"raw": body}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} · {name}" + (f" · {detail}" if detail else ""))


def main() -> int:
    status, boot = call("/api/bootstrap")
    check("bootstrap 200", status == 200, str(status))
    state = boot.get("state", {})
    check("门店为珠海 1440054", state.get("store", {}).get("code") == "1440054",
          str(state.get("store")))
    check("来源标注不是冒充",
          boot.get("provider_source") in ("live", "offline")
          and boot.get("cache", {}).get("source") in ("live", "cache", "offline"),
          f"provider={boot.get('provider_source')} cache={boot.get('cache', {}).get('source')}")
    check("营养表已加载", (boot.get("catalog") or {}).get("rows", 0) > 0,
          str(boot.get("catalog")))

    # ------------------------------------------------------------- settings
    status, saved = call("/api/settings", {
        "date": "2026-10-20",
        "store": {"code": "1440054"},
        "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    }, "POST")
    check("保存门店/日期/时段 200", status == 200, str(status))
    status, bad_time = call("/api/settings", {"meal_times": {"lunch": "25:99"}}, "POST")
    check("非法时间被拒 400", status == 400, str(bad_time.get("error")))
    status, empty_store = call("/api/settings", {"store": {"code": ""}}, "POST")
    check("空门店代码被拒 400", status == 400, str(empty_store.get("error")))

    status, refreshed = call("/api/refresh", {}, "POST")
    check("刷新菜单 200", status == 200,
          f"source={refreshed.get('source')} key={refreshed.get('cache_key')}")

    # -------------------------------------------------------------- solving
    body = {"meal": "lunch", "protein_min": 20, "fat_max": 20, "budget": 30}
    status, plan = call("/api/meal", body, "POST")
    check("午餐求解 200", status == 200, str(status))
    items = plan.get("items", [])
    mains = [i for i in items if i.get("kind") == "main"]
    totals = plan.get("totals") or {}
    check("结构：恰好 1 个主食、数量 1",
          len(mains) == 1 and mains[0].get("quantity") == 1,
          str([(i["name"], i["quantity"]) for i in items]))
    check("价格不超上限", (plan.get("cost") or 99) <= 30, str(plan.get("cost")))
    check("脂肪不超上限", (totals.get("fat") or 99) <= 20, str(totals.get("fat")))
    check("达到蛋白质下限", (totals.get("protein") or 0) >= 20, str(totals.get("protein")))
    check("严格模式标记", plan.get("nutrition_basis") == "official_standard"
          and plan.get("is_estimate") is False, str(plan.get("nutrition_basis")))
    check("返回方案签名", bool(plan.get("signature")), str(plan.get("signature")))

    status, infeasible = call("/api/meal", {**body, "protein_min": 99}, "POST")
    cap = (infeasible.get("frontier") or {}).get("max_protein")
    check("无解 200 且给出可达上限", status == 200 and cap is not None, str(cap))
    check("可达上限仍不超价格", cap and cap["cost"] <= 30, str(cap and cap["cost"]))
    check("可达上限仍不超脂肪", cap and cap["fat"] <= 20, str(cap and cap["fat"]))

    for label, payload in [
        ("零价格 400", {**body, "budget": 0}),
        ("负蛋白 400", {**body, "protein_min": -1}),
        ("缺蛋白质 400", {"meal": "lunch", "fat_max": 20, "budget": 30}),
        ("未知餐次 400", {**body, "meal": "brunch"}),
        ("热量区间颠倒 400", {**body, "kcal_min": 900, "kcal_max": 500}),
    ]:
        status, bad = call("/api/meal", payload, "POST")
        check(label, status == 400, str(bad.get("error")))

    # ---------------------------------------------------------- deleted API
    for path, method, payload in [
        ("/api/plan", "POST", {"targets": {}}),
        ("/api/replan", "POST", {}),
        ("/api/intake", "POST", {"meal": "lunch", "items": []}),
        ("/api/swap", "POST", {"combo": "1", "round": 2, "replace": "2"}),
        ("/api/capability", "GET", None),
        ("/api/targets", "POST", {"targets": {}}),
        ("/api/pools", "GET", None),
        ("/api/price", "POST", {"items": []}),
        ("/api/intake/abc", "PATCH", {"items": []}),
        ("/api/intake/abc", "DELETE", None),
    ]:
        status, _ = call(path, payload, method)
        check(f"已删除接口 404 · {method} {path}", status == 404, str(status))

    # --------------------------------------------------------------- quote
    sig = plan.get("signature")
    status, quoted = call("/api/quote", {"signature": sig}, "POST")
    q = (quoted.get("quotes") or {}).get("lunch") or {}
    check("当前方案报价 200", status == 200, str(status))
    check("报价返回金额与查询时间", q.get("status") == "ok" and q.get("amount") is not None
          and bool(q.get("queried_at")), str(q.get("amount")))
    check("报价后签名不变", quoted.get("signature") == sig, str(quoted.get("signature")))

    status, stale = call("/api/quote", {"signature": "deadbeefdeadbeef"}, "POST")
    check("错误签名 409 stale_signature",
          status == 409 and stale.get("kind") == "stale_signature", str(status))
    status, missing = call("/api/quote", {}, "POST")
    check("缺签名 400 missing_signature",
          status == 400 and missing.get("kind") == "missing_signature", str(status))

    # ------------------------------------------------------------ recompute
    code = mains[0]["code"]
    status, recalc = call("/api/recompute", {"quantities": {"lunch": {code: 2}}}, "POST")
    check("数量 2 重算 200", status == 200, str(status))
    check("数量 2 营养翻倍",
          abs((recalc.get("totals") or {}).get("energyKcal", 0)
              - (totals.get("energyKcal") or 0) * 2) < 1,
          str((recalc.get("totals") or {}).get("energyKcal")))
    status, quoted2 = call("/api/quote", {"signature": recalc.get("signature")}, "POST")
    sent = ((quoted2.get("quotes") or {}).get("lunch") or {}).get("requested_items") or []
    check("报价按新的数量 2 试算", status == 200 and sent and sent[0]["quantity"] == 2,
          str(sent))

    status, bad_action = call("/api/recompute", {
        "customizations": {"lunch": {code: {"action": "add_patty"}}}}, "POST")
    check("加料动作被拒 400 unsupported_customization",
          status == 400 and bad_action.get("kind") == "unsupported_customization"
          and bad_action.get("supported") == ["suggest_remove"],
          str(bad_action.get("supported")))

    # ------------------------------------------------------------ removable
    status, rm_breakfast = call("/api/removable?code=514700&meal=breakfast")
    check("514700 早餐特调可查", status == 200 and rm_breakfast.get("ok") is True,
          str(rm_breakfast.get("reason")))
    status, rm_lunch = call("/api/removable?code=514700&meal=lunch")
    check("514700 午餐特调不可用（按餐次时间）",
          status == 200 and rm_lunch.get("ok") is False, str(rm_lunch.get("reason")))
    options = rm_breakfast.get("options") or []
    check("可去项带 protein_risk 标记",
          all("protein_risk" in o for o in options), str(len(options)))

    # ------------------------------------------------------- context change
    status, changed = call("/api/settings", {"date": "2026-10-21"}, "POST")
    check("改日期 200", status == 200, str(status))
    status, stale_base = call("/api/recompute", {}, "POST")
    check("上下文变化后重算 409 stale_baseline",
          status == 409 and stale_base.get("kind") == "stale_baseline", str(status))
    status, stale_quote = call("/api/quote", {"signature": recalc.get("signature")}, "POST")
    check("上下文变化后报价 409 stale_context",
          status == 409 and stale_quote.get("kind") == "stale_context", str(status))
    call("/api/settings", {"date": "2026-10-20"}, "POST")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
