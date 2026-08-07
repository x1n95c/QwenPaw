/**
 * Which of a template's bundled skills a new job should reference.
 *
 * Templates used to *install* their skills into the workspace and enable
 * them. They no longer do: the job holds a `{name, template}` ref and the
 * trigger path reads `SKILL.md` out of the package in place. So applying a
 * template selects skills instead of copying them, and this decides which.
 *
 * Extracted from `index.tsx` so it can be tested — that file cannot be
 * rendered under vitest, same constraint as `templateScripts.ts`.
 */

import type { SkillRefFormValue } from "./skillOptions";
import { normalizeSkillRefs } from "./constants";

interface TemplateSkillSource {
  /** Backend package name — the `template` half of every ref produced. */
  packageName: string;
  /** Skill directory names bundled under the package's `skills/`. */
  skills: string[];
}

/**
 * Resolve the `skills` form value for a job created from a template.
 *
 * The author's declaration wins when the package has one: a package may
 * bundle three skills and mean for the job to reference only one of them,
 * and there is no way to recover that intent from the directory listing.
 *
 * Otherwise every bundled skill is referenced. That fallback is for
 * imported third-party packages — `buildCreateTemplateRequest` never
 * packages skills, so no user-saved template has a `skills/` directory —
 * and it mirrors `copyTemplateScripts`, which copies every bundled script
 * while only *declaring* the ones the preprocess names.
 */
export function resolveTemplateSkills(
  templateValues: Record<string, unknown>,
  template?: TemplateSkillSource,
): SkillRefFormValue[] {
  const declared = normalizeSkillRefs(templateValues.skills);
  if (declared.length > 0) return declared;
  if (!template?.packageName || template.skills.length === 0) return [];
  return normalizeSkillRefs(
    template.skills.map((name) => ({
      name,
      template: template.packageName,
    })),
  );
}
