#!/usr/bin/env bash
# 启动一个带完整数据目录的实时服务。
# 用法：bash scripts/serve_live.sh <端口> [数据目录]
# 数据目录必须包含 combo_catalog.json —— 缺了它，服务会退回「结果不含套餐」，
# 页面上的推荐就与在线快照对不上。
set -euo pipefail
cd /Users/zhangnanfan/WorkBuddy/2026-10-18-22-01-22/mai-mai-planner
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
PORT=${1:?用法: serve_live.sh <端口> [数据目录]}
DIR=${2:-/tmp/mai-live/data}
PY=/Users/zhangnanfan/.workbuddy/binaries/python/versions/3.13.12/bin/python3

# 每次都用干净的目录：旧目录里的 state 可能停留在别的日期，
# 服务会拿它跟套餐目录的日期对不上，然后把套餐整批拒掉。
rm -rf "$DIR"
mkdir -p "$DIR"
cp data/aliases.json data/upgrade_ladder.json "$DIR/"
# 套餐目录：构建产物，按门店 + 日期 + 餐次校验后才会被采用
cp data/combo_catalog.json "$DIR/"
"$PY" - "$DIR" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
state = {"store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅",
                   "beType": 1, "orderType": 1},
         "date": "2026-10-23",
         "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"}}
(d / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")
print("data dir:", d, sorted(p.name for p in d.iterdir()))
PY
MAI_MAI_DATA="$DIR" "$PY" src/server.py --port "$PORT"
