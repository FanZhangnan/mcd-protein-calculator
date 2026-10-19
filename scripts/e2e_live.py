"""End-to-end live check: real MCP -> pools -> plan -> per-meal quote.

Usage: python3 scripts/e2e_live.py [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from service import LiveProvider, Service  # noqa: E402
from store import Store  # noqa: E402

TEST_TARGETS = {
    "kcal_min": 1800,
    "kcal_max": 2100,
    "protein_min": 120,
    "fat_max": 75,
    "carb_max": 250,
    "budget": 80,
}


def main() -> int:
    svc = Service(LiveProvider(), Store(ROOT / "data"), base=ROOT / "data")
    now = svc.provider.now()
    server_date = (now.get("data") or {}).get("date") or date.today().isoformat()
    day = sys.argv[1] if len(sys.argv) > 1 else (date.fromisoformat(server_date) + timedelta(days=1)).isoformat()

    print(f"[live] server time {(now.get('data') or {}).get('formatted')} "
          f"{(now.get('data') or {}).get('timezone')} | plan date {day}")

    svc.store.save_state({"date": day, "targets": TEST_TARGETS})
    info = svc.refresh(day)
    print(f"[live] cache {info}")

    cat = svc.catalog()
    print(f"[live] catalog {cat.describe()}")

    pools = {meal: svc.pools(meal) for meal in planner_mod.MEALS}
    for meal, buckets in pools.items():
        print(f"[live] pool {meal}: main={len(buckets['main'])} side={len(buckets['side'])} "
              f"drink={len(buckets['drink'])} | unknown={len(svc.unknown_items(meal))}")

    targets = planner_mod.Targets.from_dict(TEST_TARGETS)
    plan = svc.plan(targets)
    print(f"[live] feasible={plan.feasible} score={plan.score:.4f} cost={plan.cost:.2f}元")
    print(f"[live] totals {plan.totals}")
    for meal in plan.meals:
        names = " + ".join(f"{i.name}[{i.nutrition_status}]" for i in meal.items)
        print(f"  {planner_mod.MEAL_LABEL[meal.meal]}: {names} | {meal.nutrition.get('energyKcal')} kcal "
              f"P{meal.nutrition.get('protein')} | ref {meal.price_ref:.2f}元")
    if plan.violations:
        print("[live] violations:", json.dumps(plan.violations, ensure_ascii=False))

    quotes = {}
    for meal in plan.meals:
        q = svc.quote_meal(meal)
        quotes[meal.meal] = q
        print(f"[live] quote {planner_mod.MEAL_LABEL[meal.meal]}: status={q['status']} amount={q.get('amount')} "
              f"fen={q.get('amount_fen')} at={q.get('queried_at')} msg={q.get('message')}")

    out = {
        "server_time": now.get("data"),
        "plan_date": day,
        "cache": info,
        "targets": TEST_TARGETS,
        "plan": plan.as_dict(),
        "quotes": quotes,
    }
    (ROOT / "data" / "last_live_run.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), "utf-8"
    )
    print("[live] saved data/last_live_run.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
