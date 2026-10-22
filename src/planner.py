"""Single-meal solver (strict mode).

Every number this module produces is the *official standard-version* value of
one finished menu item. Removing a component never changes nutrition here: it
only writes POS order parameters (see ``service.recompute``).

Structure rules
---------------
* 1..N mains (N = ``max_mains``; the product default is 2, the same SKU twice
  is allowed and is merged into one line with ``quantity = 2``)
* 0..1 drink, drawn from a whitelist built in :mod:`service`
* 0..1 side, only when the caller allows it
* purchase quantities are positive integers

Hard constraints
----------------
``budget`` (price cap), ``fat_max`` and the kcal band (when set) must never be
broken — not even by the fallback card. ``protein_min`` is the *goal*: a combo
that misses it is not "达标", but it can still be shown as 预算内最多.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations_with_replacement
from typing import Any, Iterable

MEALS = ("breakfast", "lunch", "dinner")
MEAL_LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
NUTRI_KEYS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]


@dataclass
class Targets:
    """One meal's targets. No day-level fields survive here.

    ``protein_tolerance`` is how many grams below ``protein_min`` the user is
    willing to accept (0–10). The solver first tries the real target, then the
    tolerated threshold, so “达标” and “接近达标” are decided in one place.
    """

    protein_min: float
    budget: float | None = None
    fat_max: float | None = None
    kcal_min: float | None = None
    kcal_max: float | None = None
    protein_tolerance: float = 0.0

    @property
    def kcal_low(self) -> float:
        return 0.0 if self.kcal_min is None else float(self.kcal_min)

    @property
    def kcal_high(self) -> float:
        return 10**9 if self.kcal_max is None else float(self.kcal_max)

    @property
    def accept_min(self) -> float:
        """Lowest protein number that still counts as acceptable."""
        tolerance = max(0.0, float(self.protein_tolerance or 0.0))
        return max(0.1, float(self.protein_min) - tolerance)

    def with_protein_min(self, value: float) -> "Targets":
        return Targets(
            protein_min=float(value),
            budget=self.budget,
            fat_max=self.fat_max,
            kcal_min=self.kcal_min,
            kcal_max=self.kcal_max,
            protein_tolerance=self.protein_tolerance,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protein_min": self.protein_min,
            "budget": self.budget,
            "fat_max": self.fat_max,
            "kcal_min": self.kcal_min,
            "kcal_max": self.kcal_max,
            "protein_tolerance": self.protein_tolerance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Targets":
        def num(key: str):
            value = (data or {}).get(key)
            return None if value in ("", None) else float(value)

        return cls(
            protein_min=float(num("protein_min") or 0.0),
            budget=num("budget"),
            fat_max=num("fat_max"),
            kcal_min=num("kcal_min"),
            kcal_max=num("kcal_max"),
            protein_tolerance=float(num("protein_tolerance") or 0.0),
        )


@dataclass
class Item:
    code: str
    name: str
    kind: str
    nutrition: dict[str, float | None] | None
    nutrition_status: str
    nutrition_note: str = ""
    price_ref: float | None = None
    quantity: float = 1.0
    meal: str = ""
    modification: list[dict[str, Any]] | None = None
    removals: list[dict[str, Any]] | None = None
    #: ``official`` — the number is the official standard version;
    #: ``standard_unadjusted`` — a removal is *suggested* but the nutrition is
    #: still the standard version (strict mode).
    nutrition_basis: str = "official"
    removal_notes: list[str] = field(default_factory=list)
    #: combo variants only: the rounds sent back to ``calculate-price``
    round_list: list[dict[str, Any]] | None = None
    #: combo variants only: ``[{role, code, name, diff_price}]`` for the card
    combo_rounds: list[dict[str, Any]] | None = None
    #: combo variants only: rounds where an option without official nutrition
    #: was replaced by another option of the same round
    combo_replacements: list[dict[str, Any]] | None = None
    combo_variant: str = ""
    combo_menu_price: float | None = None

    def copy(self) -> "Item":
        return Item(
            code=self.code, name=self.name, kind=self.kind,
            nutrition=dict(self.nutrition) if self.nutrition else None,
            nutrition_status=self.nutrition_status, nutrition_note=self.nutrition_note,
            price_ref=self.price_ref, quantity=self.quantity, meal=self.meal,
            modification=self.modification, removals=self.removals,
            nutrition_basis=self.nutrition_basis,
            removal_notes=list(self.removal_notes or []),
            round_list=[dict(r) for r in (self.round_list or [])] or None,
            combo_rounds=[dict(r) for r in (self.combo_rounds or [])] or None,
            combo_replacements=[dict(r) for r in (self.combo_replacements or [])] or None,
            combo_variant=self.combo_variant,
            combo_menu_price=self.combo_menu_price,
        )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "combo": (None if self.kind != "combo" else {
                "variant": self.combo_variant,
                "menu_price": self.combo_menu_price,
                "rounds": self.combo_rounds or [],
                "round_list": self.round_list or [],
                "replacements": self.combo_replacements or [],
            }),
            "nutrition": self.nutrition,
            "nutrition_status": self.nutrition_status,
            "nutrition_note": self.nutrition_note,
            "nutrition_basis": self.nutrition_basis,
            "removal_notes": self.removal_notes or [],
            "is_estimate": False,
            "price_ref": self.price_ref,
            "quantity": self.quantity,
            "line_total_ref": None if self.price_ref is None else round(self.price_ref * self.quantity, 2),
            "meal": self.meal,
            "modification": self.modification or [],
            "removals": self.removals or [],
        }
        return payload


@dataclass
class MealPlan:
    meal: str
    items: list[Item]
    nutrition: dict[str, float | None] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    price_ref: float = 0.0
    is_estimate: bool = False
    quote: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "meal": self.meal,
            "meal_label": MEAL_LABEL.get(self.meal, self.meal),
            "items": [i.as_dict() for i in self.items],
            "nutrition": self.nutrition,
            "unknown": self.unknown,
            "price_ref": round(self.price_ref, 2),
            "quote": self.quote,
            "is_estimate": self.is_estimate,
        }


@dataclass
class PlanResult:
    feasible: bool
    meals: list[MealPlan]
    totals: dict[str, float | None]
    unknowns: list[str]
    is_estimate: bool
    cost: float
    violations: list[dict[str, Any]]
    score: Any
    note: str = ""
    basis: str = "single_meal"
    evaluated: dict[str, Any] | None = None
    signature: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "meals": [m.as_dict() for m in self.meals],
            "totals": self.totals,
            "unknowns": self.unknowns,
            "is_estimate": self.is_estimate,
            "cost": round(self.cost, 2),
            "violations": self.violations,
            "score": round(self.score, 4) if isinstance(self.score, (int, float)) else self.score,
            "note": self.note,
            "basis": self.basis,
            "signature": self.signature,
        }


# --------------------------------------------------------------------- helpers
def _known(nutrition: dict[str, float | None] | None, key: str) -> float | None:
    if not nutrition:
        return None
    value = nutrition.get(key)
    return None if value is None else float(value)


def _qty(item: Item) -> float:
    try:
        qty = float(item.quantity)
    except (TypeError, ValueError):
        return 0.0
    return qty if qty > 0 else 0.0


def sum_nutrition(items: Iterable[Item]) -> tuple[dict[str, float | None], list[str]]:
    items = list(items)
    totals: dict[str, float | None] = {}
    unknown: list[str] = []
    for key in NUTRI_KEYS:
        values = []
        for item in items:
            qty = _qty(item)
            if qty <= 0:
                continue
            value = _known(item.nutrition, key)
            values.append(None if value is None else value * qty)
        if not values or any(v is None for v in values):
            totals[key] = None
            unknown.append(key)
        else:
            totals[key] = round(sum(values), 1)
    return totals, unknown


def sum_reference_cost(items: Iterable[Item]) -> float | None:
    total = 0.0
    for item in items:
        if item.price_ref is None:
            return None
        total += float(item.price_ref) * _qty(item)
    return round(total, 2)


def _fmt_bound(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:.0f}–{high:.0f}"
    if low is not None:
        return f"≥{low:.0f}"
    if high is not None:
        return f"≤{high:.0f}"
    return "未设置"


def violations(totals: dict[str, float | None], cost: float | None,
               targets: Targets) -> list[dict[str, Any]]:
    """Every hard-constraint breach, in the wording the UI shows."""
    out: list[dict[str, Any]] = []

    def add(constraint: str, label: str, value: Any, target: str,
            gap: Any, unit: str) -> None:
        out.append({"constraint": constraint, "label": label, "value": value,
                    "target": target, "gap": gap, "unit": unit})

    protein = totals.get("protein")
    if protein is None:
        add("unknown", "蛋白质", None, f"≥{targets.protein_min:.0f}", None, "g")
    elif protein < targets.protein_min:
        add("protein", "蛋白质", protein, f"≥{targets.protein_min:.0f}",
            round(protein - targets.protein_min, 1), "g")

    fat = totals.get("fat")
    if targets.fat_max is not None:
        if fat is None:
            add("unknown", "脂肪", None, f"≤{targets.fat_max:.0f}", None, "g")
        elif fat > targets.fat_max:
            add("fat", "脂肪", fat, f"≤{targets.fat_max:.0f}",
                round(fat - targets.fat_max, 1), "g")

    kcal = totals.get("energyKcal")
    if targets.kcal_min is not None or targets.kcal_max is not None:
        if kcal is None:
            add("unknown", "热量", None,
                _fmt_bound(targets.kcal_min, targets.kcal_max), None, "kcal")
        elif kcal < targets.kcal_low:
            add("kcal", "热量", kcal, _fmt_bound(targets.kcal_min, targets.kcal_max),
                round(kcal - targets.kcal_low, 1), "kcal")
        elif kcal > targets.kcal_high:
            add("kcal", "热量", kcal, _fmt_bound(targets.kcal_min, targets.kcal_max),
                round(kcal - targets.kcal_high, 1), "kcal")

    if targets.budget is not None:
        if cost is None:
            add("unknown", "价格", None, f"≤{targets.budget:.2f}", None, "元")
        elif cost > targets.budget:
            add("budget", "价格", round(cost, 2), f"≤{targets.budget:.2f}",
                round(cost - targets.budget, 2), "元")

    return out


def is_feasible(totals: dict[str, float | None], cost: float | None,
                targets: Targets) -> bool:
    """Hard feasibility: protein goal reached and every cap respected."""
    if totals.get("protein") is None:
        return False
    if targets.fat_max is not None and totals.get("fat") is None:
        return False
    if (targets.kcal_min is not None or targets.kcal_max is not None) \
            and totals.get("energyKcal") is None:
        return False
    if targets.budget is not None and cost is None:
        return False
    return not violations(totals, cost, targets)


def caps_ok(totals: dict[str, float | None], cost: float | None,
            targets: Targets) -> bool:
    """Price / fat / kcal caps only — the protein goal is deliberately excluded.

    This is the gate the fallback card must pass: 无解时也不许突破硬约束.
    """
    if cost is None:
        return False
    if targets.budget is not None and cost > targets.budget + 1e-9:
        return False
    fat = totals.get("fat")
    if targets.fat_max is not None and (fat is None or fat > targets.fat_max + 1e-9):
        return False
    kcal = totals.get("energyKcal")
    if targets.kcal_min is not None or targets.kcal_max is not None:
        if kcal is None:
            return False
        if kcal < targets.kcal_low - 1e-9 or kcal > targets.kcal_high + 1e-9:
            return False
    return True


def single_meal_rank(totals: dict[str, float | None], cost: float | None,
                     targets: Targets) -> tuple:
    """Cheapest first, then most protein, then least fat.

    ``plan_open`` filters the feasible set before sorting, so this ordering
    only ever compares combos that already reached the protein goal.
    """
    return (round(cost if cost is not None else float("inf"), 2),
            -round(float(totals.get("protein") or 0.0), 3),
            round(float(totals.get("fat") or 0.0), 3))


def fallback_rank(totals: dict[str, float | None], cost: float | None,
                  targets: Targets) -> tuple:
    """Most protein within the caps, then cheapest, then least fat."""
    return (-round(float(totals.get("protein") or 0.0), 3),
            round(cost if cost is not None else float("inf"), 2),
            round(float(totals.get("fat") or 0.0), 3))


# ------------------------------------------------------------------- combos
def _merge_mains(picks: list[Item]) -> list[Item]:
    """Same SKU twice becomes one line with quantity 2 (one POS line)."""
    order: list[str] = []
    counts: dict[str, int] = {}
    by_code: dict[str, Item] = {}
    for item in picks:
        key = str(item.code)
        if key not in counts:
            order.append(key)
            counts[key] = 0
            by_code[key] = item
        counts[key] += 1
    out = []
    for key in order:
        copy = by_code[key].copy()
        copy.quantity = float(counts[key])
        out.append(copy)
    return out


def build_meal_options(mains: list[Item], sides: list[Item], drinks: list[Item],
                       *, max_mains: int = 1,
                       combos: list[Item] | None = None) -> list[list[Item]]:
    """Every structure-legal combo.

    * no combo: 1..N mains × {no side, side} × {no drink, drink}
    * combo: exactly one combo + 0..N-1 mains, and no extra side/drink — the
      combo already carries them. The combo counts as one main towards
      ``max_mains``, so ``max_mains = 1`` means "combo alone".

    Only items with official nutrition *and* a known price can enter: a price
    cap cannot be enforced against an unknown price, so such an item is not a
    candidate at all (it is reported in ``unknown`` instead).
    """
    usable_mains = [m for m in mains if m.nutrition and m.price_ref is not None]
    usable_sides = [s for s in sides if s.nutrition and s.price_ref is not None]
    usable_drinks = [d for d in drinks if d.nutrition and d.price_ref is not None]
    usable_combos = [c for c in (combos or []) if c.nutrition and c.price_ref is not None]

    max_mains = max(1, int(max_mains))
    combos: list[list[Item]] = []
    for size in range(1, max_mains + 1):
        for idx in combinations_with_replacement(range(len(usable_mains)), size):
            picks = [usable_mains[i] for i in idx]
            base = _merge_mains(picks)
            for side in [None] + usable_sides:
                for drink in [None] + usable_drinks:
                    combo = list(base)
                    if side:
                        combo.append(side.copy())
                    if drink:
                        combo.append(drink.copy())
                    combos.append(combo)

    # at most one combo per meal; with a combo there is no extra drink or side
    for combo_item in usable_combos:
        for size in range(0, max_mains):
            for idx in combinations_with_replacement(range(len(usable_mains)), size):
                picks = [usable_mains[i] for i in idx]
                combos.append([combo_item.copy()] + _merge_mains(picks))
    return combos


def plan_open(pools: dict[str, dict[str, list[Item]]], targets: Targets,
              open_meals: Iterable[str] | None = None,
              beam: int = 400, ranker=None, full_enumeration: bool = False,
              max_mains: int = 1) -> PlanResult:
    """Solve one meal (the parameter names are kept from the single-meal API).

    Full enumeration is the product rule; ``beam`` is accepted only so old
    callers keep working and is ignored when ``full_enumeration`` is true.
    """
    open_meals = list(MEALS) if open_meals is None else list(open_meals)
    if not open_meals:
        return PlanResult(
            feasible=False, meals=[], totals={}, unknowns=[], is_estimate=False,
            cost=0.0, violations=[{"constraint": "meal", "label": "餐次", "value": None,
                                   "target": "必须指定一个餐次", "gap": None, "unit": ""}],
            score=float("inf"), note="未指定餐次", basis="single_meal",
        )
    meal = open_meals[0]
    pool = pools.get(meal, {})
    combos = build_meal_options(pool.get("main", []), pool.get("side", []),
                                pool.get("drink", []), max_mains=max_mains,
                                combos=pool.get("combo", []))
    if not combos:
        return PlanResult(
            feasible=False, meals=[], totals={}, unknowns=[], is_estimate=False,
            cost=0.0,
            violations=[{"constraint": "pool", "label": "可售池", "value": 0,
                         "target": "至少 1 个有官方营养和价格的主食", "gap": None,
                         "unit": "", "detail": "该餐次没有可用主食"}],
            score=float("inf"), note="可售池为空，无法规划", basis="single_meal",
            evaluated={"candidates": 0, "feasible": 0, "capped": 0},
        )

    evaluated: list[dict[str, Any]] = []
    for combo in combos:
        totals, unknown = sum_nutrition(combo)
        cost = sum_reference_cost(combo)
        evaluated.append({
            "combo": combo, "totals": totals, "unknown": unknown, "cost": cost,
            "feasible": is_feasible(totals, cost, targets),
            "capped": caps_ok(totals, cost, targets),
        })

    feasible_set = [e for e in evaluated if e["feasible"]]
    capped_set = [e for e in evaluated if e["capped"]]
    if feasible_set:
        best = min(feasible_set, key=lambda e: single_meal_rank(
            e["totals"], e["cost"], targets))
        note = ""
    elif capped_set:
        best = min(capped_set, key=lambda e: fallback_rank(
            e["totals"], e["cost"], targets))
        note = "当前条件下没有满足全部要求的方案，以下为预算内蛋白质最高的组合。"
    else:
        # nothing can satisfy the caps -> the caller must not render a card
        return PlanResult(
            feasible=False, meals=[], totals={}, unknowns=[], is_estimate=False,
            cost=0.0,
            violations=violations({}, None, targets) + [{
                "constraint": "caps", "label": "硬约束", "value": None,
                "target": "价格/脂肪/热量上限", "gap": None, "unit": "",
                "detail": "没有任何组合同时满足价格、脂肪与热量上限"}],
            score=float("inf"),
            note="没有任何组合满足全部硬约束，不提供商品卡片。",
            basis="single_meal",
            evaluated={"candidates": len(evaluated), "feasible": 0, "capped": 0},
        )

    items = best["combo"]
    nutrition, unknown = sum_nutrition(items)
    plan = MealPlan(
        meal=meal, items=items, nutrition=nutrition, unknown=unknown,
        price_ref=sum_reference_cost(items) or 0.0,
    )
    cost = best["cost"] or 0.0
    return PlanResult(
        feasible=bool(feasible_set),
        meals=[plan],
        totals=best["totals"],
        unknowns=best["unknown"],
        is_estimate=False,
        cost=cost,
        violations=violations(best["totals"], best["cost"], targets),
        score=single_meal_rank(best["totals"], best["cost"], targets),
        note=note,
        basis="single_meal",
        evaluated={"candidates": len(evaluated), "feasible": len(feasible_set),
                   "capped": len(capped_set)},
    )


def brief(combo: list[Item], totals: dict[str, float | None],
          cost: float | None, targets: Targets) -> dict[str, Any]:
    """Compact one-line description of a combo (used by payloads and charts)."""
    head = (next((i for i in combo if i.kind == "combo"), None)
            or next((i for i in combo if i.kind == "main"), None))
    return {
        "code": head.code if head else "",
        "name": head.name if head else "",
        "protein": None if totals.get("protein") is None else round(float(totals["protein"]), 1),
        "fat": None if totals.get("fat") is None else round(float(totals["fat"]), 1),
        "kcal": None if totals.get("energyKcal") is None else round(float(totals["energyKcal"]), 1),
        "cost": None if cost is None else round(float(cost), 2),
        "items": [i.name if i.quantity == 1 else f"{i.name} ×{int(i.quantity)}" for i in combo],
        "protein_gap": None if totals.get("protein") is None else round(
            max(0.0, targets.protein_min - float(totals["protein"])), 1),
    }


def single_meal_frontier(pools: dict[str, dict[str, list[Item]]], targets: Targets,
                         meal: str, max_mains: int = 1) -> dict[str, Any]:
    """What the store can reach when exactly one constraint is relaxed.

    Never relaxes the structure rule, never relaxes two caps at once, and never
    returns a combo that breaks a cap it was not asked to relax.
    """
    pool = pools.get(meal, {})
    combos = build_meal_options(pool.get("main", []), pool.get("side", []),
                                pool.get("drink", []), max_mains=max_mains,
                                combos=pool.get("combo", []))
    rows: list[dict[str, Any]] = []
    for combo in combos:
        totals, _ = sum_nutrition(combo)
        cost = sum_reference_cost(combo)
        if totals.get("protein") is None or cost is None:
            continue
        rows.append({"combo": combo, "totals": totals, "cost": cost,
                     "protein": float(totals["protein"])})

    def other_ok(row, *, relax_fat=False, relax_price=False, relax_kcal=False) -> bool:
        fat = row["totals"].get("fat")
        if not relax_fat and targets.fat_max is not None:
            if fat is None or fat > targets.fat_max + 1e-9:
                return False
        if not relax_price and targets.budget is not None:
            if row["cost"] > targets.budget + 1e-9:
                return False
        if not relax_kcal and (targets.kcal_min is not None or targets.kcal_max is not None):
            kcal = row["totals"].get("energyKcal")
            if kcal is None or kcal < targets.kcal_low - 1e-9 or kcal > targets.kcal_high + 1e-9:
                return False
        return True

    hard = [r for r in rows if other_ok(r)]
    best = max(hard, key=lambda r: (r["protein"], -r["cost"],
                                    -(r["totals"].get("fat") or 0))) if hard else None

    reachable = any(r["protein"] >= targets.protein_min for r in rows)

    reach_price_rows = [r for r in rows if r["protein"] >= targets.protein_min
                        and other_ok(r, relax_price=True)]
    reach_price = min(reach_price_rows, key=lambda r: (r["cost"],
                                                       r["totals"].get("fat") or 0)
                      ) if reach_price_rows else None

    relax_fat = None
    if targets.fat_max is not None:
        relax_fat_rows = [r for r in rows if r["protein"] >= targets.protein_min
                          and other_ok(r, relax_fat=True)]
        relax_fat = min(relax_fat_rows, key=lambda r: (
            r["totals"].get("fat") if r["totals"].get("fat") is not None else 10**6,
            r["cost"])) if relax_fat_rows else None

    relax_kcal = None
    if targets.kcal_max is not None:
        rows_max = [r for r in rows if r["protein"] >= targets.protein_min
                    and other_ok(r, relax_kcal=True)]
        relax_kcal = min(rows_max, key=lambda r: (
            r["totals"].get("energyKcal") or 10**9, r["cost"])) if rows_max else None
    elif targets.kcal_min is not None:
        rows_min = [r for r in rows if r["protein"] >= targets.protein_min
                    and other_ok(r, relax_kcal=True)]
        relax_kcal = max(rows_min, key=lambda r: (
            r["totals"].get("energyKcal") or 0, -r["cost"])) if rows_min else None

    if not reachable:
        top = max(rows, key=lambda r: r["protein"]) if rows else None
        suffix = f"（整份最高 {top['protein']:.0f} g，{top['combo'][0].name}）" if top else ""
        message = f"这家店当前菜单达不到 {targets.protein_min:g} g 蛋白质{suffix}"
    elif best is None:
        message = "当前价格/脂肪/热量限制下没有任何可选组合"
    else:
        message = ""

    return {
        "max_protein": None if best is None else brief(best["combo"], best["totals"],
                                                        best["cost"], targets),
        "reach_price": None if reach_price is None else brief(
            reach_price["combo"], reach_price["totals"], reach_price["cost"], targets),
        "relax_fat": None if relax_fat is None else brief(
            relax_fat["combo"], relax_fat["totals"], relax_fat["cost"], targets),
        "relax_kcal": None if relax_kcal is None else brief(
            relax_kcal["combo"], relax_kcal["totals"], relax_kcal["cost"], targets),
        "reachable": reachable,
        "candidates": len(rows),
        "message": message,
        "basis": "official_standard",
    }
