import { describe, expect, it } from "vitest";
import dayjs from "dayjs";
import type { CronTemplateInfo } from "../../../../api/types";
import {
  baseScriptName,
  declaredPreprocessScripts,
  hasUnresolvedPlaceholder,
  hydrateFormValues,
  remapPreprocessScripts,
  substitutePlaceholders,
  toTemplateDefinition,
  toTemplateDefinitions,
} from "./packageTemplates";
import { templateTitle } from "./templates";

const t = (key: string) => key;

function makeInfo(overrides: Partial<CronTemplateInfo> = {}): CronTemplateInfo {
  return {
    name: "daily-brief",
    title: "每日简报",
    description: "说明",
    category: "cron",
    frequency: "每天 09:00",
    emoji: "📊",
    tags: ["personal", "calendar"],
    version_text: "1.0",
    source: "user",
    title_key: "",
    description_key: "",
    frequency_key: "",
    content: "# 文档",
    payload: {
      form: { scheduleType: "cron", cronCustom: "0 9 * * *" },
      job: null,
      batch_entry: "batch/go.json",
    },
    batch_files: ["batch/go.json"],
    skills: ["writer"],
    files: ["TEMPLATE.md", "template.json"],
    package_dir: "/pool/daily-brief",
    batch_entry_path: "/pool/daily-brief/batch/go.json",
    updated_at: "",
    ...overrides,
  };
}

describe("hydrateFormValues", () => {
  it("fills in the timezone when the package left it blank", () => {
    const values = hydrateFormValues({ scheduleType: "cron" }, "Asia/Shanghai");
    expect(values.schedule).toEqual({ timezone: "Asia/Shanghai" });
  });

  it("keeps a timezone the package pinned deliberately", () => {
    const values = hydrateFormValues(
      { schedule: { type: "cron", timezone: "UTC" } },
      "Asia/Shanghai",
    );
    expect((values.schedule as Record<string, unknown>).timezone).toBe("UTC");
  });

  it("turns stored timestamps into dayjs objects", () => {
    const values = hydrateFormValues(
      {
        scheduleType: "once",
        onceRunAt: "2026-05-13T09:00:00",
        onceRepeatUntil: "2026-06-13T09:00:00",
      },
      "UTC",
    );
    expect(dayjs.isDayjs(values.onceRunAt)).toBe(true);
    expect(dayjs.isDayjs(values.onceRepeatUntil)).toBe(true);
    expect((values.onceRunAt as dayjs.Dayjs).format("YYYY-MM-DD HH:mm")).toBe(
      "2026-05-13 09:00",
    );
  });

  it("defaults a once template with no run time to tomorrow", () => {
    const values = hydrateFormValues({ scheduleType: "once" }, "UTC");
    expect(dayjs.isDayjs(values.onceRunAt)).toBe(true);
    expect((values.onceRunAt as dayjs.Dayjs).isAfter(dayjs())).toBe(true);
  });

  it("parses HH:mm clock strings onto today", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", cronType: "daily", cronTime: "07:45" },
      "UTC",
    );
    const time = values.cronTime as dayjs.Dayjs;
    expect(dayjs.isDayjs(time)).toBe(true);
    expect(time.hour()).toBe(7);
    expect(time.minute()).toBe(45);
  });

  it("leaves an unparseable clock string alone", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", cronType: "daily", cronTime: "99:99" },
      "UTC",
    );
    expect(values.cronTime).toBe("99:99");
  });

  it("derives the cron sub-form from a raw expression", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", cronCustom: "30 8 * * mon,wed" },
      "UTC",
    );
    expect(values.cronType).toBe("weekly");
    expect(values.cronDaysOfWeek).toEqual(["mon", "wed"]);
    expect((values.cronTime as dayjs.Dayjs).hour()).toBe(8);
  });

  it("keeps a cronType the package already specified", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", cronType: "custom", cronCustom: "*/5 * * * *" },
      "UTC",
    );
    expect(values.cronType).toBe("custom");
    expect(values.cronCustom).toBe("*/5 * * * *");
  });

  it("falls back to schedule.cron when cronCustom is absent", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", schedule: { type: "cron", cron: "0 6 * * *" } },
      "UTC",
    );
    expect(values.cronType).toBe("daily");
    expect((values.cronTime as dayjs.Dayjs).hour()).toBe(6);
  });

  it("passes through fields it does not know about", () => {
    const values = hydrateFormValues(
      { scheduleType: "cron", cronType: "custom", future_field: 42 },
      "UTC",
    );
    expect(values.future_field).toBe(42);
  });
});

