---
name: pomodoro-break-reminder
description: "During work hours, remind every 25 minutes to hydrate or rest your eyes."
metadata:
  qwenpaw:
    category: cron
    title_key: cronJobs.templates.pomodoroBreakReminder.title
    description_key: cronJobs.templates.pomodoroBreakReminder.description
    frequency_key: cronJobs.templates.pomodoroBreakReminder.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 休息提醒

工作时段每 25 分钟提醒起身喝水或远眺，减少疲劳。

执行频率：工作日 9:00-17:59 每 25 分钟（*/25 9-17 * * 1-5）

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
