#!/bin/bash
# proxy-watchdog.sh —— 代理内核健康检查与自动恢复。
#
# 检测顺序：
#   1) 控制 API 是否应答（最准确：内核活着且配置加载成功）
#   2) 进程是否存在（API 短暂无响应时给一次重试机会）
#   3) geoip / geosite 数据文件是否损坏（损坏是常见的内核起不来原因）
# 恢复：杀残留内核 → 重启代理 App → 复查。只尝试一轮，不反复重启。
#
# 用法:
#   PROXY_CTL_SECRET=xxx bash proxy-watchdog.sh
#   PROXY_CTL_SECRET=xxx bash proxy-watchdog.sh --check   # 只检查，不重启
#
# 输出约定：健康 → 零输出（作为 cron 任务时不打扰人）；有动作或失败才输出。
set -uo pipefail

CTL_API="${PROXY_CTL_API:-http://127.0.0.1:9090}"
CTL_SECRET="${PROXY_CTL_SECRET:-}"
CORE_PATTERN="${PROXY_CORE_PATTERN:-mihomo|ClashMac/core/current}"
PROFILES_DIR="${PROXY_PROFILES_DIR:-}"
WORK_DIR="${PROXY_WORK_DIR:-}"
APP_NAME="${PROXY_APP_NAME:-}"
RESTART_CMD="${PROXY_RESTART_CMD:-}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

api_ok() {
  local out code body
  out=$(curl -s --max-time 3 -w '\n%{http_code}' \
        ${CTL_SECRET:+-H "Authorization: Bearer $CTL_SECRET"} \
        "$CTL_API/version" 2>/dev/null) || return 1
  code=${out##*$'\n'}
  body=${out%$'\n'*}
  # 必须 200 + JSON 正文。401 的 {"message":"Unauthorized"} 也是 JSON，
  # 只 grep "{" 会把「要认证」误判成「健康」——那是最危险的假阳性。
  [ "$code" = "200" ] || return 1
  printf '%s' "$body" | grep -q '{'
}

core_alive() {
  pgrep -f "$CORE_PATTERN" >/dev/null 2>&1
}

restart_core() {
  if [ "$CHECK_ONLY" = "1" ]; then
    echo "[check] 内核无响应，未执行恢复（--check）"
    return 1
  fi

  if [ -n "$RESTART_CMD" ]; then
    eval "$RESTART_CMD" >/dev/null 2>&1 || true
    return 0
  fi

  pkill -f "$CORE_PATTERN" 2>/dev/null || true
  sleep 2
  if [ -n "$APP_NAME" ]; then
    osascript -e "tell application \"$APP_NAME\" to quit" >/dev/null 2>&1 || true
    sleep 2
    open -a "$APP_NAME" >/dev/null 2>&1 || true
    sleep 5
  fi
  return 0
}

# --- 1) API ---
if api_ok; then
  exit 0
fi

# --- 2) 进程在但 API 没响应：等 2 秒再试一次 ---
if core_alive; then
  sleep 2
  api_ok && exit 0
fi

echo "[WATCHDOG] ${CTL_API} 无响应（进程状态: $(core_alive && echo 在 || echo 不在)）"

# --- 3) geoip / geosite 数据文件 ---
if [ -n "$PROFILES_DIR" ] && [ -d "$PROFILES_DIR" ]; then
  if [ -f "$PROFILES_DIR/geoip.metadb" ]; then
    # metadb 不是文本；头部不是合法 magic 就当作损坏
    if ! head -c 6 "$PROFILES_DIR/geoip.metadb" 2>/dev/null | grep -q "metadb"; then
      if [ "$CHECK_ONLY" = "1" ]; then
        echo "[check] geoip.metadb 疑似损坏"
      else
        echo "[WATCHDOG] geoip.metadb 疑似损坏，删除后由内核重建"
        rm -f "$PROFILES_DIR/geoip.metadb"
      fi
    fi
  fi
  if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    for f in geoip.dat geosite.dat; do
      if [ ! -f "$PROFILES_DIR/$f" ] && [ -f "$WORK_DIR/$f" ]; then
        [ "$CHECK_ONLY" = "1" ] && echo "[check] 缺少 $f（可从 work 目录复制）"
        [ "$CHECK_ONLY" = "0" ] && cp "$WORK_DIR/$f" "$PROFILES_DIR/" && echo "[WATCHDOG] 已复制 $f"
      fi
    done
  fi
fi

# --- 4) 恢复并复查 ---
restart_core || exit 1
sleep 3
if api_ok; then
  echo "[WATCHDOG] 代理内核已恢复"
  exit 0
fi

echo "[WATCHDOG] 恢复失败，需要人工处理（检查内核日志与配置）"
exit 1
