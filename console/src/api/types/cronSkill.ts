/**
 * Skills a cron job can attach, from either source.
 *
 * One shape for both an installed workspace skill and a skill bundled in a
 * template package, so the picker renders them through one code path. The
 * discriminator is `source`; `name` + `template` are exactly the pair a
 * `CronJobSkillRef` stores.
 *
 * There is no `content` field on purpose: the picker shows a name and a
 * description, and the skill body is read from disk by the trigger path.
 */
export interface CronSkillInfo {
  /** Skill directory name — the stable identity, and what a ref holds. */
  name: string;
  source: "workspace" | "template";
  /** Package that bundles it; empty for an installed workspace skill. */
  template: string;
  /**
   * Title literal and its i18n key, resolved key-first by the client — a
   * builtin that ships `metadata.title_key` would otherwise render its
   * untranslated literal.
   */
  template_title?: string;
  template_title_key?: string;
  template_source?: string;
  /** Frontmatter `name`, for display. Falls back to `name`. */
  display_name?: string;
  description?: string;
  /**
   * Batch JSON the skill carries, skill-relative (e.g. `scripts/x.json`).
   * Copyable into a job as a preprocess script.
   */
  batch_files?: string[];
}
