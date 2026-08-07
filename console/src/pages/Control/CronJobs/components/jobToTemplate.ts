/**
 * Convert an existing cron job into template-package inputs.
 *
 * Shares one spec→form conversion with the edit drawer (`jobToFormValues`)
 * so a template saved from a job reproduces exactly what editing that job
 * would show. The template variant then serializes the dayjs fields back to
 * strings, because `template.json` has to stay plain JSON.
 */

import dayjs from "dayjs";
import type {
  CreateCronTemplateRequest,
  CronJobSpecOutput,
} from "../../../../api/types";
import { normalizePreprocessValue, normalizeSkillRefs } from "./constants";
import { parseCron } from "./parseCron";

type CronJob = CronJobSpecOutput;

/**
 * Spec → drawer form values. Extracted from the page's edit handler so the
 * "save as template" path cannot drift from the "edit job" path.
 */
export function jobToFormValues(job: CronJob): Record<string, unknown> {
  const values: Record<string, unknown> = {
    ...job,
    request: {
      ...job.request,
      input: job.request?.input
        ? JSON.stringify(job.request.input, null, 2)
        : "",
    },
    scheduleType: job.schedule?.type || "cron",
    // Always emit a complete preprocess value: the drawer form is not
    // reset before editing, so a job without the block must overwrite
    // whatever the previously edited job left behind.
    preprocess: normalizePreprocessValue(job.preprocess),
    // Unconditional for the same reason — an empty array has to overwrite
    // the previous job's selection, not inherit it.
    skills: normalizeSkillRefs(job.skills),
  };

  if (job.schedule?.type === "once") {
    values.onceRunAt = job.schedule.run_at ? dayjs(job.schedule.run_at) : null;
    values.onceRepeatEnabled = Boolean(job.schedule.repeat_every_days);
    values.onceRepeatEveryDays = job.schedule.repeat_every_days || 1;
    values.onceRepeatEndType = job.schedule.repeat_end_type || "never";
    values.onceRepeatUntil = job.schedule.repeat_until
      ? dayjs(job.schedule.repeat_until)
      : null;
    values.onceRepeatCount = job.schedule.repeat_count || 2;
    return values;
  }

  const parts = parseCron(job.schedule?.cron || "0 9 * * *");
  values.cronType = parts.type;
  if (parts.type === "daily" || parts.type === "weekly") {
    values.cronTime = dayjs()
      .hour(parts.hour ?? 9)
      .minute(parts.minute ?? 0);
  }
  if (parts.type === "weekly" && parts.daysOfWeek) {
    values.cronDaysOfWeek = parts.daysOfWeek;
  }
  if (parts.type === "custom" && parts.rawCron) {
    values.cronCustom = parts.rawCron;
  }
  return values;
}

const BLANK_TARGET = { user_id: "", session_id: "" };

/**
 * Spec → JSON-safe form values for storage inside a package.
 *
 * `includeDispatchTarget` defaults to false: a template is meant to be
 * shared, and the dispatch target embeds a real user / session id. Callers
 * opt in when the package stays local.
 */
