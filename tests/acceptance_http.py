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


def fetch_text(path: str) -> str:
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return ""


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
        ("零蛋白 400", {**body, "protein_min": 0}),
        ("负蛋白 400", {**body, "protein_min": -1}),
        ("缺蛋白质 400", {"meal": "lunch", "budget": 30}),
        ("未知餐次 400", {**body, "meal": "brunch"}),
        ("热量区间颠倒 400", {**body, "kcal_min": 900, "kcal_max": 500}),
        ("负脂肪 400", {**body, "fat_max": -1}),
        ("主食上限 0 拒绝 400", {**body, "max_mains": 0}),
        ("主食上限 4 拒绝 400", {**body, "max_mains": 4}),
    ]:
        status, bad = call("/api/meal", payload, "POST")
        check(label, status == 400, str(bad.get("error")))

    # ---------------------------------------------------------- structure N
    status, one_main = call("/api/meal", {**body, "protein_min": 20, "max_mains": 1}, "POST")
    one_mains = [i for i in one_main.get("items", []) if i.get("kind") == "main"]
    check("N=1 时只有一个主食", status == 200 and len(one_mains) == 1,
          str([(i["name"], i["quantity"]) for i in one_mains]))

    status, two_mains = call("/api/meal", {**body, "protein_min": 40, "budget": 60,
                                           "fat_max": 80, "max_mains": 2}, "POST")
    two = [i for i in two_mains.get("items", []) if i.get("kind") == "main"]
    check("N=2 时主食不超过 2 行", status == 200 and 1 <= len(two) <= 2,
          str([(i["name"], i["quantity"]) for i in two]))
    check("主食总份数不超过 N", sum(i["quantity"] for i in two) <= 2,
          str(sum(i["quantity"] for i in two)))

    # ------------------------------------------------------------ landscape
    status, land = call("/api/meal/landscape?meal=lunch&max_mains=2")
    check("landscape 200", status == 200, str(status))
    rows = land.get("items") or []
    check("landscape 只含官方营养主食",
          bool(rows) and all(r.get("protein") is not None and r.get("price") for r in rows),
          str(len(rows)))
    check("landscape 每元蛋白质按降序",
          all(rows[i]["protein_per_yuan"] >= rows[i + 1]["protein_per_yuan"]
              for i in range(len(rows) - 1)),
          str([r["protein_per_yuan"] for r in rows[:5]]))
    check("landscape 给出预算→蛋白前沿", bool(land.get("frontier")),
          str(len(land.get("frontier") or [])))

    # ------------------------------------------------- no-solution contract
    status, nofit = call("/api/meal", {**body, "protein_min": 99, "budget": 35,
                                       "fat_max": 20, "max_mains": 2}, "POST")
    cap = (nofit.get("frontier") or {}).get("max_protein")
    check("无解主卡片仍在硬约束内",
          status == 200 and nofit.get("feasible") is False
          and bool(nofit.get("items"))
          and (nofit.get("cost") or 99) <= 35
          and ((nofit.get("totals") or {}).get("fat") or 99) <= 20,
          str(cap))
    status, nocards = call("/api/meal", {**body, "protein_min": 20, "budget": 5}, "POST")
    check("硬约束下无组合时不返回商品卡片",
          status == 200 and nocards.get("feasible") is False
          and nocards.get("items") == [],
          str(nocards.get("items")))

    # ---------------------------------------------------------------- copy
    page = fetch_text("/")
    script = fetch_text("/app.js")
    check("页面标题为增肌计算器", "麦当劳增肌计算器" in page, "index.html")
    blob = f"{page}{script}"
    check("页面文案不含禁用词",
          all(word not in blob for word in ("减脂", "瘦", "加肉饼", "全天", "三餐")),
          "checked 5 words")

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
    # regenerate a clean plan: the probes above replaced the working snapshot
    status, plan = call("/api/meal", body, "POST")
    check("报价前重新生成方案", status == 200 and bool(plan.get("signature")), str(status))
    totals = plan.get("totals") or {}
    mains = [i for i in plan.get("items", []) if i.get("kind") == "main"]
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

    # ------------------------------------------------------------ tolerance
    status, tol_default = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                             "budget": 45}, "POST")
    status, tol_zero = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                          "budget": 45, "protein_tolerance": 0}, "POST")
    check("容差默认 3", tol_default.get("protein_tolerance") == 3
          and tol_default.get("protein_accept_min") == 37, str(tol_default.get("protein_accept_min")))
    check("tolerance=0 主方案与默认一致",
          [i["code"] for i in tol_default.get("items", [])] ==
          [i["code"] for i in tol_zero.get("items", [])]
          and tol_default.get("cost") == tol_zero.get("cost"),
          f"{tol_default.get('cost')} vs {tol_zero.get('cost')}")

    status, tol_near = call("/api/meal", {"meal": "lunch", "protein_min": 44,
                                          "budget": 45}, "POST")
    check("原目标无解时进入接近达标", tol_near.get("protein_status") == "near",
          str(tol_near.get("protein_status")))
    check("接近达标显示真实蛋白质",
          tol_near.get("totals", {}).get("protein") == 43.0
          and tol_near.get("protein_gap") == 1.0,
          f"P{tol_near.get('totals', {}).get('protein')} gap {tol_near.get('protein_gap')}")

    status, tol_under = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                           "budget": 35}, "POST")
    check("37 g 也无解时仍为预算内最多", tol_under.get("protein_status") == "under",
          str(tol_under.get("protein_status")))
    check("放宽方案仍按原目标 40 g 计算",
          (tol_under.get("frontier") or {}).get("max_protein", {}).get("protein_gap") == 6.0,
          str((tol_under.get("frontier") or {}).get("max_protein")))
    check("无解时不把容差说成达标", tol_under.get("feasible") is False, "feasible")

    for label, value in [("负容差", -1), ("超范围", 11), ("非数字", "abc"), ("小数", 3.5)]:
        status, bad = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                         "budget": 45, "protein_tolerance": value}, "POST")
        check(f"{label}容差 400", status == 400, f"{status} {bad.get('error')}")

    status, bad_select = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                            "budget": 45, "select": "cheapest"}, "POST")
    check("未知 select 400", status == 400, str(bad_select.get("error")))

    status, saver = call("/api/meal", {"meal": "lunch", "protein_min": 40,
                                       "budget": 45, "select": "saver"}, "POST")
    check("选择省钱选项后成为主方案",
          status == 200 and saver.get("selected") == "saver"
          and saver.get("protein_status") == "near"
          and saver.get("cost") == 40.5,
          f"{saver.get('cost')} {saver.get('protein_status')}")

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
