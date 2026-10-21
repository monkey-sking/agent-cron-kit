# dsh-watchdog：桌面 App 掉线自动拉起

给「定时任务全跑在一个桌面 App 里」这种架构用的保险丝。

## 为什么需要

当调度器本身就是个 GUI App 时，它挂了 = 所有任务静默消失。没有报错、没有告警，
只是从某一天起什么都没发生。这个脚本每 60 秒确认一次，不在就拉起。

## 三重检测，缺一不可

| 检测 | 手段 | 防什么 |
|---|---|---|
| 1 | `lsappinfo find bundleid=` | 权威判断 GUI 进程是否存在 |
| 2 | preferences 里的端口 + `lsof -sTCP:LISTEN` | App 在跑但 UI 没起来 |
| 3 | `SingletonLock` 里的 PID + 校验该 PID 的可执行文件 | 崩溃残留的锁文件、PID 被系统复用 |

任一为真就算在跑——**宁可漏拉起，也不要重复拉起**。重复拉起会开出第二个实例，
抢同一份数据目录，比不拉起更糟。

## 为什么不 `pgrep`

Electron 主进程对 `pgrep -f` 常常不可见（只能匹配到 Helper 子进程），
于是「明明在跑」被判成「没跑」，触发重复拉起。这个坑实测过，已写进检测逻辑。

## 配置

默认值是 DeepSeek Harness 桌面版；换别的 Electron App 改这几个变量即可：

```bash
export APP_BUNDLE_ID=com.example.app
export APP_DISPLAY_NAME="Example"
export APP_PATH="/Applications/Example.app"
export APP_PREFS="$HOME/.example/preferences.json"
export APP_SINGLETON_LOCK="$HOME/.example/SingletonLock"
export APP_DEFAULT_PORT=8080
```

## 挂载

`StartInterval` = 60，`RunAtLoad` = true。模板见 `examples/launchd/`。
