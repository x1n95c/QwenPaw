---
name: daily-tech-news-brief
description: "At 09:30 on weekdays, compile trending tech headlines into a short digest."
metadata:
  qwenpaw:
    category: cron
    title_key: cronJobs.templates.dailyTechNewsBrief.title
    description_key: cronJobs.templates.dailyTechNewsBrief.description
    frequency_key: cronJobs.templates.dailyTechNewsBrief.frequency
    tags:
      - personal
      - reminder
      - calendar
    version: '1.0'
---

# 工作日科技新闻早报

工作日 9:30 自动整理当日热门科技资讯并推送简报。

执行频率：工作日 09:30（30 9 * * 1-5）

标题与说明走 i18n（`title_key` / `description_key` / `frequency_key`），因此会跟随界面语言显示。

创建任务前请补齐投递渠道与目标会话；本包不预设投递目标。
