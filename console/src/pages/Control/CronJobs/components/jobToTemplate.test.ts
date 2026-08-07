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

  const REF_JOB = makeJob({
    preprocess: {
      enabled: true,
      steps: [
        { script: "weather", args: { city: "北京" } },
        { script: "collect" },
      ],
    },
  } as Partial<CronJobSpecOutput>);

  function stepsOf(half: unknown): unknown[] {
    const preprocess = (half as { preprocess?: { steps?: unknown[] } })
      .preprocess;
    return preprocess?.steps ?? [];
  }

  it("names the same scripts in BOTH halves of the package", () => {
    // `form` goes through normalizePreprocessValue and `job` is a raw
    // spread. They are allowed to differ in shape — form fills in `args` —
    // but never in which scripts the chain runs, or the package's two
    // halves would disagree about what it needs.
    const body = buildCreateTemplateRequest(REF_JOB, OPTIONS);
    expect(stepsOf(body.form)).toEqual([
      { script: "weather", args: { city: "北京" } },
      { script: "collect", args: {} },
    ]);
    expect(stepsOf(body.job)).toEqual([
      { script: "weather", args: { city: "北京" } },
      { script: "collect" },
    ]);
  });

  it("does not mutate the job it was handed", () => {
    buildCreateTemplateRequest(REF_JOB, {
      ...OPTIONS,
      batchFiles: { "weather.json": "[]" },
      batchEntry: "batch/weather.json",
    });
    expect(REF_JOB.preprocess?.steps?.[0].script).toBe("weather");
    expect(REF_JOB.preprocess?.steps?.[1].args).toBeUndefined();
  });
});

describe("skill refs across the template round trip", () => {
  it("always emits skills, so editing job B cannot inherit job A's", () => {
    // The drawer form is not reset between jobs, so an absent field would
    // leave the previous selection on screen.
    expect(jobToFormValues(makeJob()).skills).toEqual([]);
  });

  it("carries refs into the form values", () => {
    const values = jobToFormValues(
      makeJob({
        skills: [{ name: "advisor" }, { name: "b", template: "pkg" }],
      }),
    );

    expect(values.skills).toEqual([
      { name: "advisor" },
      { name: "b", template: "pkg" },
    ]);
  });

  it("normalizes junk out of a hand-edited spec", () => {
    const values = jobToFormValues(
      makeJob({
        skills: [{ name: "  a  " }, { name: "" }] as never,
      }),
    );

    expect(values.skills).toEqual([{ name: "a" }]);
  });

  it("packages refs into the job spec half", () => {
    const spec = jobToTemplateSpec(
      makeJob({ skills: [{ name: "b", template: "pkg" }] }),
    );

    expect(spec.skills).toEqual([{ name: "b", template: "pkg" }]);
  });

  it("omits skills from the spec half when there are none", () => {
    expect(jobToTemplateSpec(makeJob())).not.toHaveProperty("skills");
  });

  it("does not alias the job's own ref objects", () => {
    // The caller keeps using the job it passed in; a shared object would
    // let a later edit of the template mutate the live job.
    const job = makeJob({ skills: [{ name: "b", template: "pkg" }] });
    const spec = jobToTemplateSpec(job);

    expect((spec.skills as unknown[])[0]).not.toBe(job.skills![0]);
  });

  it("keeps a template-qualified ref in the form half", () => {
    // The importer may not have that package. Kept anyway: an unresolvable
    // ref degrades into a note in the prompt at run time, whereas
    // stripping it would silently discard what the author chose.
    const form = jobToTemplateForm(
      makeJob({ skills: [{ name: "b", template: "workspace-usage" }] }),
    );

    expect(form.skills).toEqual([{ name: "b", template: "workspace-usage" }]);
  });

  it("puts refs in both halves of a create request", () => {
    const request = buildCreateTemplateRequest(
      makeJob({ skills: [{ name: "advisor" }] }),
      {
        name: "pkg",
        title: "T",
        description: "",
        frequency: "",
        emoji: "",
        tags: [],
        includeDispatchTarget: false,
      },
    );

    expect(request.form.skills).toEqual([{ name: "advisor" }]);
    expect(request.job?.skills).toEqual([{ name: "advisor" }]);
  });
});

describe("provenance does not travel into a template", () => {
  it("strips meta from the form half", () => {
    // A job derived from `weather-report` and saved as `my-thing` would
    // otherwise ship a package claiming `weather-report` provenance, and
    // every job created from it would lead its skill picker with the wrong
    // package.
    const form = jobToTemplateForm(
      makeJob({ meta: { from_template: "weather-report" } } as never),
    );

    expect(form).not.toHaveProperty("meta");
  });

  it("never put meta in the job half to begin with", () => {
    const spec = jobToTemplateSpec(
      makeJob({ meta: { from_template: "weather-report" } } as never),
    );

    expect(spec).not.toHaveProperty("meta");
  });

  it("keeps meta when merely editing a job", () => {
    // `jobToFormValues` feeds the edit drawer, where provenance must survive
    // — that is what lets a re-opened job still lead with its own package.
    const values = jobToFormValues(
      makeJob({ meta: { from_template: "weather-report" } } as never),
    );

    expect(values.meta).toEqual({ from_template: "weather-report" });
  });
});
