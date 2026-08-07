/**
 * Copying a template package's bundled scripts into a freshly created job.
 *
 * A job owns its scripts outright (`cron_jobs/<job_id>/batch/`), so applying
 * a template has to duplicate everything the package ships — otherwise the
 * new job's script list is empty and the user has to go browsing other
 * packages to fetch back the very scripts their own template came with.
 *
 * Split out of `index.tsx` so it can be tested: the copy is injected, and
 * the page component itself cannot be rendered under vitest.
 */

import { baseScriptName } from "./packageTemplates";

/** The one field of a copy response this module cares about. */
export interface CopiedScript {
  /** The name that actually landed; the server renames on collision. */
  name: string;
}

export interface CopyTemplateScriptsArgs {
  /** Package name and its bundled files, package-relative. */
  template: { packageName: string; batchFiles: string[] };
  /** Script names the template's preprocess chain declares. */
  declared: string[];
  copy: (body: {
    from_template: string;
    file: string;
  }) => Promise<CopiedScript>;
}

export interface CopyTemplateScriptsResult {
  /** `declared name → landed name`, for `remapPreprocessScripts`. */
  landed: Record<string, string>;
  /**
   * Files that did not copy, split by whether the preprocess chain names
   * them. A failed declared script leaves the job broken; a failed extra
   * is cosmetic, and the two should not read the same to the user.
   */
  failed: { declared: string[]; extra: string[] };
}

/**
 * Copy every file the package bundles into `jobId`'s own directory.
 *
 * Every file, not just the ones the chain names: a package may also ship
 * scripts the *agent* picks between at run time (workspace-usage carries a
 * unix and a windows variant), and those are exactly the ones the user
 * would otherwise have to hunt for under "more scripts".
 */
export async function copyTemplateScripts({
  template,
  declared,
  copy,
}: CopyTemplateScriptsArgs): Promise<CopyTemplateScriptsResult> {
  const landed: Record<string, string> = {};
  const failed = { declared: [] as string[], extra: [] as string[] };
  const wanted = new Set(declared);

  for (const file of copyOrder(template.batchFiles, wanted)) {
    const base = baseScriptName(file);
    // Sequential on purpose: the server resolves a name collision against
    // the directory as it stands, so two copies in flight at once could
    // both be told the same name is free.
    try {
      const info = await copy({
        from_template: template.packageName,
        file,
      });
      // First writer of a basename keeps the key. Combined with the
      // ordering above this means a declared script always owns its own
      // name, and a same-named extra lands beside it as `<name>-2`.
      if (!(base in landed)) landed[base] = info.name;
    } catch (error) {
      console.error("Failed to copy template batch script", file, error);
      (wanted.has(base) ? failed.declared : failed.extra).push(base);
    }
  }
  return { landed, failed };
}

/**
 * The order in which a basename's claimants get their shot at it.
 *
 * `batch_files` arrives sorted by full path, so a package holding both
 * `batch/legacy/weather.json` and `batch/weather.json` lists the nested one
 * first. Copying in that order would hand the `weather` key to the legacy
 * copy and rewrite the preprocess step onto it — the job would run the
 * wrong script, and nothing would say so.
 *
 * Declared beats undeclared, then shallow beats nested. Depth is the
 * tiebreak that actually does the work here: `declared` holds basenames, so
 * both of those files are "declared" and only the nesting tells them apart.
 * A step saying `weather` means the package's own `batch/weather.json`;
 * anything filed away in a subdirectory is the variant.
 */
function copyOrder(files: string[], declared: Set<string>): string[] {
  const rank = (file: string) => [
    declared.has(baseScriptName(file)) ? 0 : 1,
    file.split("/").length,
  ];
  // Stable, so files of equal rank keep the backend's ordering.
  return [...files].sort((a, b) => {
    const [aDeclared, aDepth] = rank(a);
    const [bDeclared, bDepth] = rank(b);
    return aDeclared - bDeclared || aDepth - bDepth;
  });
}
