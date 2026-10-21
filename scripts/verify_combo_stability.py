#!/usr/bin/env python3
"""套餐目录稳定性验证：等北京时间白天再连续生成两次，比对数量。

比较时**只看业务数量**（SKU / 套餐 / 变体 / 单轮 / 排除数 + 变体指纹），
不看 built_at —— 那是生成时间，本来就每次不同。

用法：
    python3 scripts/verify_combo_stability.py [--date 2026-10-22] [--gap-min 30]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT = Path("/tmp/stability")
BJ = timezone(timedelta(hours=8))


def bj_now() -> datetime:
    return datetime.now(BJ)


def build(date: str, out: Path, workers: int = 6) -> None:
    proc = subprocess.run(
        [PY, str(ROOT / "scripts/build_combo_catalog.py"),
         "--date", date, "--workers", str(workers), "--out", str(out)],
        capture_output=True, text=True, cwd=str(ROOT))
    (OUT / f"build_{out.stem}.log").write_text(proc.stdout + proc.stderr, "utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"生成失败：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


def summarize(path: Path) -> dict:
    """只保留业务数量，去掉 built_at。"""
    d = json.loads(path.read_text("utf-8"))
    out = {"date": d.get("context", {}).get("date"), "meals": {}}
    for meal, m in (d.get("meals") or {}).items():
        variants = m.get("variants") or []
        excluded = m.get("excluded") or []
        singles = m.get("single_round") or []
        sex = m.get("single_round_excluded") or []
        reasons: dict[str, int] = {}
        for e in excluded:
            reasons[e.get("reason")] = reasons.get(e.get("reason"), 0) + 1
        sreasons: dict[str, int] = {}
        for e in sex:
            sreasons[e.get("reason")] = sreasons.get(e.get("reason"), 0) + 1
        vkey = "|".join(sorted(
            f"{v['combo_code']}:{v['variant']}:{v['quoted_price']}:{v['nutrition']['protein']}"
            for v in variants))
        out["meals"][meal] = {
            "sku": m.get("checked"),
            "combos": m.get("combo_count"),
            "variants": len(variants),
            "singles": len(singles),
            "single_excluded": len(sex),
            "single_reasons": sreasons,
            "excluded": len(excluded),
            "reasons": reasons,
            "fingerprint": hashlib.sha256(vkey.encode()).hexdigest()[:12],
        }
    out["total"] = {
        "sku": sum(v["sku"] or 0 for v in out["meals"].values()),
        "combos": sum(v["combos"] or 0 for v in out["meals"].values()),
        "variants": sum(v["variants"] for v in out["meals"].values()),
        "singles": sum(v["singles"] for v in out["meals"].values()),
        "excluded": sum(v["excluded"] for v in out["meals"].values()),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-22")
    ap.add_argument("--gap-min", type=int, default=30)
    ap.add_argument("--wait-until-bj", default="",
                    help="北京时间 HH:MM 之前不开始（例如 08:30）")
    ap.add_argument("--rounds", type=int, default=3,
                    help="最多连续生成几轮；任意两轮一致即判定稳定")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    if args.wait_until_bj:
        hh, mm = (int(x) for x in args.wait_until_bj.split(":"))
        target = bj_now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        while bj_now() < target:
            wait = min(300, (target - bj_now()).total_seconds())
            print(f"[{bj_now():%H:%M:%S}] 等待白天窗口，{wait/60:.0f} 分钟后开始",
                  flush=True)
            time.sleep(max(5, wait))

    print(f"=== 稳定性验证开始 日期={args.date} 间隔={args.gap_min} 分钟 "
          f"轮数={args.rounds}（北京时间 {bj_now():%Y-%m-%d %H:%M}）===", flush=True)

    prev: dict | None = None
    for rnd in range(1, args.rounds + 1):
        out = OUT / f"catalog_r{rnd}.json"
        print(f"[{bj_now():%H:%M:%S}] 第 {rnd} 轮生成…", flush=True)
        build(args.date, out)
        summary = summarize(out)
        (OUT / f"summary_r{rnd}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if prev is not None:
            if prev == summary:
                print(f"=== 稳定：第 {rnd-1} 轮与第 {rnd} 轮数量完全一致 ===", flush=True)
                (OUT / "catalog_stable.json").write_text(
                    out.read_text("utf-8"), "utf-8")
                (OUT / "stable_summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
                print(f"STABLE rounds={rnd} file={OUT/'catalog_stable.json'}", flush=True)
                return 0
            print(f"[{bj_now():%H:%M:%S}] 第 {rnd-1} 轮与第 {rnd} 轮不一致，"
                  f"{args.gap_min} 分钟后重试", flush=True)
            for meal in summary["meals"]:
                a, b = prev["meals"].get(meal, {}), summary["meals"].get(meal, {})
                for key in ("sku", "combos", "variants", "singles", "excluded"):
                    if a.get(key) != b.get(key):
                        print(f"    {meal}.{key}: {a.get(key)} -> {b.get(key)}", flush=True)
        prev = summary
        if rnd < args.rounds:
            time.sleep(args.gap_min * 60)
    print("=== 达到最大轮数仍未稳定 ===", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
