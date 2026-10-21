# git-guard：自动推送 + 脏状态巡检

两个互补的任务，**都不替你提交**。

| 脚本 | 作用 | 输出 |
|---|---|---|
| `git_autopush.sh` | 把「已提交但未推送」的提交推上去 | 有推送/异常才输出 |
| `git_push_watchdog.sh` | 报告「有未提交改动」或「有未推送提交」 | 有脏状态才输出 |

## 为什么分成两个

自动提交是最容易出事的自动化：`git add -A` 会顺手带走 `.env`、cookie、几十兆的模型文件，
而且一旦提交就进了历史。所以这里划一条明确的线：

- **推送**是幂等的、可解释的 → 可以自动；
- **提交**需要人判断哪些文件该进 → 永远不自动。

巡检脚本负责在积压时提醒你，而不是替你决定。

## 保守策略

`git_autopush.sh` 的每个限制都对应一次真实事故：

1. **落后远端就不推。** 需要 merge/rebase 时自动 rebase 会丢本地提交（实测一次丢过 16 个），
   所以这里只报告，让人处理。
2. **没设上游就跳过。** 否则 `git push` 的默认行为取决于 `push.default` 配置，不可预期。
3. **上游是第三方的仓库要跳过。** 有些仓库 fork 了别人的项目，推上去等于往别人的仓库写东西，
   或者把私有适配器发到公开仓库。用 `repos.skip` 列出来。
4. **单仓库失败不影响其他仓库。**

## 配置

```bash
export CRON_KIT_GIT_ROOTS="$HOME/dev/Projects"      # 自动发现（最多 3 层）
export CRON_KIT_GIT_REPOS_FILE=~/.config/cron-kit/repos.txt
export CRON_KIT_GIT_SKIP="wechatsync vendor-*"       # 空格分隔的 glob
```

## 自检

```bash
bash git_autopush.sh --dry-run     # 只看会推什么
bash -n git_autopush.sh            # 语法检查
```
