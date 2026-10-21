#!/usr/bin/env python3
"""共享前的秘密扫描器。

不是通用漏洞扫描器的替代品，只针对「把自己机器上的定时脚本推到公开仓库」
这一件事：找出不该出现在 diff 里的凭据、个人路径与身份标识。

用法:
    python3 tools/secrets_scan.py --path .
    python3 tools/secrets_scan.py --path . --json
    python3 tools/secrets_scan.py --path . --fail-on high   # CI 用，默认即 high

分词规则:
    high   —— 一旦公开就等同于泄露（私钥、平台 token、聊天 ID、公网 IP）
    medium —— 需要人工判断（邮箱、手机号、绝对路径、账号 handle）
    low    —— 提示（带 secret/key 字样的变量名、TODO 里的凭据）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv"}

# 占位符与文档专用值：命中这些不报警（否则扫描器会被自己的文档气死）
PLACEHOLDER_RX = re.compile(
    r"REPLACE_ME|__HOME__|__REPO__|CHANGEME|your[_-]|example\.(?:com|org|net)|"
    r"/Users/(?:you|example)|<[^>]{1,40}>|\{\{SECRET_[A-Za-z0-9_]+\}\}|\$\{[A-Z_]+\}",
    re.IGNORECASE,
)
# 公开 DNS / 文档专用网段，本来就不该当秘密
SAFE_IPS = {"223.5.5.5", "8.8.8.8", "1.1.1.1", "114.114.114.114", "127.0.0.1"}


def _is_doc_ip(ip: str) -> bool:
    if ip in SAFE_IPS:
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    a, b = int(parts[0]), int(parts[1])
    return (a, b) in {(192, 0), (198, 51), (203, 0)}  # RFC 5737 文档段


def _ignored_line(line: str) -> bool:
    return "secrets-scan: ignore" in line
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tgz", ".mp4", ".mp3"}

# (规则名, 严重度, 正则, 说明)
RULES = [
    ("private-key", "high", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "私钥内容"),
    ("openai-style-key", "high", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
     "API key"),
    ("anthropic-key", "high", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
     "API key"),
    ("bearer-token", "high", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
     "硬编码 Bearer token"),
    ("feishu-chat-id", "high", re.compile(r"\boc_[0-9a-f]{20,}\b"),
     "飞书会话 ID（可定位到具体群）"),
    ("telegram-chat-id", "high", re.compile(r"(?i)\b(TELEGRAM_CHAT_ID|chat_id)\b\s*[=:]\s*[\"']?(-?\d{8,})"),
     "Telegram 会话 ID"),
    ("url-with-credentials", "high", re.compile(r"[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]+@"),
     "URL 里内嵌用户名密码"),
    ("public-ip", "high", re.compile(r"\b(?!127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.|255\.)((?:[1-9]\d?|1\d\d|2[0-4]\d|25[0-5])(?:\.(?:[0-9]{1,3})){3})\b"),
     "公网 IP（可能是内网主机/服务器地址）"),
    ("generic-assignment", "medium",
     re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key|client[_-]?secret)\b\s*[=:]\s*[\"'][^\"'\s]{12,}[\"']"),
     "变量名像凭据且赋了长字符串"),
    ("email", "medium", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
     "邮箱"),
    ("cn-phone", "medium", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
     "手机号"),
    ("abs-path", "medium", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.\-]+"),
     "个人绝对路径"),
    ("volume-path", "medium", re.compile(r"/Volumes/[A-Za-z0-9_\-]+"),
     "外接卷路径"),
    ("account-handle", "low", re.compile(r"(?<![\w/])@[A-Za-z0-9_]{4,20}\b"),
     "账号 handle（可能是个体标识）"),
    ("secret-placeholder", "low", re.compile(r"\{\{SECRET_[A-Za-z0-9_]+\}\}"),
     "宿主专用 secret 占位符（离开宿主会失效）"),
    ("cookie-file", "low", re.compile(r"(?i)cookies?[\w\-]*\.(?:txt|json)|storage_state"),
     "cookie 文件引用"),
]


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            path = os.path.join(dirpath, name)
            if os.path.getsize(path) > 2_000_000:
                continue
            yield path


def mask(value: str) -> str:
    v = value.strip()
    if len(v) <= 8:
        return v[:2] + "*" * max(len(v) - 2, 0)
    return v[:4] + "*" * (len(v) - 6) + v[-2:]


def scan(root: str):
    findings = []
    for path in iter_files(root):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        for name, severity, rx, desc in RULES:
            for line_no, line in enumerate(text.splitlines(), 1):
                if len(line) > 2000:
                    line = line[:2000]
                if _ignored_line(line):
                    continue
                for m in rx.finditer(line):
                    hit = m.group(0)
                    if PLACEHOLDER_RX.search(hit):
                        continue
                    if name == "public-ip" and _is_doc_ip(hit):
                        continue
                    findings.append({
                        "file": rel,
                        "line": line_no,
                        "rule": name,
                        "severity": severity,
                        "description": desc,
                        "match": mask(hit),
                    })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", choices=["low", "medium", "high", "none"], default="high")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isdir(root):
        print(f"不是目录: {root}", file=sys.stderr)
        return 2

    findings = scan(root)
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f["severity"]] += 1

    if args.json:
        print(json.dumps({"path": root, "counts": counts, "findings": findings},
                         ensure_ascii=False, indent=1))
    else:
        for f in sorted(findings, key=lambda x: (-SEVERITY_ORDER[x["severity"]], x["file"], x["line"])):
            print(f"{f['severity'].upper():<6} {f['file']}:{f['line']}  {f['rule']:<20} {f['match']}  # {f['description']}")
        print(f"\n合计: high={counts['high']} medium={counts['medium']} low={counts['low']}  ({root})")
        if counts["high"]:
            print("存在 high：不要 push，先替换成占位符。")
        elif counts["medium"]:
            print("只有 medium/low：逐条确认后再 push。")
        else:
            print("干净。")

    if args.fail_on == "none":
        return 0
    threshold = SEVERITY_ORDER[args.fail_on]
    worst = max((SEVERITY_ORDER[f["severity"]] for f in findings), default=0)
    return 1 if worst >= threshold else 0


if __name__ == "__main__":
    sys.exit(main())
