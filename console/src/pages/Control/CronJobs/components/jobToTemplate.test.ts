import { describe, expect, it } from "vitest";
import dayjs from "dayjs";
import type { CronJobSpecOutput } from "../../../../api/types";
import {
  buildCreateTemplateRequest,
  jobToFormValues,
  jobToTemplateForm,
  jobToTemplateSpec,
} from "./jobToTemplate";

function makeJob(
  overrides: Partial<CronJobSpecOutput> = {},
): CronJobSpecOutput {
  return {
    id: "job-1",
    name: "每日简报",
    enabled: true,
    schedule: { type: "cron", cron: "30 8 * * mon,wed", timezone: "UTC" },
    task_type: "agent",
    request: {
      input: [{ role: "user", content: [{ type: "text", text: "hi" }] }],
      user_id: "u1",
      session_id: "console:u1",
    },
    dispatch: {
      type: "channel",
      channel: "console",
      target: { user_id: "u1", session_id: "console:u1" },
      mode: "stream",
    },
    runtime: { timeout_seconds: 600 },
    ...overrides,
  } as CronJobSpecOutput;
}

const ONCE_JOB = makeJob({
  schedule: {
    type: "once",
    run_at: "2026-05-13T09:00:00",
    timezone: "UTC",
    repeat_every_days: 7,
    repeat_end_type: "until",
    repeat_until: "2026-06-13T09:00:00",
  },
  task_type: "text",
  text: "开会了",
  request: undefined,
});

const OPTIONS = {
  name: "my-tpl",
  title: "标题",
  description: "说明",
  frequency: "每周一三 08:30",
  emoji: "📊",
  tags: ["team"],
  includeDispatchTarget: false,
};

describe("jobToFormValues", () => {
  it("stringifies request.input for the textarea", () => {
    const values = jobToFormValues(makeJob());
    const request = values.request as Record<string, unknown>;
    expect(typeof request.input).toBe("string");
    expect(JSON.parse(request.input as string)[0].role).toBe("user");
  });

  it("derives the weekly cron sub-form", () => {
    const values = jobToFormValues(makeJob());
    expect(values.scheduleType).toBe("cron");
    expect(values.cronType).toBe("weekly");
    expect(values.cronDaysOfWeek).toEqual(["mon", "wed"]);
    expect((values.cronTime as dayjs.Dayjs).hour()).toBe(8);
  });

  it("maps a repeating once schedule onto the repeat fields", () => {
    const values = jobToFormValues(ONCE_JOB);
    expect(values.scheduleType).toBe("once");
    expect(dayjs.isDayjs(values.onceRunAt)).toBe(true);
    expect(values.onceRepeatEnabled).toBe(true);
    expect(values.onceRepeatEveryDays).toBe(7);
    expect(values.onceRepeatEndType).toBe("until");
    expect(dayjs.isDayjs(values.onceRepeatUntil)).toBe(true);
  });

  it("keeps custom expressions verbatim", () => {
    const values = jobToFormValues(
      makeJob({ schedule: { type: "cron", cron: "*/15 * * * *" } }),
    );
    expect(values.cronType).toBe("custom");
    expect(values.cronCustom).toBe("*/15 * * * *");
  });
});

describe("jobToTemplateForm", () => {
  it("produces JSON-safe values", () => {
    const form = jobToTemplateForm(makeJob());
    expect(() => JSON.stringify(form)).not.toThrow();
    expect(form.cronTime).toBe("08:30");
  });

  it("drops the job identity", () => {
    const form = jobToTemplateForm(makeJob());
    expect(form.id).toBeUndefined();
    expect(form.name).toBe("");
  });

  it("blanks the dispatch target by default", () => {
    const form = jobToTemplateForm(makeJob());
    const dispatch = form.dispatch as Record<string, unknown>;
    expect(dispatch.target).toEqual({ user_id: "", session_id: "" });
    expect(dispatch.channel).toBe("console");
  });

  it("keeps the dispatch target when explicitly opted in", () => {
    const form = jobToTemplateForm(makeJob(), true);
    const dispatch = form.dispatch as Record<string, unknown>;
    expect(dispatch.target).toEqual({
      user_id: "u1",
      session_id: "console:u1",
    });
  });

  it("always blanks request identity, even when target is kept", () => {
    const form = jobToTemplateForm(makeJob(), true);
    const request = form.request as Record<string, unknown>;
    expect(request.user_id).toBe("");
    expect(request.session_id).toBe("");
  });

  it("strips the timezone so the importer's zone wins", () => {
    const form = jobToTemplateForm(makeJob());
    expect((form.schedule as Record<string, unknown>).timezone).toBeUndefined();
  });

  it("serializes once timestamps to plain strings", () => {
    const form = jobToTemplateForm(ONCE_JOB);
    expect(form.onceRunAt).toBe("2026-05-13T09:00:00");
    expect(form.onceRepeatUntil).toBe("2026-06-13T09:00:00");
  });

  it("omits undefined keys instead of emitting nulls", () => {
    const form = jobToTemplateForm(makeJob());
    expect("onceRunAt" in form).toBe(false);
    expect("onceRepeatUntil" in form).toBe(false);
  });
});

describe("jobToTemplateSpec", () => {
  it("keeps the schedule and blanks identities", () => {
    const spec = jobToTemplateSpec(makeJob());
    expect(spec.schedule).toEqual({
      type: "cron",
      cron: "30 8 * * mon,wed",
      timezone: "UTC",
    });
    expect((spec.request as Record<string, unknown>).user_id).toBe("");
    expect((spec.dispatch as Record<string, unknown>).target).toEqual({
      user_id: "",
      session_id: "",
    });
  });

  it("carries text tasks' body", () => {
    const spec = jobToTemplateSpec(ONCE_JOB);
    expect(spec.text).toBe("开会了");
    expect(spec.request).toBeUndefined();
  });

  it("preserves runtime settings", () => {
    const spec = jobToTemplateSpec(makeJob());
    expect(spec.runtime).toEqual({ timeout_seconds: 600 });
  });
});

describe("buildCreateTemplateRequest", () => {
  it("assembles the create payload", () => {
    const body = buildCreateTemplateRequest(makeJob(), OPTIONS);
    expect(body.name).toBe("my-tpl");
    expect(body.category).toBe("cron");
    expect(body.frequency).toBe("每周一三 08:30");
    expect(body.tags).toEqual(["team"]);
    expect(body.form).toBeTruthy();
    expect(body.job).toBeTruthy();
  });

  it("categorizes once schedules", () => {
    const body = buildCreateTemplateRequest(ONCE_JOB, OPTIONS);
    expect(body.category).toBe("once");
  });

  it("falls back to the job name for an empty title", () => {
    const body = buildCreateTemplateRequest(makeJob(), {
      ...OPTIONS,
      title: "",
    });
    expect(body.title).toBe("每日简报");
  });
});
