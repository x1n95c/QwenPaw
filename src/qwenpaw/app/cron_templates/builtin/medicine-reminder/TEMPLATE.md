---
name: medicine-reminder
description: "Remind to take medicine on time for a 14-run course."
metadata:
  qwenpaw:
    category: once
    title_key: cronJobs.templates.repeatCountTextMedicineReminder.title
    description_key: cronJobs.templates.repeatCountTextMedicineReminder.description
    frequency_key: cronJobs.templates.repeatCountTextMedicineReminder.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 吃药提醒（14次）

提醒按时吃药，本疗程共14次。

执行频率：首次执行：2026年1月1日 09:00｜每1天一次｜限定14次

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
