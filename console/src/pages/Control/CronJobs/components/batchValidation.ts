/**
 * Pure helpers shared by the batch script editor and the preprocess form.
 *
 * The backend executor (`run_tool_batch`) is the source of truth for the
 * script format; these checks mirror its rules closely enough that anything
 * accepted here also runs there:
 *
 * - `${args.name}` placeholders follow the backend's
 *   `_ARG_REF_INLINE_PATTERN` exactly, so the UI and the executor agree on
 *   what counts as a placeholder.
 * - `${steps.N}` references are positional indexes into the actions array.
 *   A reference is only resolvable when N points at an EARLIER step (the
 *   executor raises "Step reference has no result" otherwise), which is
 *   also why the graphical editor only appends steps and removes the last
 *   one: reordering would silently rewrite every downstream reference.
 *
 * Control-flow steps are ordinary actions whose `tool_name` is one of
 * `label` / `goto` / `set_var`; their arguments live under the same
 * `arguments` key (`label` -> name, `goto` -> label + condition,
 * `set_var` -> expr).
 */

/** Mirrors `MAX_BATCH_STEPS` in run_tool_batch.py. */
export const MAX_BATCH_STEPS = 50;

/** Must stay identical to the backend's `_ARG_REF_INLINE_PATTERN`. */
export const ARG_REF_PATTERN = /\$\{args\.([A-Za-z0-9_.-]+)\}/g;

/** Positional step reference, with an optional result path suffix. */
const STEP_REF_PATTERN = /\$\{steps\.(\d+)(?:\.[A-Za-z0-9_.-]+)?\}/g;

export type BatchControlFlowTool = "label" | "goto" | "set_var";

export const CONTROL_FLOW_TOOLS: BatchControlFlowTool[] = [
  "label",
  "goto",
  "set_var",
];

export function isControlFlowTool(toolName: string): boolean {
  return (CONTROL_FLOW_TOOLS as string[]).includes(toolName);
}

export interface BatchAction {
  tool_name: string;
  arguments: Record<string, unknown>;
  stop_on_error?: boolean;
  wait?: number;
  [key: string]: unknown;
}

export type BatchValidationErrorCode =
  /** Not a bare action array nor an object with an `actions` array. */
  | "invalid_content"
  /** Step is not an object or has no usable `tool_name`. */
  | "invalid_action"
  /** `arguments` is present but not an object. */
  | "invalid_arguments"
  /** More than MAX_BATCH_STEPS actions. */
  | "too_many_steps"
  /** `${steps.N}` with N >= the referencing step's index. */
  | "forward_step_ref"
  /** `goto` targeting a label no `label` step defines. */
  | "unknown_label"
  /** Two `label` steps define the same name. */
  | "duplicate_label";

export interface BatchValidationError {
  code: BatchValidationErrorCode;
  /** Action index the error belongs to, when applicable. */
  index?: number;
  /** The offending reference, label or count. */
  detail?: string;
}

export interface BatchValidationResult {
  ok: boolean;
  /** Normalized actions (tool_name/arguments resolved); empty unless ok. */
  actions: BatchAction[];
  /** `${args.*}` placeholder names found anywhere in the script, sorted. */
  argNames: string[];
  errors: BatchValidationError[];
}

/**
 * Collect `${args.*}` placeholder names from every string value in the
 * content, deduplicated and sorted. Accepts parsed JSON of either container
 * shape (bare array or `{actions: [...]}`).
 */
export function extractArgNames(content: unknown): string[] {
  const names = new Set<string>();
  const walk = (node: unknown): void => {
    if (typeof node === "string") {
      for (const match of node.matchAll(ARG_REF_PATTERN)) {
        names.add(match[1]);
      }
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node && typeof node === "object") {
      Object.values(node as Record<string, unknown>).forEach(walk);
    }
  };
  walk(content);
  return [...names].sort();
}

/**
 * Expand dotted arg keys into nested objects for the backend.
 *
 * The executor's `_lookup_arg` resolves `${args.out.dir}` by walking a
 * NESTED path (`args.out.dir`), so submitting the flat key `"out.dir"`
 * that the form uses would raise "Missing arg: $args.out.dir". Call
 * this at the submit boundary only; the form and the args editor keep
 * flat keys because there is one input per placeholder.
 *
 * Plain keys are left untouched. Dotted keys sharing a prefix merge into
 * one nested object; when types collide, later entries win.
 */
export function expandDottedArgKeys(
  args: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(args)) {
    if (!key.includes(".")) {
      result[key] = value;
      continue;
    }
    const parts = key.split(".");
    let node = result;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const part = parts[i];
      const existing = node[part];
      if (
        !existing ||
        typeof existing !== "object" ||
        Array.isArray(existing)
      ) {
        node[part] = {};
      }
      node = node[part] as Record<string, unknown>;
    }
    node[parts[parts.length - 1]] = value;
  }
  return result;
}

function collectStepRefs(node: unknown, refs: number[]): void {
  if (typeof node === "string") {
    for (const match of node.matchAll(STEP_REF_PATTERN)) {
      refs.push(Number(match[1]));
    }
    return;
  }
  if (Array.isArray(node)) {
    node.forEach((item) => collectStepRefs(item, refs));
    return;
  }
  if (node && typeof node === "object") {
    Object.values(node as Record<string, unknown>).forEach((value) =>
      collectStepRefs(value, refs),
    );
  }
}