describe("substitutePlaceholders", () => {
  const info = makeInfo();

  it("resolves both placeholders in a plain string", () => {
    expect(
      substitutePlaceholders("run {{batch_entry}} in {{template_dir}}", info),
    ).toBe("run /pool/daily-brief/batch/go.json in /pool/daily-brief");
  });

  it("replaces every occurrence, not just the first", () => {
    expect(
      substitutePlaceholders("{{batch_entry}} {{batch_entry}}", info),
    ).toBe("/pool/daily-brief/batch/go.json /pool/daily-brief/batch/go.json");
  });

  it("reaches into nested objects and arrays", () => {
    const out = substitutePlaceholders(
      { a: [{ b: "x {{template_dir}}" }] },
      info,
    ) as { a: { b: string }[] };
    expect(out.a[0].b).toBe("x /pool/daily-brief");
  });

  it("reaches prompt text held as a JSON string", () => {
    const input = JSON.stringify([
      {
        role: "user",
        content: [{ type: "text", text: "use {{batch_entry}}" }],
      },
    ]);
    const out = substitutePlaceholders({ request: { input } }, info) as {
      request: { input: string };
    };
    expect(JSON.parse(out.request.input)[0].content[0].text).toBe(
      "use /pool/daily-brief/batch/go.json",
    );
  });

  it("leaves dayjs objects untouched", () => {
    const time = dayjs("2026-05-13T09:00:00");
    const out = substitutePlaceholders({ onceRunAt: time }, info) as {
      onceRunAt: unknown;
    };
    expect(out.onceRunAt).toBe(time);
  });

  it("leaves non-string scalars alone", () => {
    expect(substitutePlaceholders(42, info)).toBe(42);
    expect(substitutePlaceholders(true, info)).toBe(true);
    expect(substitutePlaceholders(null, info)).toBe(null);
  });

  it("leaves a placeholder in place when the path is unknown", () => {
    const noEntry = makeInfo({ batch_entry_path: "" });
    expect(substitutePlaceholders("{{batch_entry}}", noEntry)).toBe(
      "{{batch_entry}}",
    );
  });
});

describe("hasUnresolvedPlaceholder", () => {
  it("is false when the form has no placeholders", () => {
    expect(hasUnresolvedPlaceholder(makeInfo())).toBe(false);
  });

  it("is false when a placeholder can be resolved", () => {
    const info = makeInfo({
      payload: { form: { text: "{{batch_entry}}" } },
    });
    expect(hasUnresolvedPlaceholder(info)).toBe(false);
  });

  it("is true when batch_entry is referenced but not configured", () => {
    const info = makeInfo({
      payload: { form: { text: "{{batch_entry}}" } },
      batch_entry_path: "",
    });
    expect(hasUnresolvedPlaceholder(info)).toBe(true);
  });

  it("is true when template_dir is referenced but unknown", () => {
    const info = makeInfo({
      payload: { form: { text: "{{template_dir}}" } },
      package_dir: "",
    });
    expect(hasUnresolvedPlaceholder(info)).toBe(true);
  });
});

describe("toTemplateDefinition", () => {
  it("maps a user package onto the picker shape", () => {
    const def = toTemplateDefinition(makeInfo());
    expect(def.id).toBe("package:daily-brief");
    expect(def.packageSource).toBe("user");
    expect(def.packageName).toBe("daily-brief");
    expect(def.category).toBe("cron");
    expect(def.batchFiles).toEqual(["batch/go.json"]);
    expect(def.skills).toEqual(["writer"]);
    expect(def.docs).toBe("# 文档");
    expect(templateTitle(def, t)).toBe("每日简报");
  });

  it("marks builtin packages as read-only", () => {
    const def = toTemplateDefinition(makeInfo({ source: "builtin" }));
    expect(def.packageSource).toBe("builtin");
  });

  it("flags calendar recommendation from tags", () => {
    expect(toTemplateDefinition(makeInfo()).showInCalendarRecommended).toBe(
      true,
    );
    expect(
      toTemplateDefinition(makeInfo({ tags: ["team"] }))
        .showInCalendarRecommended,
    ).toBe(false);
  });

  it("normalizes an unexpected category to cron", () => {
    const def = toTemplateDefinition(
      makeInfo({ category: "weird" as unknown as "cron" }),
    );
    expect(def.category).toBe("cron");
  });

  it("stamps template provenance into the form meta", () => {
    const values = toTemplateDefinition(makeInfo()).toFormValues("UTC");
    expect(values.meta).toMatchObject({
      template_id: "daily-brief",
      template_source: "package",
    });
  });

  it("falls back to the package name when title is empty", () => {
    const def = toTemplateDefinition(makeInfo({ title: "" }));
    expect(templateTitle(def, t)).toBe("daily-brief");
  });

  it("resolves placeholders when instantiating the form", () => {
    const info = makeInfo({
      payload: {
        form: {
          scheduleType: "cron",
          cronType: "custom",
          cronCustom: "0 9 * * *",
          request: {
            input: JSON.stringify([
              {
                role: "user",
                content: [{ type: "text", text: "run {{batch_entry}}" }],
              },
            ]),
          },
        },
      },
    });
    const values = toTemplateDefinition(info).toFormValues("UTC");
    const request = values.request as { input: string };
    expect(JSON.parse(request.input)[0].content[0].text).toBe(
      "run /pool/daily-brief/batch/go.json",
    );
  });

  it("tolerates a package with no form payload", () => {
    const def = toTemplateDefinition(
      makeInfo({
        payload: { form: {} } as CronTemplateInfo["payload"],
      }),
    );
    expect(() => def.toFormValues("UTC")).not.toThrow();
  });
});

