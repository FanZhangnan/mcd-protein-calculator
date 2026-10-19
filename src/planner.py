"""Three-meal planner.

Math contract (fixed in this revision)
-------------------------------------
Two quantities must never be mixed:

* ``targets`` passed to :func:`plan_open` describe **the meals still open**.
  When a meal is already eaten, the caller subtracts it once and passes the
  *remaining* targets. This module never sees eaten intake.
* eaten intake is merged afterwards by :func:`merge_locked`, purely for display
  and for the day-level feasibility statement.

Item semantics
--------------
``nutrition``  per single serving (official single-item declaration)
``quantity``   number of servings
``price_ref``  unit reference price (menu price for one serving)

Totals always use ``nutrition * quantity``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable

WEIGHTS = {"kcal": 1.0, "protein": 1.0, "fat": 0.5, "carb": 0.5, "budget": 0.8}
DIVERSITY_PENALTY = 0.08

MEALS = ("breakfast", "lunch", "dinner")
MEAL_LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
NUTRI_KEYS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]


@dataclass
class Targets:
    kcal_min: float
    kcal_max: float
    protein_min: float
    fat_min: float | None = None
    fat_max: float | None = None
    carb_min: float | None = None
    carb_max: float | None = None
    budget: float | None = None

    @property
    def kcal_mid(self) -> float:
        return (self.kcal_min + self.kcal_max) / 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "kcal_min": self.kcal_min,
            "kcal_max": self.kcal_max,
            "protein_min": self.protein_min,
            "fat_min": self.fat_min,
            "fat_max": self.fat_max,
            "carb_min": self.carb_min,
            "carb_max": self.carb_max,
            "budget": self.budget,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Targets":
        def num(key: str, default=None):
            value = (data or {}).get(key, default)
            if value in ("", None):
                return default
            return float(value)

        return cls(
            kcal_min=num("kcal_min", 1800.0),
            kcal_max=num("kcal_max", 2200.0),
            protein_min=num("protein_min", 120.0),
            fat_min=num("fat_min"),
            fat_max=num("fat_max"),
            carb_min=num("carb_min"),
            carb_max=num("carb_max"),
            budget=num("budget"),
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
    is_estimate: bool = False
    estimate: dict[str, Any] | None = None
    meal: str = ""
    modification: list[dict[str, Any]] | None = None
    can_remove_sauce: bool = False
    removals: list[dict[str, Any]] | None = None
    #: ``official`` — the number is the official standard version;
    #: ``standard_unadjusted`` — a removal is *suggested* but the nutrition is
    #: still the standard version (strict mode); ``estimate`` — derived value.
    nutrition_basis: str = "official"
    removal_notes: list[str] = field(default_factory=list)

    def copy(self) -> "Item":
        return Item(
            code=self.code, name=self.name, kind=self.kind, nutrition=dict(self.nutrition) if self.nutrition else None,
            nutrition_status=self.nutrition_status, nutrition_note=self.nutrition_note,
            price_ref=self.price_ref, quantity=self.quantity, is_estimate=self.is_estimate,
            estimate=self.estimate, meal=self.meal, modification=self.modification,
            can_remove_sauce=self.can_remove_sauce, removals=self.removals,
            nutrition_basis=self.nutrition_basis,
            removal_notes=list(self.removal_notes or []),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "nutrition": self.nutrition,
            "nutrition_status": self.nutrition_status,
            "nutrition_note": self.nutrition_note,
            "nutrition_basis": self.nutrition_basis,
            "removal_notes": self.removal_notes or [],
            "price_ref": self.price_ref,
            "quantity": self.quantity,
            "line_total_ref": None if self.price_ref is None else round(self.price_ref * self.quantity, 2),
            "is_estimate": self.is_estimate,
            "estimate": self.estimate,
            "meal": self.meal,
            "modification": self.modification or [],
            "can_remove_sauce": self.can_remove_sauce,
            "removals": self.removals or [],
        }


@dataclass
class MealPlan:
    meal: str
    items: list[Item]
    nutrition: dict[str, float | None] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    price_ref: float = 0.0
    #: money actually booked for an eaten meal (only meaningful when ``locked``)
    amount_actual: float | None = None
    amount_source: str = "reference"      # manual | quoted | unknown | reference
    is_estimate: bool = False
    locked: bool = False
    #: a *trial* quote (calculate-price) — never the same thing as amount_actual
    quote: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "meal": self.meal,
            "meal_label": MEAL_LABEL.get(self.meal, self.meal),
            "items": [i.as_dict() for i in self.items],
            "nutrition": self.nutrition,
            "unknown": self.unknown,
            "price_ref": round(self.price_ref, 2),
            "amount_actual": self.amount_actual,
            "amount_source": self.amount_source,
            "quote": self.quote,
            "is_estimate": self.is_estimate,
            "locked": self.locked,
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
    weights: dict[str, float] = field(default_factory=lambda: dict(WEIGHTS))
    note: str = ""
    basis: str = "full_day"          # full_day | remaining | single_meal
    evaluated: dict[str, Any] | None = None
    quote_total: float | None = None
    budget_status: str = "reference"  # reference | quoted | not_evaluated
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
            "weights": self.weights,
            "note": self.note,
            "basis": self.basis,
            "quote_total": self.quote_total,
            "budget_status": self.budget_status,
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
            if value is None:
                values.append(None)
            else:
                values.append(value * qty)
        if not values or any(v is None for v in values):
            totals[key] = None
            unknown.append(key)
        else:
            totals[key] = round(sum(values), 1)
    return totals, unknown


_sum_nutrition = sum_nutrition  # backward-compatible alias


def sum_reference_cost(items: Iterable[Item]) -> float | None:
    total = 0.0
    for item in items:
        if item.price_ref is None:
            return None
        total += float(item.price_ref) * _qty(item)
    return round(total, 2)


def _violation(value: float, low: float | None, high: float | None) -> float:
    if low is not None and value < low:
        return low - value
    if high is not None and value > high:
        return value - high
    return 0.0


def _score(totals: dict[str, float | None], cost: float, targets: Targets) -> float:
    kcal = totals.get("energyKcal") or 0.0
    protein = totals.get("protein") or 0.0
    fat = totals.get("fat") or 0.0
    carb = totals.get("carbohydrate") or 0.0

    score = WEIGHTS["kcal"] * abs(kcal - targets.kcal_mid) / max(targets.kcal_mid, 1)
    score += WEIGHTS["protein"] * max(0.0, targets.protein_min - protein) / max(targets.protein_min, 1)

    fat_ref = targets.fat_max or targets.fat_min or 70.0
    carb_ref = targets.carb_max or targets.carb_min or 250.0
    score += WEIGHTS["fat"] * _violation(fat, targets.fat_min, targets.fat_max) / fat_ref
    score += WEIGHTS["carb"] * _violation(carb, targets.carb_min, targets.carb_max) / carb_ref

    if targets.budget:
        score += WEIGHTS["budget"] * max(0.0, cost - targets.budget) / targets.budget
    return score


def _fmt_bound(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:.0f}–{high:.0f}"
    if low is not None:
        return f"≥{low:.0f}"
    if high is not None:
        return f"≤{high:.0f}"
    return "未设置"


def violations(totals: dict[str, float | None], cost: float | None, targets: Targets) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(constraint: str, label: str, value: Any, target: str, gap: Any, unit: str) -> None:
        out.append({"constraint": constraint, "label": label, "value": value,
                    "target": target, "gap": gap, "unit": unit})

    kcal = totals.get("energyKcal")
    if kcal is None:
        add("unknown", "热量", None, _fmt_bound(targets.kcal_min, targets.kcal_max), None, "kcal")
    elif kcal < targets.kcal_min or kcal > targets.kcal_max:
        gap = kcal - (targets.kcal_min if kcal < targets.kcal_min else targets.kcal_max)
        add("kcal", "热量", kcal, _fmt_bound(targets.kcal_min, targets.kcal_max), round(gap, 1), "kcal")

    checks = [
        ("protein", "蛋白质", "protein_min", None, totals.get("protein"), "g"),
        ("fat", "脂肪", "fat_min", "fat_max", totals.get("fat"), "g"),
        ("carbo", "碳水", "carb_min", "carb_max", totals.get("carbohydrate"), "g"),
    ]
    for key, label, low_key, high_key, value, unit in checks:
        low = getattr(targets, low_key, None)
        high = getattr(targets, high_key, None) if high_key else None
        if low is None and high is None:
            continue
        if value is None:
            add("unknown", label, None, _fmt_bound(low, high), None, unit)
            continue
        gap = _violation(value, low, high)
        if not gap:
            continue
        signed = (value - low) if (low is not None and value < low) else (value - high)
        add(key, label, value, _fmt_bound(low, high), round(signed, 1), unit)

    if targets.budget is not None:
        if cost is None:
            add("unknown", "预算", None, f"≤{targets.budget:.2f}", None, "元")
        elif cost > targets.budget:
            add("budget", "预算", round(cost, 2), f"≤{targets.budget:.2f}", round(cost - targets.budget, 2), "元")

    return out


def is_feasible(totals: dict[str, float | None], cost: float | None, targets: Targets) -> bool:
    """Hard feasibility: every constrained nutrient known and inside bounds."""
    if totals.get("energyKcal") is None:
        return False
    if totals.get("protein") is None:
        return False
    if (targets.fat_min is not None or targets.fat_max is not None) and totals.get("fat") is None:
        return False
    if (targets.carb_min is not None or targets.carb_max is not None) and totals.get("carbohydrate") is None:
        return False
    if targets.budget is not None and cost is None:
        return False
    return not violations(totals, cost, targets)


# ------------------------------------------------------- single day evaluation
#: money basis for the day total. Never mix them.
MONEY_BASIS = ("quoted", "booked", "reference", "unknown", "no_budget")


def evaluate_day(meals: Iterable["MealPlan"], targets: Targets,
                 quotes: dict[str, dict[str, Any]] | None = None,
                 quote_attempted: bool | None = None) -> dict[str, Any]:
    """The one and only day-level verdict.

    Used by planning, recompute, replan and the „quote current plan“ endpoint so
    that the four paths can never drift apart.

    Money is split into three strictly separate quantities:

    ``booked_total``    money recorded for eaten meals (``amount_actual``)
    ``quote_total``     sum of successful trial quotes for the *open* meals
    ``day_total``       booked + quote (only when both are fully known)

    A missing booked amount or a failed quote makes the day total ``None`` and
    the budget ``not_evaluated`` — it is never silently replaced by 0 or by the
    reference price.
    """
    meals = list(meals)
    quotes = quotes or {}
    if quote_attempted is None:
        quote_attempted = bool(quotes)

    items = [i for m in meals for i in m.items]
    totals, unknown = sum_nutrition(items)
    locked = [m for m in meals if m.locked]
    open_meals = [m for m in meals if not m.locked]

    booked_known = all(m.amount_actual is not None for m in locked)
    booked_total = round(sum(float(m.amount_actual) for m in locked), 2) if booked_known else None
    reference_total = sum_reference_cost(items)
    open_reference = sum_reference_cost([i for m in open_meals for i in m.items])

    # nothing open -> there is nothing to quote; the day is fully booked
    if quote_attempted and not open_meals:
        quote_attempted = False

    quotes_ok = True
    quote_total: float | None = None
    if quote_attempted:
        for meal in open_meals:
            quote = quotes.get(meal.meal) or {}
            if quote.get("status") != "ok" or quote.get("amount") is None:
                quotes_ok = False
                break
        if quotes_ok:
            quote_total = round(sum(float(quotes[m.meal]["amount"]) for m in open_meals), 2)

    # ---- money basis -------------------------------------------------------
    if quote_attempted and booked_known and quotes_ok:
        basis, day_cost = "quoted", round((booked_total or 0.0) + (quote_total or 0.0), 2)
    elif not open_meals and booked_known:
        basis, day_cost = "booked", booked_total
    elif not quote_attempted and booked_known and open_reference is not None:
        basis, day_cost = "reference", round((booked_total or 0.0) + open_reference, 2)
    else:
        basis, day_cost = "unknown", None

    budget_status = "no_budget" if targets.budget is None else (
        "not_evaluated" if basis == "unknown" else basis)

    vio = violations(totals, day_cost, targets)
    if budget_status == "not_evaluated" and targets.budget is not None:
        reason = ("已吃记录缺少记账金额" if not booked_known
                  else "存在未成功试算的餐次")
        for entry in vio:
            if entry["constraint"] == "unknown" and entry["label"] == "预算":
                entry["detail"] = f"{reason}，完整日预算尚未评估（不按 0 元计算）"

    feasible = is_feasible(totals, day_cost, targets)
    return {
        "totals": totals,
        "unknown": unknown,
        "is_estimate": any(m.is_estimate for m in meals),
        "booked_total": booked_total,
        "booked_known": booked_known,
        "quote_total": quote_total if quote_attempted else None,
        "quotes_ok": quotes_ok if quote_attempted else None,
        "quote_attempted": quote_attempted,
        "open_reference_total": open_reference,
        "reference_total": reference_total,
        "day_cost": day_cost,
        "money_basis": basis,
        "budget_status": budget_status,
        "feasible": feasible,
        "violations": vio,
        "gaps": {
            "kcal": None if totals.get("energyKcal") is None else round(
                totals["energyKcal"] - targets.kcal_mid, 1),
            "protein": None if totals.get("protein") is None else round(
                totals["protein"] - targets.protein_min, 1),
            "fat": None if totals.get("fat") is None else round(
                totals["fat"] - (targets.fat_max if targets.fat_max is not None
                                 else (targets.fat_min or 0)), 1),
            "carbo": None if totals.get("carbohydrate") is None else round(
                totals["carbohydrate"] - (targets.carb_max if targets.carb_max is not None
                                          else (targets.carb_min or 0)), 1),
            "budget": None if (targets.budget is None or day_cost is None) else round(
                targets.budget - day_cost, 2),
        },
    }


# ------------------------------------------------------------------- planning
def build_meal_options(mains: list[Item], sides: list[Item], drinks: list[Item],
                       max_mains: int = 18, max_sides: int = 8, max_drinks: int = 10,
                       full_enumeration: bool = False) -> list[list[Item]]:
    mains = [m for m in mains if m.nutrition]
    sides = [s for s in sides if s.nutrition]
    drinks = [d for d in drinks if d.nutrition]
    if not full_enumeration:
        mains = mains[:max_mains]
        sides = sides[:max_sides]
        drinks = drinks[:max_drinks]

    options: list[list[Item]] = []
    for main, side, drink in product(mains, [None] + sides, [None] + drinks):
        combo = [main]
        if side:
            combo.append(side)
        if drink:
            combo.append(drink)
        options.append(combo)
    return options


def single_meal_rank(totals: dict[str, float | None], cost: float | None, targets: Targets) -> tuple:
    """Lexicographic order for one meal: protein gap, price, fat.

    ``plan_open`` filters the feasible set first, so inside that set the first
    key is always 0 and the order reduces to „cheapest, then least fat“ — which
    is exactly the product rule. The gap key still orders the fallback set when
    nothing is feasible.
    """
    protein = totals.get("protein")
    fat = totals.get("fat")
    gap = 10**6 if protein is None else max(0.0, targets.protein_min - protein)
    return (round(gap, 3), round(cost or 0.0, 2), round(fat or 0.0, 3))


def plan_open(pools: dict[str, dict[str, list[Item]]], targets: Targets,
              open_meals: Iterable[str] | None = None,
              beam: int = 400,
              ranker=None,
              full_enumeration: bool = False) -> PlanResult:
    """Solve only the given meals, against ``targets`` that already exclude eaten intake.

    ``None`` means "all three meals". An explicit empty list means "nothing is
    open" (every meal already eaten) and must NOT fall back to three meals.

    ``ranker`` overrides the default weighted score (single-meal mode uses a
    lexicographic key). ``full_enumeration`` disables the per-kind caps and the
    beam — used by the single-meal solver, whose candidate space is small.
    """
    if open_meals is None:
        open_meals = list(MEALS)
    else:
        open_meals = [m for m in open_meals if m in MEALS]

    if not open_meals:
        return PlanResult(
            feasible=True, meals=[], totals={}, unknowns=[], is_estimate=False,
            cost=0.0, violations=[], score=0.0,
            note="三餐均已记录，没有需要重新规划的餐次", basis="remaining",
            evaluated={"candidates": 0, "feasible": 0},
        )

    rank = ranker or _score
    per_meal: dict[str, list[list[Item]]] = {}
    for meal in open_meals:
        pool = pools.get(meal, {})
        per_meal[meal] = build_meal_options(
            pool.get("main", []), pool.get("side", []), pool.get("drink", []),
            full_enumeration=full_enumeration)

    empty = [m for m in open_meals if not per_meal[m]]
    if empty:
        return PlanResult(
            feasible=False, meals=[], totals={}, unknowns=[], is_estimate=False, cost=0.0,
            violations=[{
                "constraint": "pool", "label": "可售池", "value": 0,
                "target": "至少 1 个有营养数据的组合", "gap": None, "unit": "",
                "detail": "、".join(MEAL_LABEL[m] for m in empty) + " 没有可用单品",
            }],
            score=float("inf"), note="可售池为空，无法规划", basis="remaining",
        )

    beams: list[tuple[Any, list[list[Item]]]] = [(0.0, [])]
    total = len(open_meals)
    for idx, meal in enumerate(open_meals):
        candidates: list[tuple[Any, list[list[Item]]]] = []
        frac = (idx + 1) / total
        scaled = Targets(
            kcal_min=targets.kcal_min * frac, kcal_max=targets.kcal_max * frac,
            protein_min=targets.protein_min * frac,
            fat_min=None if targets.fat_min is None else targets.fat_min * frac,
            fat_max=None if targets.fat_max is None else targets.fat_max * frac,
            carb_min=None if targets.carb_min is None else targets.carb_min * frac,
            carb_max=None if targets.carb_max is None else targets.carb_max * frac,
            budget=None if targets.budget is None else targets.budget * frac,
        )
        for _s, chosen in beams:
            for option in per_meal[meal]:
                new_choice = chosen + [option]
                flat = [i for group in new_choice for i in group]
                totals, _ = sum_nutrition(flat)
                cost = sum_reference_cost(flat) or 0.0
                candidates.append((rank(totals, cost, scaled), new_choice))
        candidates.sort(key=lambda x: x[0])
        beams = candidates if full_enumeration else candidates[:beam]

    # Two-phase selection: feasible set first, preference second.
    evaluated: list[dict[str, Any]] = []
    for _s, choice in beams:
        flat = [i for group in choice for i in group]
        totals, unknown = sum_nutrition(flat)
        cost = sum_reference_cost(flat)
        mains = [i.code for group in choice for i in group if i.kind == "main"]
        dup = len(mains) - len(set(mains))
        feasible = is_feasible(totals, cost, targets)
        sc = rank(totals, cost or 0.0, targets)
        if isinstance(sc, (int, float)):
            sc += DIVERSITY_PENALTY * dup
        evaluated.append({
            "choice": choice, "totals": totals, "unknown": unknown, "cost": cost,
            "feasible": feasible, "score": sc,
        })

    feasible_set = [e for e in evaluated if e["feasible"]]
    if feasible_set:
        best = min(feasible_set, key=lambda e: e["score"])
        note = ""
    else:
        best = min(evaluated, key=lambda e: e["score"])
        note = "启发式候选集中没有完全满足硬约束的方案；以下为最接近方案，不视为达标，也不代表全局无解。"

    meals_out: list[MealPlan] = []
    for meal, group in zip(open_meals, best["choice"]):
        nutrition, unknown = sum_nutrition(group)
        meals_out.append(MealPlan(
            meal=meal, items=group, nutrition=nutrition, unknown=unknown,
            price_ref=sum_reference_cost(group) or 0.0,
            is_estimate=any(i.is_estimate for i in group),
        ))

    cost = best["cost"]
    return PlanResult(
        feasible=bool(feasible_set),
        meals=meals_out,
        totals=best["totals"],
        unknowns=best["unknown"],
        is_estimate=any(m.is_estimate for m in meals_out),
        cost=cost if cost is not None else 0.0,
        violations=violations(best["totals"], cost, targets),
        score=best["score"],
        note=note,
        basis="remaining" if len(open_meals) < len(MEALS) else "full_day",
        evaluated={"candidates": len(evaluated), "feasible": len(feasible_set)},
    )


def single_meal_frontier(pools: dict[str, dict[str, list[Item]]], targets: Targets,
                         meal: str) -> dict[str, Any]:
    """What this store can actually reach for one meal, under strict rules.

    Every number is the official standard-version value. Nothing here relaxes
    the structure rule (one main, quantity 1) — only fat or price is relaxed,
    one at a time, exactly as reported to the user.
    """
    pool = pools.get(meal, {})
    combos = build_meal_options(pool.get("main", []), pool.get("side", []),
                                pool.get("drink", []), full_enumeration=True)
    rows: list[dict[str, Any]] = []
    for combo in combos:
        totals, _ = sum_nutrition(combo)
        cost = sum_reference_cost(combo)
        protein = totals.get("protein")
        if protein is None or cost is None:
            continue
        main = next((i for i in combo if i.kind == "main"), None)
        rows.append({
            "combo": combo,
            "code": main.code if main else "",
            "name": main.name if main else "",
            "protein": round(float(protein), 1),
            "fat": None if totals.get("fat") is None else round(float(totals["fat"]), 1),
            "carb": None if totals.get("carbohydrate") is None else round(float(totals["carbohydrate"]), 1),
            "kcal": None if totals.get("energyKcal") is None else round(float(totals["energyKcal"]), 1),
            "cost": round(float(cost), 2),
            "items": [i.name for i in combo],
        })

    def other_ok(row: dict[str, Any]) -> bool:
        """Constraints that neither relaxation is allowed to break."""
        if targets.carb_max is not None and (row["carb"] is None or row["carb"] > targets.carb_max):
            return False
        if row["kcal"] is None:
            return targets.kcal_min <= 0 and targets.kcal_max >= 10**6
        if row["kcal"] < targets.kcal_min or row["kcal"] > targets.kcal_max:
            return False
        return True

    def hard_ok(row: dict[str, Any], *, relax_fat: bool = False,
                relax_price: bool = False) -> bool:
        if not other_ok(row):
            return False
        if not relax_fat and targets.fat_max is not None and (
                row["fat"] is None or row["fat"] > targets.fat_max):
            return False
        if not relax_price and targets.budget is not None and row["cost"] > targets.budget:
            return False
        return True

    def brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {k: row[k] for k in ("code", "name", "protein", "fat", "carb",
                                    "kcal", "cost", "items")}

    hard = [r for r in rows if hard_ok(r)]
    best = max(hard, key=lambda r: (r["protein"], -r["cost"], -(r["fat"] or 0))) if hard else None
    reachable_rows = [r for r in rows if r["protein"] >= targets.protein_min]
    reachable = bool(reachable_rows)

    relax_fat_rows = [r for r in rows
                      if r["protein"] >= targets.protein_min and hard_ok(r, relax_fat=True)]
    relax_fat = min(relax_fat_rows, key=lambda r: ((r["fat"] if r["fat"] is not None else 10**6),
                                                   r["cost"])) if relax_fat_rows else None

    relax_price_rows = [r for r in rows
                        if r["protein"] >= targets.protein_min and hard_ok(r, relax_price=True)]
    relax_price = min(relax_price_rows, key=lambda r: (r["cost"], (r["fat"] or 0))) if relax_price_rows else None

    if not reachable:
        top = max(rows, key=lambda r: r["protein"]) if rows else None
        suffix = f"（整份最高 {top['protein']:.0f} g，{top['name']}）" if top else ""
        message = f"这家店当前菜单达不到 {targets.protein_min:g} g 蛋白质{suffix}"
    elif best is None:
        message = "当前价格/脂肪限制下没有任何可选组合"
    elif relax_fat is None and relax_price is None:
        message = "只放宽脂肪或只放宽价格，仍然达不到蛋白质目标"
    elif relax_fat is None:
        message = "只放宽脂肪仍然达不到蛋白质目标"
    elif relax_price is None:
        message = "只放宽价格仍然达不到蛋白质目标"
    else:
        message = ""

    return {
        "max_protein": brief(best),
        "relax_fat": brief(relax_fat),
        "relax_price": brief(relax_price),
        "reachable": reachable,
        "candidates": len(rows),
        "message": message,
        "basis": "official_standard",
    }


def merge_locked(open_plan: PlanResult, locked_meals: list[MealPlan],
                 day_targets: Targets | None = None,
                 open_targets: Targets | None = None) -> PlanResult:
    """Display-level merge of eaten meals and open meals.

    When ``day_targets`` is supplied the verdict is recomputed on the **whole
    day** (eaten + open), which is algebraically identical to judging the open
    meals against the remaining targets — but it is the only wording used in the
    UI, and it still works when nothing is open at all.

    ``day_targets=None`` keeps the legacy open-meal verdict for callers that
    only have the remainder (used by the ``plan()`` test wrapper).
    """
    if not locked_meals:
        return open_plan
    all_items = [i for m in locked_meals for i in m.items] + [i for m in open_plan.meals for i in m.items]
    totals, unknown = sum_nutrition(all_items)
    merged = [m for m in locked_meals] + list(open_plan.meals)
    merged.sort(key=lambda m: MEALS.index(m.meal))

    if day_targets is not None:
        evaluation = evaluate_day(merged, day_targets)
        return PlanResult(
            feasible=evaluation["feasible"],
            meals=merged,
            totals=evaluation["totals"],
            unknowns=evaluation["unknown"],
            is_estimate=any(m.is_estimate for m in merged),
            cost=evaluation["day_cost"] or 0.0,
            violations=evaluation["violations"],
            score=open_plan.score,
            note=open_plan.note if evaluation["feasible"] else (
                open_plan.note or "全天合计不满足目标，不视为达标"),
            basis="remaining" if open_plan.meals else "full_day",
            evaluated=open_plan.evaluated,
            quote_total=open_plan.quote_total,
            budget_status=open_plan.budget_status,
        )

    costs = [m.amount_actual if m.amount_actual is not None else m.price_ref for m in locked_meals]
    known_cost = all(c is not None for c in costs)
    cost = (sum(c for c in costs if c is not None) + open_plan.cost) if known_cost else open_plan.cost
    return PlanResult(
        feasible=open_plan.feasible,
        meals=merged,
        totals=totals,
        unknowns=unknown,
        is_estimate=any(m.is_estimate for m in merged),
        cost=cost,
        violations=open_plan.violations,
        score=open_plan.score,
        note=open_plan.note,
        basis="remaining",
        evaluated=open_plan.evaluated,
        quote_total=open_plan.quote_total,
        budget_status=open_plan.budget_status,
    )


def plan(pools: dict[str, dict[str, list[Item]]], targets: Targets,
         locked: dict[str, list[Item]] | None = None, beam: int = 400,
         day_targets: Targets | None = None) -> PlanResult:
    """Convenience wrapper: locked meals are excluded from solving, merged for display."""
    locked = locked or {}
    locked_meals: list[MealPlan] = []
    for meal in MEALS:
        items = locked.get(meal)
        if not items:
            continue
        nutrition, unknown = sum_nutrition(items)
        locked_meals.append(MealPlan(
            meal=meal, items=items, nutrition=nutrition, unknown=unknown,
            price_ref=sum_reference_cost(items) or 0.0,
            is_estimate=any(i.is_estimate for i in items), locked=True,
        ))
    if locked_meals:
        # ``targets`` is the remainder when locked meals exist; ``day_targets``
        # is optional and only used for the day-level statement.
        open_meals = [m for m in MEALS if m not in locked]
        result = plan_open(pools, targets, open_meals, beam)
        return merge_locked(result, locked_meals, day_targets)
    return plan_open(pools, targets, MEALS, beam)
