# agent-cron-kit

<!-- badges:start -->
[![License](https://img.shields.io/github/license/monkey-sking/agent-cron-kit?style=flat-square)](https://github.com/monkey-sking/agent-cron-kit/blob/main/LICENSE) [![Last commit](https://img.shields.io/github/last-commit/monkey-sking/agent-cron-kit?style=flat-square)](https://github.com/monkey-sking/agent-cron-kit/commits/main) [![Python](https://img.shields.io/github/languages/top/monkey-sking/agent-cron-kit?style=flat-square)](https://github.com/monkey-sking/agent-cron-kit/search?l=Python) [![Stars](https://img.shields.io/github/stars/monkey-sking/agent-cron-kit?style=flat-square)](https://github.com/monkey-sking/agent-cron-kit/stargazers) [![Forks](https://img.shields.io/github/forks/monkey-sking/agent-cron-kit?style=flat-square)](https://github.com/monkey-sking/agent-cron-kit/network/members) [![Security Scan](https://github.com/monkey-sking/agent-cron-kit/actions/workflows/secrets-scan.yml/badge.svg?branch=main)](https://github.com/monkey-sking/agent-cron-kit/actions/workflows/secrets-scan.yml)
<!-- badges:end -->

把一个人机器上跑了一年的定时脚本拆开来看：**哪些能公开，哪些只能留在自己机器上。**

这不是「把 dotfiles 传上来」，而是一次公开前的分拣。仓库里只放分拣后**确认可以公开**的部分；
不能公开的那部分，写清楚为什么不能，以及共享前必须做的脱敏。

## 本机磁盘上的定时任务，其实是 4 套调度器

| 调度面 | 数量 | 形态 | 备注 |
|---|---|---|---|
| Hermes cron（`~/.hermes/cron/jobs.json`） | 59（启用 35） | 47 个纯脚本 + 12 个 Agent 提示词 | 历史主调度 |
| launchd（`~/Library/LaunchAgents/com.hermes-migrated.*`） | 28 | 上面那批任务的落地执行层 | 与 cron 表镜像，不是新增任务 |
| DSH noagent（`~/.dsh/noagent/jobs.json`） | 11（启用 10） | 纯脚本 + 变化才唤醒 | 新调度器，重叠上面一部分 |
| DSH schedule（`~/.dsh/storages/schedule.json`） | 14 | Agent 任务（提示词） | 一次性/定时混合 |

去重后约 **65 个真实任务、50 个不同脚本**。分拣结果见 [docs/SHARE-AUDIT.md](docs/SHARE-AUDIT.md)。

一句话结论：**50 个脚本里，A 级（可直接公开）6 个文件 / 5 个任务，B 级（改造后可公开）9 个，
其余属于账号登录态、个人数据与内部设施，不在可共享范围内，也不在这里列举。**

本仓库收录：**A 级全部 6 个文件**，加上 B 级里已经改造完的 4 个（清理、网络看门狗、
代理内核看门狗、定时任务体检）。剩下 5 个（ego 3 个、Jev 2 个）依赖私有 CLI 或商业服务，
留在原处等真的有人需要再改。

## 这个仓库里有什么

```
lib/deliver.py                  投递桥：脚本 stdout → 飞书 / Telegram；空输出=不打扰
jobs/github-trending/           GitHub Trending 日报（零登录、零 Token、公开页）
jobs/git-guard/                 每日自动推送 + 脏仓库巡检（保守策略，不替你提交）
jobs/maintenance/               日志/备份/残留清理（带 --dry-run）
jobs/network-watchdog/          网络与本地代理端点检查
jobs/proxy-watchdog/            代理内核健康检查与自动恢复（Clash/Mihomo 可配）
jobs/cron-audit/                定时任务体检：漏跑/连挂/重复/投递失败/噪音/探针哑了
jobs/dsh-watchdog/              桌面 App 掉线自动拉起（三重检测，防重复拉起）
tools/secrets_scan.py           共享前的秘密扫描器（本次审计就是用它跑的）
docs/SHARE-AUDIT.md             全量分级：能共享 / 需改造 / 不可共享，逐条给理由
docs/SECRETS.md                 脱敏规则与操作清单
docs/PORTABILITY.md             从私有环境搬出来会踩的坑（附真实事故）
examples/launchd/               LaunchAgent 模板（占位符版）
```

## 为什么值得抄的两个设计

**1. 静默语义。** 所有任务遵守同一条约定：**正常时零输出，有异常才输出**。
调度器把「stdout 非空」当作「需要打扰人」。这样 65 个任务里每天真正发消息的不超过几条，
而不是 65 条「一切正常」。`lib/deliver.py` 把这层语义补齐了——脱离原调度器单独跑，
告警也不会静默消失。

**2. 失败必须可见。** 空输出是静默，但**失败不能也是静默**。脚本报错要写 stderr、
要落 outbox、要留日志。真实事故：一个清理脚本在 launchd 下 exit 1 且没有任何输出，
连续几周没人知道它没跑。

## 快速开始

```bash
# 1) 配置（复制后填自己的）
cp .env.example .env
$EDITOR .env

# 2) 试试投递桥（不会真发）
python3 lib/deliver.py --target telegram --dry-run -- echo "hello"

# 3) 跑一个任务
CRON_KIT_ENV_FILE=./.env CRON_KIT_GIT_ROOTS=~/dev/Projects \
  bash jobs/git-guard/git_push_watchdog.sh
```

正式挂调度时，用 `examples/launchd/` 的模板改路径，或者交给任意支持 cron 的调度器。
注意：**调用命令里的参数含空格或全角括号时必须加引号**，否则会被 shell 拆开（见 PORTABILITY.md）。

## 共享前必读

公开这个仓库之前，至少做三件事：

1. `python3 tools/secrets_scan.py --path .` —— 必须 0 个 high。
2. 确认没有把 `~/.hermes/.env`、`secrets/`、cookies、`jobs.json` 原文件带进来。
3. 通读 [docs/SECRETS.md](docs/SECRETS.md) 的红线清单。

## 配套项目

- [dsh-noagent-schedule](https://github.com/monkey-sking/dsh-noagent-schedule) —— 管「什么时候跑」：
  在 Harness 宿主内按 cron/间隔执行 shell 脚本，零模型调用、零 token，带任务页与日志账本。
  本仓库管「跑什么」。两者用同一套约定：静默输出、失败非零退出、每次运行留日志。

## License

MIT。脚本按「当时能跑」的状态分享，不附任何担保；平台自动化类脚本**不在**本仓库内。
