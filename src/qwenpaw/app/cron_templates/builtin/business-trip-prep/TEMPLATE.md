---
name: business-trip-prep
description: "Check destination weather and provide trip preparation suggestions."
metadata:
  qwenpaw:
    category: once
    title_key: cronJobs.templates.onceAgentBusinessTripPrep.title
    description_key: cronJobs.templates.onceAgentBusinessTripPrep.description
    frequency_key: cronJobs.templates.onceAgentBusinessTripPrep.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 出差前天气与行程准备

查询目的地天气并给出行程准备建议。

执行频率：执行时间：2026年1月1日 20:00

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
