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


def main_count(rounds: list[dict]) -> int:
    """How many main-dish portions a selected combo really contains (min 1).

    堡堡随心拼 picks two burgers (主食轮 quantity=2), 薄皮V翅双拼十翅 two portions
    of wings — counting either as "one main" let them stack on top of two
    burgers. The floor of 1 covers the 酥脆无双 / 麦乐鸡 / BFF 四宫格 style combos
    whose rounds are all sides and a drink: that is still a whole meal, so it
    must take a main slot rather than ride along for free.
    """
    total = 0
    for rnd in rounds:
        if (rnd.get("role") or "") != "主食":
            continue
        try:
            total += int(rnd.get("quantity") or 1)
        except (TypeError, ValueError):
            total += 1
    return max(1, total)


def selection_rounds(rounds: list[dict], selection: dict[str, dict]) -> list[dict]:
    out = []
    for rnd in rounds:
        choice = selection.get(str(rnd.get("id")))
        if not choice:
            continue
        # A mixed round (e.g. 随心配's 粉区 holds drinks and pies) still gets a
        # per-choice label so the card never says「主食：无糖可口可乐」.
        choice_kind = catalog_mod.classify(choice.get("name") or "")
        out.append({
            "round": str(rnd.get("id")),
            "round_name": rnd.get("name") or "",
            "role": ROUND_ROLE.get(choice_kind) or ROUND_ROLE.get(round_role(rnd), "主食"),
            "code": str(choice.get("code")),
            "name": choice.get("name") or "",
            "diff_price": parse_diff_price(choice.get("diffPrice")),
            # A round may ask for 2 mains / 3 sides (``quantity``); both the
            # nutrition sum and the roundList quantity must honour it.
            "quantity": round_quantity(rnd),
        })
    return out


def round_quantity(rnd: dict) -> int:
    try:
        return max(1, int(rnd.get("quantity") or 1))
    except (TypeError, ValueError):
        return 1


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
            "comboItemList": [{"code": str(chosen.get("code")),
                               "quantity": round_quantity(rnd)}],
        })
    return out


def reference_price(menu_price: float, rounds: list[dict],
                    selection: dict[str, dict]) -> float:
    total = float(menu_price)
    for rnd in rounds:
        choice = selection.get(str(rnd.get("id")))
        if choice:
            total += parse_diff_price(choice.get("diffPrice")) * round_quantity(rnd)
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
        qty = round_quantity(rnd)
        for key in NUTRI_KEYS:
            totals[key] += nutrition[key] * qty
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


def _drink_rank(choice: dict, nutrition: dict) -> int | None:
    """0 零度可乐 > 1 纯悦 > 2 纯牛奶 > 3 美式 > 4 other drink < 20 kcal."""
    name = choice.get("name") or ""
    if "零度" in name or "无糖可口可乐" in name:
        return 0
    if "纯悦" in name:
        return 1
    if "纯牛奶" in name:
        return 2
    if "美式" in name:
        return 3
    kcal = nutrition.get("energyKcal")
    if kcal is not None and float(kcal) < DRINK_MAX_KCAL:
        return 4
    return None


def replacement_candidates(round_payload: dict, chosen: dict,
                           catalog: catalog_mod.NutritionCatalog) -> list[dict]:
    """Only options this round really offers, never an item added from outside."""
    kind = catalog_mod.classify(chosen.get("name") or "")
    priced: list[tuple] = []
    for index, choice in enumerate(round_payload.get("choices") or []):
        if str(choice.get("code")) == str(chosen.get("code")):
            continue
        nutrition = nutrition_for(choice, catalog)
        if nutrition is None:
            continue
        if kind == "drink":
            rank = _drink_rank(choice, nutrition)
            if rank is None:
                continue
            # within one rank keep the POS order ("first available")
            priced.append((rank, index, 0.0, 0.0, str(choice.get("code")), choice))
        elif kind in ("main", "side"):
            diff = parse_diff_price(choice.get("diffPrice"))
            protein = float(nutrition.get("protein") or 0.0)
            fat = float(nutrition.get("fat") or 0.0)
            # diff <= 0 first, then most protein, then least fat, then code
            priced.append((0 if diff <= 0 else 1, diff if diff > 0 else 0.0,
                           -protein, fat, str(choice.get("code")), choice))
        # sauces / toys / desserts have no nutrition-bearing substitute
    priced.sort(key=lambda row: row[:5])
    return [row[5] for row in priced]


