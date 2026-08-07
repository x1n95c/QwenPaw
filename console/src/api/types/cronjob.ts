export interface CronJobScheduleCron {
  type: "cron";
  cron: string;
  timezone?: string;
}

export interface CronJobScheduleOnce {
  type: "once";
  run_at: string;
  timezone?: string;
  repeat_every_days?: number;
  repeat_end_type?: "never" | "until" | "count";
  repeat_until?: string;
  repeat_count?: number;
}

export type CronJobSchedule = CronJobScheduleCron | CronJobScheduleOnce;

export interface CronJobTarget {
  user_id: string;
  session_id: string;
}

export interface CronJobDispatch {
  type: "channel";
  channel?: string;
  target: CronJobTarget;
  mode?: "stream" | "final";
  silent?: boolean;
  meta?: Record<string, unknown>;
}

export interface CronJobRuntime {
  max_concurrency?: number;
  timeout_seconds?: number;
  misfire_grace_seconds?: number;
  tool_safety?: boolean;
}

export interface CronJobRequest {
  input: unknown;
  session_id?: string | null;
  user_id?: string | null;
  [key: string]: unknown;
}

/**
 * One script in the preprocess chain. Exactly one of the two per step.
 *
 * `script` is either a name in the shared pool, or — when it contains a
 * `/` — a script bundled inside a cron template package, addressed as
 * `<template>/batch/<file>.json`. `actions` is the inline alternative
 * (the UI only edits `script`).
 */
export interface CronJobPreprocessStep {
  script?: string;
  actions?: Record<string, unknown>[];
  args?: Record<string, unknown>;
}

/**
 * Deterministic run_tool_batch scripts executed before the job body, in
 * `steps` order.
 */
export interface CronJobPreprocessSpec {
  enabled?: boolean;
  steps?: CronJobPreprocessStep[];
  /**
   * Legacy single-script form. The server folds it into a one-entry
   * `steps` and clears it, so a *response* only ever carries `steps`;
   * these stay because the CLI and older clients still send them.
   */
  script?: string;
  actions?: Record<string, unknown>[];
  args?: Record<string, unknown>;
  last_only?: boolean;
  stop_on_error?: boolean;
  maxstep?: number;
  /** Budget for the whole chain, not per script. */
  timeout_seconds?: number;
  on_failure?: "continue" | "abort";
}

/**
 * A skill the job runs with, addressed rather than installed.
 *
 * `template` is the discriminator: absent means a skill installed in this
 * workspace, set means one bundled inside that template package and read
 * in place. Nothing is copied — the trigger path reads `SKILL.md` out of
 * whichever directory this names and prepends the body to the prompt.
 */
export interface CronJobSkillRef {
  /** Skill directory name. */
  name: string;
  template?: string;
}

export interface CronJobSpecInput {
  id: string;
  name: string;
  enabled?: boolean;
  save_result_to_inbox?: boolean;
  schedule: CronJobSchedule;
  task_type?: "text" | "agent";
  text?: string;
  request?: CronJobRequest;
  preprocess?: CronJobPreprocessSpec | null;
  /** Inert for `task_type: "text"` — that path never runs a model. */
  skills?: CronJobSkillRef[];
  dispatch: CronJobDispatch;
  runtime?: CronJobRuntime;
  meta?: Record<string, unknown>;
}

export type CronJobSpecOutput = CronJobSpecInput;

export interface CronJobView extends CronJobSpecOutput {
  // Extended view with runtime state
  state?: unknown;
  next_run_time?: number;
  last_run_time?: number;
}

export interface CronJobExecutionRecord {
  run_at: string;
  status: "success" | "error" | "running" | "skipped" | "cancelled";
  error?: string | null;
  trigger?: "scheduled" | "manual";
}

export interface CronDispatchTargetItem {
  channel: string;
  user_id: string;
  session_id: string;
}

export interface CronDispatchTargetsResponse {
  channels: string[];
  items: CronDispatchTargetItem[];
}

export type CronJobSpecInputLegacy = Record<string, unknown>;
export type CronJobSpecOutputLegacy = Record<string, unknown>;
export type CronJobViewLegacy = Record<string, unknown>;
