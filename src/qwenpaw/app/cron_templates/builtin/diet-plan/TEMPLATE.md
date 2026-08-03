---
name: diet-plan
description: "Generate and send daily meal suggestions for 14 days."
metadata:
  qwenpaw:
    category: once
    title_key: cronJobs.templates.repeatCountAgentDietPlan.title
    description_key: cronJobs.templates.repeatCountAgentDietPlan.description
    frequency_key: cronJobs.templates.repeatCountAgentDietPlan.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 14天饮食计划

连续14天生成并发送当日饮食建议。

执行频率：首次执行：2026年1月1日 08:00｜每1天一次｜限定14次

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
