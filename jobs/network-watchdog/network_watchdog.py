#!/usr/bin/env python3
"""网络与本地代理看门狗。

两件事：
  1. 公网是否通（ping 一个目标）；
  2. 本地代理端口是否还在听（典型故障：代理软件重生成配置成纯 TUN，
     入站端口被清零，但系统代理仍指向它 → 所有请求超时）。

判断逻辑刻意保守：

  * 网络不通 → 尝试重启代理内核（需要配置 CLASH_RESTART_CMD），再复查；
  * 网络通但代理端口死 → 尝试把端口开回来（需要 CLASH_API + CLASH_SECRET）；
  * 都不行 → 只记日志 + 写 flag 文件，**不反复重启**（避免把系统搞得更乱）。

用法:
    python3 network_watchdog.py            # 检查并按需修复
    python3 network_watchdog.py --check    # 只检查，不修复
    python3 network_watchdog.py --verbose  # 同时打印结果（默认只写日志）

输出约定：正常时零输出（调度器不打扰人）；修复动作与失败写日志 + 告警。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STATE_DIR = Path(
    os.environ.get("CRON_KIT_STATE_DIR")
    or (Path.home() / ".local" / "state" / "cron-kit")
)
LOG_FILE = STATE_DIR / "network_watchdog.log"

PING_TARGET = os.environ.get("WATCHDOG_PING_TARGET", "223.5.5.5")
PROXY_HOST = os.environ.get("WATCHDOG_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("WATCHDOG_PROXY_PORT", "7890"))
CLASH_API = os.environ.get("CLASH_API", "http://127.0.0.1:9090").rstrip("/")
CLASH_SECRET = os.environ.get("CLASH_SECRET", "")
CLASH_RESTART_CMD = os.environ.get("CLASH_RESTART_CMD", "")


def log(msg: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def ping(host: str) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-t", "3", host],
                           capture_output=True, timeout=6)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def port_alive() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((PROXY_HOST, PROXY_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def restore_proxy_port() -> bool:
    """通过代理控制 API 把入站端口开回来。"""
    if not CLASH_SECRET:
        log("restore skipped: CLASH_SECRET 未配置")
        return False
    body = json.dumps({"mixed-port": PROXY_PORT}).encode()
    req = urllib.request.Request(
        CLASH_API + "/configs",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {CLASH_SECRET}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            log(f"restore_proxy_port: PATCH /configs mixed-port={PROXY_PORT} -> HTTP {resp.status}")
            return True
    except Exception as exc:  # noqa: BLE001
        log(f"restore_proxy_port failed: {exc}")
        return False


def restart_clash() -> bool:
    if not CLASH_RESTART_CMD:
        log("restart skipped: CLASH_RESTART_CMD 未配置")
        return False
    try:
        subprocess.run(CLASH_RESTART_CMD, shell=True, timeout=60,
                       capture_output=True)
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"restart failed: {exc}")
        return False


def flag(name: str, text: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / name).write_text(text)
    except OSError:
        pass


def clear_flags() -> None:
    for name in ("NETWORK_DOWN_FLAG", "PROXY_DOWN_FLAG"):
        p = STATE_DIR / name
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不修复")
    ap.add_argument("--verbose", action="store_true", help="正常时也输出")
    args = ap.parse_args()

    if not ping(PING_TARGET):
        log(f"NETWORK DOWN — ping {PING_TARGET} failed")
        if not args.check:
            restart_clash()
            time.sleep(5)
        if ping(PING_TARGET):
            log("NETWORK RECOVERED")
            clear_flags()
            print("✅ 网络已恢复（代理内核重启后）")
            return 0
        flag("NETWORK_DOWN_FLAG", f"down since {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"❌ 网络不通：ping {PING_TARGET} 失败，自动恢复未成功，需要人工处理。")
        return 1

    if not port_alive():
        log(f"network OK but proxy {PROXY_HOST}:{PROXY_PORT} dead")
        if not args.check:
            restore_proxy_port()
            time.sleep(3)
        if port_alive():
            log("proxy port restored")
            clear_flags()
            print(f"✅ 代理端口 {PROXY_PORT} 已恢复")
            return 0
        flag("PROXY_DOWN_FLAG", f"proxy down since {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⚠️ 网络通，但本地代理端口 {PROXY_HOST}:{PROXY_PORT} 不监听，已尝试恢复未成功。")
        return 1

    log("Network OK")
    clear_flags()
    if args.verbose:
        print(f"网络正常，代理端口 {PROXY_PORT} 正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
