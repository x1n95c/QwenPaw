---
name: weekend-relaxation-reminder
description: "On weekend mornings, push curated hot movie picks for a lighter break."
metadata:
  qwenpaw:
    category: cron
    title_key: cronJobs.templates.weekendRelaxationReminder.title
    description_key: cronJobs.templates.weekendRelaxationReminder.description
    frequency_key: cronJobs.templates.weekendRelaxationReminder.frequency
    tags:
      - team
      - reminder
      - calendar
    version: '1.0'
---

# 周末电影推荐

周末上午推送近期热门电影推荐。

执行频率：周末 10:00（0 10 * * 6,0）

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