def apply_replacements(rounds: list[dict], selection: dict[str, dict],
                       catalog: catalog_mod.NutritionCatalog
                       ) -> tuple[dict[str, dict], list[dict[str, Any]]]:
    """Fill a round whose chosen item has no nutrition with another option.

    The substitute must be one of *this round's* choices; nothing is invented.
    If the round offers no option with official nutrition, the variant stays
    unknown (and is excluded later).
    """
    out = dict(selection)
    replacements: list[dict[str, Any]] = []
    for rnd in rounds:
        key = str(rnd.get("id"))
        chosen = out.get(key)
        if not chosen:
            continue
        if nutrition_for(chosen, catalog) is not None:
            continue
        candidates = replacement_candidates(rnd, chosen, catalog)
        if not candidates:
            continue
        pick = candidates[0]
        out[key] = pick
        replacements.append({
            "round": key,
            "round_name": rnd.get("name") or "",
            "from": chosen.get("name") or str(chosen.get("code")),
            "to": pick.get("name") or str(pick.get("code")),
            "reason": "原选项暂无官方营养",
        })
    return out, replacements


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
        selection, replacements = apply_replacements(rounds, selection, catalog)
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
            "replacements": replacements,
            "round_list": round_list(rounds, selection),
            "reference_price": reference_price(menu_price, rounds, selection),
        }
        if nutrition is None:
            excluded.append({**base, "reason": "nutrition_unknown",
                             "detail": "缺官方营养：" + "、".join(missing),
                             "missing": missing})
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

    ``combos_available + combos_excluded == combos_found`` by construction: a
    combo is "available" as soon as one of its variants is usable, and
    "excluded" otherwise. ``variants_excluded`` is a *variant*-level number and
    is reported separately — mixing it into the combo count is what made the
    page read "识别 50 … 排除 45" while 17 combos were in use.
    """
    meta = meta or {}
    excluded = meta.get("excluded") or []
    reason_entries: dict[str, int] = {}
    for item in excluded:
        reason = item.get("reason") or "other"
        reason_entries[reason] = reason_entries.get(reason, 0) + 1
    variants = [v for v in meta.get("variants") or [] if usable_variant(v)]
    usable_codes = {str(v.get("combo_code")) for v in variants}

    combo_reasons: dict[str, int] = {}
    reason_of: dict[str, str] = {}
    unavailable: dict[str, set[str]] = {}
    for item in excluded:
        code = str(item.get("combo_code") or "")
        if not code or code in usable_codes:
            continue
        reason = item.get("reason") or "other"
        if code not in reason_of:
            reason_of[code] = reason
            combo_reasons[reason] = combo_reasons.get(reason, 0) + 1
        for name in item.get("missing") or []:
            unavailable.setdefault(str(name), set()).add(item.get("combo_name") or code)

    combos_found = int(meta.get("combo_count") or 0)
    combos_available = len(usable_codes)
    combos_excluded = len(reason_of)
    return {
        "available": bool(meta),
        "source": source,
        "built_at": built_at,
        "single_round": len(meta.get("single_round") or []),
        "single_round_excluded": len(meta.get("single_round_excluded") or []),
        "checked": meta.get("checked") or 0,
        # combos the POS reports (rounds >= 2), not the combos that survived
        "combos_found": combos_found,
        "combos_available": combos_available,
        "combos_excluded": combos_excluded,
        "combos_consistent": combos_available + combos_excluded == combos_found,
        "combo_reasons": combo_reasons,
        "variants_used": len(variants),
        "variants_excluded": len(excluded),
        # legacy entry-level count (kept for the audit list / old snapshots)
        "excluded": len(excluded),
        "reasons": reason_entries,
        "unavailable_products": [
            {"name": name, "combos": sorted(combos)} for name, combos in
            sorted(unavailable.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ],
        "excluded_categories": meta.get("excluded_categories") or [],
        "items": [{"code": e.get("combo_code"), "name": e.get("combo_name"),
                   "variant": e.get("variant"), "reason": e.get("reason"),
                   "detail": e.get("detail")} for e in excluded[:limit]],
        "note": REPORT_NOTE,
    }
