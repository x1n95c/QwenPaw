/**
 * Option tree for the preprocess script picker.
 *
 * A step's `script` is always a plain name in the job's own directory.
 * Scripts from a template or another job appear in the picker but are
 * *copied* in on select, so no foreign identifier is ever stored.
 *
 * It lives apart from `PreprocessSection` because that component cannot be
 * rendered in vitest: `@agentscope-ai/design` is aliased to a stub that has
 * no `Select`. Keeping the logic here keeps it testable.
 */

import type {
  CronSkillInfo,
  JobToolBatches,
  TemplateBatchScriptInfo,
  ToolBatchInfo,
} from "../../../../api/types";
import { templateTitle } from "./templates";

/** A leaf option. Extra fields arrive on `option.data` in `optionRender`. */
export interface ScriptSelectOption {
  value: string;
  label: string;
  /**
   * Always empty. rc-select fills the DOM `title` attribute from a string
   * label, which would pop a native tooltip alongside the antd `Tooltip`
   * we render ourselves.
   */
  title: string;
  /** What `filterOption` matches against — never shown. */
  searchText: string;
  /** Hover text: the script's own description, else its identity. */
  tooltip: string;
  /**
   * Where the script lives. `own` means this job already has it, so
   * selecting it is a plain selection. The rest are foreign and are
   * *copied* in on select; `copySource` says how to ask for that copy.
   */
  kind: "own" | "template" | "job" | "skill";
  /** Present on foreign options only. Transient: never stored. */
  copySource?:
    | { from_template: string; file: string }
    | { from_job_id: string; file: string }
    | {
        from_skill: string;
        /** Package bundling the skill; absent when it is installed. */
        from_skill_template?: string;
        file: string;
      };
}

export interface ScriptSelectGroup {
  label: string;
  options: ScriptSelectOption[];
}

/** The describing fields both sources have in common. */
export interface ResolvedScript {
  value: string;
  label: string;
  kind: "own" | "template";
  description: string;
  arg_names: string[];
  action_count: number;
  preview_actions: unknown[];
}

type Translate = (key: string) => string;

/** Resolve a template script's display title, i18n key first. */
export function scriptTemplateTitle(
  script: TemplateBatchScriptInfo,
  t: Translate,
): string {
  return templateTitle(
    {
      titleKey: script.template_title_key,
      title: script.template_title,
      packageName: script.template,
    },
    t,
  );
}

/** Just the script's name, without the `.json` the file happens to have.
 *
 * The group header already names the template, so repeating it on every
 * row only made the labels long — and the suffix is an implementation
 * detail of how a batch is stored, not part of the script's identity.
 * `scriptQualifiedName` is the disambiguating form, used on hover.
 */
export function scriptDisplayLabel(script: TemplateBatchScriptInfo): string {
  return script.file_name.replace(/\.json$/i, "");
}

/** `<template title>/<script name>` — for tooltips and warnings, where
 * the template is not otherwise on screen. */
export function scriptQualifiedName(
  script: TemplateBatchScriptInfo,
  t: Translate,
): string {
  return `${scriptTemplateTitle(script, t)}/${scriptDisplayLabel(script)}`;
}

function ownScriptOption(batch: ToolBatchInfo): ScriptSelectOption {
  return {
    value: batch.name,
    label: batch.name,
    title: "",
    searchText: `${batch.name} ${batch.description}`.toLowerCase(),
    // The description is what the user is actually after; falling back to
    // the identity keeps every row hoverable rather than having tooltips
    // appear only on the documented ones.
    tooltip: batch.description || batch.name,
    kind: "own",
  };
}

function templateOption(
  script: TemplateBatchScriptInfo,
  t: Translate,
): ScriptSelectOption {
  const label = scriptDisplayLabel(script);
  const qualified = scriptQualifiedName(script, t);
  return {
    value: script.ref,
    label,
    title: "",
    // Both the translated title and the raw ref are searchable: the label
    // shows neither, so typing either has to still find the row.
    searchText:
      `${qualified} ${script.description} ${script.ref}`.toLowerCase(),
    // Falls back to the qualified name rather than the label, so hovering
    // an undocumented script at least tells you which template it is from.
    tooltip: script.description || qualified,
    kind: "template",
    copySource: {
      from_template: script.template,
      file: script.file_path,
    },
  };
}

function jobOption(
  group: JobToolBatches,
  batch: ToolBatchInfo,
): ScriptSelectOption {
  const owner = group.job_name || group.job_id;
  const qualified = `${owner}/${batch.name}`;
  return {
    // Not a value that is ever stored: selecting this copies the script
    // and the row is filled from the copy's real name. Prefixed with the
    // job id so two jobs owning a same-named script stay distinct keys.
    value: `job:${group.job_id}/${batch.name}`,
    label: batch.name,
    title: "",
    searchText: `${qualified} ${batch.description}`.toLowerCase(),
    tooltip: batch.description || qualified,
    kind: "job",
    copySource: { from_job_id: group.job_id, file: batch.name },
  };
}

