#!/bin/bash
# 仓库发现逻辑，被 git_autopush.sh / git_push_watchdog.sh 共同 source。
#
# 仓库来源（按优先级合并、去重）：
#   1. CRON_KIT_GIT_REPOS_FILE（默认 ~/.config/cron-kit/repos.txt，一行一个路径）
#   2. CRON_KIT_GIT_ROOTS（冒号分隔的根目录，递归找 .git，最多 3 层）
#
# 排除规则：CRON_KIT_GIT_SKIP 或 $CRON_KIT_STATE_DIR/repos.skip，一行一个 glob。
# 用途：某些仓库不能自动推（上游是第三方、或含私有适配器），列进来永久跳过。

discover_repos() {
  local seen_file
  seen_file="$(mktemp -t cron-kit-repos.XXXXXX)"
  local list_file="${CRON_KIT_GIT_REPOS_FILE:-$HOME/.config/cron-kit/repos.txt}"

  emit() {  # 去重后输出
    local r="$1"
    [ -n "$r" ] || return 0
    grep -qxF "$r" "$seen_file" && return 0
    printf '%s\n' "$r" >> "$seen_file"
    printf '%s\n' "$r"
  }

  if [ -f "$list_file" ]; then
    while IFS= read -r line; do
      line="${line%%#*}"
      line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      case "$line" in
        '~'*) line="${HOME}${line#\~}" ;;
      esac
      [ -n "$line" ] && [ -d "$line/.git" ] && emit "$line"
    done < "$list_file"
  fi

  local root
  local IFS=':'
  for root in ${CRON_KIT_GIT_ROOTS:-}; do
    [ -d "$root" ] || continue
    [ -d "$root/.git" ] && emit "$root"
    while IFS= read -r gitdir; do
      [ -n "$gitdir" ] && emit "${gitdir%/.git}"
    done < <(find "$root" -maxdepth 3 -name .git -type d 2>/dev/null)
  done

  rm -f "$seen_file"
}

# 返回 0 = 该仓库在跳过名单里
repo_skipped() {
  local repo="$1" name patterns f
  name="$(basename "$repo")"
  patterns="${CRON_KIT_GIT_SKIP:-}"
  f="${CRON_KIT_STATE_DIR:-$HOME/.local/state/cron-kit}/repos.skip"
  local blob="$patterns"
  [ -f "$f" ] && blob="$blob
$(sed 's/#.*//' "$f")"
  local p
  while IFS= read -r p; do
    p="$(printf '%s' "$p" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -n "$p" ] || continue
    case "$name" in
      $p) return 0 ;;
    esac
  done <<< "$blob"
  return 1
}
