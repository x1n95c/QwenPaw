---
name: workspace-usage
description: 每天巡检磁盘余量与 agent workspace 占用，只在超过阈值时提醒（跨平台）
metadata:
  qwenpaw:
    title: workspace 占用巡检
    category: cron
    frequency: 每天 08:00
    emoji: 💾
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# workspace 占用巡检

每天采集磁盘余量与 agent workspace 下各目录的占用，交给附带的 skill 判断是否需要清理。

## 包含内容

- `template.json` — 每天 08:00 的 agent 任务
- `batch/scan-unix.json` — macOS / Linux 采集脚本（`df` + `du`）
- `batch/scan-windows.json` — Windows 采集脚本（PowerShell）
- `skills/disk-usage-advisor/` — **选脚本 + 判读阈值 + 汇报格式都在这个 skill 里**

## 平台差异放在 skill 里，不是拆成两个模板

`df`/`du` 在 Windows 上不存在，PowerShell 的 `Get-PSDrive` 在 macOS 上默认没有，
所以采集脚本必须有两份。但**判读口径和汇报格式是跨平台的**，
所以做成一个模板：skill 里写明看系统提示的 Platform 选哪个脚本，
用户不需要在两个模板之间挑。

## 为什么不需要你填路径

脚本里的 `${args.path}` 由 agent 填入自己的 workspace 目录
（系统提示里的 Working directory）。workspace 位置可以被 `QWENPAW_WORKING_DIR`
改掉，所以模板里不写死路径。

## 占位符

| 占位符 | 实例化时替换为 |
|---|---|
| `{{template_dir}}` | 本包在磁盘上的绝对路径 |

本包**不设 `batch_entry`**（有两个脚本，按平台选），prompt 用 `{{template_dir}}`
给出目录，skill 负责拼出具体文件名。

**不要往 `{{template_dir}}` 里写文件**：未复制到模板池的内置包位于安装目录，
生产环境下通常只读。

## 自定义

- 改阈值：改 `skills/disk-usage-advisor/SKILL.md` 里的阈值表。
- 改巡检目标：改 prompt 里传给 `run_tool_batch` 的 `args.path`。
- 只想后台记录、不想收到消息：创建任务时勾选静默，结果仍会进 Inbox。
