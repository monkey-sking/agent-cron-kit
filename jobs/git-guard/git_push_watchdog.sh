#!/bin/bash
# git_push_watchdog.sh —— 巡检各仓库「有未提交改动」或「有未推送提交」。
#
# 为什么需要它：本地改动积压是无声的。真实情况是有仓库积压了两周、
# 另一个积压了两个月才被发现——因为没有任何东西会主动告诉你。
#
# 用法:
#   bash git_push_watchdog.sh            # 有脏状态才输出
#   CRON_KIT_STATE_DIR=... bash git_push_watchdog.sh
#
# 忽略规则：$CRON_KIT_STATE_DIR/watchdog.ignore，一行一个正则，
# 用于排除运行时产物（.db / .log / 备份文件 / 测试输出）。
# 输出约定：全干净 → 零输出。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_repos.sh
. "$HERE/_repos.sh"

STATE_DIR="${CRON_KIT_STATE_DIR:-$HOME/.local/state/cron-kit}"
IGNORE_FILE="$STATE_DIR/watchdog.ignore"

# 把注释行去掉，合成一条 grep -vE 表达式
IGNORE_RE=""
if [ -f "$IGNORE_FILE" ]; then
  IGNORE_RE="$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$IGNORE_FILE" | paste -sd'|' -)"
fi

OUT=""
DIRTY=0

while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  repo_skipped "$repo" && continue
  [ -d "$repo/.git" ] || continue
  cd "$repo" 2>/dev/null || continue
  NAME="$(basename "$repo")"

  # 1) 未提交改动
  STATUS="$(git status --porcelain 2>/dev/null)"
  if [ -n "$IGNORE_RE" ]; then
    STATUS="$(printf '%s\n' "$STATUS" | grep -vE -- "$IGNORE_RE")"
  fi
  UNCOMMITTED="$(printf '%s\n' "$STATUS" | sed '/^$/d' | head -20)"

  # 2) 未推送提交（没有 remote 的本地仓库也要报未提交，不能直接跳过）
  AHEAD="0"
  if git remote | grep -q .; then
    REMOTE="origin"; git remote | grep -qx origin || REMOTE="$(git remote | head -1)"
    git fetch "$REMOTE" -q 2>/dev/null
    AHEAD="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo '?')"
  fi

  local_dirty=0
  [ -n "$UNCOMMITTED" ] && local_dirty=1
  { [ "$AHEAD" != "0" ] && [ "$AHEAD" != "?" ] && [ -n "$AHEAD" ]; } && local_dirty=1
  [ "$local_dirty" = "1" ] || continue

  DIRTY=1
  OUT+="📦 ${NAME}\n"
  if [ -n "$UNCOMMITTED" ]; then
    CNT="$(printf '%s\n' "$UNCOMMITTED" | wc -l | tr -d ' ')"
    OUT+="  ├─ 未提交: ${CNT} 项改动\n"
    OUT+="$(printf '%s\n' "$UNCOMMITTED" | sed 's|^|     |' | head -8)\n"
  fi
  if [ "$AHEAD" != "0" ] && [ "$AHEAD" != "?" ] && [ -n "$AHEAD" ]; then
    OUT+="  └─ 未推送: ${AHEAD} 个提交\n"
  fi
  OUT+="\n"
done < <(discover_repos)

if [ "$DIRTY" = "1" ]; then
  echo "⚠️ Git 仓库有未提交/未推送内容($(date '+%m-%d %H:%M')):"
  echo ""
  printf "%b" "$OUT" | sed '/^$/N;/^\n$/D'
  echo "处理: cd <repo> && 逐项确认后 git add <file> && git commit && git push"
fi
# 全干净 → 零输出
exit 0
