---
name: weekly-meeting-reminder
description: "Send a weekly meeting reminder until the configured deadline."
metadata:
  qwenpaw:
    category: once
    title_key: cronJobs.templates.repeatUntilTextWeeklyMeeting.title
    description_key: cronJobs.templates.repeatUntilTextWeeklyMeeting.description
    frequency_key: cronJobs.templates.repeatUntilTextWeeklyMeeting.frequency
    tags:
      - team
      - reminder
      - calendar
    version: '1.0'
---

# 两个月周会提醒

每周提醒参加周会，持续到设定截止时间。

执行频率：首次执行：2026年1月2日 08:45｜每7天一次｜截止到2026年3月1日

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
