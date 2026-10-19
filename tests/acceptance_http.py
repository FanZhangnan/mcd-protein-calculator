"""HTTP acceptance run against a live server (real MCP provider).

Usage: python3 tests/acceptance_http.py [base_url]
Exit code 0 only when every check passes.

The server must be started with an isolated data directory, e.g.

    MAI_MAI_DATA=/tmp/mai-live-accept python3 src/server.py --port 8771

so the real user state / history is never touched.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8771"
RESULTS: list[tuple[str, bool, str]] = []

TARGETS = {"kcal_min": 1700, "kcal_max": 2000, "protein_min": 85,
           "fat_max": 85, "carb_max": 250, "budget": 100}
# a second, equally plausible setting that the cached Zhuhai menu answers with
# 麦香鸡 as one of the mains (needed for the positive remove-sauce path)
SAUCE_TARGETS = {"kcal_min": 1600, "kcal_max": 1900, "protein_min": 70,
                 "fat_max": 90, "carb_max": 260, "budget": 100}


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
    # ---------------------------------------------------------------- fixtures
    call("/api/targets", {"store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅"},
                          "date": "2026-10-20",
                          "meal_times": {"breakfast": "08:15", "lunch": "12:15",
                                         "dinner": "18:30"},
                          "targets": TARGETS}, "POST")
    status, boot = call("/api/bootstrap")
    check("bootstrap 200", status == 200, str(status))
    state = boot.get("state", {})
    check("目标门店为珠海 1440054", state.get("store", {}).get("code") == "1440054",
          str(state.get("store")))
    check("来源标注不是冒充", boot.get("provider_source") in ("live", "offline") and
          boot.get("cache", {}).get("source") in ("live", "cache", "offline"),
          f"provider={boot.get('provider_source')} cache={boot.get('cache', {}).get('source')}")

    status, bad = call("/api/targets", {"targets": {**TARGETS, "kcal_min": 2500}}, "POST")
    check("上下限颠倒被拒 400", status == 400, str(bad.get("error")))
    status, bad2 = call("/api/targets", {"targets": {**TARGETS, "budget": 0}}, "POST")
    check("零预算被拒 400", status == 400, str(bad2.get("error")))
    status, bad3 = call("/api/targets", {"targets": {**TARGETS, "protein_min": -5}}, "POST")
    check("负数被拒 400", status == 400, str(bad3.get("error")))

    status, refreshed = call("/api/refresh", {}, "POST")
    check("刷新真实菜单 200", status == 200,
          f"source={refreshed.get('source')} key={refreshed.get('cache_key')}")
    key_a = refreshed.get("cache_key")

    # ------------------------------------------------------- plan + first quote
    status, plan = call("/api/plan", {"targets": TARGETS, "quote": True}, "POST")
    check("规划 200", status == 200, str(plan.get("error", "")))
    check("存在可行方案", bool(plan.get("feasible")),
          f"violations={len(plan.get('violations', []))}")
    quotes = plan.get("quotes", {})
    check("三笔试算全部成功", len(quotes) == 3 and all(q.get("status") == "ok"
                                                       for q in quotes.values()),
          str({k: v.get("status") for k, v in quotes.items()}))
    check("预算状态为已试算", plan.get("budget_status") == "quoted", str(plan.get("budget_status")))
    check("试算合计等于三笔之和",
          plan.get("quote_total") is not None and
          abs(plan["quote_total"] - sum(q["amount"] for q in quotes.values())) < 0.01,
          f'{plan.get("quote_total")} vs {sum(q["amount"] for q in quotes.values()):.2f}')
    check("试算行带 queried_at",
          all(q.get("queried_at") for q in quotes.values()),
          str([q.get("queried_at") for q in quotes.values()]))
    check("计划餐没有伪造成已付金额",
          all(m.get("amount_actual") is None for m in plan["meals"]),
          str([(m["meal"], m.get("amount_actual")) for m in plan["meals"]]))
    sig_plan = plan.get("signature")
    items_before = [(m["meal"], [(i["code"], i["quantity"]) for i in m["items"]])
                    for m in plan["meals"]]

    # ------------------------------------------- quote the plan that is on screen
    status, q1 = call("/api/quote", {"signature": sig_plan}, "POST")
    check("当前方案报价 200", status == 200, str(q1.get("error", "")))
    check("当前方案报价保留商品与数量",
          [(m["meal"], [(i["code"], i["quantity"]) for i in m["items"]]) for m in q1["meals"]]
          == items_before, "items unchanged")
    check("当前方案报价与首次试算一致",
          abs((q1.get("quote_total") or 0) - (plan.get("quote_total") or 0)) < 0.01,
          f'{q1.get("quote_total")} vs {plan.get("quote_total")}')

    # -------------------------------------------- purchase quantity contract
    breakfast = q1["meals"][0]
    first = breakfast["items"][0]
    for bad in (0.5, 1.5, 2.5, 0, -1, "", "abc", None):
        st, rejected = call("/api/recompute",
                            {"quantities": {breakfast["meal"]: {first["code"]: bad}},
                             "customizations": {}}, "POST")
        check(f"待买数量 {bad!r} 被拒 400",
              st == 400 and rejected.get("kind") == "invalid_quantity",
              f'{st} {rejected.get("kind")} {str(rejected.get("error"))[:60]}')
        check(f"待买数量 {bad!r} 未产生试算", not rejected.get("quotes"),
              str(rejected.get("quotes")))
    st, still = call("/api/quote", {"signature": sig_plan}, "POST")
    check("被拒后原签名仍可报价",
          st == 200 and still.get("budget_status") == "quoted",
          f'{st} {still.get("budget_status")}')

    # ----------------------------------------------- quantities survive a quote
    st, rc = call("/api/recompute", {"quantities": {breakfast["meal"]: {first["code"]: 2}},
                                     "customizations": {}}, "POST")
    check("改数量重算 200", st == 200, str(rc.get("error", "")))
    check("数量已按 2 重算", rc["meals"][0]["items"][0]["quantity"] == 2,
          str(rc["meals"][0]["items"][0]["quantity"]))
    check("改数量后签名变化", rc.get("signature") != sig_plan,
          f'{sig_plan} -> {rc.get("signature")}')
    st, stale = call("/api/quote", {"signature": sig_plan}, "POST")
    check("旧签名报价被拒 409", st == 409 and stale.get("kind") == "stale_signature",
          f'{st} {stale.get("kind")}')
    check("旧签名被拒时不产生试算", not stale.get("quotes"), str(stale.get("quotes")))

    st, q2 = call("/api/quote", {"signature": rc.get("signature")}, "POST")
    check("改数量后的当前方案可报价", st == 200 and q2.get("budget_status") == "quoted",
          f'{st} {q2.get("budget_status")}')
    check("报价没有把数量改回去", q2["meals"][0]["items"][0]["quantity"] == 2,
          str(q2["meals"][0]["items"][0]["quantity"]))

    # --------------------------------------- eaten money is booked, not re-quoted
    st, rec = call("/api/intake", {"meal": breakfast["meal"],
                                   "items": q2["meals"][0]["items"],
                                   "amount_actual": 95.0,
                                   "amount_source": "manual"}, "POST")
    check("记录早餐（记账 95 元）200", st == 200, str(rec.get("record", {}).get("id")))
    record_id = rec.get("record", {}).get("id")
    check("记账金额已保存", rec["record"]["amount_actual"] == 95.0,
          str(rec["record"].get("amount_actual")))

    st, replan = call("/api/replan", {}, "POST")
    check("重排 200", st == 200, str(replan.get("error", "")))
    check("重排只开放午晚", replan.get("open_meals") == ["lunch", "dinner"],
          str(replan.get("open_meals")))
    locked = [m for m in replan["meals"] if m.get("locked")]
    check("早餐被锁定且只有一条", len(locked) == 1 and locked[0]["meal"] == "breakfast",
          str([m["meal"] for m in locked]))
    check("早餐内容未变",
          [i["code"] for i in locked[0]["items"]] == [i["code"] for i in breakfast["items"]])
    check("早餐记账金额未被重排覆盖", locked[0].get("amount_actual") == 95.0,
          str(locked[0].get("amount_actual")))
    check("未报价时全天按参考价", replan.get("budget_status") == "reference",
          str(replan.get("budget_status")))
    check("全天参考价含已吃记账",
          abs((replan.get("cost") or 0) - (95.0 + (replan.get("open_reference_total") or 0))) < 0.01,
          f'{replan.get("cost")} vs 95 + {replan.get("open_reference_total")}')

    st, q3 = call("/api/quote", {"signature": replan.get("signature")}, "POST")
    open_quotes = {m: v for m, v in (q3.get("quotes") or {}).items()}
    check("重排后报价只覆盖开放餐次", set(open_quotes) == {"lunch", "dinner"},
          str(sorted(open_quotes)))
    check("全天合计 = 已吃记账 + 开放试算",
          abs((q3.get("cost") or 0) - (95.0 + sum(v["amount"] for v in open_quotes.values()))) < 0.01,
          f'{q3.get("cost")} vs 95 + {sum(v["amount"] for v in open_quotes.values()):.2f}')
    check("报价未清空早餐记账",
          next(m for m in q3["meals"] if m["meal"] == "breakfast").get("amount_actual") == 95.0,
          str(next(m for m in q3["meals"] if m["meal"] == "breakfast").get("amount_actual")))
    check("已吃 95 + 开放试算必然超 100 元预算",
          (q3.get("cost") or 0) > 100, str(q3.get("cost")))
    check("超预算时 feasible=false 且给出预算差额",
          q3.get("feasible") is False and
          any(v["constraint"] == "budget" and (v.get("gap") or 0) > 0 for v in q3["violations"]),
          f'cost={q3.get("cost")} feasible={q3.get("feasible")} '
          f'{[v for v in q3["violations"] if v["constraint"]=="budget"]}')

    # --------------------------------------------------- all three meals eaten
    for m in q3["meals"]:
        if m["meal"] == "breakfast":
            continue
        call("/api/intake", {"meal": m["meal"], "items": m["items"],
                             "amount_actual": 20.0, "amount_source": "manual"}, "POST")
    st, full = call("/api/replan", {}, "POST")
    check("三餐全吃后 open_meals 为空", full.get("open_meals") == [], str(full.get("open_meals")))
    check("三餐全吃后仍是 3 条", len(full.get("meals", [])) == 3,
          str([m["meal"] for m in full.get("meals", [])]))
    check("三餐全吃后全部锁定", all(m.get("locked") for m in full["meals"]),
          str([(m["meal"], m.get("locked")) for m in full["meals"]]))
    check("三餐全吃后合计=已吃之和",
          abs((full.get("totals", {}).get("energyKcal") or 0) -
              sum(m["nutrition"]["energyKcal"] for m in full["meals"])) < 0.5,
          str(full.get("totals", {}).get("energyKcal")))
    check("三餐全吃后金额=记账之和",
          abs((full.get("cost") or 0) - (95.0 + 20.0 + 20.0)) < 0.01, str(full.get("cost")))
    check("三餐全吃后按记账口径", full.get("budget_status") == "booked",
          str(full.get("budget_status")))

    dinner_id = next((r["id"] for r in full.get("intake", []) if r["meal"] == "dinner"), None)
    call(f"/api/intake/{dinner_id}", None, "DELETE")
    st, partial = call("/api/replan", {}, "POST")
    check("撤销晚餐后只开放晚餐", partial.get("open_meals") == ["dinner"],
          str(partial.get("open_meals")))
    check("撤销后仍是 3 条且 2 条锁定",
          len(partial["meals"]) == 3 and sum(1 for m in partial["meals"] if m["locked"]) == 2,
          str([(m["meal"], m["locked"]) for m in partial["meals"]]))

    for r in call("/api/bootstrap")[1].get("intake", []):
        call(f"/api/intake/{r['id']}", None, "DELETE")
    check("清理隔离数据", call("/api/bootstrap")[1].get("intake") == [],
          "intake empty")

    # ---------------------------------------- positive remove-sauce (real option)
    call("/api/targets", {"targets": SAUCE_TARGETS}, "POST")
    st, plan2 = call("/api/plan", {"quote": False}, "POST")
    check("换目标后规划 200", st == 200, str(plan2.get("error", "")))
    sauce = None
    for m in plan2["meals"]:
        for item in m["items"]:
            if item.get("kind") != "main":
                continue
            st, cap = call(f"/api/capability?code={item['code']}")
            opts = [o for o in (cap.get("options") or []) if "麦香鸡酱" in o.get("name", "")]
            if opts:
                sauce = (m["meal"], item, opts[0])
                break
        if sauce:
            break
    check("方案中存在可正向去酱的主食", sauce is not None,
          str(sauce[1]["name"] if sauce else "none"))
    if sauce:
        meal, item, option = sauce
        before_kcal = next(m for m in plan2["meals"] if m["meal"] == meal)["nutrition"]["energyKcal"]
        st, cust = call("/api/recompute", {
            "customizations": {meal: {item["code"]: {"action": "remove_sauce",
                                                     "option_code": option["code"]}}},
            "scenario": "tw_sauce_serving"}, "POST")
        after_kcal = next(m for m in cust["meals"] if m["meal"] == meal)["nutrition"]["energyKcal"]
        check("去酱生效且有估算标记", cust.get("is_estimate") is True, item["name"])
        check("去酱后热量下降", after_kcal < before_kcal, f"{before_kcal} -> {after_kcal}")
        check("去酱后钠为缺项", cust["totals"].get("sodium") is None,
              str(cust["totals"].get("sodium")))
        check("去酱后旧试算失效", cust.get("quotes_valid") is False,
              str(cust.get("quotes_valid")))
        customized = next(i for m in cust["meals"] for i in m["items"] if i["code"] == item["code"])
        check("定制参数写入方案", bool(customized.get("modification")),
              json.dumps(customized.get("modification"), ensure_ascii=False))

        st, cleared = call("/api/recompute", {"customizations": {},
                                              "scenario": "tw_sauce_serving"}, "POST")
        restored = next(m for m in cleared["meals"] if m["meal"] == meal)["nutrition"]["energyKcal"]
        check("取消去酱完全恢复", abs(restored - before_kcal) < 0.01,
              f"{restored} vs {before_kcal}")
        check("取消后不再标记估算", cleared.get("is_estimate") is False,
              str(cleared.get("is_estimate")))

    # a real sauce option with no sourced scenario must still be refused
    refused = None
    for m in plan2["meals"]:
        for item in m["items"]:
            if item.get("kind") != "main" or (sauce and item["code"] == sauce[1]["code"]):
                continue
            st, cap = call(f"/api/capability?code={item['code']}")
            opts = cap.get("options") or []
            if cap.get("ok") and opts and all(not o.get("scenarios") for o in opts):
                refused = (m["meal"], item, opts[0])
                break
        if refused:
            break
    check("方案中存在不可估算的酱料（拒绝路径）", refused is not None,
          str(refused[1]["name"] if refused else "none"))
    if refused:
        meal, item, option = refused
        st, bad_sauce = call("/api/recompute", {
            "customizations": {meal: {item["code"]: {"action": "remove_sauce",
                                                     "option_code": option["code"]}}},
            "scenario": "tw_sauce_serving"}, "POST")
        check("不适用酱料被明确拒绝且不估算",
              bool(bad_sauce.get("issues")) and bad_sauce.get("is_estimate") is False,
              json.dumps(bad_sauce.get("issues"), ensure_ascii=False)[:160])

    # ------------------------------------------- context change retires baseline
    keep = plan2["meals"][0]
    st, kept = call("/api/intake", {"meal": keep["meal"], "items": keep["items"],
                                    "amount_actual": 33.0, "amount_source": "manual"}, "POST")
    kept_id = kept.get("record", {}).get("id")
    check("切换前留下一条历史记录", st == 200 and kept_id is not None, str(kept_id))

    status, switched = call("/api/targets", {"store": {"code": "1450713"},
                                             "meal_times": {"breakfast": "09:00",
                                                            "lunch": "12:15",
                                                            "dinner": "18:30"}}, "POST")
    check("切换门店 200", status == 200, str(switched.get("error", "")))
    status, boot2 = call("/api/bootstrap")
    cache2 = boot2.get("cache", {})
    check("缓存按门店切换", cache2.get("store_code") == "1450713", str(cache2.get("store_code")))
    check("缓存按时段切换", cache2.get("meal_times", {}).get("breakfast") == "09:00",
          str(cache2.get("meal_times", {}).get("breakfast")))
    check("缓存 key 变化", cache2.get("cache_key") != key_a,
          f'{key_a} -> {cache2.get("cache_key")}')
    check("旧基线被标记失效", boot2.get("baseline", {}).get("valid") is False,
          json.dumps(boot2.get("baseline"), ensure_ascii=False)[:120])
    st, stale_recompute = call("/api/recompute", {"quantities": {}, "customizations": {}}, "POST")
    check("旧基线重算被拒 409", st == 409 and stale_recompute.get("kind") == "stale_baseline",
          f'{st} {stale_recompute.get("kind")}')
    kept_records = [r for r in boot2.get("intake", []) if r.get("id") == kept_id]
    check("历史记录不随方案失效删除",
          len(kept_records) == 1 and kept_records[0]["amount_actual"] == 33.0,
          str(len(boot2.get("intake", []))))
    call(f"/api/intake/{kept_id}", None, "DELETE")

    # ------------------------------------------------ eaten half a serving
    st, plan3 = call("/api/plan", {"quote": False}, "POST")
    check("切店后可重新规划", st == 200 and bool(plan3.get("meals")), str(st))
    m0 = plan3["meals"][0]
    i0 = m0["items"][0]
    st, half_rec = call("/api/intake", {
        "meal": m0["meal"], "items": [dict(i0, quantity=0.5)],
        "amount_actual": 18.50, "amount_source": "manual"}, "POST")
    rec3 = half_rec.get("record", {})
    check("已吃 0.5 份按半份计入营养",
          abs((rec3.get("totals") or {}).get("energyKcal", -1)
              - i0["nutrition"]["energyKcal"] * 0.5) < 0.11,
          f'{(rec3.get("totals") or {}).get("energyKcal")} vs '
          f'{i0["nutrition"]["energyKcal"] * 0.5}')
    check("已吃 0.5 份不把记账金额折半",
          rec3.get("amount_actual") == 18.50, str(rec3.get("amount_actual")))
    check("已吃 0.5 份的参考价按半份",
          abs((rec3.get("amount_reference") or 0)
              - (i0.get("price_ref") or 0) * 0.5) < 0.01,
          f'{rec3.get("amount_reference")} vs {(i0.get("price_ref") or 0) * 0.5}')
    call(f"/api/intake/{rec3.get('id')}", None, "DELETE")

    # ---------------------------------------------------------------- restore
    call("/api/targets", {"store": {"code": "1440054"},
                          "meal_times": {"breakfast": "08:15", "lunch": "12:15",
                                         "dinner": "18:30"},
                          "targets": TARGETS}, "POST")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
