# proxy-watchdog：代理内核看门狗

代理挂了的表现往往不是「报错」，而是**所有依赖它的定时任务一起静默失败**。
这个脚本每 5 分钟确认一次内核是否真的在工作。

## 为什么先看 API，而不是先看进程

进程在 ≠ 内核可用。实测过的两种状态：

- 进程活着，但配置加载失败 → 端口不监听；
- 进程是残留的僵尸外壳，实际已经不处理流量。

所以第一判据是控制 API 是否返回 JSON；进程只作为「要不要等 2 秒再试一次」的参考。
另外，从 `ps` 的进程命令行里用正则抠 `-secret` 是能跑的，但那等于绕过配置、
依赖别人的命令行格式 —— 这里改成显式读 `PROXY_CTL_SECRET`。

## 恢复顺序

1. 杀残留内核进程；
2. 退出并重新打开代理 App（`PROXY_APP_NAME`）；有 `PROXY_RESTART_CMD` 时优先用它；
3. 复查 API。

**只尝试一轮。** 反复重启会把系统搞得更乱，而且掩盖真正的根因。

## 顺带处理的一个常见故障

`geoip.metadb` 损坏会导致内核起不来，而报错往往只在内核日志里。
脚本检查文件头，坏了就删掉让内核重建；`geoip.dat` / `geosite.dat` 缺失时从 work 目录复制。

## 配置

```bash
export PROXY_CTL_API=http://127.0.0.1:9090
export PROXY_CTL_SECRET=...
export PROXY_CORE_PATTERN="mihomo|ClashMac/core/current"
export PROXY_PROFILES_DIR="$HOME/Library/Application Support/ClashMac/profiles"
export PROXY_WORK_DIR="$HOME/Library/Application Support/ClashMac/work"
export PROXY_APP_NAME="ClashMac"
```

只想检查不想动手：`bash proxy-watchdog.sh --check`。
