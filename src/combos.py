"""Combo (套餐) detection, variants and strict reference pricing.

Design rules
------------
* A SKU **is** a combo when ``query-meal-detail`` returns rounds. The name is
  never the decision — a product called "…套餐" with no rounds stays a single
  item, and a combo whose name lacks the word is still a combo.
* A variant is one concrete selection: one choice per round. Two variants per
  combo at most — the POS default, and one "muscle" variant that only swaps the
  drink round for a whitelisted drink and the side round for the highest-protein
  option.
* Nutrition is the sum of the *official single-item* values of the selected
  choices. If any choice cannot be matched, the whole variant is
  ``nutrition_unknown`` — never estimated, never treated as zero.
* The reference price is ``menu price + Σ diffPrice``. It must equal the real
  ``calculate-price`` result for the same ``roundList``; a mismatch excludes the
  variant instead of guessing which number is right.
* Combos whose price depends on an account benefit (早餐卡 / 麦金卡 / member
  price) never enter the candidate pool: this tool has no account context.
"""

from __future__ import annotations

import re
from typing import Any

import catalog as catalog_mod

#: category / tag / discount markers that mean "this price needs an account
#: benefit". Recorded in the catalog as ``excluded_categories``.
MEMBERSHIP_CATEGORY_HINTS = ("早餐卡", "麦金卡", "会员", "专享")
MEMBERSHIP_TAG_HINTS = ("早餐卡", "麦金卡", "会员", "专享")
#: ``discountType`` is the POS wording, e.g. "随单购早餐卡优惠"
MEMBERSHIP_DISCOUNT_HINTS = ("早餐卡", "麦金卡", "会员", "专享", "优惠")

#: drinks allowed in the muscle variant (same whitelist as single meals)
DRINK_HINTS = ("纯牛奶", "零度可乐", "美式")
DRINK_MAX_KCAL = 20.0

NUTRI_KEYS = ("energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium")

DRINK_ROUND_HINTS = ("饮料", "饮品", "咖啡", "可乐")
SIDE_ROUND_HINTS = ("小食", "配餐", "薯条", "鸡块")
ROUND_ROLE = {"main": "主食", "side": "小食", "drink": "饮品"}


def parse_diff_price(raw: Any) -> float:
    """``"+ ¥1.5"`` / ``"- ¥2"`` / ``""`` → float. Never negative-guesses."""
    text = str(raw or "").replace("¥", "").replace("￥", "").strip()
    if not text:
        return 0.0
    negative = text.startswith("-")
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits:
        return 0.0
    try:
        value = float(digits)
    except ValueError:
        return 0.0
    return -value if negative else value


def detail_data(payload: dict | None) -> dict:
    return (payload or {}).get("data") or {}


def rounds_of(payload: dict | None) -> list[dict]:
    """Rounds with at least one choice; empty means "not a combo"."""
    data = detail_data(payload)
    return [r for r in (data.get("rounds") or []) if r.get("choices")]


#: A product with a single round is a *configurable single item* (flavour,
#: size, one piece of chicken), not a multi-part combo. Counting those as combos
#: would let 新地 / 麦旋风 / 麦麦脆汁鸡1块 bypass the side & drink structure and
#: win the ranking against real meals.
MIN_COMBO_ROUNDS = 2


def is_combo_detail(payload: dict | None) -> bool:
    """Authoritative combo test: the POS returns **two or more** rounds."""
    return len(rounds_of(payload)) >= MIN_COMBO_ROUNDS


def is_single_round_detail(payload: dict | None) -> bool:
    """A product with exactly one round: a configurable *single* item.

    新地 / 麦旋风 / 麦麦脆汁鸡1块 answer with one round (flavour or size).
    They are not combos — they are one product that can be ordered on its own,
    so they belong in the single-item pools, priced by their menu price.
    """
    return len(rounds_of(payload)) == 1


def single_round_choice(payload: dict | None) -> dict | None:
    """The default choice of a one-round product (its own composition)."""
    rounds = rounds_of(payload)
    if len(rounds) != 1:
        return None
    return default_choice(rounds[0])


def default_choice(round_payload: dict) -> dict:
    choices = round_payload.get("choices") or []
    for choice in choices:
        if str(choice.get("isDefault")) == "1":
            return choice
    return choices[0]


def _choice_kind(choice: dict) -> str:
    return catalog_mod.classify(choice.get("name") or "")


