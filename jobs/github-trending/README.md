# GitHub Trending 日报

每天一条 Markdown 日报：正在快速涨 Star 的新项目，按需只看 AI / Agent 相关。

## 为什么是纯 HTTP

`github.com/trending` 是公开页，实测直接 200。用不着浏览器，也用不着模型——
**零登录态、零 Token**。反过来，如果为了「抓个排行榜」去起浏览器、调 LLM，
成本比收益高两个数量级。

## 输出

```
# GitHub Trending · 今日新增 Star 榜（12 个 · 🆕3）

1. 🆕 **owner/repo** [Rust] ⭐1,234(今日) 共 12,345
   一句话描述
   https://github.com/owner/repo

2. =  **owner/other** [Python] ⭐876(今日) 共 9,876
   ...
```

`🆕` = 7 天内没推过；`=` = 老面孔（配合 `--always` 才会重复出现）。

## 用法

```bash
python3 github_trending_scout.py --filter ai --limit 12 --always   # 日报模式
python3 github_trending_scout.py --json                            # 给下游用
python3 github_trending_scout.py --quiet                           # 无新增就静默
bash run.sh                                                        # cron 入口（带日志轮转）
```

## 去重状态

`$CRON_KIT_STATE_DIR/github_trending_seen.json`，只保留 30 天内的记录。
删掉这个文件 = 重置去重（第二天全部标 🆕）。

## 为什么「每天必推」

最初设计是「只在有新增时推」——听起来更克制，实际结果是**几天不看就忘了有这回事**。
改成每天必推、老面孔标 `=` 之后，一眼能看出「今天有没有新东西」，成本只是每天一条消息。

这是「静默语义」的一个有意例外：**信息型日报不适合静默**，它会变成没人记得存在的任务。
