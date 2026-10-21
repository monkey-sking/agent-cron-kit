#!/bin/bash
# git_autopush.sh —— 每天把各仓库「已提交但未推送」的提交推上去。
#
# 设计原则（保守，避免误提交）：
#   1) 只推已提交的内容 —— 绝不 `git add -A` 自动提交工作区改动。
#      未提交的改动交给 git_push_watchdog.sh 提醒（可能有半成品/凭据/大文件）。
#   2) 推送前先 fetch；若落后远端（需要 merge/rebase）→ 不强行推，报告出来让人处理。
#   3) 每个仓库独立，失败不影响其他仓库。
#
# 用法:
#   bash git_autopush.sh --dry-run    # 只说会推什么，不真推
#   bash git_autopush.sh
#
# 输出约定：全部干净 → 零输出（调度器不打扰人）；有动作或异常才输出。
# 退出码：0 正常；1 有仓库失败（让调度器能记录 failure_streak）。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_repos.sh
. "$HERE/_repos.sh"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

PUSHED=""
FAILED=""
BEHIND=""

while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  repo_skipped "$repo" && continue
  [ -d "$repo/.git" ] || continue
  cd "$repo" 2>/dev/null || continue
  NAME="$(basename "$repo")"

  # 没有 remote 的仓库直接跳过（不是错误，只是本地仓库）
  git remote | grep -q . || continue

  REMOTE="origin"
  git remote | grep -qx "origin" || REMOTE="$(git remote | head -1)"

  if ! git fetch "$REMOTE" -q 2>/dev/null; then
    FAILED="${FAILED}  - ${NAME}: fetch 失败 (remote=${REMOTE})\n"
    continue
  fi

  UP="$(git rev-parse --abbrev-ref '@{u}' 2>/dev/null)"
  if [ -z "$UP" ]; then
    FAILED="${FAILED}  - ${NAME}: 未设上游分支 (跳过)\n"
    continue
  fi

  AHEAD="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo '?')"
  BEHIND_N="$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo '0')"

  if [ "$AHEAD" = "?" ] || [ -z "$AHEAD" ]; then
    FAILED="${FAILED}  - ${NAME}: 无法确定上游分支\n"
    continue
  fi
  [ "$AHEAD" = "0" ] && continue

  if [ "$BEHIND_N" != "0" ] && [ -n "$BEHIND_N" ]; then
    BEHIND="${BEHIND}  - ${NAME}: 落后 ${REMOTE} ${BEHIND_N} 个提交 (需先 pull/rebase)，跳过\n"
    continue
  fi

  BRANCH="$(git branch --show-current)"
  LAST="$(git log --oneline -1 | cut -c1-60)"

  if [ "$DRY" = "1" ]; then
    PUSHED="${PUSHED}  - ${NAME}: [dry-run] 将推 ${AHEAD} 个提交 (${REMOTE}/${BRANCH}) latest: ${LAST}\n"
  elif git push -q "$REMOTE" "$BRANCH" 2>/dev/null; then
    PUSHED="${PUSHED}  - ${NAME}: pushed ${AHEAD} commits (${REMOTE}/${BRANCH}) latest: ${LAST}\n"
  else
    FAILED="${FAILED}  - ${NAME}: push 失败 (${REMOTE}/${BRANCH})\n"
  fi
done < <(discover_repos)

if [ -n "$PUSHED" ] || [ -n "$FAILED" ] || [ -n "$BEHIND" ]; then
  DRY_TAG=""; [ "$DRY" = "1" ] && DRY_TAG=" [dry-run]"
echo "🚀 Git 自动推送 ($(date '+%m-%d %H:%M'))${DRY_TAG}"
  echo ""
  [ -n "$PUSHED" ] && { echo "✅ 已推送:"; printf "%b" "$PUSHED"; echo ""; }
  [ -n "$BEHIND" ] && { echo "⚠️ 需人工处理（落后远端）:"; printf "%b" "$BEHIND"; echo ""; }
  [ -n "$FAILED" ] && { echo "❌ 失败:"; printf "%b" "$FAILED"; echo ""; }
fi

[ -n "$FAILED" ] && exit 1
exit 0
