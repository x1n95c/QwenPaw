import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Button,
  Card,
  Dropdown,
  Form,
  Modal,
  Popover,
  Select,
  Table,
} from "@agentscope-ai/design";
import {
  CalendarOutlined,
  LeftOutlined,
  MoreOutlined,
  RightOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import timezone from "dayjs/plugin/timezone";
import utc from "dayjs/plugin/utc";
import type {
  CronDispatchTargetItem,
  CronJobExecutionRecord,
  CronJobSpecOutput,
} from "../../../api/types";
import { useTranslation } from "react-i18next";
import api from "../../../api";
import {
  createColumns,
  JobDrawer,
  SaveAsTemplateModal,
  TemplatePickerModal,
  useCronJobs,
  DEFAULT_FORM_VALUES,
} from "./components";
import { expandDottedArgKeys } from "./components/batchValidation";
import { parseCron, serializeCron } from "./components/parseCron";
import { jobToFormValues } from "./components/jobToTemplate";
import {
  declaredPreprocessScripts,
  remapPreprocessScripts,
} from "./components/packageTemplates";
import { copyTemplateScripts } from "./components/templateScripts";
import { resolveTemplateSkills } from "./components/templateSkills";
import type { CronTemplateDefinition } from "./components/templates";
import { useCronTemplates } from "./components/useCronTemplates";
import { useAppMessage } from "../../../hooks/useAppMessage";
import { PageHeader } from "@/components/PageHeader";
import styles from "./index.module.less";

type CronJob = CronJobSpecOutput;
type OneTimeCronJob = CronJob & {
  schedule: {
    type: "once";
    run_at: string;
    timezone?: string;
    repeat_every_days?: number;
    repeat_end_type?: "never" | "until" | "count";
    repeat_until?: string;
    repeat_count?: number;
  };
};
type CronViewMode = "list" | "calendar";
type ScheduleTypeFilter = "all" | "cron" | "once";
type OneTimeJobEvent = {
  job: OneTimeCronJob;
  runAtInUserTimezone: dayjs.Dayjs;
};

dayjs.extend(utc);
dayjs.extend(timezone);

function CronJobsPage() {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  // One instance for the page: the picker and "save as template" both read
  // it, and two separate hooks would each hold their own list — saving a
  // template would then not show up in the picker until a reload.
  const cronTemplates = useCronTemplates();
  const {
    jobs,
    loading,
    createJob,
    updateJob,
    deleteJob,
    toggleEnabled,
    executeNow,
  } = useCronJobs();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingJob, setEditingJob] = useState<CronJob | null>(null);
  /**
   * The id of the job the drawer is editing.
   *
   * Minted here rather than by the server, because the preprocess block
   * writes batch scripts to `cron_jobs/<jobId>/batch/` while the drawer is
   * still open — a server-generated id would arrive too late. `POST
   * /cron/jobs` honours it and rejects one that is already taken; an
   * abandoned drawer leaves a directory the cron manager reaps at start.
   */
  const [drawerJobId, setDrawerJobId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [templateSourceJob, setTemplateSourceJob] = useState<CronJob | null>(
    null,
  );
  const [viewMode, setViewMode] = useState<CronViewMode>("list");
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    setIsMobile(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  const [scheduleTypeFilter, setScheduleTypeFilter] =
    useState<ScheduleTypeFilter>("all");
  const [calendarMonth, setCalendarMonth] = useState(dayjs());
  const [activePopoverDate, setActivePopoverDate] = useState<string | null>(
    null,
  );
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyRecords, setHistoryRecords] = useState<
    CronJobExecutionRecord[]
  >([]);
  const [historyJobName, setHistoryJobName] = useState("");
  const [expandedHistoryErrors, setExpandedHistoryErrors] = useState<
    Set<string>
  >(new Set());
  const [userTimezone, setUserTimezone] = useState("UTC");
  const [form] = Form.useForm<CronJob>();
  const userTimezoneRef = useRef("UTC");
  const [targetItems, setTargetItems] = useState<CronDispatchTargetItem[]>([]);
  const [targetChannels, setTargetChannels] = useState<string[]>(["console"]);
  const [targetsLoading, setTargetsLoading] = useState(false);

  const isOneTimeJob = (job: CronJob): job is OneTimeCronJob =>
    job.schedule?.type === "once" && typeof job.schedule?.run_at === "string";

  useEffect(() => {
    api
      .getUserTimezone()
      .then((res) => {
        if (res.timezone) {
          userTimezoneRef.current = res.timezone;
          setUserTimezone(res.timezone);
          setCalendarMonth(dayjs().tz(res.timezone));
        }
      })
      .catch((err) => console.error("Failed to fetch user timezone:", err));
  }, []);

  const loadDispatchTargets = useCallback(async () => {
    setTargetsLoading(true);
    try {
      const res = await api.listCronDispatchTargets();
      setTargetItems(res?.items || []);
      setTargetChannels(res?.channels?.length ? res.channels : ["console"]);
    } catch (error) {
      console.error("Failed to fetch cron dispatch targets", error);
      setTargetItems([]);
      setTargetChannels(["console"]);
    } finally {
      setTargetsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDispatchTargets();
  }, [loadDispatchTargets]);

  const handleCreate = () => {
    setEditingJob(null);
    setDrawerJobId(crypto.randomUUID());
    form.resetFields();
    form.setFieldsValue({
      ...DEFAULT_FORM_VALUES,
      schedule: {
        ...DEFAULT_FORM_VALUES.schedule,
        timezone: userTimezoneRef.current,
      },
    });
    setDrawerOpen(true);
  };

  const handleOpenTemplateModal = () => {
    setTemplateModalOpen(true);
  };

  const handleUseTemplate = async (
    templateValues: Record<string, unknown>,
    template?: CronTemplateDefinition,
  ) => {
    setTemplateModalOpen(false);
    setEditingJob(null);
    const jobId = crypto.randomUUID();
    form.resetFields();

    // Every bundled script is copied into the new job's own directory, and
    // this is the only place that can do it: the job id is minted here, and
    // a script has to belong to a job to resolve at all. Copying only the
    // ones the preprocess chain names was the "我的脚本 is empty" bug — a
    // package may ship scripts for the *agent* to pick between at run time,
    // and the user should still find them under their own job.
    let landed: Record<string, string> = {};
    if (template?.packageName && template.batchFiles.length > 0) {
      // The picker is already closed and the drawer is not open yet, so
      // without this the screen is blank for the whole copy — which is now
      // one scanned write per bundled file, not one in total.
      const done = message.loading(t("cronJobs.templateCopyingScripts"), 0);
      try {
        const result = await copyTemplateScripts({
          template,
          declared: declaredPreprocessScripts(templateValues),
          copy: (body) => api.copyJobBatch(jobId, body),
        });
        landed = result.landed;
        // One message per outcome, not per file. A declared script that
        // failed leaves the chain broken; an extra that failed does not, so
        // they must not read the same.
        if (result.failed.declared.length > 0) {
          message.error(
            t("cronJobs.templateCopyScriptsFailed", {
              names: result.failed.declared.join(", "),
            }),
          );
        }
        if (result.failed.extra.length > 0) {
          message.warning(
            t("cronJobs.templateCopyExtraScriptsFailed", {
              count: result.failed.extra.length,
            }),
          );
        }
      } finally {
        done();
      }
    }
    // Unconditional: with no preprocess block this returns the same object.
    const values = remapPreprocessScripts(templateValues, landed);

    // Bundled skills are *referenced*, not installed: the job holds a ref
    // and the trigger path reads SKILL.md out of the package in place. So
    // nothing is written to the workspace here and nothing has to be
    // enabled — a template whose prompt says "follow the X skill" works
    // straight away, and the user is free to un-pick it in the drawer.
    const skills = resolveTemplateSkills(templateValues, template);

    // Published only now, deliberately. `useToolBatches(jobId)` re-lists as
    // soon as the id changes, so announcing it before the copy finished
    // would list an empty directory and flash "script not found" on a chain
    // that is about to be perfectly valid.
    setDrawerJobId(jobId);
    form.setFieldsValue({
      ...DEFAULT_FORM_VALUES,
      schedule: {
        ...DEFAULT_FORM_VALUES.schedule,
        timezone: userTimezoneRef.current,
      },
      ...values,
      // After the spread: a package that declares no skills must still
      // clear whatever the previously opened job left in the field.
      skills,
      // Provenance, so the skill picker can lead with this package's own
      // skills instead of burying them behind the expander. Recorded on the
      // job rather than held as drawer state so it survives a later edit.
      meta: {
        ...(values.meta as Record<string, unknown> | undefined),
        from_template: template?.packageName,
      },
    });
    setDrawerOpen(true);
  };

  const formatSchedule = (job: CronJob) => {
    if (job.schedule?.type === "once") {
      return job.schedule?.run_at
        ? dayjs(job.schedule.run_at).format("YYYY-MM-DD HH:mm")
        : "-";
    }
    const cron = job.schedule?.cron || "-";
    const parts = parseCron(cron);
    switch (parts.type) {
      case "hourly":
        return t("cronJobs.cronTypeHourly");
      case "daily":
        return `${t("cronJobs.cronTypeDaily")} ${String(parts.hour).padStart(
          2,
          "0",
        )}:${String(parts.minute).padStart(2, "0")}`;
      case "weekly": {
        const dayNames = (parts.daysOfWeek || [])
          .map((d) => {
            const dayMap: Record<string, string> = {
              mon: t("cronJobs.cronDayMon"),
              tue: t("cronJobs.cronDayTue"),
              wed: t("cronJobs.cronDayWed"),
              thu: t("cronJobs.cronDayThu"),
              fri: t("cronJobs.cronDayFri"),
              sat: t("cronJobs.cronDaySat"),
              sun: t("cronJobs.cronDaySun"),
            };
            return dayMap[d] || d;
          })
          .join(",");
        return `${t("cronJobs.cronTypeWeekly")} ${dayNames}`;
      }
      default:
        return cron;
    }
  };

  const handleEdit = (job: CronJob) => {
    setEditingJob(job);
    setDrawerJobId(job.id || "");
    // Shared with the "save as template" flow so a template built from a job
    // reproduces exactly what editing that job shows. The cast is needed
    // because the drawer carries UI-only fields (scheduleType, cronType,
    // cronTime, …) that are not part of CronJob.
    form.setFieldsValue(
      jobToFormValues(job) as unknown as Parameters<
        typeof form.setFieldsValue
      >[0],
    );
    setDrawerOpen(true);
  };

  const handleSaveAsTemplate = (job: CronJob) => {
    setTemplateSourceJob(job);
    setSaveTemplateOpen(true);
  };

  const handleDelete = (jobId: string) => {
    Modal.confirm({
      title: t("cronJobs.confirmDelete"),
      content: t("cronJobs.deleteConfirm"),
      okText: t("cronJobs.deleteText"),
      okType: "primary",
      cancelText: t("cronJobs.cancelText"),
      onOk: async () => {
        await deleteJob(jobId);
      },
    });
  };

  const handleToggleEnabled = async (job: CronJob) => {
    await toggleEnabled(job);
  };

  const handleExecuteNow = async (job: CronJob) => {
    Modal.confirm({
      title: t("cronJobs.executeNowTitle"),
      content: t("cronJobs.executeNowContent", { name: job.name }),
      okText: t("cronJobs.executeNowConfirm"),
      okType: "primary",
      cancelText: t("cronJobs.cancelText"),
      onOk: async () => {
        await executeNow(job.id);
      },
    });
  };

  const handleDrawerClose = () => {
    setDrawerOpen(false);
    setEditingJob(null);
  };

  const handleViewHistory = async (job: CronJob) => {
    setHistoryJobName(job.name);
    setHistoryModalOpen(true);
    setExpandedHistoryErrors(new Set());
    setHistoryLoading(true);
    try {
      const records = await api.getCronJobHistory(job.id);
      setHistoryRecords(records || []);
    } catch (error) {
      console.error("Failed to fetch cron history", error);
      setHistoryRecords([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleSubmit = async (values: any) => {
    let schedule: any = values.schedule || {};
    if ((values.scheduleType || "cron") === "once") {
      const onceRepeatEnabled = Boolean(values.onceRepeatEnabled);
      const repeatEndType = values.onceRepeatEndType || "never";
      schedule = {
        type: "once",
        run_at: values.onceRunAt
          ? dayjs(values.onceRunAt).format("YYYY-MM-DDTHH:mm:00")
          : undefined,
        timezone: values.schedule?.timezone || userTimezoneRef.current,
        repeat_every_days: onceRepeatEnabled
          ? Number(values.onceRepeatEveryDays || 1)
          : undefined,
        repeat_end_type: onceRepeatEnabled ? repeatEndType : undefined,
        repeat_until:
          onceRepeatEnabled &&
          repeatEndType === "until" &&
          values.onceRepeatUntil
            ? dayjs(values.onceRepeatUntil).format("YYYY-MM-DDTHH:mm:00")
            : undefined,
        repeat_count:
          onceRepeatEnabled && repeatEndType === "count"
            ? Number(values.onceRepeatCount || 1)
            : undefined,
      };
    } else {
      const cronParts: any = {
        type: values.cronType || "daily",
      };

      if (values.cronType === "daily" || values.cronType === "weekly") {
        if (values.cronTime) {
          cronParts.hour = values.cronTime.hour();
          cronParts.minute = values.cronTime.minute();
        }
      }

      if (values.cronType === "weekly" && values.cronDaysOfWeek) {
        cronParts.daysOfWeek = values.cronDaysOfWeek;
      }

      if (values.cronType === "custom" && values.cronCustom) {
        cronParts.rawCron = values.cronCustom;
      }

      schedule = {
        ...values.schedule,
        type: "cron",
        cron: serializeCron(cronParts),
      };
    }

    let processedValues = {
      ...values,
      schedule,
    };
    delete processedValues.scheduleType;
    delete processedValues.onceRunAt;
    delete processedValues.onceRepeatEnabled;
    delete processedValues.onceRepeatEveryDays;
    delete processedValues.onceRepeatEndType;
    delete processedValues.onceRepeatUntil;
    delete processedValues.onceRepeatCount;
    delete processedValues.cronType;
    delete processedValues.cronTime;
    delete processedValues.cronDaysOfWeek;
    delete processedValues.cronCustom;

    if (processedValues.task_type === "text") {
      // Remove request object entirely for text tasks
      delete processedValues.request;
    } else if (processedValues.task_type === "agent") {
      //Ensure request object exists
      if (!processedValues.request) {
        processedValues.request = {};
      }

      // Parse request input JSON
      if (
        processedValues.request?.input &&
        typeof processedValues.request.input === "string"
      ) {
        try {
          processedValues.request.input = JSON.parse(
            processedValues.request.input,
          );
        } catch (error) {
          console.error("❌ Failed to parse request.input JSON:", error);
        }
      }
    }

    // Preprocess block: drop it entirely when disabled or empty, and drop
    // blank rows / empty args so the payload carries no noise.
    const preprocess = processedValues.preprocess;
    const rawSteps: { script?: string; args?: Record<string, unknown> }[] =
      Array.isArray(preprocess?.steps) ? preprocess.steps : [];
    const steps = rawSteps.filter((step) => step?.script?.trim());
    if (!preprocess?.enabled || steps.length === 0) {
      // An enabled block with no script has nothing to run and the server
      // rejects it, so send no preprocess rather than an empty one.
      delete processedValues.preprocess;
    } else {
      processedValues.preprocess = {
        enabled: true,
        steps: steps.map((step) => {
          const entry: Record<string, unknown> = {
            script: (step.script as string).trim(),
          };
          if (step.args && Object.keys(step.args).length > 0) {
            // The executor resolves ${args.out.dir} as a nested path, so
            // flat dotted keys from the form must be expanded on submit.
            entry.args = expandDottedArgKeys(step.args);
          }
          return entry;
        }),
        last_only: preprocess.last_only !== false,
        on_failure: preprocess.on_failure || "continue",
        timeout_seconds:
          preprocess.timeout_seconds ??
          DEFAULT_FORM_VALUES.preprocess.timeout_seconds,
      };
    }

    let success = false;
    setSaving(true);
    try {
      if (editingJob) {
        success = await updateJob(editingJob.id, processedValues);
      } else {
        // The id the drawer already wrote scripts under, so the saved job
        // owns them rather than pointing at a directory nobody claims.
        success = await createJob({ ...processedValues, id: drawerJobId });
      }
    } finally {
      setSaving(false);
    }
    if (success) {
      setDrawerOpen(false);
    }
  };

  const columns = createColumns({
    onToggleEnabled: handleToggleEnabled,
    onExecuteNow: handleExecuteNow,
    onViewHistory: handleViewHistory,
    onEdit: handleEdit,
    onDelete: handleDelete,
    onSaveAsTemplate: handleSaveAsTemplate,
    t,
  });

  const HISTORY_ERROR_PREVIEW_LINES = 4;
  const HISTORY_ERROR_PREVIEW_CHARS = 280;

  const shouldShowErrorToggle = (errorText: string) => {
    const lineCount = errorText.split("\n").length;
    return (
      lineCount > HISTORY_ERROR_PREVIEW_LINES ||
      errorText.length > HISTORY_ERROR_PREVIEW_CHARS
    );
  };

  const toggleHistoryError = (recordKey: string) => {
    setExpandedHistoryErrors((prev) => {
      const next = new Set(prev);
      if (next.has(recordKey)) {
        next.delete(recordKey);
      } else {
        next.add(recordKey);
      }
      return next;
    });
  };

  const parseAtInTimezone = (timeText: string, timezoneName: string) => {
    const hasOffset = /([zZ]|[+-]\d{2}:?\d{2})$/.test(timeText);
    if (hasOffset) {
      return dayjs(timeText).tz(timezoneName);
    }
    return dayjs.tz(timeText, timezoneName);
  };

  const oneTimeJobs = useMemo(() => jobs.filter(isOneTimeJob).slice(), [jobs]);

  const filteredListJobs = useMemo(() => {
    if (scheduleTypeFilter === "all") return jobs;
    return jobs.filter((job) => job.schedule?.type === scheduleTypeFilter);
  }, [jobs, scheduleTypeFilter]);

  const calendarDays = useMemo(() => {
    const monthStart = calendarMonth.startOf("month");
    const calendarStart = monthStart.startOf("week");
    return Array.from({ length: 42 }, (_, index) =>
      calendarStart.add(index, "day"),
    );
  }, [calendarMonth]);

  const oneTimeJobEvents = useMemo<OneTimeJobEvent[]>(() => {
    if (calendarDays.length === 0) return [];
    const rangeStartInUserTz = calendarDays[0].startOf("day");
    const rangeEndInUserTz = calendarDays[calendarDays.length - 1].endOf("day");
    const events: OneTimeJobEvent[] = [];

    oneTimeJobs.forEach((job) => {
      const scheduleTimezone = job.schedule.timezone || "UTC";
      const baseInScheduleTz = parseAtInTimezone(
        job.schedule.run_at,
        scheduleTimezone,
      );
      const rangeStartInScheduleTz = rangeStartInUserTz.tz(scheduleTimezone);
      const rangeEndInScheduleTz = rangeEndInUserTz.tz(scheduleTimezone);
      const repeatEveryDays = job.schedule.repeat_every_days;

      if (!repeatEveryDays) {
        const runAtInUserTimezone = baseInScheduleTz.tz(userTimezone);
        if (
          !runAtInUserTimezone.isBefore(rangeStartInUserTz) &&
          !runAtInUserTimezone.isAfter(rangeEndInUserTz)
        ) {
          events.push({
            job,
            runAtInUserTimezone,
          });
        }
        return;
      }

      const countLimit =
        job.schedule.repeat_end_type === "count"
          ? job.schedule.repeat_count ?? 0
          : null;
      if (countLimit !== null && countLimit <= 0) return;

      const untilInScheduleTz =
        job.schedule.repeat_end_type === "until" && job.schedule.repeat_until
          ? parseAtInTimezone(job.schedule.repeat_until, scheduleTimezone)
          : null;

      let startIndex = 0;
      if (baseInScheduleTz.isBefore(rangeStartInScheduleTz)) {
        const diffDays = rangeStartInScheduleTz
          .startOf("day")
          .diff(baseInScheduleTz.startOf("day"), "day");
        startIndex = Math.max(0, Math.floor(diffDays / repeatEveryDays));
      }

      let index = startIndex;
      let current = baseInScheduleTz.add(index * repeatEveryDays, "day");
      while (current.isBefore(rangeStartInScheduleTz)) {
        index += 1;
        current = baseInScheduleTz.add(index * repeatEveryDays, "day");
      }

      const maxIterations = 400;
      let iterations = 0;
      while (
        !current.isAfter(rangeEndInScheduleTz) &&
        iterations < maxIterations
      ) {
        iterations += 1;
        const runNumber = index + 1;
        if (countLimit !== null && runNumber > countLimit) break;
        if (untilInScheduleTz && current.isAfter(untilInScheduleTz)) break;

        events.push({
          job,
          runAtInUserTimezone: current.tz(userTimezone),
        });
        index += 1;
        current = baseInScheduleTz.add(index * repeatEveryDays, "day");
      }
    });

    return events.sort(
      (a, b) =>
        a.runAtInUserTimezone.valueOf() - b.runAtInUserTimezone.valueOf(),
    );
  }, [calendarDays, oneTimeJobs, userTimezone]);

  const oneTimeJobsByDate = useMemo(() => {
    return oneTimeJobEvents.reduce<Record<string, OneTimeJobEvent[]>>(
      (acc, event) => {
        const dateKey = event.runAtInUserTimezone.format("YYYY-MM-DD");
        if (!acc[dateKey]) acc[dateKey] = [];
        acc[dateKey].push(event);
        return acc;
      },
      {},
    );
  }, [oneTimeJobEvents]);

  return (
    <div className={styles.cronJobsPage}>
      <PageHeader
        items={[{ title: t("nav.control") }, { title: t("cronJobs.title") }]}
        extra={
          <div className={styles.headerActions}>
            {viewMode === "list" && (
              <Select<ScheduleTypeFilter>
                value={scheduleTypeFilter}
                onChange={setScheduleTypeFilter}
                style={
                  isMobile ? { width: "100%", maxWidth: 160 } : { width: 200 }
                }
                options={[
                  {
                    label: t("cronJobs.scheduleFilterAll"),
                    value: "all",
                  },
                  {
                    label: t("cronJobs.scheduleTypeRecurring"),
                    value: "cron",
                  },
                  {
                    label: t("cronJobs.scheduleTypeOnce"),
                    value: "once",
                  },
                ]}
              />
            )}
            <div className={styles.viewToggle}>
              <button
                className={`${styles.viewToggleBtn} ${
                  viewMode === "list" ? styles.viewToggleBtnActive : ""
                }`}
                onClick={() => setViewMode("list")}
                title={t("cronJobs.listView")}
              >
                <UnorderedListOutlined />
              </button>
              <button
                className={`${styles.viewToggleBtn} ${
                  viewMode === "calendar" ? styles.viewToggleBtnActive : ""
                }`}
                onClick={() => setViewMode("calendar")}
                title={t("cronJobs.calendarView")}
              >
                <CalendarOutlined />
              </button>
            </div>
            {!isMobile && (
              <Button type="primary" onClick={handleCreate}>
                + {t("cronJobs.createJob")}
              </Button>
            )}
            {isMobile && (
              <Button type="primary" onClick={handleCreate} size="small">
                +
              </Button>
            )}
            {!isMobile && (
              <Button onClick={handleOpenTemplateModal}>
                {t("cronJobs.createFromTemplate")}
              </Button>
            )}
          </div>
        }
      />

      {viewMode === "list" ? (
        isMobile ? (
          <div className={styles.mobileCardList}>
            {filteredListJobs.map((job) => (
              <Card
                key={job.id}
                className={styles.mobileJobCard}
                size="small"
                bodyStyle={{ padding: 24 }}
              >
                <div className={styles.mobileJobHeader}>
                  <span className={styles.mobileJobName}>{job.name}</span>
                  <span
                    className={`${styles.mobileJobStatus} ${
                      job.enabled ? styles.enabled : ""
                    }`}
                  >
                    <span
                      className={`${styles.statusDot} ${
                        job.enabled ? styles.enabled : styles.disabled
                      }`}
                    />
                    {job.enabled ? t("common.enabled") : t("common.disabled")}
                  </span>
                </div>
                <div className={styles.mobileJobSchedule}>
                  {formatSchedule(job)}
                </div>
                <div className={styles.mobileJobActions}>
                  <Button
                    size="small"
                    className={styles.mobileActionBtn}
                    onClick={() => toggleEnabled(job)}
                  >
                    {job.enabled ? t("cronJobs.disable") : t("common.enable")}
                  </Button>
                  <Button
                    size="small"
                    className={styles.mobileActionBtn}
                    onClick={() => executeNow(job.id as string)}
                  >
                    {t("cronJobs.executeNow")}
                  </Button>
                  <Button
                    size="small"
                    className={styles.mobileActionBtn}
                    onClick={() => handleViewHistory(job)}
                  >
                    {t("cronJobs.executionHistory")}
                  </Button>
                  <Dropdown
                    menu={{
                      items: [
                        {
                          key: "edit",
                          label: t("cronJobs.edit"),
                          onClick: () => handleEdit(job),
                        },
                        {
                          key: "delete",
                          label: t("cronJobs.delete"),
                          danger: true,
                          onClick: () => handleDelete(job.id as string),
                        },
                      ],
                    }}
                    placement="bottomRight"
                  >
                    <Button
                      type="text"
                      size="small"
                      className={styles.mobileMoreBtn}
                      icon={<MoreOutlined />}
                    />
                  </Dropdown>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <Card className={styles.tableCard} bodyStyle={{ padding: 0 }}>
            <Table
              columns={columns}
              dataSource={filteredListJobs}
              loading={loading}
              rowKey="id"
              scroll={{ x: 2840 }}
              pagination={{
                pageSize: 10,
                showSizeChanger: false,
              }}
            />
          </Card>
        )
      ) : (
        <Card className={styles.calendarCard} bodyStyle={{ padding: 0 }}>
          <div className={styles.calendarHeader}>
            <Button
              type="text"
              icon={<LeftOutlined />}
              onClick={() =>
                setCalendarMonth((prev) => prev.subtract(1, "month"))
              }
            />
            <div className={styles.calendarTitle}>
              {calendarMonth.tz(userTimezone).format("YYYY-MM")}
            </div>
            <Button
              type="text"
              icon={<RightOutlined />}
              onClick={() => setCalendarMonth((prev) => prev.add(1, "month"))}
            />
          </div>

          {oneTimeJobs.length === 0 && (
            <div className={styles.calendarEmptyHint}>
              {t("cronJobs.calendarEmptyHint")}
            </div>
          )}

          <div className={styles.calendarWeekHeader}>
            {[0, 1, 2, 3, 4, 5, 6].map((day) => (
              <div key={day} className={styles.calendarWeekCell}>
                {dayjs().day(day).format("dd")}
              </div>
            ))}
          </div>
          <div className={styles.calendarGrid}>
            {calendarDays.map((day) => {
              const dateKey = day.format("YYYY-MM-DD");
              const dayEvents = oneTimeJobsByDate[dateKey] || [];
              const isCurrentMonth = day.month() === calendarMonth.month();
              const isToday = day.isSame(dayjs().tz(userTimezone), "day");
              const visibleEvents = dayEvents.slice(0, 3);
              const hiddenCount = Math.max(dayEvents.length - 3, 0);
              const popoverContent = (
                <div className={styles.dayJobPopover}>
                  <div className={styles.dayJobPopoverHeader}>
                    <span className={styles.dayJobPopoverDay}>
                      {day.format("D")}
                    </span>
                    <span className={styles.dayJobPopoverWeek}>
                      {day.format("ddd")}
                    </span>
                  </div>
                  <div className={styles.dayJobList}>
                    {dayEvents.map(({ job, runAtInUserTimezone }) => (
                      <div
                        key={job.id}
                        className={`${styles.dayJobItem} ${
                          job.enabled ? "" : styles.dayJobItemDisabled
                        }`}
                        onClick={() => {
                          setActivePopoverDate(null);
                          handleEdit(job);
                        }}
                      >
                        <span className={styles.dayJobItemTime}>
                          {runAtInUserTimezone.format("HH:mm")}
                        </span>
                        <span className={styles.dayJobItemName}>
                          {job.name}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              );
              return (
                <div
                  key={dateKey}
                  className={`${styles.calendarCell} ${
                    !isCurrentMonth ? styles.calendarCellMuted : ""
                  } ${isToday ? styles.calendarCellToday : ""}`}
                >
                  <div className={styles.calendarCellDate}>{day.date()}</div>
                  <div className={styles.calendarEvents}>
                    {visibleEvents.map(({ job, runAtInUserTimezone }) => (
                      <div
                        key={job.id}
                        className={`${styles.calendarEvent} ${
                          job.enabled ? "" : styles.calendarEventDisabled
                        }`}
                        title={`${runAtInUserTimezone.format("HH:mm")} ${
                          job.name
                        }`}
                        onClick={() => handleEdit(job)}
                      >
                        <span className={styles.calendarEventDot} />
                        <span className={styles.calendarEventText}>
                          {runAtInUserTimezone.format("HH:mm")} {job.name}
                        </span>
                      </div>
                    ))}
                    {hiddenCount > 0 && (
                      <Popover
                        trigger="click"
                        placement="rightTop"
                        open={activePopoverDate === dateKey}
                        onOpenChange={(open) =>
                          setActivePopoverDate(open ? dateKey : null)
                        }
                        overlayClassName={styles.dayJobPopoverOverlay}
                        content={popoverContent}
                      >
                        <button className={styles.calendarMoreBtn}>
                          {t("cronJobs.calendarMoreItems", {
                            count: hiddenCount,
                          })}
                        </button>
                      </Popover>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      <JobDrawer
        open={drawerOpen}
        editingJob={editingJob}
        jobId={drawerJobId}
        form={form}
        saving={saving}
        targetItems={targetItems}
        targetChannels={targetChannels}
        targetsLoading={targetsLoading}
        onReloadTargets={loadDispatchTargets}
        onClose={handleDrawerClose}
        onSubmit={handleSubmit}
      />

      <TemplatePickerModal
        open={templateModalOpen}
        timezone={userTimezoneRef.current}
        onCancel={() => setTemplateModalOpen(false)}
        onUseTemplate={handleUseTemplate}
        cronTemplates={cronTemplates}
      />

      <SaveAsTemplateModal
        open={saveTemplateOpen}
        job={templateSourceJob}
        onCancel={() => {
          setSaveTemplateOpen(false);
          setTemplateSourceJob(null);
        }}
        cronTemplates={cronTemplates}
      />

      <Modal
        visible={historyModalOpen}
        title={t("cronJobs.historyTitle", { name: historyJobName })}
        footer={null}
        onCancel={() => setHistoryModalOpen(false)}
      >
        <div className={styles.historyList}>
          {historyLoading ? (
            <div className={styles.historyEmpty}>{t("common.loading")}</div>
          ) : historyRecords.length === 0 ? (
            <div className={styles.historyEmpty}>
              {t("cronJobs.historyEmpty")}
            </div>
          ) : (
            historyRecords.map((record, index) => (
              <div
                key={`${record.run_at}-${index}`}
                className={styles.historyItem}
              >
                <div className={styles.historyItemMain}>
                  <span className={styles.historyItemTime}>
                    {dayjs(record.run_at)
                      .tz(userTimezone)
                      .format("YYYY-MM-DD HH:mm:ss")}
                  </span>
                  <span
                    className={`${styles.historyItemStatus} ${
                      record.status === "success"
                        ? styles.historyItemStatusSuccess
                        : styles.historyItemStatusError
                    }`}
                  >
                    {record.status === "success"
                      ? t("cronJobs.historyStatusSuccess")
                      : record.status === "running"
                      ? t("cronJobs.historyStatusRunning")
                      : record.status === "cancelled"
                      ? t("cronJobs.historyStatusCancelled")
                      : t("cronJobs.historyStatusFailed")}
                  </span>
                </div>
                <div className={styles.historyItemMeta}>
                  {record.trigger === "manual"
                    ? t("cronJobs.historyTriggerManual")
                    : t("cronJobs.historyTriggerScheduled")}
                </div>
                {record.error &&
                  (() => {
                    const recordKey = `${record.run_at}-${index}`;
                    const expanded = expandedHistoryErrors.has(recordKey);
                    const showToggle = shouldShowErrorToggle(record.error);
                    return (
                      <div>
                        <div
                          className={`${styles.historyItemError} ${
                            !expanded && showToggle
                              ? styles.historyItemErrorCollapsed
                              : ""
                          }`}
                        >
                          {record.error}
                        </div>
                        {showToggle && (
                          <button
                            type="button"
                            className={styles.historyItemErrorToggle}
                            onClick={() => toggleHistoryError(recordKey)}
                          >
                            {expanded
                              ? t("cronJobs.historyCollapse")
                              : t("cronJobs.historyExpand")}
                          </button>
                        )}
                      </div>
                    );
                  })()}
              </div>
            ))
          )}
        </div>
      </Modal>
    </div>
  );
}

export default CronJobsPage;
