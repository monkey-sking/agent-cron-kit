#!/bin/bash
# GitHub Trending 日报 —— 定时任务入口。
#
# 输出策略：**每天必推**（日报要每天看到才不忘），新进榜标 🆕、老面孔标 =。
# 抓取失败退出非 0，让调度器把失败记下来；正常时输出日报正文。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${CRON_KIT_STATE_DIR:-$HOME/.local/state/cron-kit}"
LOG="$STATE_DIR/github_trending.log"
mkdir -p "$STATE_DIR"

OUT=$(python3 "$HERE/github_trending_scout.py" --since daily --limit 12 --filter ai --always 2>&1)
RC=$?

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] rc=$RC"
  echo "$OUT"
} >> "$LOG" 2>&1

# 日志轮转：超过 500 行截断到 200 行
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 500 ]; then
  tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then
  exit 1
fi

printf '%s\n' "$OUT"
exit 0