def round_role(round_payload: dict) -> str:
    """main / side / drink, from the choices first and the round name second."""
    choices = round_payload.get("choices") or []
    kinds = [_choice_kind(c) for c in choices]
    if kinds and all(k == "drink" for k in kinds):
        return "drink"
    name = round_payload.get("name") or ""
    if any(h in name for h in DRINK_ROUND_HINTS):
        return "drink"
    return "side" if kinds and all(k == "side" for k in kinds) else "main"


def nutrition_for(choice: dict, catalog: catalog_mod.NutritionCatalog) -> dict | None:
    match = catalog.lookup(str(choice.get("code")), choice.get("name") or "")
    if not match.usable or not match.nutrition:
        return None
    if any(match.nutrition.get(k) is None for k in NUTRI_KEYS):
        return None
    return {k: float(match.nutrition[k]) for k in NUTRI_KEYS}


def is_whitelist_drink(choice: dict, nutrition: dict | None) -> bool:
    name = choice.get("name") or ""
    if any(h in name for h in DRINK_HINTS):
        return True
    kcal = (nutrition or {}).get("energyKcal")
    return kcal is not None and float(kcal) < DRINK_MAX_KCAL


def default_selection(rounds: list[dict]) -> dict[str, dict]:
    return {str(r.get("id")): default_choice(r) for r in rounds}


def muscle_selection(rounds: list[dict],
                     catalog: catalog_mod.NutritionCatalog) -> dict[str, dict]:
    """Default, except: drink → whitelisted drink, side → most protein."""
    selection = default_selection(rounds)
    for rnd in rounds:
        role = round_role(rnd)
        key = str(rnd.get("id"))
        default_p = float((nutrition_for(default_choice(rnd), catalog) or {}).get("protein") or 0.0)
        if role == "drink":
            best = None
            for choice in rnd.get("choices") or []:
                nutrition = nutrition_for(choice, catalog)
                if not is_whitelist_drink(choice, nutrition):
                    continue
                # equal protein must not buy a pricier drink
                score = (float((nutrition or {}).get("protein") or 0.0),
                         -parse_diff_price(choice.get("diffPrice")),
                         -float((nutrition or {}).get("energyKcal") or 1e9))
                if best is None or score > best[0]:
                    best = (score, choice)
            # never trade protein away just to get a whitelisted drink
            if best is not None and float((best[1] and nutrition_for(best[1], catalog) or {})
                                          .get("protein") or 0.0) >= default_p:
                selection[key] = best[1]
        elif role == "side":
            best = None
            for choice in rnd.get("choices") or []:
                nutrition = nutrition_for(choice, catalog)
                if nutrition is None:
                    continue
                score = float(nutrition.get("protein") or 0.0)
                if best is None or score > best[0]:
                    best = (score, choice)
            if best is not None and best[0] > default_p:
                selection[key] = best[1]
    return selection


def selection_rounds(rounds: list[dict], selection: dict[str, dict]) -> list[dict]:
    out = []
    for rnd in rounds:
        choice = selection.get(str(rnd.get("id")))
        if not choice:
            continue
        out.append({
            "round": str(rnd.get("id")),
            "round_name": rnd.get("name") or "",
            "role": ROUND_ROLE.get(round_role(rnd), "主食"),
            "code": str(choice.get("code")),
            "name": choice.get("name") or "",
            "diff_price": parse_diff_price(choice.get("diffPrice")),
        })
    return out


def round_list(rounds: list[dict], selection: dict[str, dict]) -> list[dict]:
    """Only rounds that differ from the POS default need to be sent."""
    out = []
    for rnd in rounds:
        key = str(rnd.get("id"))
        chosen = selection.get(key)
        if not chosen:
            continue
        if str(chosen.get("code")) == str(default_choice(rnd).get("code")):
            continue
        out.append({
            "round": key,
            "comboItemList": [{"code": str(chosen.get("code")), "quantity": 1}],
        })
    return out


def reference_price(menu_price: float, rounds: list[dict],
                    selection: dict[str, dict]) -> float:
    total = float(menu_price)
    for rnd in rounds:
        choice = selection.get(str(rnd.get("id")))
        if choice:
            total += parse_diff_price(choice.get("diffPrice"))
    return round(total, 2)


def variant_nutrition(rounds: list[dict], selection: dict[str, dict],
                      catalog: catalog_mod.NutritionCatalog) -> tuple[dict | None, list[str]]:
    """Sum of official values; a single miss makes the whole variant unknown."""
    totals = {k: 0.0 for k in NUTRI_KEYS}
    missing: list[str] = []
    for rnd in rounds:
        choice = selection.get(str(rnd.get("id")))
        if not choice:
            continue
        nutrition = nutrition_for(choice, catalog)
        if nutrition is None:
            missing.append(choice.get("name") or str(choice.get("code")))
            continue
        for key in NUTRI_KEYS:
            totals[key] += nutrition[key]
    if missing:
        return None, missing
    return {k: round(v, 1) for k, v in totals.items()}, []


