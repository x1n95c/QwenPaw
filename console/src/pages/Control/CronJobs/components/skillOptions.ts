/**
 * Option tree for the "use skill" picker, and the ref shape the form holds.
 *
 * A skill is *referenced*, never copied or installed: the job stores a
 * `{ name, template? }` pair and the backend reads `SKILL.md` out of that
 * directory at trigger time. So unlike the preprocess picker, selecting a
 * foreign option changes nothing on disk — which is why there is no
 * `copySource` here and no edit affordance anywhere in this feature.
 *
 * It lives apart from `SkillSection` for the same reason `scriptOptions`
 * lives apart from `PreprocessSection`: that component cannot be rendered
 * in vitest, because `@agentscope-ai/design` is aliased to a stub with no
 * `Select`. Keeping the logic here keeps it testable.
 */

import type { CronSkillInfo } from "../../../../api/types";
import { templateTitle } from "./templates";

/** What the form field stores, and what the backend persists. */
export interface SkillRefFormValue {
  /** Skill directory name. */
  name: string;
  /** Package that bundles it; absent for an installed workspace skill. */
  template?: string;
}

/** A leaf option. Extra fields arrive on `option.data` in `optionRender`. */
export interface SkillSelectOption {
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
  /** Hover text: the skill's own description, else its identity. */
  tooltip: string;
  /** Which root resolves it. Drives the group it lands in. */
  source: "workspace" | "template";
}

export interface SkillSelectGroup {
  label: string;
  options: SkillSelectOption[];
}

type Translate = (key: string) => string;

/**
 * Pack a ref into a Select value.
 *
 * A DOM key only — never persisted. The form stores `{name, template}`
 * objects, and `onChange` maps these back through `parseSkillOptionValue`.
 * Prefixed because an installed skill and a bundled skill can share a name
 * and still have to be distinct keys. (Same trick as `scriptOptions`'
 * `job:<id>/<name>`.)
 */
export function skillOptionValue(ref: SkillRefFormValue): string {
  return ref.template ? `tpl:${ref.template}/${ref.name}` : `ws:${ref.name}`;
}

/** Inverse of `skillOptionValue`. `null` for anything unrecognised. */
export function parseSkillOptionValue(value: string): SkillRefFormValue | null {
  if (value.startsWith("ws:")) {
    const name = value.slice(3);
    return name ? { name } : null;
  }
  if (value.startsWith("tpl:")) {
    const rest = value.slice(4);
    // The package name cannot contain `/` (the backend rejects it), so the
    // first separator is the boundary and the skill name is the remainder.
    const cut = rest.indexOf("/");
    if (cut <= 0) return null;
    const template = rest.slice(0, cut);
    const name = rest.slice(cut + 1);
    return name ? { name, template } : null;
  }
  return null;
}

/** `<template title>/<skill name>` — for tooltips, where the group header
 * is not necessarily on screen (a selected tag shows no group). */
export function skillQualifiedName(skill: CronSkillInfo, t: Translate): string {
  if (skill.source !== "template") return skill.name;
  return `${skillTemplateTitle(skill, t)}/${skill.name}`;
}

/** Resolve a bundled skill's package title, i18n key first. */
export function skillTemplateTitle(skill: CronSkillInfo, t: Translate): string {
  return templateTitle(
    {
      titleKey: skill.template_title_key || "",
      title: skill.template_title || "",
      packageName: skill.template,
    },
    t,
  );
}

function toOption(skill: CronSkillInfo, t: Translate): SkillSelectOption {
  const qualified = skillQualifiedName(skill, t);
  const display = skill.display_name || skill.name;
  return {
    value: skillOptionValue({
      name: skill.name,
      template: skill.template || undefined,
    }),
    // The directory name, not the frontmatter title: that is the identity
    // the ref stores, and the one a user sees in the skills page.
    label: skill.name,
    title: "",
    // Both the frontmatter title and the translated package title are
    // searchable even though neither is the label — typing either has to
    // still find the row.
    searchText: `${qualified} ${display} ${skill.description || ""}`
      .toLowerCase()
      .trim(),
    // Falls back to the qualified name rather than the label, so hovering
    // an undocumented skill at least says which package it came from.
    tooltip: skill.description || qualified,
    source: skill.source,
  };
}

export interface BuildSkillOptionsArgs {
  skills: CronSkillInfo[];
  t: Translate;
  /** Whether the template groups are revealed. */
  expanded: boolean;
  /**
   * Packed values currently selected. A bundled skill that is *selected*
   * gets its option emitted even while collapsed: rc-select takes a tag's
   * text from the matching option, so hiding it would leave the tag
   * rendering whatever label it happened to cache first — for a job loaded
   * before the skill list arrived, that is the qualified fallback rather
   * than the skill's own name.
   */
  selected?: string[];
  /**
   * Package this job was created from, if any (`meta.from_template`).
   *
   * Its skills lead the list: applying a template is the one moment when a
   * particular package's skills are obviously the relevant ones, and making
   * the user expand to reach them would be backwards. Absent for a job
   * written from scratch, or one created before this was recorded — then the
   * installed group simply leads instead.
   */
  currentTemplate?: string;
  labels: {
    /** Group holding this workspace's installed skills. */
    installed: string;
    /** Prefix marking a group as a template, e.g. "任务模板". */
    template: string;
  };
}

