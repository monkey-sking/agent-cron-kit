#!/usr/bin/env python3
"""定时任务体检 —— 用固定规则自动发现「该跑没跑 / 连着失败 / 重复 / 投递失败 / 噪音」。

## 为什么是规则，而不是给每个任务配监控

逐任务写监控，等于给 N 个任务再写 N 个监控任务，最后还得监控这些监控。
这里改成**数据驱动**：读任务表 + 读输出目录，用六条固定规则发现异常。
新加的任务自动被覆盖，不需要改本脚本。

## 六条规则（每条都有真实事故背书）

  R1 该跑没跑   —— 启用中，但按排程算已经漏了 N 轮
                   （坑：限定小时的任务如 `*/2 9-20`，夜里本来就不该跑，
                    只看「距上次多久」会误报，必须按小时窗口折算应跑次数）
  R2 连续失败   —— 最近窗口里过半输出含失败特征
                   （坑：已经自愈的不该继续报；文档文本被截进来的不算失败）
  R3 重复任务   —— 名字归一化后重复（迁库/手滑最容易造出双胞胎）
  R4 投递失败   —— 任务跑了但消息没送到
  R5 静默率异常 —— 高频任务几乎全在输出（噪音），或一直零输出（可能哑了）
  R6 探针哑了   —— 「变化才唤醒」的探针如果很久没有变化，可能是探针挂了

## 用法

    python3 cron_audit.py --jobs ~/.hermes/cron/jobs.json --outputs ~/.hermes/cron/output
    python3 cron_audit.py --jobs ~/.dsh/noagent/jobs.json --outputs ~/.dsh/noagent/output
    python3 cron_audit.py --json

输出约定：一切正常 → 零输出（调度器不打扰人）。`--verbose` 时正常也说话。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_STATE = Path(
    os.environ.get("CRON_KIT_STATE_DIR")
    or (Path.home() / ".local" / "state" / "cron-kit")
)

# 非静默输出里出现这些 = 疑似失败。刻意收窄：宽泛的「失败/❌」会把
# 「失败 0」这种统计口径、以及引用了踩坑文本的报告误判成失败。
FAIL_PATTERNS = [
    r"\bFAILED\b",
    r"Traceback \(most recent call last\)",
    r"\bCRITICAL\b",
    r"❌\s*(?!0\b)",
    r"失败\s*[:：]\s*[1-9]",
    r"本轮[^\n]*失败\s*[1-9]",
    r"❌.*失败",
    r"错误[:：]\s*\S",
]
_FAIL_RE = [re.compile(p) for p in FAIL_PATTERNS]

# 命中这些 = 明显是文档/skill 正文被截进来，不算任务失败
DOC_MARKERS = ["踩坑", "必读", "Use this skill for"]

# last_run 超过「周期 × 倍数」才算异常（留出边界抖动）
OVERDUE_FACTOR = 2.5
HIGH_FREQ_SEC = 3600


# ---------------------------------------------------------------- 读取任务表

def load_jobs(path: Path) -> list[dict]:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if isinstance(d, list):
        return d
    return d.get("jobs") or list(d.values())


def job_id(j: dict) -> str:
    return str(j.get("id") or j.get("job_id") or j.get("jobId") or "")


def job_name(j: dict) -> str:
    return str(j.get("name") or j.get("title") or job_id(j) or "?")


def job_enabled(j: dict) -> bool:
    return bool(j.get("enabled", j.get("active", True)))


def job_schedule(j: dict):
    """返回 {"kind": "cron"|"interval", "expr"/"minutes"/"seconds": ...} 或 None。

    兼容两种常见写法：
      hermes:  {"kind":"cron","expr":"30 18 * * *"} / {"kind":"interval","minutes":30}
      noagent: {"kind":"cron","cron":"30 18 * * *"} / {"kind":"every","everySeconds":120}
    """
    sch = j.get("schedule") or j.get("cron")
    if isinstance(sch, dict):
        kind = sch.get("kind")
        if kind == "interval":
            if sch.get("minutes"):
                return {"kind": "interval", "minutes": float(sch["minutes"])}
            if sch.get("seconds"):
                return {"kind": "interval", "seconds": float(sch["seconds"])}
        if kind in ("cron", None):
            expr = sch.get("expr") or sch.get("cron")
            if expr:
                return {"kind": "cron", "expr": str(expr)}
        if kind == "every" and sch.get("everySeconds"):
            return {"kind": "interval", "seconds": float(sch["everySeconds"])}
    if isinstance(sch, str):
        if len(sch.split()) == 5:
            return {"kind": "cron", "expr": sch}
    if j.get("everySeconds"):
        return {"kind": "interval", "seconds": float(j["everySeconds"])}
    return None


def job_last_run(j: dict):
    return parse_ts(
        j.get("last_run_at")
        or j.get("lastRunAt")
        or (j.get("last") or {}).get("startedAt")
        or (j.get("last") or {}).get("started_at")
    )


def job_delivery_error(j: dict) -> str | None:
    err = j.get("last_delivery_error") or j.get("lastDeliveryError")
    if not err:
        err = (j.get("last") or {}).get("deliveryError")
    return str(err) if err and str(err) not in ("None", "0") else None


def parse_ts(v):
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s[:26])
    except ValueError:
        return None


# ---------------------------------------------------------------- 排程推算

def schedule_seconds(sch) -> float | None:
    if not sch:
        return None
    if sch["kind"] == "interval":
        if sch.get("seconds"):
            return sch["seconds"]
        if sch.get("minutes"):
            return sch["minutes"] * 60
        return None
    parts = sch["expr"].split()
    if len(parts) != 5:
        return None
    mi, hr, dom, mon, dow = parts
    if dom.isdigit():          # 月度
        return 30 * 86400
    if dow.isdigit():          # 每周
        return 7 * 86400
    if mi.startswith("*/"):    # 每 N 分钟
        return int(mi[2:]) * 60
    if mi == "0" and hr == "*":            # 每小时
        return 3600
    if mi.isdigit() and re.match(r"^[\d,]+$", hr):   # 一天多个整点
        return int(86400 / max(len(hr.split(",")), 1))
    if mi.isdigit() and hr.isdigit():
        return 86400
    if mi.isdigit() and hr == "*":
        return 3600
    return None


def active_hours(sch) -> set[int] | None:
    """cron 小时窗口，如 `*/2 9-20 * * *` → {9..20}；全天返回 None。"""
    if not sch or sch["kind"] != "cron":
        return None
    parts = sch["expr"].split()
    if len(parts) != 5:
        return None
    hr = parts[1]
    if hr == "*":
        return None
    hours: set[int] = set()
    for part in hr.split(","):
        try:
            if "-" in part:
                a, b = part.split("-")
                hours.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                hours.add(int(part))
        except ValueError:
            return None
    return hours or None


def expected_runs(sch, last: datetime, now: datetime, period: float) -> float:
    """[last, now] 内理论上应跑几次（考虑小时窗口，否则夜里必然误报）。"""
    hours = active_hours(sch)
    if not hours:
        return (now - last).total_seconds() / period
    total = 0.0
    day = last.date()
    while day <= now.date():
        w_start = datetime.combine(day, time(min(hours), 0, 0))
        w_end = datetime.combine(day, time(max(hours), 59, 59))
        lo, hi = max(w_start, last), min(w_end, now)
        if hi > lo:
            total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total / period


# ---------------------------------------------------------------- 输出目录

def recent_outputs(root: Path, days: int = 1):
    """{job_id: [(mtime, silent, body_tail)]}，只看最近 N 天。"""
    out = defaultdict(list)
    if not root.is_dir():
        return out
    cutoff = datetime.now() - timedelta(days=days)
    for jid in os.listdir(root):
        d = root / jid
        if not d.is_dir():
            continue
        for fn in os.listdir(d):
            if not fn.endswith((".md", ".txt", ".log")) or "last_output" in fn:
                continue
            p = d / fn
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if mt < cutoff:
                continue
            try:
                txt = p.read_text(errors="ignore")
            except OSError:
                continue
            silent = _is_silent(txt)
            parts = txt.split("\n---\n")
            # 有分隔线就取正文段；没有分隔线（普通脚本的纯文本输出）就当全文是正文
            body = (parts[-1] if len(parts) > 1 else txt).strip()
            if body == "[SILENT]":
                silent = True
            if not body and "no_agent" in txt:
                silent = True
            out[jid].append((mt, silent, body[:600]))
    return out


def _is_silent(txt: str) -> bool:
    if "silent (empty output)" in txt:
        return True
    if "no_change (agent run suppressed)" in txt:
        return True
    resp = txt.split("## Response")[-1].strip() if "## Response" in txt else ""
    return resp == "[SILENT]"


def _has_failure(body: str) -> bool:
    if any(marker in body for marker in DOC_MARKERS):
        return False
    return any(rx.search(body) for rx in _FAIL_RE)


def _recovered_since(records) -> bool:
    """最后一次失败之后是否已有成功记录（含静默）。有 = 自愈，不必再报。"""
    records.sort()
    last_fail = -1
    for i, (_, silent, body) in enumerate(records):
        if not silent and _has_failure(body):
            last_fail = i
    if last_fail < 0:
        return True
    return any(silent or not _has_failure(b) for _, silent, b in records[last_fail + 1:])


# ---------------------------------------------------------------- 审计规则

def load_silent_by_design(path: Path) -> set[str]:
    """「设计如此就该静默」的名单。不写死在代码里：换个环境就不适用了。"""
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def audit(jobs: list[dict], outs, days: int, silent_ok: set[str]):
    now = datetime.now()
    findings: list[tuple[str, str, str, str]] = []   # (severity, rule, name, detail)

    # R1 该跑没跑
    for j in jobs:
        if not job_enabled(j):
            continue
        sch = job_schedule(j)
        period = schedule_seconds(sch)
        last = job_last_run(j)
        if not (period and last):
            continue
        last_naive = last.replace(tzinfo=None)
        gap = (now - last_naive).total_seconds()
        missed = expected_runs(sch, last_naive, now, period)
        if missed > OVERDUE_FACTOR:
            findings.append((
                "HIGH", "R1该跑没跑", job_name(j),
                f"排程周期 {int(period // 60)}min，但已 {gap / 3600:.1f}h 未运行（约漏 {int(missed)} 轮）",
            ))

    # R2 连续失败
    by_id = {job_id(j): j for j in jobs}
    for jid, records in outs.items():
        records.sort()
        name = job_name(by_id.get(jid, {"id": jid}))
        noisy = [(mt, b) for mt, s, b in records if not s]
        if len(noisy) < 2:
            continue
        fails = [(mt, b) for mt, b in noisy if _has_failure(b)]
        if len(fails) >= 2 and len(fails) >= len(noisy) * 0.5 and not _recovered_since(records):
            findings.append((
                "HIGH", "R2连续失败", name,
                f"{days} 天内 {len(fails)}/{len(noisy)} 次输出含失败特征",
            ))

    # R3 重复任务（名字归一化后相同）
    def norm(n: str) -> str:
        return re.sub(r"[\s（）()·\-—]+", "", n or "")

    groups = defaultdict(list)
    for j in jobs:
        if not job_enabled(j):
            continue
        groups[norm(job_name(j))].append(j)
    for group in groups.values():
        if len(group) > 1:
            ids = ", ".join(job_id(g)[:12] for g in group)
            findings.append(("MED", "R3重复任务", job_name(group[0]),
                             f"{len(group)} 个同名任务: {ids}"))

    # R4 投递失败
    for j in jobs:
        err = job_delivery_error(j)
        if err:
            findings.append(("MED", "R4投递失败", job_name(j), err[:110]))

    # R5 静默率异常
    silent_candidates = []
    for jid, records in outs.items():
        j = by_id.get(jid, {"id": jid})
        name = job_name(j)
        period = schedule_seconds(job_schedule(j))
        if not period or period > HIGH_FREQ_SEC:
            continue
        total = len(records)
        if total < 30:
            continue
        noisy = sum(1 for _, s, _ in records if not s)
        ratio = noisy / total
        if ratio > 0.6:
            findings.append((
                "MED", "R5噪音", name,
                f"高频任务 {total} 次运行中 {noisy} 次非静默（{ratio:.0%}），建议静音或降频",
            ))
        elif noisy == 0 and total >= 50 and name not in silent_ok:
            # 「变化才唤醒」的探针恒静默是设计如此，健康度看 R6
            if j.get("monitor") or j.get("monitor_script") or j.get("watch"):
                continue
            silent_candidates.append(name)
    if silent_candidates:
        findings.append((
            "LOW", "R5恒静默", f"{len(silent_candidates)} 个任务",
            "、".join(silent_candidates[:8]) + ("…" if len(silent_candidates) > 8 else "")
            + " —— 确认是设计如此，还是哑了",
        ))

    # R6 探针哑了
    stale_days_allowed = 3
    for j in jobs:
        if not job_enabled(j):
            continue
        if not (j.get("monitor") or j.get("monitor_script") or j.get("watch")):
            continue
        state = j.get("monitor_state") or {}
        changed = parse_ts(state.get("last_changed_at") or state.get("lastChangedAt"))
        if not changed:
            continue
        stale = (now - changed.replace(tzinfo=None)).total_seconds() / 86400
        if stale > stale_days_allowed:
            findings.append((
                "MED", "R6探针哑了", job_name(j),
                f"监控探针已 {stale:.1f} 天没有检测到变化，确认是探针故障还是真的无变化",
            ))

    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS), help="任务表 JSON")
    ap.add_argument("--outputs", default=None, help="输出目录（默认取任务表同级的 output/）")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="正常时也输出")
    ap.add_argument("--silent-by-design",
                    default=str(DEFAULT_STATE / "silent_by_design.txt"),
                    help="恒静默白名单文件，一行一个任务名")
    ap.add_argument("--fail-on", choices=["none", "high", "med"], default="none",
                    help="有该级别异常时以非零退出（CI 用）")
    args = ap.parse_args()

    jobs_path = Path(os.path.expanduser(args.jobs))
    outputs = Path(os.path.expanduser(
        args.outputs or str(jobs_path.parent / "output")
    ))

    jobs = load_jobs(jobs_path)
    if not jobs:
        print(f"读不到任务表（或为空）: {jobs_path}", file=sys.stderr)
        return 2

    findings = audit(
        jobs,
        recent_outputs(outputs, args.days),
        args.days,
        load_silent_by_design(Path(os.path.expanduser(args.silent_by_design))),
    )

    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=1))
    else:
        order = {"HIGH": 0, "MED": 1, "LOW": 2}
        findings.sort(key=lambda f: order.get(f[0], 9))
        if findings:
            icons = {"HIGH": "🔴", "MED": "🟡", "LOW": "⚪"}
            print(f"🩺 定时任务体检 · {datetime.now():%m-%d %H:%M} · {len(jobs)} 个任务")
            print(f"发现 {len(findings)} 项异常：\n")
            for sev, rule, name, detail in findings:
                print(f"{icons.get(sev, '•')} [{rule}] {name}")
                print(f"    {detail}")
        elif args.verbose:
            print(f"✅ 体检通过：{len(jobs)} 个任务未发现异常")

    if args.fail_on == "none":
        return 0
    level = {"high": {"HIGH"}, "med": {"HIGH", "MED"}}[args.fail_on]
    return 1 if any(f[0] in level for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
