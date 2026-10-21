# network-watchdog：网络与本地代理

每 5 分钟检查一次，只在**出问题时**说话。

## 两个故障模式

1. **公网不通。** 常见原因：代理内核挂了。若配置了 `CLASH_RESTART_CMD`，脚本会执行一次重启命令并复查。
2. **网络通，但本地代理端口不监听。** 这是更隐蔽的一种：代理软件重新生成配置时变成纯 TUN 模式，
   入站端口被清零，而系统代理设置还指着 `127.0.0.1:7890`。表现是「网明明通，但所有程序都超时」。
   若配置了 `CLASH_API` + `CLASH_SECRET`，脚本会 PATCH `/configs` 把 mixed-port 开回来。

## 为什么不去 `ps` 里抠密钥

有些实现会从进程命令行里正则提取代理的 `-secret`。能跑，但那是黑魔法：
等于绕过配置、依赖别人的命令行格式，而且把密钥暴露在进程表里。
这里改成显式读 `CLASH_SECRET`，没有就跳过修复并记日志。

## 配置

```bash
export CLASH_API=http://127.0.0.1:9090
export CLASH_SECRET=...          # 没有就跳过自动修复
export CLASH_RESTART_CMD="open -a ClashMac"   # 可选
export WATCHDOG_PING_TARGET=223.5.5.5
export WATCHDOG_PROXY_PORT=7890
```

## 行为约定

- 正常 → 零输出，只写日志；
- 修好了 → 输出一行「已恢复」；
- 修不好 → 输出告警 + 写 flag 文件（`NETWORK_DOWN_FLAG` / `PROXY_DOWN_FLAG`），退出码 1；
- **不反复重启。** 一轮只尝试一次，避免把系统搞得更乱。
