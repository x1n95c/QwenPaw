/**
 * Folder-based cron job template packages.
 *
 * A package is a directory (TEMPLATE.md + template.json + optional
 * batch/*.json run_tool_batch scripts + optional skills/) that can be
 * imported and exported as a zip — same model as skill packages.
 */

export type CronTemplateCategoryValue = "cron" | "once";
export type CronTemplateSource = "user" | "builtin";

export interface CronTemplatePayload {
  schema_version?: string;
  /** Values fed straight into the job drawer form. */
  form: Record<string, unknown>;
  /** Optional CronJobSpec-shaped payload for headless creation. */
  job?: Record<string, unknown> | null;
  /** Package-relative path of the run_tool_batch entry file. */
  batch_entry?: string | null;
}

export interface CronTemplateInfo {
  name: string;
  title: string;
  description: string;
  category: CronTemplateCategoryValue;
  frequency: string;
  emoji: string;
  tags: string[];
  version_text: string;
  source: CronTemplateSource;
  /**
   * i18n keys for the three display strings above. The packages QwenPaw
   * ships set these so they follow the UI language; user-authored packages
   * carry literals and leave these empty.
   */
  title_key: string;
  description_key: string;
  frequency_key: string;
  /** TEMPLATE.md body with the frontmatter stripped. */
  content: string;
  payload: CronTemplatePayload;
  /** e.g. ["batch/collect.json"] */
  batch_files: string[];
  /** Bundled skill directory names. */
  skills: string[];
  /** Every file in the package, package-relative. */
  files: string[];
  /**
   * Absolute path of the package on disk. Substituted into the
   * `{{template_dir}}` placeholder when the template is instantiated —
   * a package-relative path means nothing to a tool whose cwd is the
   * agent workspace.
   */
  package_dir: string;
  /** Absolute path of `payload.batch_entry`; substituted into `{{batch_entry}}`. */
  batch_entry_path: string;
  updated_at: string;
}

export interface CreateCronTemplateRequest {
  name: string;
  title?: string;
  description?: string;
  category?: CronTemplateCategoryValue;
  frequency?: string;
  emoji?: string;
  tags?: string[];
  version_text?: string;
  /** Markdown body for TEMPLATE.md; generated server-side when omitted. */
  body?: string;
  form: Record<string, unknown>;
  job?: Record<string, unknown> | null;
  batch_entry?: string | null;
  /** { "collect.json": "<json text>" } written under batch/ */
  batch_files?: Record<string, string>;
  /** { "report-writer": "<SKILL.md text>" } written under skills/ */
  skills?: Record<string, string>;
  extra_files?: Record<string, unknown>;
  overwrite?: boolean;
}

/**
 * Patch an existing package. Every field is optional and omitting one means
 * "leave as-is" — the server preserves package files you do not mention
 * (bundled skills, assets, other batch scripts).
 */
export interface UpdateCronTemplateRequest {
  title?: string;
  description?: string;
  category?: CronTemplateCategoryValue;
  frequency?: string;
  emoji?: string;
  tags?: string[];
  version_text?: string;
  body?: string;
  form?: Record<string, unknown>;
  job?: Record<string, unknown> | null;
  /** Empty string clears the entry; omit to keep it. */
  batch_entry?: string;
  /** Batch files to add or replace; unlisted files are kept. */
  batch_files?: Record<string, string>;
  /** Batch file names to delete. */
  remove_batch_files?: string[];
}

export interface CronTemplateImportConflict {
  reason: string;
  name: string;
  message: string;
  suggested_name: string;
}

export interface CronTemplateImportResult {
  imported: string[];
  count: number;
  conflicts: CronTemplateImportConflict[];
}

export interface CronTemplateFileContent {
  name: string;
  path: string;
  content: string;
}

/**
 * One `batch/*.json` script bundled inside a template package.
 *
 * A preprocess step can reference one of these directly by its `ref`
 * instead of picking from the flat pool, which is what keeps a script
 * visibly attached to the task it came with.
 *
 * `template_title` / `template_title_key` arrive unresolved on purpose:
 * packages that ship with QwenPaw carry an i18n key, so the title has to
 * be resolved key-first on the client (see `templateTitle` in
 * `pages/Control/CronJobs/components/templates.ts`).
 */
export interface TemplateBatchScriptInfo {
  /** What a step stores, e.g. `weather-report/batch/weather.json`. */
  ref: string;
  /** Package directory name — the stable identity, not the title. */
  template: string;
  template_title: string;
  template_title_key: string;
  template_source: "user" | "builtin";
  /** Package-relative path, e.g. `batch/collect.json`. */
  file_path: string;
  file_name: string;
  description: string;
  arg_names: string[];
  action_count: number;
  preview_actions: unknown[];
  updated_at: string;
}