export function jobToTemplateForm(
  job: CronJob,
  includeDispatchTarget = false,
): Record<string, unknown> {
  const values = jobToFormValues(job);

  // Identity belongs to the job, not the recipe.
  delete values.id;
  values.name = "";
  // Including where *this* job came from: a job derived from
  // `weather-report` and saved as `my-thing` would otherwise ship a package
  // claiming `weather-report` provenance, and every job created from it
  // would lead its skill picker with the wrong package.
  delete values.meta;

  const runAt = values.onceRunAt;
  values.onceRunAt = dayjs.isDayjs(runAt)
    ? runAt.format("YYYY-MM-DDTHH:mm:00")
    : undefined;
  const until = values.onceRepeatUntil;
  values.onceRepeatUntil = dayjs.isDayjs(until)
    ? until.format("YYYY-MM-DDTHH:mm:00")
    : undefined;
  const cronTime = values.cronTime;
  if (dayjs.isDayjs(cronTime)) {
    values.cronTime = cronTime.format("HH:mm");
  }

  // Timezone is resolved from the importing machine, not baked in.
  if (values.schedule && typeof values.schedule === "object") {
    const schedule = { ...(values.schedule as Record<string, unknown>) };
    delete schedule.timezone;
    values.schedule = schedule;
  }

  if (values.dispatch && typeof values.dispatch === "object") {
    const dispatch = { ...(values.dispatch as Record<string, unknown>) };
    if (!includeDispatchTarget) {
      dispatch.target = { ...BLANK_TARGET };
    }
    values.dispatch = dispatch;
  }

  if (values.request && typeof values.request === "object") {
    const req = { ...(values.request as Record<string, unknown>) };
    req.user_id = "";
    req.session_id = "";
    values.request = req;
  }

  for (const key of Object.keys(values)) {
    if (values[key] === undefined) delete values[key];
  }
  return values;
}

/** The `job` half of a package: a spec skeleton for headless creation. */
export function jobToTemplateSpec(
  job: CronJob,
  includeDispatchTarget = false,
): Record<string, unknown> {
  const spec: Record<string, unknown> = {
    name: job.name,
    enabled: job.enabled ?? true,
    schedule: { ...job.schedule },
    task_type: job.task_type || "agent",
  };
  if (job.text) spec.text = job.text;
  if (job.request) {
    spec.request = { ...job.request, user_id: "", session_id: "" };
  }
  // A template is the job definition, so the preprocess block travels
  // with it; the scripts it names ride along under `batch/` and are copied
  // into whichever job the template is applied to.
  if (job.preprocess) spec.preprocess = { ...job.preprocess };
  // Skill refs travel too, `template` qualifier included. That qualifier
  // may name a package the importer does not have — kept anyway rather
  // than stripped, because stripping silently discards what the author
  // chose, whereas an unresolvable ref degrades into a note in the prompt
  // at run time. Fail-soft beats fail-quiet.
  if (job.skills?.length) {
    spec.skills = job.skills.map((ref) => ({ ...ref }));
  }
  if (job.save_result_to_inbox !== undefined) {
    spec.save_result_to_inbox = job.save_result_to_inbox;
  }
  if (job.runtime) spec.runtime = { ...job.runtime };
  if (job.dispatch) {
    spec.dispatch = includeDispatchTarget
      ? { ...job.dispatch }
      : { ...job.dispatch, target: { ...BLANK_TARGET } };
  }
  return spec;
}

export interface SaveAsTemplateOptions {
  name: string;
  title: string;
  description: string;
  frequency: string;
  emoji: string;
  tags: string[];
  includeDispatchTarget: boolean;
  /**
   * Every script the job owns, packaged under `batch/` so the template is
   * self-contained. Keys are `<name>.json`; applying the template copies
   * each one into the target job's own directory.
   *
   * No rename map travels with these: the names come from a directory
   * listing, so they are unique by construction.
   */
  batchFiles?: Record<string, string>;
  /** Package-relative entry, e.g. `batch/collect.json`. */
  batchEntry?: string;
}

/** Build the request body for POST /cron-templates from a job. */
export function buildCreateTemplateRequest(
  job: CronJob,
  options: SaveAsTemplateOptions,
): CreateCronTemplateRequest {
  const request: CreateCronTemplateRequest = {
    name: options.name,
    title: options.title || job.name,
    description: options.description,
    category: job.schedule?.type === "once" ? "once" : "cron",
    frequency: options.frequency,
    emoji: options.emoji,
    tags: options.tags,
    form: jobToTemplateForm(job, options.includeDispatchTarget),
    job: jobToTemplateSpec(job, options.includeDispatchTarget),
  };
  if (options.batchFiles && Object.keys(options.batchFiles).length > 0) {
    request.batch_files = options.batchFiles;
    if (options.batchEntry) request.batch_entry = options.batchEntry;
  }
  return request;
}
