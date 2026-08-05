import dayjs from "dayjs";

/** One script in the preprocess chain, as the drawer form holds it. */
export interface PreprocessStepFormValue {
  script: string;
  /**
   * Keys are dynamic — they follow the selected script's `${args.*}`
   * placeholders — which is why the form binds the whole steps array
   * through one Form.Item instead of a field per key.
   */
  args: Record<string, string>;
}

/**
 * Drawer form shape of the preprocess block. `steps` are the pool scripts
 * to run, in order.
 */
export const PREPROCESS_DEFAULTS = {
  enabled: false,
  steps: [] as PreprocessStepFormValue[],
  last_only: true,
  on_failure: "continue" as "continue" | "abort",
  timeout_seconds: 120,
};

export type PreprocessFormValue = typeof PREPROCESS_DEFAULTS;

/** An empty row, which is what the ＋ button appends. */
export function emptyPreprocessStep(): PreprocessStepFormValue {
  return { script: "", args: {} };
}

function normalizeArgs(input: unknown): Record<string, string> {
  const args: Record<string, string> = {};
  if (input && typeof input === "object" && !Array.isArray(input)) {
    for (const [key, value] of Object.entries(
      input as Record<string, unknown>,
    )) {
      args[key] = typeof value === "string" ? value : JSON.stringify(value);
    }
  }
  return args;
}

/**
 * Merge a stored spec into the drawer defaults so editing a job always
 * round-trips a complete preprocess value (and never leaks state from the
 * previously edited job into fields the new one does not carry).
 *
 * Accepts both server shapes: the current `steps` array and the legacy
 * single `script`/`args` pair, which older jobs and the CLI still send.
 * Inline `actions` scripts are not editable here, so such a step is
 * dropped rather than shown as an empty row pretending to be a pool
 * script — the backend keeps whatever it is not sent.
 */
export function normalizePreprocessValue(input: unknown): PreprocessFormValue {
  if (!input || typeof input !== "object") {
    return { ...PREPROCESS_DEFAULTS, steps: [] };
  }
  const raw = input as Record<string, unknown>;

  let steps: PreprocessStepFormValue[] = [];
  if (Array.isArray(raw.steps)) {
    steps = raw.steps
      .filter(
        (step): step is Record<string, unknown> =>
          Boolean(step) && typeof step === "object",
      )
      .filter((step) => typeof step.script === "string" && step.script.trim())
      .map((step) => ({
        script: String(step.script).trim(),
        args: normalizeArgs(step.args),
      }));
  } else if (typeof raw.script === "string" && raw.script.trim()) {
    steps = [{ script: raw.script.trim(), args: normalizeArgs(raw.args) }];
  }

  const timeout = Number(raw.timeout_seconds);
  return {
    enabled: Boolean(raw.enabled),
    steps,
    last_only: raw.last_only !== false,
    on_failure: raw.on_failure === "abort" ? "abort" : "continue",
    timeout_seconds:
      Number.isFinite(timeout) && timeout > 0
        ? timeout
        : PREPROCESS_DEFAULTS.timeout_seconds,
  };
}

export const DEFAULT_FORM_VALUES = {
  enabled: false,
  save_result_to_inbox: true,
  scheduleType: "cron" as const,
  schedule: {
    type: "cron" as const,
    cron: "0 9 * * *",
    timezone: "UTC",
  },
  onceRunAt: dayjs().add(1, "hour"),
  onceRepeatEnabled: false,
  onceRepeatEveryDays: 1,
  onceRepeatEndType: "never" as const,
  onceRepeatUntil: dayjs().add(7, "day"),
  onceRepeatCount: 2,
  cronType: "daily",
  cronTime: dayjs().hour(9).minute(0),
  task_type: "agent" as const,
  request: {
    input: "",
    session_id: "",
    user_id: "",
  },
  text: "",
  preprocess: { ...PREPROCESS_DEFAULTS, steps: [] },
  dispatch: {
    type: "channel" as const,
    channel: "console",
    target: {
      user_id: "",
      session_id: "",
    },
    mode: "stream" as const,
    silent: false,
  },
  runtime: {
    share_session: true,
    max_concurrency: 1,
    timeout_seconds: 120,
    misfire_grace_seconds: 600,
    tool_safety: false,
  },
};
