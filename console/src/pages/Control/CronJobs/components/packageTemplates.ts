/**
 * Bridge between backend template *packages* and the picker's
 * `CronTemplateDefinition` shape.
 *
 * `template.json` has to stay JSON-serializable, so date-ish fields are
 * stored as strings there and hydrated into dayjs objects here — the job
 * drawer's DatePicker / TimePicker need real dayjs instances. Everything
 * else is passed through untouched, which keeps the package format
 * forward-compatible: a field the current console does not know about
 * still reaches the form.
 */

import dayjs from "dayjs";
import type { CronTemplateInfo } from "../../../../api/types";
import type { CronTemplateCategory, CronTemplateDefinition } from "./templates";
import { parseCron } from "./parseCron";

/** Form fields holding a full timestamp. */
const DATE_FIELDS = ["onceRunAt", "onceRepeatUntil"] as const;
/** Form fields holding a time of day ("HH:mm"). */
const TIME_FIELDS = ["cronTime"] as const;

function hydrateDate(value: unknown): unknown {
  if (typeof value !== "string" || !value.trim()) return value;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed : value;
}

const CLOCK_RE = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/;

/**
 * Parse "HH:mm" / "HH:mm:ss" onto today's date.
 *
 * Done by hand instead of via dayjs' customParseFormat plugin: this page
 * only registers utc + timezone, and pulling in another global plugin for
 * one field is not worth the side effect.
 */
function hydrateTime(value: unknown): unknown {
  if (typeof value !== "string" || !value.trim()) return value;
  const match = value.trim().match(CLOCK_RE);
  if (match) {
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    const second = Number(match[3] ?? 0);
    if (hour <= 23 && minute <= 59 && second <= 59) {
      return dayjs().hour(hour).minute(minute).second(second).millisecond(0);
    }
  }
  const iso = dayjs(value);
  return iso.isValid() ? iso : value;
}

/**
 * Turn a package's stored form values into live form values.
 *
 * Also fills in the current user timezone when the package left it blank,
 * so a template shared across machines schedules in the importer's zone
 * rather than the author's.
 */
export function hydrateFormValues(
  form: Record<string, unknown>,
  timezone: string,
): Record<string, unknown> {
  const values: Record<string, unknown> = { ...form };

  for (const field of DATE_FIELDS) {
    if (field in values) values[field] = hydrateDate(values[field]);
  }
  for (const field of TIME_FIELDS) {
    if (field in values) values[field] = hydrateTime(values[field]);
  }

  const schedule =
    values.schedule && typeof values.schedule === "object"
      ? { ...(values.schedule as Record<string, unknown>) }
      : {};
  if (!schedule.timezone) schedule.timezone = timezone;
  values.schedule = schedule;

  // A `once` template with no run time would render an empty DatePicker;
  // default to tomorrow at the same clock time so it is immediately usable.
  if (values.scheduleType === "once" && !values.onceRunAt) {
    values.onceRunAt = dayjs().add(1, "day");
  }

  // Derive the cron sub-form when the package only recorded an expression.
  if (values.scheduleType === "cron" && !values.cronType) {
    const raw =
      typeof values.cronCustom === "string" && values.cronCustom.trim()
        ? values.cronCustom
        : String((schedule.cron as string) || "0 9 * * *");
    const parts = parseCron(raw);
    values.cronType = parts.type;
    if (parts.type === "custom") {
      values.cronCustom = parts.rawCron || raw;
    } else {
      values.cronTime = dayjs()
        .hour(parts.hour ?? 9)
        .minute(parts.minute ?? 0)
        .second(0);
      if (parts.daysOfWeek) values.cronDaysOfWeek = parts.daysOfWeek;
    }
  }

  return values;
}

