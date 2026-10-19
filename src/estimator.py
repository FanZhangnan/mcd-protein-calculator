"""Customisation nutrition estimation.

The official table only publishes *finished, standard single-serving* items.
There is no official sauce / patty / cheese component data for mainland China,
so any customised figure is an ESTIMATE and must carry its provenance.

Two named scenarios are provided. They are *scenarios*, not an interval:
each rests on a different substitute ingredient and a different portion
assumption, so they must never be presented as a reliable min/max bound.
"""

from __future__ import annotations

from typing import Any

KJ_PER_KCAL = 4.184

NUTRI_KEYS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]

# --------------------------------------------------------------------- sources
SOURCES: dict[str, dict[str, Any]] = {
    "S1": {
        "publisher": "麦当劳中国官网 / MCP list-nutrition-foods",
        "url": "https://www.mcdonalds.com.cn/product/Spicy-Chicken-Filet",
        "checked": "2026-10-18",
        "kind": "official",
        "basis": "每份（标准版，2025年4月更新）",
        "note": "官方标准版单份标示值，不是门店实际供应份量的测量值",
    },
    "S2": {
        "publisher": "百度健康用户发布内容（自称整理自台湾官网营养计算器）",
        "url": "https://health.baidu.com/m/detail/ar_1974821526389170820",
        "checked": "2026-10-18",
        "kind": "secondary_unverified",
        "basis": "每份（未标注克重）",
        "note": "非中国大陆同款；作者自述依据台湾官网，未在台湾官网原始页面核实；"
                "不能称为大陆麦香鸡酱官方数据",
    },
    "S3": {
        "publisher": "中国妇幼保健协会（引用市售品牌标签）",
        "url": "https://www.cmcha.org/detail/17126540250861350636.html",
        "checked": "2026-10-18",
        "kind": "substitute_label",
        "basis": "每 100 g 标签值",
        "note": "普通蛋黄酱型沙拉酱，非麦当劳产品；仅作替代配料估算",
    },
}

# Per-portion contribution of the sauce, per scenario.
SCENARIOS: dict[str, dict[str, Any]] = {
    "official_only": {
        "label": "不估算（仅官方标准版）",
        "description": "不做去酱估算，展示标准版参考值，定制后营养标记为未知。",
        "source": None,
        "removed": None,
    },
    "tw_sauce_serving": {
        "label": "情景A：按『麦香鸡酱 每份』扣除",
        "description": "以二手整理中『麦香鸡酱 每份 68 kcal』作为整份酱料贡献扣除。",
        "source": "S2",
        "substitute": "台湾麦当劳「麥香鷄醬」（配方与中国大陆不同）",
        "target": "中国大陆门店 POS 特调项「麦香鸡酱」(code 100136)",
        "assumption": "整份酱料即该条目的『每份』，未额外假设克重",
        "removed": {
            "energyKcal": 68.0,
            "protein": 0.2,
            "fat": 6.5,
            "carbohydrate": 2.3,
            "sodium": None,   # 该来源未提供钠
            "calcium": None,
        },
    },
    "mayo_15g": {
        "label": "情景B：按『普通蛋黄酱 15 g』扣除",
        "description": "以市售蛋黄酱标签每 100 g 数值 × 15 g 份量假设扣除。",
        "source": "S3",
        "substitute": "市售普通蛋黄酱型沙拉酱",
        "target": "中国大陆门店 POS 特调项「麦香鸡酱」(code 100136)",
        "assumption": "假设整份酱料为 15 g —— 该克重未获门店确认，属场景假设",
        "removed": {
            "energyKj": 2912.0 * 0.15,
            "protein": 2.7 * 0.15,
            "fat": 76.5 * 0.15,
            "carbohydrate": 2.1 * 0.15,
            "sodium": 637.0 * 0.15,
            "calcium": None,
        },
    },
}

SCENARIO_KEYS = list(SCENARIOS)


def _kcal_to_kj(kcal: float) -> float:
    return kcal * KJ_PER_KCAL


def _round(value: float | None, ndigits: int = 1) -> float | None:
    return None if value is None else round(value, ndigits)


def scenario_meta(key: str) -> dict[str, Any]:
    sc = SCENARIOS[key]
    meta = {
        "key": key,
        "label": sc["label"],
        "description": sc["description"],
        "assumption": sc.get("assumption"),
        "substitute": sc.get("substitute"),
        "target": sc.get("target"),
        "source": SOURCES.get(sc["source"]) if sc["source"] else None,
        "is_estimate": key != "official_only",
    }
    return meta


def estimate_remove_sauce(base: dict[str, float | None], scenario_key: str) -> dict[str, Any]:
    """Return the estimated nutrition after removing the sauce.

    Unknown nutrients stay ``None`` -- they are never coerced to 0, and we do
    not claim "customised sodium is strictly lower than the standard item"
    unless the removed amount is actually known.
    """
    if scenario_key not in SCENARIOS:
        raise KeyError(f"unknown scenario: {scenario_key}")

    sc = SCENARIOS[scenario_key]
    meta = scenario_meta(scenario_key)

    if sc["removed"] is None:
        return {
            "meta": meta,
            "removed": {k: None for k in NUTRI_KEYS},
            "result": {k: base.get(k) for k in NUTRI_KEYS},
            "unknown": [k for k in NUTRI_KEYS if base.get(k) is None],
            "is_estimate": False,
            "display": "定制后未知；以下为标准版参考值",
        }

    removed = dict(sc["removed"])
    # Keep kJ and kcal consistent inside one scenario.
    if removed.get("energyKj") is None and removed.get("energyKcal") is not None:
        removed["energyKj"] = _kcal_to_kj(removed["energyKcal"])
    if removed.get("energyKcal") is None and removed.get("energyKj") is not None:
        removed["energyKcal"] = removed["energyKj"] / KJ_PER_KCAL

    result: dict[str, float | None] = {}
    unknown: list[str] = []
    for key in NUTRI_KEYS:
        base_v = base.get(key)
        rem_v = removed.get(key)
        if base_v is None:
            result[key] = None
            unknown.append(key)
        elif rem_v is None:
            # Base known but removal unknown -> customised value unknown.
            result[key] = None
            unknown.append(key)
        else:
            result[key] = _round(base_v - rem_v)

    return {
        "meta": meta,
        "removed": {k: _round(removed.get(k), 2) for k in NUTRI_KEYS},
        "result": result,
        "unknown": unknown,
        "is_estimate": True,
        "display": "估算值（含替代配料与份量假设）",
    }


def compare_scenarios(base: dict[str, float | None]) -> list[dict[str, Any]]:
    """Side-by-side of all scenarios, for the UI 'assumptions' drawer."""
    out = []
    for key in SCENARIO_KEYS:
        if key == "official_only":
            continue
        out.append(estimate_remove_sauce(base, key))
    return out


# --------------------------------------------------------------- applicability
def scenarios_for(component_name: str) -> list[str]:
    """Which estimate scenarios legitimately cover this POS component name.

    Both scenarios were built for 「麦香鸡酱」. Any other removal target has no
    sourced substitute, so it must fall back to ``official_only`` (unknown).
    """
    keys: list[str] = []
    for key, sc in SCENARIOS.items():
        if key == "official_only":
            continue
        target = (sc.get("target") or "")
        if "麦香鸡酱" in target and "麦香鸡酱" in (component_name or ""):
            keys.append(key)
    return keys
