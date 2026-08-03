---
name: pet-care-reminder
description: "On the 15th each month at night, remind pet deworming or vaccine follow-up."
metadata:
  qwenpaw:
    category: cron
    title_key: cronJobs.templates.petCareReminder.title
    description_key: cronJobs.templates.petCareReminder.description
    frequency_key: cronJobs.templates.petCareReminder.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 宠物驱虫/疫苗提醒

每月 15 日晚提醒给宠物做体外驱虫。

执行频率：每月 15 日 20:00（0 20 15 * *）

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
