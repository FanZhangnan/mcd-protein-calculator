"""Live single-meal check: real MCP -> pools -> solve -> quote.

Usage: python3 scripts/e2e_live.py [YYYY-MM-DD] [breakfast|lunch|dinner] [protein] [budget] [fat]
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
from store import data_dir  # noqa: E402


def main() -> int:
    base = data_dir()          # honours MAI_MAI_DATA for isolated acceptance runs
    svc = Service(LiveProvider(), base=base)
    now = svc.provider.now()
    server_date = (now.get("data") or {}).get("date") or date.today().isoformat()
    day = sys.argv[1] if len(sys.argv) > 1 else (
        date.fromisoformat(server_date) + timedelta(days=1)).isoformat()
    meal = sys.argv[2] if len(sys.argv) > 2 else "lunch"
    protein = float(sys.argv[3]) if len(sys.argv) > 3 else 40.0
    budget = float(sys.argv[4]) if len(sys.argv) > 4 else 45.0
    fat = float(sys.argv[5]) if len(sys.argv) > 5 else 40.0

    print(f"[live] server time {(now.get('data') or {}).get('formatted')} "
          f"{(now.get('data') or {}).get('timezone')} | plan date {day}")
    svc.store.save_state({"date": day})
    info = svc.refresh(day)
    print(f"[live] cache {info}")

    cat = svc.catalog()
    print(f"[live] catalog {cat.describe()['rows']} rows, "
          f"{cat.describe()['unique_names']} unique names")
    for m in planner_mod.MEALS:
        buckets = svc.pools(m)
        print(f"[live] pool {m}: main={len(buckets['main'])} side={len(buckets['side'])} "
              f"drink={len(buckets['drink'])} | unknown={len(svc.unknown_items(m))}")

    targets = planner_mod.Targets(protein_min=protein, budget=budget, fat_max=fat)
    payload = svc.plan_meal(meal, targets, allow_side=False, quote=True)
    print(json.dumps({
        "meal": payload["meal"],
        "feasible": payload["feasible"],
        "items": [{"name": i["name"], "qty": i["quantity"]} for i in payload["items"]],
        "totals": payload["totals"],
        "cost": payload["cost"],
        "protein_per_yuan": payload.get("protein_per_yuan"),
        "quote": (payload.get("quotes") or {}).get(meal),
        "frontier": payload.get("frontier"),
        "upgrade": payload.get("upgrade"),
    }, ensure_ascii=False, indent=2))
    (base / "last_live_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
