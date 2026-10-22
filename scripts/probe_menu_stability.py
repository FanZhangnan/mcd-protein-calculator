#!/usr/bin/env python3
"""轻量探针：记录门店菜单的 SKU 集合指纹，观察菜单何时稳定。

麦当劳会在凌晨调整次日菜单。如果在调整窗口里抓数据，同一份代码两次运行会
得到不同的 SKU 数，发布出去的数字就对不上。这个脚本每 8 分钟打一次指纹，
用来确认「菜单已经稳定」再跑正式生成。

只读接口，不写任何文件到仓库（输出写到 /tmp/stability/probe.log）。

用法：
    python3 scripts/probe_menu_stability.py [--interval-min 8] [--rounds 40]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp_client import McpClient  # noqa: E402

DEFAULT_STORE = "1440054"
DEFAULT_DAYS = ("2026-10-22", "2026-10-23")
DEFAULT_SLOTS = ("08:15", "12:15", "18:30")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--days", default=",".join(DEFAULT_DAYS))
    ap.add_argument("--slots", default=",".join(DEFAULT_SLOTS))
    ap.add_argument("--interval-min", type=float, default=8)
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--out", default="/tmp/stability/probe.log")
    args = ap.parse_args()

    days = [d for d in args.days.split(",") if d]
    slots = [s for s in args.slots.split(",") if s]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    client = McpClient()
    for _ in range(args.rounds):
        # 北京时间：门店的菜单、餐次和日期都是这个时区的口径，
        # 记录本机时间会让人误判变更时刻。
        row = {"t": datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")}
        for day in days:
            for slot in slots:
                try:
                    r = client.call("query-meals", {
                        "storeCode": args.store, "orderType": 1, "beType": 1,
                        "reservationDate": f"{day} {slot}"})
                    codes = sorted((r.get("data") or {}).get("meals") or {})
                    h = hashlib.sha256("|".join(codes).encode()).hexdigest()[:10]
                    row[f"{day[5:]} {slot}"] = f"{len(codes)}/{h}"
                except Exception as exc:            # 探针失败不打断循环
                    row[f"{day[5:]} {slot}"] = f"err {str(exc)[:60]}"
        line = json.dumps(row, ensure_ascii=False)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)
        time.sleep(args.interval_min * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
