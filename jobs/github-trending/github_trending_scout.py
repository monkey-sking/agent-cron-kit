#!/usr/bin/env python3
"""GitHub Trending 日报采集器（只读，零 Token）。

只抓 github.com/trending 这个公开页，不需要登录态、不需要浏览器、不花模型 Token。
定位：发现刚发布、Star 增速快的轻量工具 / AI Agent 框架 / 开发者基建。

用法:
  python3 github_trending_scout.py                  # daily / top 15，Markdown
  python3 github_trending_scout.py --since weekly
  python3 github_trending_scout.py --filter ai      # 只看 AI/Agent 相关
  python3 github_trending_scout.py --always         # 每天都推，新进榜标 🆕
  python3 github_trending_scout.py --json

去重: 已推过的 repo 记在 $CRON_KIT_STATE_DIR/github_trending_seen.json，
      同一 repo 7 天内不重复推（否则日报变复读机）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

STATE_DIR = Path(
    os.environ.get("CRON_KIT_STATE_DIR")
    or (Path.home() / ".local" / "state" / "cron-kit")
)
STATE_FILE = STATE_DIR / "github_trending_seen.json"
DEDUP_DAYS = 7

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

AI_KEYWORDS = [
    "ai", "llm", "agent", "gpt", "claude", "gemini", "diffusion", "transformer",
    "rag", "embedding", "mcp", "prompt", "inference", "model", "neural",
    "machine-learning", "deep-learning", "copilot", "chatbot", "openai",
    "anthropic", "ollama", "llama", "qwen", "deepseek", "vision", "speech",
]


def fetch(url: str, retries: int = 3, timeout: int = 25) -> str:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - 网络错误类型很多，统一重试
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"fetch failed: {url} — {last}")


def parse_trending(html: str) -> list[dict]:
    """解析 trending 页。结构：<article class="Box-row"> ..."""
    repos: list[dict] = []
    blocks = re.split(r'<article\s+class="Box-row"', html)[1:]
    for b in blocks:
        try:
            m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"]+)"', b)
            if not m:
                m = re.search(r'href="/([^"/]+/[^"/]+)"', b)
            if not m:
                continue
            full_name = m.group(1).strip()
            if full_name.count("/") != 1:
                continue

            d = re.search(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', b, re.S)
            desc = re.sub(r"<[^>]+>", "", d.group(1)).strip() if d else ""

            lang = ""
            lm = re.search(r'<span itemprop="programmingLanguage">([^<]+)</span>', b)
            if lm:
                lang = lm.group(1).strip()

            stars_total = ""
            sm = re.search(r'href="/[^"]+/stargazers"[^>]*>\s*([\d,]+)', b)
            if sm:
                stars_total = sm.group(1).strip()

            stars_period = ""
            tm = re.search(r"([\d,]+)\s*stars?\s*(?:today|this week)", b)
            if tm:
                stars_period = tm.group(1).strip()
            if not stars_period:
                tm2 = re.search(r'class="d-inline-block float-sm-right"[^>]*>\s*([\d,]+)\s*stars', b)
                if tm2:
                    stars_period = tm2.group(1).strip()

            repos.append({
                "name": full_name,
                "url": f"https://github.com/{full_name}",
                "desc": desc,
                "lang": lang,
                "stars_total": stars_total,
                "stars_period": stars_period,
            })
        except Exception:  # noqa: BLE001 - 单个块解析失败不影响整页
            continue
    return repos


def is_ai(r: dict) -> bool:
    blob = (r["name"] + " " + r["desc"] + " " + r["lang"]).lower()
    return any(k in blob for k in AI_KEYWORDS)


def load_seen() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001 - 状态文件缺失/损坏都当作空
        return {}


def save_seen(d: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    except OSError as exc:
        print(f"[warn] 状态写不进去: {exc}", file=sys.stderr)


def fresh_enough(value: str, now: datetime) -> bool:
    try:
        return (now - datetime.fromisoformat(value)) < timedelta(days=30)
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="daily", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--filter", default="", choices=["", "ai"])
    ap.add_argument("--quiet", action="store_true", help="无新增时静默")
    ap.add_argument("--always", action="store_true",
                    help="每次都推（无新增也推）；新进榜标 🆕，老面孔标 =")
    args = ap.parse_args()

    try:
        html = fetch(f"https://github.com/trending?since={args.since}")
    except Exception as e:  # noqa: BLE001
        print(f"❌ GitHub Trending 抓取失败: {e}", file=sys.stderr)
        return 1

    repos = parse_trending(html)
    if args.filter == "ai":
        repos = [r for r in repos if is_ai(r)]
    repos = repos[: args.limit]

    seen = load_seen()
    now = datetime.now()
    fresh: list[dict] = []
    for r in repos:
        last = seen.get(r["name"])
        r["is_new"] = True
        if last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(days=DEDUP_DAYS):
                    r["is_new"] = False
                    if not args.always:
                        continue
            except Exception:  # noqa: BLE001
                pass
        fresh.append(r)

    if args.json:
        print(json.dumps({"since": args.since, "count": len(fresh), "repos": fresh},
                         ensure_ascii=False, indent=1))
    elif not fresh:
        if not args.quiet:
            print(f"[GitHub Trending/{args.since}] 无新增项目（{len(repos)} 个候选均已推送过）")
    else:
        period_cn = {"daily": "今日", "weekly": "本周", "monthly": "本月"}[args.since]
        new_cnt = sum(1 for r in fresh if r.get("is_new"))
        head = f"# GitHub Trending · {period_cn}新增 Star 榜（{len(fresh)} 个"
        head += f" · 🆕{new_cnt}）\n" if args.always else "）\n"
        print(head)
        for i, r in enumerate(fresh, 1):
            star = f" ⭐{r['stars_period']}({period_cn})" if r["stars_period"] else ""
            tot = f" 共{r['stars_total']}" if r["stars_total"] else ""
            lang = f" [{r['lang']}]" if r["lang"] else ""
            mark = ("🆕 " if r.get("is_new") else "=  ") if args.always else ""
            print(f"{i}. {mark}**{r['name']}**{lang}{star}{tot}")
            if r["desc"]:
                print(f"   {r['desc']}")
            print(f"   {r['url']}\n")

    for r in fresh:
        seen[r["name"]] = now.isoformat(timespec="seconds")
    seen = {k: v for k, v in seen.items() if fresh_enough(v, now)}
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
