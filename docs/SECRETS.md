# 脱敏规则：共享之前必须过的关

脚本好审，**配置和数据才是真正的雷区**。这份清单是按「最容易被一起带走」的顺序排的。

## 一、先认清这台机器上有哪些凭据

本机 `.env` 里有 37 个键（其中 36 个是凭据类变量），粗略分类：

| 类别 | 键（只列名字） | 泄露后果 |
|---|---|---|
| 模型 API | `DEEPSEEK_API_KEY` / `OPENCODE_GO_API_KEY` / `OPENCODE_ZEN_API_KEY` / `NVIDIA_API_KEY` / `OLLAMA_API_KEY` / `RADEON_API_KEY` | 直接烧钱、被刷额度 |
| IM 凭据 | `TELEGRAM_BOT_TOKEN`、`FEISHU_APP_ID/SECRET`、`WEIXIN_TOKEN`、`DINGTALK_CLIENT_SECRET` | 冒充机器人发消息、读群 |
| 路由标识 | `TELEGRAM_CHAT_ID`、`FEISHU_CHAT_ID`、`FEISHU_HOME_CHANNEL(_THREAD_ID)`、`WEIXIN_HOME_CHANNEL` | 定位到具体的人和群 |

外加 `~/.hermes/secrets/`（第三方 API key）与 `~/.hermes/.env.bak_*`（**4 份历史备份**）。

> 铁律：**不要把家目录当仓库。** 在家目录 `git init && git add -A` 的那一下，上面全部会进第一个 commit。
> 仓库只放脚本，配置放仓库外，靠环境变量或 `CRON_KIT_ENV_FILE` 注入。

## 二、共享前的 5 步

```bash
# 1. 扫描（0 high 才继续）
python3 tools/secrets_scan.py --path .

# 2. 确认没被 git 跟踪
git ls-files | grep -Ei '(\.env|secret|cookie|token|credential|\.pem|id_rsa)'

# 3. 确认没有个人路径
grep -rn '/Users/\|/Volumes/\|/home/' --include='*' . | grep -v '^./docs/'

# 4. 确认没有真实账号/姓名/服务器地址
grep -rnE '@[A-Za-z0-9_]{4,}|[0-9]{1,3}(\.[0-9]{1,3}){3}' . --exclude-dir=.git

# 5. 看一遍 git diff 再 push
git diff --stat HEAD~1
```

## 三、替换而不是删除

脱敏不是把值删掉留空，而是**换成明确占位符**，让使用者知道这里该填什么：

| 原值 | 替换为 |
|---|---|
| `oc_xxx`（飞书会话 ID，32 位十六进制） | `oc_REPLACE_ME_0000000000000000` |
| 纯数字的会话/群 ID | `REPLACE_ME` |
| `@somebody` | `@example_user` |
| 任何 `a.b.c.d` 形式的主机地址 | `203.0.113.10`（RFC 5737 文档专用段） |
| `{{SECRET_xxx}}` | `${CLASH_SECRET}` 之类的显式环境变量 |

理由：占位符能让 `secrets_scan.py` 通过，也能让读者一眼看出「这里需要配置」。
删成空白，脚本会拿着空值去请求，然后**静默失败**——比泄露更难查。

## 四、日志与输出也会泄露

脚本本身干净，不代表它的输出干净。共享日志/截图/issue 之前同样要过一遍：

- 投递失败时落到 outbox 的正文（含业务数据）；
- `curl -v` 的输出（含 Authorization 头）；
- 报错栈里的完整 URL（有些 API 把 key 放在 query string）；
- 终端历史（`~/.zsh_history`）。

## 五、一个真实的反面教材

有两类脚本曾经差点被一起打包公开：一类在源码里写死了远端主机地址与 SSH 私钥路径，
另一类会打包整个浏览器 profile 与登录态快照。

它们的代码质量都没问题，但**一段都不适合公开**——不是写得差，而是它描述的东西本身就是机密。
审脚本时先问「这段代码在操作什么」，再看「这段代码写得怎么样」。
