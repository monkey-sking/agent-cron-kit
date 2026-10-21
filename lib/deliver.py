#!/usr/bin/env python3
"""投递桥：把脚本的 stdout 转发到飞书 / Telegram。

## 解决什么问题

cron 脚本的惯例是「只 print」。投递是调度器的隐藏能力：它读 stdout，按配置
转发到某个渠道。脚本一旦离开原来的调度器单独跑（迁移、换机、手动执行），
这些告警就会**静默消失**——看起来任务还在跑，其实没人收到任何东西。

本包装器把这层补回来，并保持原语义：

    stdout 为空  -> 不发送（无异常就不打扰人）
    stdout 非空  -> 分片转发到目标渠道
    发送失败     -> 落 outbox + stderr，绝不静默吞掉

## 用法

    python3 deliver.py --target feishu   -- <cmd...>
    python3 deliver.py --target telegram --title '【日报】' -- <cmd...>
    python3 deliver.py --target local    -- <cmd...>   # 只打印，不发送
    python3 deliver.py --target feishu --dry-run -- <cmd...>   # 不发真消息

## 凭据

优先级：环境变量 > env 文件。env 文件路径：$CRON_KIT_ENV_FILE，
默认 ~/.config/cron-kit/.env。

    FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

凭据缺失不报错退出，而是落 outbox 并在 stderr 说明——宁可没送达，也要让人看得见。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

STATE_DIR = Path(
    os.environ.get("CRON_KIT_STATE_DIR")
    or (Path.home() / ".local" / "state" / "cron-kit")
)
OUTBOX = STATE_DIR / "outbox"
ENV_FILE = Path(
    os.environ.get("CRON_KIT_ENV_FILE")
    or (Path.home() / ".config" / "cron-kit" / ".env")
)
MAX_CHARS = 3500  # 飞书/Telegram 单条上限的保守值
TIMEOUT = 15


def load_env_file() -> dict[str, str]:
    """解析 KEY=VALUE 文件，忽略注释、空行与引号。"""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        env[k] = v.strip().strip("'").strip('"')
    return env


def get(key: str, file_env: dict[str, str]) -> str | None:
    return os.environ.get(key) or file_env.get(key)


def http_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def send_feishu(text: str, dry_run: bool) -> bool:
    fe = load_env_file()
    app_id = get("FEISHU_APP_ID", fe)
    app_secret = get("FEISHU_APP_SECRET", fe)
    chat_id = get("FEISHU_CHAT_ID", fe)
    if dry_run:
        print(f"[dry-run] Feishu chat={chat_id or '<unset>'}: {text[:200]}", file=sys.stderr)
        return True
    if not (app_id and app_secret and chat_id):
        print("Feishu 凭据缺失（FEISHU_APP_ID/SECRET/CHAT_ID）", file=sys.stderr)
        return False
    try:
        tok = http_json(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": app_secret},
        )
        token = tok.get("tenant_access_token")
        if not token:
            print(f"Feishu token 失败: {tok.get('code')} {tok.get('msg')}", file=sys.stderr)
            return False
        r = http_json(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.get("code") != 0:
            print(f"Feishu 发送失败: {r.get('code')} {r.get('msg')}", file=sys.stderr)
            return False
        return True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Feishu 网络错误: {exc}", file=sys.stderr)
        return False


def send_telegram(text: str, dry_run: bool) -> bool:
    fe = load_env_file()
    token = get("TELEGRAM_BOT_TOKEN", fe)
    chat = get("TELEGRAM_CHAT_ID", fe)
    if dry_run:
        print(f"[dry-run] Telegram chat={chat or '<unset>'}: {text[:200]}", file=sys.stderr)
        return True
    if not (token and chat):
        print("Telegram 凭据缺失（TELEGRAM_BOT_TOKEN/CHAT_ID）", file=sys.stderr)
        return False
    try:
        r = http_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat, "text": text, "disable_web_page_preview": True},
        )
        if r.get("ok") is not True:
            print(f"Telegram 发送失败: {r.get('description')}", file=sys.stderr)
            return False
        return True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Telegram 网络错误: {exc}", file=sys.stderr)
        return False


def chunk(text: str, size: int = MAX_CHARS) -> list[str]:
    """按段落切分，避免在句子中间截断。"""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        if len(buf) + len(para) + 2 > size:
            if buf.strip():
                out.append(buf.rstrip())
            buf = para
        else:
            buf += ("\n\n" if buf else "") + para
    if buf.strip():
        out.append(buf.rstrip())
    return [c for c in out if c.strip()]


def _normalize_argv(argv: list[str]) -> list[str]:
    """把被 shell 按空格拆开的 --title 重新拼回一个参数。

    调度器里的 command 常常是整串交给 shell 的，标题含空格/全角括号就会被拆成
    多个 token，argparse 会把后半截当成要执行的命令（真实事故，见 PORTABILITY.md）。
    """
    try:
        i = argv.index("--title")
    except ValueError:
        return argv
    j = i + 1
    while j < len(argv) and not str(argv[j]).startswith("--"):
        j += 1
    if j - (i + 1) > 1:
        return argv[: i + 1] + [" ".join(argv[i + 1 : j])] + argv[j:]
    return argv


def _fallback(body: str, target: str) -> None:
    """投递失败兜底：落盘 + stderr。盘也写不了时，正文直出。"""
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        fn = OUTBOX / f"{datetime.now():%Y%m%d_%H%M%S}_{target}.txt"
        fn.write_text(body)
        print(f"[投递失败] 消息已落盘: {fn}", file=sys.stderr)
        return
    except OSError as exc:
        print(f"[投递失败] 落盘不可用({exc})，正文直出:", file=sys.stderr)
    sys.stderr.write(body + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=["feishu", "telegram", "local", "none"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的实际命令")
    args = ap.parse_args(_normalize_argv(sys.argv[1:]))

    cmd = [c for c in args.cmd if c != "--"]
    if not cmd:
        print("缺少要执行的命令", file=sys.stderr)
        return 2

    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout.strip()

    if proc.stderr:
        sys.stderr.write(proc.stderr)

    if not stdout:
        return proc.returncode

    body = f"{args.title}\n\n{stdout}" if args.title else stdout

    if args.target in ("local", "none"):
        print(body)
        return proc.returncode

    delivered = True
    for part in chunk(body):
        if args.target == "feishu":
            delivered &= send_feishu(part, args.dry_run)
        else:
            delivered &= send_telegram(part, args.dry_run)

    if not delivered:
        _fallback(body, args.target)
        return 1

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
