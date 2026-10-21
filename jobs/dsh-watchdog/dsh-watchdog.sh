#!/bin/bash
# dsh-watchdog.sh —— 桌面 App 掉线自动拉起（这里以 DeepSeek Harness 为例）。
#
# 背景：桌面 App 被误关、崩溃、或系统更新后没起来，会导致所有依赖它的定时任务
# 和会话集体失联，而且是「静悄悄的」——没有报错，只是什么都不发生。
#
# 检测用三重交叉验证（任一为真即视为在跑，避免误拉起）：
#   1) lsappinfo：macOS LaunchServices 的 bundleid 注册（最权威）
#   2) 端口：读桌面 App 的 preferences 文件拿端口，看是否 LISTEN
#   3) SingletonLock：Electron 单实例锁里的主进程 PID，且该 PID 的可执行文件
#      确是本 App 主二进制（防崩溃残留锁 / PID 被系统复用）
#
# 为什么不用 pgrep：Electron 主进程对 pgrep 常常不可见，只能看到 Helper 子进程，
# 会误判「没在跑」，于是重复拉起。
#
# 用法:
#   bash dsh-watchdog.sh
#   APP_BUNDLE_ID=com.example.app bash dsh-watchdog.sh
#
# 输出约定：在跑 → 零输出；拉起成功 → 记日志；拉不起来 → 输出告警 + 退出码 1。
set -u

APP_BUNDLE="${APP_BUNDLE_ID:-ai.deepseek.dsh.desktop.next}"
APP_NAME="${APP_DISPLAY_NAME:-DSH NEXT}"
APP_PATH="${APP_PATH:-/Applications/DSH NEXT.app}"
MAIN_BIN="${MAIN_BIN:-$APP_PATH/Contents/MacOS/DSH NEXT}"
PREFS="${APP_PREFS:-$HOME/.dsh/desktop-preferences.json}"
SINGLETON_LOCK="${APP_SINGLETON_LOCK:-$HOME/.dsh/electron-user-data/SingletonLock}"
DEFAULT_PORT="${APP_DEFAULT_PORT:-52800}"
STATE_DIR="${CRON_KIT_STATE_DIR:-$HOME/.local/state/cron-kit}"
LOG="$STATE_DIR/dsh-watchdog.log"
LOCK="$STATE_DIR/dsh-watchdog.lock"
mkdir -p "$STATE_DIR" 2>/dev/null

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '[%s] %s\n' "$(ts)" "$*" >> "$LOG" 2>/dev/null || true; }

app_running_ls() {
  [ -n "$(lsappinfo find bundleid="$APP_BUNDLE" 2>/dev/null)" ]
}

port_up() {
  local port="$DEFAULT_PORT" p
  if [ -f "$PREFS" ]; then
    p=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('port',''))" "$PREFS" 2>/dev/null)
    if [ -n "$p" ] && [[ "$p" =~ ^[0-9]+$ ]] && [ "$p" -ge 1 ] && [ "$p" -le 65535 ]; then
      port="$p"
    fi
  fi
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | grep -q ":$port "
}

app_running_lock() {
  [ -L "$SINGLETON_LOCK" ] || [ -e "$SINGLETON_LOCK" ] || return 1
  local pid
  pid=$(readlink "$SINGLETON_LOCK" 2>/dev/null | sed 's/.*-//')
  [ -n "$pid" ] && [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  lsof -p "$pid" 2>/dev/null | grep -q "txt.*${MAIN_BIN}$"
}

is_up() {
  app_running_ls && return 0
  port_up && return 0
  app_running_lock && return 0
  return 1
}

# 单实例互斥：已有 watchdog 在跑就退出，避免并发重复拉起
if ! mkdir "$LOCK" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if is_up; then
  exit 0
fi

log "not running (lsappinfo/port/lock all negative), relaunching"
open -b "$APP_BUNDLE" 2>/dev/null || open -a "$APP_NAME" 2>/dev/null || true

for _ in $(seq 1 10); do
  sleep 3
  if app_running_ls || app_running_lock; then
    log "relaunched OK"
    exit 0
  fi
done

echo "⚠️ ${APP_NAME} 没在运行，尝试拉起失败（$(ts)）"
log "relaunch FAILED (LaunchServices/SingletonLock still negative)"
exit 1
