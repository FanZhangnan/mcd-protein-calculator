#!/usr/bin/env python3
"""检查仓库内所有 Markdown 引用的**图片**是否存在。

覆盖三种写法：
1. Markdown 图片 ``![alt](path)``
2. 内联 HTML ``<img src="path">``
3. 反引号里的图片路径 `` `docs/screenshots/x.png` `` —— 开发记录里常用这种写法

只检查图片扩展名；``http(s)://``、``#锚点``、含 ``*`` 的 glob 模式一律跳过
（glob 是「这类文件的命名规则」，不是某一个具体文件）。

非图片的代码块路径（例如 ``~/.workbuddy/mcp.json``、仓库外的证据目录）属于
散文描述，不是渲染出来的链接，因此不在坏链接范围内。

退出码 0 表示没有坏掉的图片链接。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)>\s]+)>?[^)]*\)")
HTML_IMG = re.compile(r"<img[^>]+?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
CODE_PATH = re.compile(r"`([^`\n]*?/[^`\n]*?\.(?:png|jpg|jpeg|gif|svg|webp))`", re.I)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

#: 这些目录里的文件**有意不进仓库**（文档里已写明），所以不算坏链接：
#: - ``11-SKILL新会话测试证据/``：SKILL 新会话测试的原始证据，文档开头已声明未随仓库发布
#: - ``17-evidence/``：套餐核查的原始接口返回，本地留存
EXTERNAL_EVIDENCE = ("11-SKILL新会话测试证据/", "17-evidence/")


def is_external(url: str) -> bool:
    return url.startswith(("http://", "https://", "//", "mailto:", "#", "data:", "file://"))


def main() -> int:
    broken: list[str] = []
    checked = 0
    docs = sorted(ROOT.rglob("*.md"))
    for doc in docs:
        if ".git/" in str(doc):
            continue
        text = doc.read_text("utf-8", errors="replace")
        rel = doc.relative_to(ROOT)
        refs: list[tuple[str, str]] = []
        for m in MD_IMAGE.finditer(text):
            refs.append(("link", m.group(1)))
        for m in HTML_IMG.finditer(text):
            refs.append(("html", m.group(1)))
        for m in CODE_PATH.finditer(text):
            refs.append(("code", m.group(1)))
        for kind, url in refs:
            clean = url.split("#", 1)[0].split("?", 1)[0].strip()
            if not clean or is_external(clean):
                continue
            if "*" in clean:            # glob 模式，不是具体文件
                continue
            if Path(clean).suffix.lower() not in IMAGE_EXT:
                continue
            if clean.startswith(EXTERNAL_EVIDENCE):
                continue                # 有意不进仓库的证据目录
            checked += 1
            candidates = [(doc.parent / clean), (ROOT / clean)]
            if not any(c.exists() for c in candidates):
                broken.append(f"{rel} [{kind}] {url}")
    print(f"扫描 {len(docs)} 个 Markdown，检查图片引用 {checked} 条")
    if broken:
        print(f"坏图片链接 {len(broken)} 条：")
        for line in broken:
            print("  -", line)
        return 1
    print("没有坏掉的图片链接。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
