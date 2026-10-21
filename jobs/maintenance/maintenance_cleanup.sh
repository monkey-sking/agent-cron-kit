#!/bin/bash
# maintenance_cleanup.sh —— 周期性清理，防止日志/备份/残留把磁盘吃满。
#
# 每一段都来自真实事故：
#   - 轮转日志单个 113M，几十年没人清
#   - 备份脚本漏排一个可再生的大模型目录 → 每份包 2.4G × 留 10 份 = 24G
#   - 已删除任务的输出目录永远留在磁盘上
#
# 用法:
#   AGENT_HOME=~/.hermes bash maintenance_cleanup.sh --dry-run   # 只看会删什么
#   AGENT_HOME=~/.hermes bash maintenance_cleanup.sh
#
# 输出约定：什么都没做 → 零输出；有动作或告警才输出。
set -uo pipefail

H="${AGENT_HOME:-$HOME/.hermes}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

OUT=""
act() {  # act <描述> <命令...>
  local desc="$1"; shift
  if [ "$DRY" = "1" ]; then
    OUT+="[dry-run] 会执行: ${desc}\n"
  else
    "$@" >/dev/null 2>&1
    OUT+="${desc}\n"
  fi
}

# 1) 轮转日志遗留 (>30 天)
if [ -d "$H/logs" ]; then
  n=$(find "$H/logs" -maxdepth 1 -type f \( -name '*.log.[0-9]*' -o -name '*.error.log.20*' \) -mtime +30 2>/dev/null | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    if [ "$DRY" = "1" ]; then
      OUT+="[dry-run] 会删 $n 个 >30 天轮转日志\n"
    else
      find "$H/logs" -maxdepth 1 -type f \( -name '*.log.[0-9]*' -o -name '*.error.log.20*' \) -mtime +30 -delete 2>/dev/null
      OUT+="[logs] 删 $n 个 >30 天轮转日志\n"
    fi
  fi
fi

# 2) 会话调试产物 (>30 天)
if [ -d "$H/sessions" ]; then
  n=$(find "$H/sessions" -name 'request_dump_*' -mtime +30 2>/dev/null | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    if [ "$DRY" = "1" ]; then
      OUT+="[dry-run] 会删 $n 个 request_dump\n"
    else
      find "$H/sessions" -name 'request_dump_*' -mtime +30 -delete 2>/dev/null
      OUT+="[sessions] 删 $n 个 request_dump\n"
    fi
  fi
fi

# 3) 已删除任务的残留输出目录 (>7 天且不在任务表里)
if command -v python3 >/dev/null && [ -f "$H/cron/jobs.json" ] && [ -d "$H/cron/output" ]; then
  gone=$(python3 - "$H/cron/jobs.json" "$H/cron/output" <<'PYEOF'
import json, os, sys
jobs_path, out_root = sys.argv[1], sys.argv[2]
active = set()
try:
    d = json.load(open(jobs_path))
    for j in (d if isinstance(d, list) else d.get("jobs", [])):
        jid = j.get("job_id") or j.get("id")
        if jid:
            active.add(str(jid))
except Exception:
    sys.exit(0)
for name in os.listdir(out_root):
    p = os.path.join(out_root, name)
    if os.path.isdir(p) and name not in active:
        print(name)
PYEOF
)
  for g in $gone; do
    [ -n "$g" ] || continue
    if [ "$DRY" = "1" ]; then
      OUT+="[dry-run] 会删残留输出目录 $g\n"
    else
      rm -rf "${H:?}/cron/output/${g:?}"
      OUT+="[cron] 清残留输出目录 $g\n"
    fi
  done
fi

# 4) 超肥 skill 提醒（主文件 >30KB 应拆 references —— 太肥会挤爆上下文）
if [ -d "$H/skills" ]; then
  big=$(find "$H/skills" -name 'SKILL.md' -size +30k 2>/dev/null | while read -r f; do
    kb=$(du -k "$f" | cut -f1)
    echo "  ${kb} KB  ${f#"$H"/skills/}"
  done | sort -rn | head -5)
  [ -n "$big" ] && OUT+="[skill] 超肥提醒 (>30KB 应拆 references):\n$big\n"
fi

# 5) 暂停超 30 天的任务提醒（停用不等于删除，会越积越多）
if command -v python3 >/dev/null && [ -f "$H/cron/jobs.json" ]; then
  stale=$(python3 - "$H/cron/jobs.json" <<'PYEOF'
import json, sys
from datetime import datetime, timezone
d = json.load(open(sys.argv[1]))
jobs = d if isinstance(d, list) else d.get("jobs", [])
now = datetime.now(timezone.utc).timestamp()
for j in jobs:
    if j.get("enabled"):
        continue
    pa = j.get("paused_at")
    if not pa:
        continue
    try:
        t = datetime.fromisoformat(pa.replace("Z", "+00:00")).timestamp()
        if now - t > 30 * 86400:
            print(f"  {j.get('name') or '?'} (暂停于 {pa[:10]})")
    except Exception:
        pass
PYEOF
)
  [ -n "$stale" ] && OUT+="[cron] 暂停>30 天建议删除:\n$stale\n"
fi

# 6) 备份异常膨胀（阈值告警，不是自动删）
if [ -d "$H/backups" ]; then
  bk_big=$(find "$H/backups" -type f -size +500M 2>/dev/null)
  if [ -n "$bk_big" ]; then
    rows=$(echo "$bk_big" | while read -r f; do echo "  $(du -h "$f" | cut -f1)  ${f#"$H"/}"; done)
    OUT+="[backups] ⚠️ 单文件 >500M —— 检查是否漏排可再生大文件(cache/模型/日志):\n$rows\n"
  fi
  bk_k=$(du -sk "$H/backups" 2>/dev/null | cut -f1)
  if [ "${bk_k:-0}" -gt 5242880 ]; then
    OUT+="[backups] ⚠️ backups/ 总占用 $(du -sh "$H/backups" 2>/dev/null | cut -f1)，超过 5G 阈值\n"
  fi
fi

# 7) 整个数据目录体积
total_k=$(du -sk "$H" 2>/dev/null | cut -f1)
if [ "${total_k:-0}" -gt 10485760 ]; then
  OUT+="[disk] ⚠️ $(du -sh "$H" 2>/dev/null | cut -f1)，超过 10G 阈值\n"
fi

if [ -n "$OUT" ]; then printf "%b" "$OUT"; fi
exit 0