/** Group bundled skills by their package, preserving the given order. */
function groupByTemplate(
  bundled: CronSkillInfo[],
  t: Translate,
  prefix: string,
): SkillSelectGroup[] {
  // Preserve backend order (user packages before builtins) rather than
  // sorting: it is the same precedence the runtime resolver applies.
  const byTemplate = new Map<string, SkillSelectGroup>();
  for (const skill of bundled) {
    let group = byTemplate.get(skill.template);
    if (!group) {
      group = {
        label: `${prefix} · ${skillTemplateTitle(skill, t)}`,
        options: [],
      };
      byTemplate.set(skill.template, group);
    }
    group.options.push(toOption(skill, t));
  }
  return [...byTemplate.values()];
}

/**
 * Build the grouped option tree, in three tiers.
 *
 * 1. The skills bundled in the package this job came from — always visible,
 *    because that is the one set that is obviously relevant.
 * 2. This workspace's installed skills — always visible.
 * 3. Every *other* package's bundled skills — behind the expander.
 *
 * Note the asymmetry with the preprocess picker: hiding a group there also
 * hides that picking it *copies* a file, whereas here nothing is copied
 * either way — the grouping is purely about keeping the default list short.
 */
export function buildSkillOptions({
  skills,
  t,
  expanded,
  selected = [],
  currentTemplate,
  labels,
}: BuildSkillOptionsArgs): {
  groups: SkillSelectGroup[];
  templateCount: number;
} {
  const installed = skills.filter((skill) => skill.source === "workspace");
  const bundled = skills.filter((skill) => skill.source === "template");
  const mine = currentTemplate
    ? bundled.filter((skill) => skill.template === currentTemplate)
    : [];
  const others = bundled.filter((skill) => !mine.includes(skill));

  const groups: SkillSelectGroup[] = [
    ...groupByTemplate(mine, t, labels.template),
  ];
  if (installed.length > 0) {
    groups.push({
      label: labels.installed,
      options: installed.map((skill) => toOption(skill, t)),
    });
  }

  // Collapsed still shows the other packages' skills this job already uses —
  // otherwise their tags lose their labels (see `selected` above), and the
  // user cannot see which package a selection came from without expanding.
  const chosen = new Set(selected);
  const shown = expanded
    ? others
    : others.filter((skill) =>
        chosen.has(
          skillOptionValue({ name: skill.name, template: skill.template }),
        ),
      );
  groups.push(...groupByTemplate(shown, t, labels.template));

  // Only what the expander actually reveals — the current package's skills
  // are already on screen, so counting them would overstate what is hidden.
  return { groups, templateCount: others.length };
}

export interface ResolvedSkillRefs {
  /** Packed Select values for the refs that resolved. */
  values: string[];
  /**
   * Refs pointing at nothing the workspace can see, as qualified names.
   * The job keeps them — a template package may simply not be installed
   * here yet, and the run-time note is designed for exactly that — so this
   * drives a warning, not a silent drop.
   */
  missing: string[];
  /**
   * Placeholder options for those same refs, so the Select has something to
   * render them *as*.
   *
   * Without these, rc-select falls back to printing the raw value, and the
   * raw value here is a packed internal key — the field would show
   * `tpl:workspace-usage/disk-usage-advisor` to the user.
   *
   * Deliberately NOT `disabled`: antd makes the tag of a disabled option
   * non-closable, which would leave a stale reference impossible to remove
   * from the form. Clickable is fine — the row is already selected, so
   * clicking it clears it, which is exactly what one does with a dead ref.
   */
  missingOptions: SkillSelectOption[];
}

/**
 * Split a job's stored refs into "the picker can show this" and "it cannot".
 *
 * Every ref still produces a Select value, including a dangling one: losing
 * it would mean the field silently stops showing part of what the job
 * actually runs with.
 */
export function findSkillRefs(
  refs: SkillRefFormValue[],
  skills: CronSkillInfo[],
): ResolvedSkillRefs {
  const known = new Set(
    skills.map((skill) =>
      skillOptionValue({
        name: skill.name,
        template: skill.template || undefined,
      }),
    ),
  );
  const values: string[] = [];
  const missing: string[] = [];
  const missingOptions: SkillSelectOption[] = [];
  for (const ref of refs) {
    const value = skillOptionValue(ref);
    values.push(value);
    if (known.has(value)) continue;
    const qualified = ref.template ? `${ref.template}/${ref.name}` : ref.name;
    missing.push(qualified);
    missingOptions.push({
      value,
      label: qualified,
      title: "",
      searchText: qualified.toLowerCase(),
      tooltip: qualified,
      source: ref.template ? "template" : "workspace",
    });
  }
  return { values, missing, missingOptions };
}
