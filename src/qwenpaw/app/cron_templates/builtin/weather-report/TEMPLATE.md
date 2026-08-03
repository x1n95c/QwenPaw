---
name: weather-report
description: 每天早上查询天气并播报穿衣与带伞建议，跨平台（curl + wttr.in）
metadata:
  qwenpaw:
    title: 每日天气播报
    category: cron
    frequency: 每天 07:30
    emoji: 🌦️
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 每日天气播报

最小形态的「批处理 + skill」示例：采集只有一条命令，判读全交给 skill。

## 包含内容

- `template.json` — 每天 07:30 的 agent 任务
- `batch/weather.json` — 一条 `curl wttr.in` 请求
- `skills/weather-report/` — 把 JSON 读成播报的 skill

## 城市怎么填

`batch/weather.json` 里是 `${args.city}`：

- 传城市名（`Beijing` / `上海` / `Tokyo`）查指定城市
- **传空字符串** `""`：URL 变成 `https://wttr.in/?format=j1`，
  wttr.in 按出口 IP 自动定位。模板默认就是这个，**开箱不需要填任何东西**。

想固定城市的话，改 `template.json` 里 prompt 传给 `run_tool_batch` 的
`args.city` 即可。

## 平台

`curl` 在 macOS / Linux 自带，Windows 10 1803 及以后自带 `curl.exe`，
所以本包**不分平台**。需要能访问 `wttr.in`。

## 占位符

| 占位符 | 实例化时替换为 |
|---|---|
| `{{template_dir}}` | 本包在磁盘上的绝对路径 |
| `{{batch_entry}}` | `batch/weather.json` 的绝对路径 |
