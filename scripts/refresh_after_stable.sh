#!/usr/bin/env bash
# 白天数据刷新：等稳定性验证完成后，用稳定目录重跑快照、测试、README 与截图。
# 用法：bash scripts/refresh_after_stable.sh
set -euo pipefail
cd /Users/zhangnanfan/WorkBuddy/2026-10-18-22-01-22/mai-mai-planner
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
PY=/Users/zhangnanfan/.workbuddy/binaries/python/versions/3.13.12/bin/python3
NODE=/Users/zhangnanfan/.workbuddy/binaries/node/versions/22.22.2-6/bin/node
STABLE=/tmp/stability/catalog_stable.json

echo "=== 1. 稳定目录落地 ==="
cp "$STABLE" data/combo_catalog.json
"$PY" - <<'PY'
import json
d = json.load(open('data/combo_catalog.json'))
print('built_at', d['built_at'], 'date', d['context']['date'])
for m, v in d['meals'].items():
    print(' ', m, 'sku', v['checked'], 'combos', v['combo_count'],
          'variants', len(v['variants']), 'single', len(v.get('single_round') or []))
PY

echo "=== 2. 重新导出在线快照 ==="
"$PY" scripts/export_demo_snapshot.py --date 2026-10-22 2>&1 | tail -8

echo "=== 3. 同步演示资源 ==="
"$PY" scripts/sync_demo_assets.py

echo "=== 4. README 数字改写 ==="
NODE_BIN="$NODE" "$PY" scripts/update_readme_numbers.py --catalog data/combo_catalog.json

echo "=== 5. 离线单测 ==="
"$PY" -m unittest discover -s tests -p "test_*.py" 2>&1 | tail -4

echo "=== 6. 图片链接检查 ==="
"$PY" scripts/check_doc_links.py

echo "=== 完成 ==="