def membership_markers(categories: list[str], tags: list[str],
                       info: dict) -> list[str]:
    """Evidence that this price needs an account benefit, if any."""
    hits: list[str] = []
    for name in categories:
        if any(h in name for h in MEMBERSHIP_CATEGORY_HINTS):
            hits.append(f"分类「{name}」")
    for tag in tags:
        if any(h in tag for h in MEMBERSHIP_TAG_HINTS):
            hits.append(f"标签「{tag}」")
    discount = str(info.get("discountType") or "")
    if discount and any(h in discount for h in MEMBERSHIP_DISCOUNT_HINTS):
        hits.append(f"优惠「{discount}」")
    if info.get("withOrder"):
        hits.append("需随单购买权益卡")
    return hits


def build_variants(combo: dict, catalog: catalog_mod.NutritionCatalog) -> tuple[list[dict], list[dict]]:
    """Return (variants, excluded). Each variant carries its own evidence."""
    rounds = combo["rounds"]
    menu_price = float(combo["menu_price"])
    variants: list[dict] = []
    excluded: list[dict] = []

    plans = [("default", default_selection(rounds)),
             ("muscle", muscle_selection(rounds, catalog))]
    seen: set[tuple] = set()
    for name, selection in plans:
        picked = selection_rounds(rounds, selection)
        signature = tuple((p["round"], p["code"]) for p in picked)
        if signature in seen:
            continue
        seen.add(signature)
        nutrition, missing = variant_nutrition(rounds, selection, catalog)
        base = {
            "combo_code": combo["code"], "combo_name": combo["name"],
            "variant": name, "menu_price": menu_price,
            "rounds": picked,
            "round_list": round_list(rounds, selection),
            "reference_price": reference_price(menu_price, rounds, selection),
        }
        if nutrition is None:
            excluded.append({**base, "reason": "nutrition_unknown",
                             "detail": "缺官方营养：" + "、".join(missing)})
            continue
        variants.append({**base, "nutrition": nutrition, "status": "pending"})
    return variants, excluded


def summarise(variants: list[dict], excluded: list[dict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in excluded:
        counts[item.get("reason") or "other"] = counts.get(item.get("reason") or "other", 0) + 1
    return {"variants": len(variants), "excluded": len(excluded), "reasons": counts}


def usable_variant(variant: dict) -> bool:
    """A variant only becomes a candidate when price and nutrition are complete."""
    if variant.get("status") != "ok" or variant.get("quoted_price") is None:
        return False
    nutrition = variant.get("nutrition") or {}
    return all(nutrition.get(key) is not None for key in NUTRI_KEYS)


REPORT_NOTE = ("套餐目录由构建脚本逐款核对营养与真实试算价；"
               "营养未知、价格不一致与会员价套餐已排除。")


def report_from_meal(meta: dict | None, *, source: str | None = None,
                     built_at: str | None = None, limit: int = 30) -> dict[str, Any]:
    """The "套餐核查" counts for one meal, derived from the catalog entry.

    This is the only place these numbers are computed: the API, the static
    snapshot and the page all read the same dict, so "识别 N 个套餐" can never
    disagree with ``data/combo_catalog.json``.
    """
    meta = meta or {}
    excluded = meta.get("excluded") or []
    counts: dict[str, int] = {}
    for item in excluded:
        reason = item.get("reason") or "other"
        counts[reason] = counts.get(reason, 0) + 1
    variants = [v for v in meta.get("variants") or [] if usable_variant(v)]
    return {
        "available": bool(meta),
        "source": source,
        "built_at": built_at,
        "single_round": len(meta.get("single_round") or []),
        "single_round_excluded": len(meta.get("single_round_excluded") or []),
        "checked": meta.get("checked") or 0,
        # combos the POS reports (rounds >= 2), not the combos that survived
        "combos_found": meta.get("combo_count") or 0,
        "variants_used": len(variants),
        "excluded": len(excluded),
        "reasons": counts,
        "excluded_categories": meta.get("excluded_categories") or [],
        "items": [{"code": e.get("combo_code"), "name": e.get("combo_name"),
                   "variant": e.get("variant"), "reason": e.get("reason"),
                   "detail": e.get("detail")} for e in excluded[:limit]],
        "note": REPORT_NOTE,
    }
