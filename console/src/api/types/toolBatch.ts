/**
 * Tool batch scripts (the shared "script pool").
 *
 * A batch script is a run_tool_batch JSON program — either a bare action
 * array or an object `{actions: [...], description?}` — that cron jobs can
 * reference from their `preprocess.script` field. Same pool model as skill
 * packages and cron template packages.
 */

export interface ToolBatchInfo {
  name: string;
  description: string;
  /** `${args.*}` placeholders found in the script, sorted. */
  arg_names: string[];
  action_count: number;
  /**
   * The leading actions verbatim, capped by the backend's
   * `PREVIEW_ACTION_LIMIT`, so a list can show a step preview without
   * fetching every script's content. `action_count` is the real total.
   */
  preview_actions: unknown[];
  updated_at: string;
}

export interface ToolBatchDetail extends ToolBatchInfo {
  /** The batch JSON: a bare action array or `{actions: [...], description?}`. */
  content: unknown;
}

export interface CreateToolBatchRequest {
  name: string;
  content: unknown;
  description?: string;
}

/** Patch an existing script; omitted fields are left as-is. */
export interface UpdateToolBatchRequest {
  content?: unknown;
  description?: string;
}

/**
 * Returned (with an empty `imported` list) when a zip holds several batch
 * files: nothing is written until the user picks files and re-uploads with
 * `select`.
 */
export interface ToolBatchImportCandidate {
  file_name: string;
  name: string;
  arg_names: string[];
  action_count: number;
  exists: boolean;
  /**
   * False when the file failed to parse or validate. It is still listed —
   * one broken script must not cost the user the whole zip — but it
   * cannot be selected, and `error` says why.
   */
  valid: boolean;
  error?: string;
}

export interface ToolBatchImportConflict {
  name: string;
  file_name: string;
  suggested_name: string;
}

export interface ToolBatchImportResult {
  imported: string[];
  candidates?: ToolBatchImportCandidate[];
  conflicts?: ToolBatchImportConflict[];
}

export interface InstallTemplateBatchesRequest {
  overwrite?: boolean;
  rename_map?: Record<string, string>;
}

export interface InstallTemplateBatchesResult {
  installed: string[];
  conflicts: ToolBatchImportConflict[];
}
