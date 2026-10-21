"""SKU / nutrition catalog with strict, non-merging matching.

Design rules (from the project spec):

* The official nutrition table is a *standard single-serving* declaration,
  not a measurement of the actual portion served.
* A menu SKU may only be linked to a nutrition row when the link is
  evidenced. We use two evidences, in order:
    1. an explicit ``code -> canonical name`` alias harvested from the real
       combo ``rounds`` data (the POS itself labels the code), or
    2. an exact name match after *bracket-width* normalisation only.
* Bracket-width normalisation must never delete size / temperature / flavour
  tokens, so ``可乐`` never matches ``可乐中杯``.
* Duplicate nutrition names are kept as a list; identical duplicates are
  flagged, conflicting duplicates become ``ambiguous`` -- never silently
  overwritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NUTRI_FIELDS = [
    "productName",
    "nutritionDescription",
    "energyKj",
    "energyKcal",
    "protein",
    "fat",
    "carbohydrate",
    "sodium",
    "calcium",
]

# The payload carries bare numbers; units come from the field names.
NUTRI_UNITS = {
    "energyKj": "kJ",
    "energyKcal": "kcal",
    "protein": "g",
    "fat": "g",
    "carbohydrate": "g",
    "sodium": "mg",
    "calcium": "mg",
}

NUTRI_KEYS = ["energyKj", "energyKcal", "protein", "fat", "carbohydrate", "sodium", "calcium"]

STATUS_OFFICIAL = "official"            # exact official single-item row
STATUS_ALIAS = "official_alias"         # resolved through an evidenced alias
STATUS_AMBIGUOUS = "ambiguous_spec"     # several specs fit, refuse to merge
STATUS_NOT_FOUND = "not_found"
STATUS_DUPLICATE_CONFLICT = "duplicate_conflict"

_BRACKETS = {"（": "(", "）": ")", "【": "[", "】": "]", "\u3000": " "}


def normalise_name(name: str) -> str:
    """Bracket-width + whitespace normalisation only. Never removes tokens."""
    out = name.strip()
    for src, dst in _BRACKETS.items():
        out = out.replace(src, dst)
    out = re.sub(r"\s+", " ", out)
    return out


def parse_nutrition_payload(payload: dict) -> list[dict[str, Any]]:
    """Parse the ``[n]{fields}:`` rowset string returned by list-nutrition-foods."""
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("data")
    if not isinstance(data, str):
        return []

    rows: list[dict[str, Any]] = []
    lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
    if not lines:
        return []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(NUTRI_FIELDS):
            continue
        row: dict[str, Any] = {}
        for idx, key in enumerate(NUTRI_FIELDS):
            raw = parts[idx]
            if idx in (2, 3, 4, 5, 6, 7, 8):
                try:
                    row[key] = float(raw)
                except ValueError:
                    row[key] = None
            else:
                row[key] = None if raw == "null" else raw
        rows.append(row)
    return rows


@dataclass
class Match:
    status: str
    name: str | None = None
    nutrition: dict[str, float] | None = None
    note: str = ""
    candidates: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in (STATUS_OFFICIAL, STATUS_ALIAS) and self.nutrition is not None


class NutritionCatalog:
    def __init__(self, rows: list[dict[str, Any]], aliases: dict[str, str] | None = None,
                 alias_evidence: dict[str, str] | None = None):
        self.rows = rows
        self.by_name: dict[str, list[dict[str, Any]]] = {}
        self.by_norm: dict[str, list[str]] = {}
        for row in rows:
            name = row.get("productName") or ""
            self.by_name.setdefault(name, []).append(row)
            self.by_norm.setdefault(normalise_name(name), []).append(name)

        self.aliases = dict(aliases or {})
        self.alias_evidence = dict(alias_evidence or {})
        self.duplicates: dict[str, dict[str, Any]] = {}
        for name, group in self.by_name.items():
            if len(group) > 1:
                values = {tuple(r.get(k) for k in NUTRI_KEYS) for r in group}
                self.duplicates[name] = {
                    "count": len(group),
                    "identical": len(values) == 1,
                    "values": [dict(r) for r in group],
                }

    # ---------------------------------------------------------------- loading
    @classmethod
    def from_files(cls, nutrition_path: Path, alias_path: Path | None = None) -> "NutritionCatalog":
        payload = json.loads(Path(nutrition_path).read_text("utf-8"))
        rows = parse_nutrition_payload(payload)
        aliases: dict[str, str] = {}
        evidence: dict[str, str] = {}
        if alias_path and Path(alias_path).exists():
            raw = json.loads(Path(alias_path).read_text("utf-8"))
            for code, payload_entry in (raw.get("codes") or {}).items():
                aliases[code] = payload_entry["name"]
                evidence[code] = payload_entry.get("evidence", "")
        return cls(rows, aliases, evidence)

    # ----------------------------------------------------------------- lookup
    def _pick_single(self, name: str) -> Match:
        group = self.by_name.get(name, [])
        if not group:
            return Match(STATUS_NOT_FOUND, name=name, note="营养表中无此名称")
        if len(group) > 1:
            values = {tuple(r.get(k) for k in NUTRI_KEYS) for r in group}
            if len(values) > 1:
                return Match(
                    STATUS_DUPLICATE_CONFLICT,
                    name=name,
                    note=f"同名 {len(group)} 条且数值不同，拒绝静默选取",
                    candidates=[name],
                )
        row = group[0]
        nutrition = {k: row.get(k) for k in NUTRI_KEYS}
        return Match(STATUS_OFFICIAL, name=name, nutrition=nutrition)

    def lookup(self, code: str, menu_name: str) -> Match:
        # 1) evidenced alias
        alias = self.aliases.get(str(code))
        if alias:
            match = self._pick_single(alias)
            if match.usable:
                match.status = STATUS_ALIAS
                match.note = self.alias_evidence.get(str(code), "code->name 别名")
                return match
            match.note = f"别名 {alias} 未能唯一命中：{match.note}"
            return match

        # 2) exact name
        exact = self._pick_single(menu_name)
        if exact.usable or exact.status == STATUS_DUPLICATE_CONFLICT:
            return exact

        # 3) bracket-width normalised, only when unambiguous
        names = self.by_norm.get(normalise_name(menu_name), [])
        if len(names) == 1:
            match = self._pick_single(names[0])
            if match.usable:
                match.note = "括号宽度归一后唯一命中"
            return match
        if len(names) > 1:
            return Match(
                STATUS_AMBIGUOUS,
                name=menu_name,
                note="归一化后命中多个规格，拒绝合并",
                candidates=names,
            )

        # 4) give up, but tell the user which specs *could* fit
        near = [n for n in self.by_name if normalise_name(menu_name) in normalise_name(n)]
        return Match(
            STATUS_NOT_FOUND,
            name=menu_name,
            note="菜单名与营养表名称不一致，且无 code 级证据" + (f"；疑似规格：{near[:4]}" if near else ""),
            candidates=near[:4],
        )

    def describe(self) -> dict[str, Any]:
        return {
            "rows": len(self.rows),
            "unique_names": len(self.by_name),
            "duplicates": self.duplicates,
            "aliases": len(self.aliases),
        }


# --------------------------------------------------------------- SKU classify
#: ``汁`` alone used to swallow 脆汁鸡 (a fried-chicken side) — keep only
#: juice-specific words so a drink word never steals a side.
DRINK_WORDS = ("可乐", "雪碧", "咖啡", "牛奶", "红茶", "豆浆", "橙汁", "柠檬汁", "柠柠",
               "美式", "奶铁", "拿铁", "矿泉水", "纯净水", "奶昔", "怡泉",
               "果汁", "蔬菜汁", "朱古力", "黑巧", "燕麦奶",
               "卡布奇诺", "玛奇朵", "阿芙佳朵", "茶")
SIDE_WORDS = ("薯条", "玉米杯", "鸡块", "麦乐鸡", "苹果片", "薯饼", "油条", "扭扭薯",
              "沙拉", "鸡排", "鸡翅", "鸡球", "脆汁鸡", "香肠", "芝士条")
#: desserts are checked first: 朱古力新地 contains a drink word, 麦旋风 contains
#: nothing at all — without this list they were either misfiled as drinks or
#: dropped as "other".
DESSERT_WORDS = ("新地", "麦旋风", "圣代", "布丁", "蛋挞", "冰淇淋", "雪糕", "摩斯",
                 "蛋糕", "曲奇", "派")
MAIN_WORDS = ("堡", "卷", "麦满分", "粥", "松饼", "叠叠", "麦香鸡", "麦香鱼")


def classify(name: str) -> str:
    """Coarse slot classification used only to build meal pools.

    Order matters: desserts first (朱古力新地 contains a drink word, 麦旋风
    matches nothing), then drinks (a 奶铁 is not a main), then mains (a 营养卷
    is a wrap even though it contains 香肠), then sides.
    """
    if any(w in name for w in DESSERT_WORDS):
        return "dessert"
    if any(w in name for w in DRINK_WORDS):
        return "drink"
    if any(w in name for w in MAIN_WORDS):
        return "main"
    if any(w in name for w in SIDE_WORDS):
        return "side"
    return "other"


COMBO_WORDS = ("套餐", "组合", "三件套", "四件套", "二件套", "随心配", "双人餐",
               "多人餐", "分享餐", "随心选", "小食盘", "四宫格", "任选",
               "单品精选", "开心乐园", "开心亲子餐")


def is_combo_name(name: str) -> bool:
    return any(w in name for w in COMBO_WORDS)
