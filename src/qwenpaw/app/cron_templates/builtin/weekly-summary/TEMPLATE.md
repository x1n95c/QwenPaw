---
name: weekly-summary
description: "Before each weekly meeting, summarize the last week's memory."
metadata:
  qwenpaw:
    category: once
    title_key: cronJobs.templates.repeatUntilAgentWeeklySummary.title
    description_key: cronJobs.templates.repeatUntilAgentWeeklySummary.description
    frequency_key: cronJobs.templates.repeatUntilAgentWeeklySummary.frequency
    tags:
      - team
      - calendar
    version: '1.0'
---

# 周会前工作总结

每次周会前基于最近一周 memory 生成工作总结。

执行频率：首次执行：2026年1月2日 08:30｜每7天一次｜截止到2026年3月1日

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
