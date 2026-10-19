"""Three required validations + fixture capture.

1) combo swap final pricing (9900005456: 中薯 -> 小杯玉米杯)
2) explicit dinner-slot menu query
3) side-item nutrition for the swap

Writes raw payloads to data/fixtures/ and prints a compact summary.
Never prints the token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp_client import McpClient, McpError  # noqa: E402

FIX = ROOT / "data" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

STORE = "1450713"
DINNER = "2026-10-19 18:30"
COMBO = "9900005456"
SIDE_SMALL_CORN = "4437"
SIDE_DEFAULT_FRIES = "4810"


def dump(name: str, payload: dict) -> None:
    (FIX / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
    )


def main() -> int:
    client = McpClient()
    if not client.has_token:
        print("NO TOKEN available (set MCD_MCP_TOKEN or rely on ~/.workbuddy/mcp.json)")
        return 2

    print("== 1. now-time-info ==")
    t = client.call("now-time-info")
    dump("now_time_info", t)
    d = t.get("data", {})
    print(f"  server: {d.get('formatted')} {d.get('timezone')} (utc {d.get('utc')})")

    print("== 2. dinner menu (explicit reservationDate) ==")
    menu = client.call(
        "query-meals",
        {"storeCode": STORE, "orderType": 1, "beType": 1, "reservationDate": DINNER},
    )
    dump("menu_dinner_1830", menu)
    cats = (menu.get("data") or {}).get("categories") or []
    print(f"  ok={menu.get('success')} datetime={menu.get('datetime')} categories={len(cats)}")
    print("  " + " | ".join(c.get("name", "").replace("\n", " ") for c in cats))

    print("== 3. combo detail ==")
    detail = client.call(
        "query-meal-detail",
        {
            "storeCode": STORE,
            "orderType": 1,
            "beType": 1,
            "code": COMBO,
            "reservationDate": DINNER,
        },
    )
    dump("combo_9900005456_detail", detail)
    dd = detail.get("data") or {}
    rounds = dd.get("rounds") or []
    print(f"  name={dd.get('name')} rounds={len(rounds)}")
    for r in rounds:
        names = [f"{c.get('name')}({c.get('code')},{c.get('diffPrice')})" for c in r.get("choices", [])]
        print(f"   round {r.get('id')} {r.get('name')} -> {', '.join(names[:8])}")

    print("== 4. calculate-price: default combo ==")
    base = {"storeCode": STORE, "orderType": 1, "beType": 1, "needTableware": False}
    try:
        p0 = client.call("calculate-price", {**base, "items": [{"productCode": COMBO, "quantity": 1}]})
        dump("price_combo_default", p0)
        print(f"  price={p0.get('data', {}).get('price')} productPrice={p0.get('data', {}).get('productPrice')}")
    except McpError as exc:
        print(f"  FAILED: {exc}")

    print("== 5. calculate-price: swap side -> 小杯玉米杯 (several shapes) ==")
    shapes = {
        "only_round2": [
            {"round": "2", "comboItemList": [{"code": SIDE_SMALL_CORN, "quantity": 1}]}
        ],
        "all_rounds": [
            {"round": "1", "comboItemList": [{"code": "1440", "quantity": 1}]},
            {"round": "2", "comboItemList": [{"code": SIDE_SMALL_CORN, "quantity": 1}]},
            {"round": "3", "comboItemList": [{"code": "3050", "quantity": 1}]},
        ],
        "all_rounds_int_id": [
            {"round": 1, "comboItemList": [{"code": "1440", "quantity": 1}]},
            {"round": 2, "comboItemList": [{"code": SIDE_SMALL_CORN, "quantity": 1}]},
            {"round": 3, "comboItemList": [{"code": "3050", "quantity": 1}]},
        ],
    }
    for label, rounds_payload in shapes.items():
        try:
            p = client.call(
                "calculate-price",
                {
                    **base,
                    "items": [
                        {"productCode": COMBO, "quantity": 1, "roundList": rounds_payload}
                    ],
                },
            )
            dump(f"price_combo_swap_{label}", p)
            dd = p.get("data", {}) or {}
            print(f"  {label}: success={p.get('success')} price={dd.get('price')} "
                  f"productPrice={dd.get('productPrice')} discount={dd.get('discount')}")
        except McpError as exc:
            print(f"  {label}: FAILED {exc}")

    print("== 6. nutrition table ==")
    nut = client.call("list-nutrition-foods")
    dump("nutrition", nut)
    raw = (nut.get("data") or "") if isinstance(nut.get("data"), str) else ""
    print(f"  ok={nut.get('success')} rows={max(0, len(raw.splitlines()) - 1)}")

    print("== 7. breakfast / lunch menus (for pools) ==")
    for label, ts in (("breakfast_0815", "2026-10-19 08:15"), ("lunch_1215", "2026-10-19 12:15")):
        m = client.call(
            "query-meals",
            {"storeCode": STORE, "orderType": 1, "beType": 1, "reservationDate": ts},
        )
        dump(f"menu_{label}", m)
        c = (m.get("data") or {}).get("categories") or []
        print(f"  {label}: categories={len(c)} ok={m.get('success')}")

    print("== 8. single-item details for pool (sample) ==")
    for code in ("1440", "1406", "4437", "4810"):
        try:
            det = client.call(
                "query-meal-detail",
                {"storeCode": STORE, "orderType": 1, "beType": 1, "code": code},
            )
            dump(f"detail_{code}", det)
            print(f"  {code}: {det.get('data', {}).get('name')}")
        except McpError as exc:
            print(f"  {code}: FAILED {exc}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
