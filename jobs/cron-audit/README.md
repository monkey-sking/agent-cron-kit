# cron-audit：定时任务体检

每天跑一次，回答一个问题：**这些定时任务，还有哪些在真干活？**

## 为什么不用「给每个任务配监控」

那样会陷入套娃：65 个任务配 65 个监控，再配一个监控这些监控的任务，
最后没人看得懂哪条告警该信。

这里换成**数据驱动**：读任务表 + 读输出目录，用六条固定规则发现异常。
新加的任务自动被覆盖，不需要改代码。

## 六条规则

| 规则 | 抓什么 | 判定依据 |
|---|---|---|
| **R1 该跑没跑** | 启用了但很久没运行 | 按排程与小时窗口折算「应跑几次」，超过 2.5 倍才算漏 |
| **R2 连续失败** | 输出里带失败特征 | 窗口内过半输出命中失败特征，且**尚未自愈** |
| **R3 重复任务** | 名字归一化后撞车 | 迁库/手滑最容易造出双胞胎 |
| **R4 投递失败** | 任务跑了但消息没送到 | 任务表里的 delivery error |
| **R5 静默率异常** | 噪音，或哑了 | 高频任务 >60% 非静默 = 吵；恒静默且非白名单 = 可能哑了 |
| **R6 探针哑了** | 「变化才唤醒」的探针长期无变化 | 超过 3 天没有 `last_changed_at` |

## 三条踩过的坑，直接写进实现

1. **限定小时的任务不能按「距今多久」判。** `*/2 9-20 * * *` 在夜里本来就不该跑，
   用 `(now-last)/period` 会把它报成「漏了 19 轮」。必须按小时窗口折算应跑次数。
2. **已经自愈的不该继续报。** 最后一次失败之后只要出现过成功记录，
   R2 就不再翻旧账——否则每个自愈任务都会在 24 小时窗口里被反复告警。
3. **「静默」有五种写法。** `silent (empty output)`、`no_change (agent run suppressed)`、
   正文为空、`[SILENT]`、以及 agent 的 `## Response` 段为 `[SILENT]`。
   漏掉任意一种，R5 就会把设计如此的任务报成 100% 噪音。

## 用法

```bash
python3 cron_audit.py --jobs ~/.hermes/cron/jobs.json --outputs ~/.hermes/cron/output
python3 cron_audit.py --jobs ~/.dsh/noagent/jobs.json --outputs ~/.dsh/noagent/state
python3 cron_audit.py --json            # 机器可读
python3 cron_audit.py --verbose         # 正常时也说话
python3 cron_audit.py --fail-on high    # CI：有 HIGH 就非零退出
```

任务表格式兼容两种常见写法：

```jsonc
// hermes 风格
{"id":"x","name":"…","enabled":true,"schedule":{"kind":"cron","expr":"30 18 * * *"},"last_run_at":"…"}
// noagent 风格
{"id":"x","name":"…","enabled":true,"schedule":{"kind":"cron","cron":"30 18 * * *"},"last":{"startedAt":"…"}}
```

## 恒静默白名单

有些任务**设计上就该永远零输出**（看门狗、备份、探针）。它们不是「哑了」，
不该每天被 R5 问一遍。白名单放 `$CRON_KIT_STATE_DIR/silent_by_design.txt`，
一行一个任务名，`#` 之后是注释：

```
# 设计如此：正常就静默
网络看门狗
代理内核看门狗
每日备份
```

不写死在代码里——换个环境这份名单就不成立了。

## 输出约定

正常 → **零输出**；有异常 → 按严重度分组列出（🔴 HIGH / 🟡 MED / ⚪ LOW）。
`--fail-on high` 时只要有 HIGH 就返回 1，方便挂到 CI 或上层告警。
