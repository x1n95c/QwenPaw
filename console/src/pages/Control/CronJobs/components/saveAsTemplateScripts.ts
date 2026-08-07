/**
 * Gathering a job's batch scripts for a template package.
 *
 * The mirror image of `templateScripts.ts`: applying a template copies
 * every bundled script into the job, so saving a job back out packages
 * every script it owns. Anything narrower makes the round trip lossy —
 * saving a job whose preprocess is switched off used to ship a package with
 * no scripts at all.
 *
 * Split out of `SaveAsTemplateModal` so it can be tested: that component
 * cannot be rendered under vitest, where `@agentscope-ai/design` is stubbed.
 */

import { declaredPreprocessScripts } from "./packageTemplates";

export interface CollectJobScriptsArgs {
  jobId: string;
  /** The job's `preprocess` block, verbatim. */
  preprocess: unknown;
  list: (jobId: string) => Promise<{ name: string }[]>;
  get: (jobId: string, name: string) => Promise<{ content: unknown }>;
}

export type CollectJobScriptsResult =
  | {
      ok: true;
      /** `<name>.json → serialized content`, the package's `batch_files`. */
      batchFiles: Record<string, string>;
      /** Package-relative, or undefined when there is no single entry. */
      batchEntry?: string;
    }
  | { ok: false; missing: string };

/**
 * Package every script the job owns.
 *
 * Reads are concurrent, unlike the copies in `templateScripts.ts`: these
 * are read-only, and the names come from a directory listing so they are
 * already unique — there is no collision to serialize against.
 */
export async function collectJobScripts({
  jobId,
  preprocess,
  list,
  get,
}: CollectJobScriptsArgs): Promise<CollectJobScriptsResult> {
  const owned = await list(jobId);
  const names = owned.map((info) => info.name);
  const declared = declaredPreprocessScripts({ preprocess });

  // Only when the chain is live. Aborting on a *disabled* chain that names
  // a since-deleted script would make such a job permanently unsavable as
  // a template, for a reference that never runs.
  if (isEnabled(preprocess)) {
    const missing = declared.find((name) => !names.includes(name));
    if (missing) return { ok: false, missing };
  }

  const batchFiles: Record<string, string> = {};
  const contents = await Promise.all(
    names.map((name) => get(jobId, name).then((detail) => detail.content)),
  );
  names.forEach((name, index) => {
    batchFiles[`${name}.json`] = JSON.stringify(contents[index], null, 2);
  });

  return { ok: true, batchFiles, batchEntry: soleEntry(declared, names) };
}

/**
 * `batch_entry` names the one script a template is "about".
 *
 * Set it only when the chain declares exactly one script and the job
 * actually owns it — the backend rejects a package whose entry points at a
 * file that is not there. With zero or several declared scripts there is no
 * single answer, and nothing needs one: `batch_entry` is read only to
 * substitute `{{batch_entry}}`, a placeholder a job-derived prompt cannot
 * contain because it was already resolved when the job was created.
 */
function soleEntry(declared: string[], owned: string[]): string | undefined {
  if (declared.length !== 1) return undefined;
  return owned.includes(declared[0]) ? `batch/${declared[0]}.json` : undefined;
}

function isEnabled(preprocess: unknown): boolean {
  if (!preprocess || typeof preprocess !== "object") return false;
  return Boolean((preprocess as { enabled?: unknown }).enabled);
}