describe("toTemplateDefinitions", () => {
  it("lists user packages before the ones QwenPaw ships", () => {
    const defs = toTemplateDefinitions([
      makeInfo({ name: "shipped", source: "builtin" }),
      makeInfo({ name: "mine", source: "user" }),
    ]);
    expect(defs.map((d) => d.packageName)).toEqual(["mine", "shipped"]);
  });

  it("returns an empty list for no packages", () => {
    expect(toTemplateDefinitions([])).toEqual([]);
  });
});

describe("i18n keys", () => {
  it("carries the keys through to the picker entry", () => {
    const def = toTemplateDefinition(
      makeInfo({
        source: "builtin",
        title_key: "cronJobs.templates.dailyTechNewsBrief.title",
        description_key: "cronJobs.templates.dailyTechNewsBrief.description",
        frequency_key: "cronJobs.templates.dailyTechNewsBrief.frequency",
      }),
    );
    expect(def.titleKey).toBe("cronJobs.templates.dailyTechNewsBrief.title");
    expect(def.descriptionKey).toBe(
      "cronJobs.templates.dailyTechNewsBrief.description",
    );
    expect(def.frequencyKey).toBe(
      "cronJobs.templates.dailyTechNewsBrief.frequency",
    );
  });

  it("leaves them empty for a user-authored package", () => {
    const def = toTemplateDefinition(makeInfo());
    expect(def.titleKey).toBe("");
    expect(templateTitle(def, (k) => k)).toBe("每日简报");
  });
});

describe("baseScriptName", () => {
  it("turns a package-relative path into the installed script name", () => {
    expect(baseScriptName("batch/weather.json")).toBe("weather");
    expect(baseScriptName("batch/sub/scan-unix.JSON")).toBe("scan-unix");
  });

  it("passes a bare name through", () => {
    expect(baseScriptName("weather")).toBe("weather");
  });
});

describe("declaredPreprocessScripts", () => {
  it("lists the scripts a template's preprocess names", () => {
    expect(
      declaredPreprocessScripts({
        preprocess: {
          enabled: true,
          steps: [{ script: "weather" }, { script: "batch/other.json" }],
        },
      }),
    ).toEqual(["weather", "other"]);
  });

  it("is empty when the template declares no preprocess", () => {
    // This is what keeps a package's agent-chosen scripts (workspace-usage
    // ships a unix and a windows variant) from being copied into the job.
    expect(declaredPreprocessScripts({})).toEqual([]);
    expect(declaredPreprocessScripts({ preprocess: {} })).toEqual([]);
    expect(
      declaredPreprocessScripts({ preprocess: { enabled: false, steps: [] } }),
    ).toEqual([]);
  });

  it("skips blank and non-string entries", () => {
    expect(
      declaredPreprocessScripts({
        preprocess: { steps: [{ script: "  " }, {}, null, { script: 5 }] },
      }),
    ).toEqual([]);
  });
});

describe("remapPreprocessScripts", () => {
  const values = {
    name: "x",
    preprocess: {
      enabled: true,
      steps: [
        { script: "weather", args: { city: "北京" } },
        { script: "other" },
      ],
    },
  };

  it("rewrites declared names to the ones that landed", () => {
    // The server renames on collision, so the form has to follow it or the
    // chain names a script the job does not have.
    const out = remapPreprocessScripts(values, { weather: "weather-2" });
    expect(out.preprocess).toEqual({
      enabled: true,
      steps: [
        { script: "weather-2", args: { city: "北京" } },
        { script: "other" },
      ],
    });
  });

  it("leaves the args and every other field untouched", () => {
    const out = remapPreprocessScripts(values, { weather: "weather-2" });
    expect(out.name).toBe("x");
    expect(
      (out.preprocess as { steps: { args?: unknown }[] }).steps[0].args,
    ).toEqual({ city: "北京" });
  });

  it("does not mutate its input", () => {
    remapPreprocessScripts(values, { weather: "weather-2" });
    expect(values.preprocess.steps[0].script).toBe("weather");
  });

  it("passes values with no preprocess through unchanged", () => {
    const plain = { name: "x" };
    expect(remapPreprocessScripts(plain, { a: "b" })).toBe(plain);
    expect(remapPreprocessScripts({ preprocess: 5 }, {})).toEqual({
      preprocess: 5,
    });
  });
});