function skillOption(skill: CronSkillInfo, file: string): ScriptSelectOption {
  // Skill-relative, e.g. `scripts/collect.json`. The subdirectory is worth
  // showing: a skill may carry the same stem under both `scripts/` and
  // `batch/`, and the label is the only thing telling them apart.
  const label = file.replace(/\.json$/i, "");
  const qualified = `${skill.name}/${label}`;
  return {
    // Never stored: selecting this copies the script and the row is filled
    // from the copy's real name. Prefixed so two skills carrying a
    // same-named script stay distinct keys.
    value: `skill:${skill.template}/${skill.name}/${file}`,
    label,
    title: "",
    searchText: `${qualified} ${skill.description || ""}`.toLowerCase(),
    tooltip: qualified,
    kind: "skill",
    copySource: {
      from_skill: skill.name,
      // Omitted rather than empty: an empty string would read as "a package
      // named ''", and the backend 400s on the orphan qualifier.
      ...(skill.template ? { from_skill_template: skill.template } : {}),
      file,
    },
  };
}

export interface BuildScriptOptionsArgs {
  /** The scripts this job already owns. */
  ownScripts: ToolBatchInfo[];
  templateScripts: TemplateBatchScriptInfo[];
  /** Other cron jobs in this workspace and the scripts they own. */
  jobScripts?: JobToolBatches[];
  /**
   * Skills that carry batch JSON of their own. An installed skill may ship
   * a collection script the job referencing it wants to run first, and
   * without this the only way to get at it is to copy the file by hand.
   */
  skillScripts?: CronSkillInfo[];
  t: Translate;
  /** Whether the foreign groups are revealed. */
  expanded: boolean;
  labels: {
    own: string;
    /** Prefix marking a group as a template, e.g. "任务模板". */
    template: string;
    /** Prefix marking a group as another cron job, e.g. "定时任务". */
    job: string;
    /** Prefix marking a group as a skill, e.g. "skill". */
    skill: string;
  };
}

/**
 * Build the grouped option tree.
 *
 * The job's own scripts come first and are always visible; foreign ones sit
 * behind the expander, grouped by the template or job that owns them, so it
 * stays obvious that picking one duplicates it rather than linking to it.
 */
export function buildScriptOptions({
  ownScripts,
  templateScripts,
  jobScripts = [],
  skillScripts = [],
  t,
  expanded,
  labels,
}: BuildScriptOptionsArgs): {
  groups: ScriptSelectGroup[];
  templateCount: number;
} {
  const groups: ScriptSelectGroup[] = [];
  if (ownScripts.length > 0) {
    groups.push({
      label: labels.own,
      options: ownScripts.map(ownScriptOption),
    });
  }
  if (expanded) {
    // Preserve backend order (user packages before builtins) rather than
    // sorting: it is the same precedence the runtime resolver applies.
    const byTemplate = new Map<string, ScriptSelectGroup>();
    for (const script of templateScripts) {
      let group = byTemplate.get(script.template);
      if (!group) {
        // Prefixed, because a template group and a cron-job group sit in
        // the same list and their names can easily coincide.
        group = {
          label: `${labels.template} · ${scriptTemplateTitle(script, t)}`,
          options: [],
        };
        byTemplate.set(script.template, group);
      }
      group.options.push(templateOption(script, t));
    }
    groups.push(...byTemplate.values());

    for (const owned of jobScripts) {
      if (!owned.batches.length) continue;
      groups.push({
        label: `${labels.job} · ${owned.job_name || owned.job_id}`,
        options: owned.batches.map((batch) => jobOption(owned, batch)),
      });
    }

    for (const skill of skillScripts) {
      const files = skill.batch_files || [];
      if (!files.length) continue;
      groups.push({
        label: `${labels.skill} · ${skill.name}`,
        options: files.map((file) => skillOption(skill, file)),
      });
    }
  }
  const foreignCount =
    templateScripts.length +
    jobScripts.reduce((total, owned) => total + owned.batches.length, 0) +
    skillScripts.reduce(
      (total, skill) => total + (skill.batch_files?.length || 0),
      0,
    );
  return { groups, templateCount: foreignCount };
}

/**
 * Look up the script a step points at, among the job's own.
 *
 * Only the job's own: a step never names a template's or another job's
 * script — those are copied in on select. `null` means the value is
 * dangling (deleted, or left over from before scripts moved under their
 * job), which is what the caller turns into the warning row.
 */
export function findScript(
  value: string | undefined | null,
  ownScripts: ToolBatchInfo[],
): ResolvedScript | null {
  const text = (value || "").trim();
  if (!text) return null;
  const batch = ownScripts.find((item) => item.name === text);
  if (!batch) return null;
  return {
    value: batch.name,
    label: batch.name,
    kind: "own",
    description: batch.description,
    arg_names: batch.arg_names,
    action_count: batch.action_count,
    preview_actions: batch.preview_actions,
  };
}