/**
 * Resolve the raw action list from either container shape. Returns null
 * (rather than throwing) when the content matches neither.
 */
export function extractActions(content: unknown): unknown[] | null {
  if (Array.isArray(content)) return content;
  if (content && typeof content === "object") {
    const actions = (content as Record<string, unknown>).actions;
    if (Array.isArray(actions)) return actions;
  }
  return null;
}

/**
 * Validate parsed batch content before saving to the pool.
 *
 * Pure and side-effect free so the editor can re-run it on every save and
 * tests can pin the semantics without touching the backend.
 */
export function validateBatchContent(content: unknown): BatchValidationResult {
  const errors: BatchValidationError[] = [];
  const rawActions = extractActions(content);

  if (rawActions === null) {
    return {
      ok: false,
      actions: [],
      argNames: [],
      errors: [{ code: "invalid_content" }],
    };
  }

  if (rawActions.length > MAX_BATCH_STEPS) {
    return {
      ok: false,
      actions: [],
      argNames: extractArgNames(content),
      errors: [{ code: "too_many_steps", detail: String(rawActions.length) }],
    };
  }

  const actions: BatchAction[] = [];
  const labelNames = new Set<string>();
  const gotoTargets: { index: number; label: string }[] = [];

  rawActions.forEach((raw, index) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      errors.push({ code: "invalid_action", index });
      return;
    }
    const step = raw as Record<string, unknown>;
    // The executor accepts `tool`/`args` aliases; normalize to the
    // canonical keys so the editor round-trips one spelling.
    const toolName = String(step.tool_name ?? step.tool ?? "").trim();
    if (!toolName) {
      errors.push({ code: "invalid_action", index });
      return;
    }
    const rawArguments = step.arguments ?? step.args ?? {};
    if (
      !rawArguments ||
      typeof rawArguments !== "object" ||
      Array.isArray(rawArguments)
    ) {
      errors.push({ code: "invalid_arguments", index });
      return;
    }
    const args = rawArguments as Record<string, unknown>;

    // Forward references can never resolve: steps run in index order and
    // a step's own result does not exist while it executes.
    const refs: number[] = [];
    collectStepRefs(step, refs);
    for (const ref of refs) {
      if (ref >= index) {
        errors.push({
          code: "forward_step_ref",
          index,
          detail: `\${steps.${ref}}`,
        });
      }
    }

    if (toolName === "label") {
      const name = String(args.name ?? "").trim();
      if (name) {
        if (labelNames.has(name)) {
          errors.push({ code: "duplicate_label", index, detail: name });
        } else {
          labelNames.add(name);
        }
      }
    }
    if (toolName === "goto") {
      const target = String(args.label ?? "").trim();
      if (target) gotoTargets.push({ index, label: target });
    }

    const action: BatchAction = { tool_name: toolName, arguments: args };
    if (typeof step.stop_on_error === "boolean") {
      action.stop_on_error = step.stop_on_error;
    }
    if (typeof step.wait === "number") {
      action.wait = step.wait;
    }
    actions.push(action);
  });

  for (const { index, label } of gotoTargets) {
    if (!labelNames.has(label)) {
      errors.push({ code: "unknown_label", index, detail: label });
    }
  }

  if (errors.length) {
    return {
      ok: false,
      actions: [],
      argNames: extractArgNames(content),
      errors,
    };
  }
  return { ok: true, actions, argNames: extractArgNames(content), errors: [] };
}

/** Read-only rendering of one batch step for the preprocess preview. */
export interface BatchStepSummary {
  /** Tool name, or the control-flow verb for label/goto/set_var steps. */
  toolName: string;
  /** Parameter rows as [key, displayValue] pairs, in declaration order. */
  params: Array<[string, string]>;
}

function toDisplayValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * Flatten one action into display rows: tool steps show their arguments,
 * control-flow steps show only the fields that matter (label name, goto
 * target + condition, set_var expression).
 */
export function summarizeStep(step: unknown): BatchStepSummary {
  const empty: BatchStepSummary = { toolName: "?", params: [] };
  if (!step || typeof step !== "object" || Array.isArray(step)) return empty;
  const record = step as Record<string, unknown>;
  const toolName = String(record.tool_name ?? record.tool ?? "").trim();
  if (!toolName) return empty;
  const rawArgs = record.arguments ?? record.args ?? {};
  const args =
    rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs)
      ? (rawArgs as Record<string, unknown>)
      : {};

  if (toolName === "label") {
    return { toolName, params: [["name", String(args.name ?? "")]] };
  }
  if (toolName === "goto") {
    const params: Array<[string, string]> = [
      ["label", String(args.label ?? "")],
    ];
    if (args.condition !== undefined && args.condition !== "") {
      params.push(["condition", toDisplayValue(args.condition)]);
    }
    return { toolName, params };
  }
  if (toolName === "set_var") {
    return { toolName, params: [["expr", String(args.expr ?? "")]] };
  }
  return {
    toolName,
    params: Object.entries(args).map(
      ([key, value]) => [key, toDisplayValue(value)] as [string, string],
    ),
  };
}
