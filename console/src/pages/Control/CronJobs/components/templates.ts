/**
 * The shape the template picker consumes.
 *
 * Every template is now a folder package on the backend — the console used
 * to compile a list of them in, which meant users could not add their own.
 * The packages QwenPaw ships set i18n *keys* for their display text so they
 * still follow the UI language; a package a user authored or imported
 * carries literal text instead. Read display text through the resolvers
 * below and that difference stays invisible to callers.
 */

export type CronTemplateCategory = "cron" | "once";
export type CronTemplateTag = string;

export interface CronTemplateDefinition {
  id: string;
  /** Backend package name, used for export / edit / delete. */
  packageName: string;
  /**
   * Where the package lives. `"builtin"` packages ship with QwenPaw and are
   * read-only on disk; editing one copies it into the pool first.
   */
  packageSource: "user" | "builtin";
  category: CronTemplateCategory;
  tags: CronTemplateTag[];
  showInCalendarRecommended: boolean;
  toFormValues: (timezone: string) => Record<string, unknown>;
  /** Literal display text; empty when the i18n key below is set. */
  title: string;
  description: string;
  frequency: string;
  /** i18n keys, set by the packages QwenPaw ships. */
  titleKey: string;
  descriptionKey: string;
  frequencyKey: string;
  emoji: string;
  batchFiles: string[];
  skills: string[];
  /** TEMPLATE.md body, shown in the package detail view. */
  docs: string;
}

/**
 * Resolve one display string: i18n key first, literal as fallback.
 *
 * A missing translation makes `t()` echo the key back, which would put
 * `cronJobs.templates.x.title` on screen — so fall through to the literal
 * whenever the lookup did not actually resolve.
 */
function resolve(
  key: string,
  literal: string,
  t: (k: string) => string,
): string {
  if (key) {
    const translated = t(key);
    if (translated && translated !== key) return translated;
  }
  return literal;
}

export function templateTitle(
  template: Pick<CronTemplateDefinition, "titleKey" | "title" | "packageName">,
  t: (key: string) => string,
): string {
  return resolve(template.titleKey, template.title, t) || template.packageName;
}

export function templateDescription(
  template: Pick<CronTemplateDefinition, "descriptionKey" | "description">,
  t: (key: string) => string,
): string {
  return resolve(template.descriptionKey, template.description, t);
}

export function templateFrequency(
  template: Pick<CronTemplateDefinition, "frequencyKey" | "frequency">,
  t: (key: string) => string,
): string {
  return resolve(template.frequencyKey, template.frequency, t);
}