/**
 * Placeholders a package author can write into any string in `form`.
 *
 * A template's agent prompt has to tell `run_tool_batch` where its batch
 * file *is*, and `batch/collect.json` means nothing to a tool whose cwd is
 * the agent workspace. So the author writes `{{batch_entry}}` and it is
 * resolved to an absolute path at the moment the template is instantiated
 * — not when it is authored, because the package lands in a different
 * directory on every machine it is imported to.
 */
const PLACEHOLDERS = ["{{template_dir}}", "{{batch_entry}}"] as const;

function substituteInString(text: string, info: CronTemplateInfo): string {
  let result = text;
  if (info.package_dir) {
    result = result.split("{{template_dir}}").join(info.package_dir);
  }
  if (info.batch_entry_path) {
    result = result.split("{{batch_entry}}").join(info.batch_entry_path);
  }
  return result;
}

/**
 * Resolve `{{...}}` placeholders throughout a form value tree.
 *
 * Walks strings wherever they sit — including inside `request.input`, which
 * the drawer holds as a JSON *string*, so a plain string replace reaches
 * the prompt text without needing to parse and re-serialize it.
 */
export function substitutePlaceholders(
  value: unknown,
  info: CronTemplateInfo,
): unknown {
  if (typeof value === "string") return substituteInString(value, info);
  if (Array.isArray(value)) {
    return value.map((item) => substitutePlaceholders(item, info));
  }
  if (value && typeof value === "object") {
    // Leave dayjs objects (and anything else non-plain) alone.
    if (!isPlainObject(value)) return value;
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      out[key] = substitutePlaceholders(item, info);
    }
    return out;
  }
  return value;
}

function isPlainObject(value: object): boolean {
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** True when any known placeholder is left unresolved in the form. */
export function hasUnresolvedPlaceholder(info: CronTemplateInfo): boolean {
  const serialized = JSON.stringify(info.payload?.form || {});
  return PLACEHOLDERS.some((token) => {
    if (!serialized.includes(token)) return false;
    return token === "{{batch_entry}}"
      ? !info.batch_entry_path
      : !info.package_dir;
  });
}

/** Adapt one backend package into a picker entry. */
export function toTemplateDefinition(
  info: CronTemplateInfo,
): CronTemplateDefinition {
  const category: CronTemplateCategory =
    info.category === "once" ? "once" : "cron";
  return {
    id: `package:${info.name}`,
    packageName: info.name,
    packageSource: info.source === "builtin" ? "builtin" : "user",
    category,
    titleKey: info.title_key || "",
    descriptionKey: info.description_key || "",
    frequencyKey: info.frequency_key || "",
    title: info.title || info.name,
    description: info.description,
    frequency: info.frequency,
    emoji: info.emoji,
    tags: info.tags || [],
    batchFiles: info.batch_files || [],
    skills: info.skills || [],
    docs: info.content,
    showInCalendarRecommended: (info.tags || []).includes("calendar"),
    toFormValues: (timezone: string) => {
      // Substitute before hydrating: placeholders only ever appear in
      // strings, and hydration is what turns some of those strings into
      // dayjs objects.
      const resolved = substitutePlaceholders(
        info.payload?.form || {},
        info,
      ) as Record<string, unknown>;
      const values = hydrateFormValues(resolved, timezone);
      const meta =
        values.meta && typeof values.meta === "object"
          ? { ...(values.meta as Record<string, unknown>) }
          : {};
      return {
        ...values,
        meta: {
          ...meta,
          template_id: info.name,
          template_source: info.source === "builtin" ? "builtin" : "package",
        },
      };
    },
  };
}

/**
 * Adapt the backend's package list for the picker.
 *
 * User packages come first: someone who saved their own template wants to
 * see it before the ones QwenPaw ships.
 */
export function toTemplateDefinitions(
  packages: CronTemplateInfo[],
): CronTemplateDefinition[] {
  const defs = packages.map(toTemplateDefinition);
  return [
    ...defs.filter((d) => d.packageSource === "user"),
    ...defs.filter((d) => d.packageSource === "builtin"),
  ];
}
