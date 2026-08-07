import { describe, it, expect } from "vitest";
import type {
  CronSkillInfo,
  TemplateBatchScriptInfo,
  ToolBatchInfo,
} from "../../../../api/types";
import {
  buildScriptOptions,
  findScript,
  scriptDisplayLabel,
  scriptQualifiedName,
} from "./scriptOptions";

const labels = {
  own: "我的脚本",
  template: "任务模板",
  job: "定时任务",
  skill: "skill",
};

/** Echoes the key back, which is what i18next does for a missing one. */
const t = (key: string) => key;
/** Resolves one known key, to exercise the key-first title path. */
const tWithWeather = (key: string) =>
  key === "cronJobs.templates.weather.title" ? "天气日报" : key;

function ownBatch(overrides: Partial<ToolBatchInfo> = {}): ToolBatchInfo {
  return {
    name: "collect",
    description: "",
    arg_names: [],
    action_count: 1,
    preview_actions: [{ tool_name: "read_file" }],
    updated_at: "",
    ...overrides,
  };
}

function templateScript(
  overrides: Partial<TemplateBatchScriptInfo> = {},
): TemplateBatchScriptInfo {
  return {
    ref: "weather-report/batch/weather.json",
    template: "weather-report",
    template_title: "每日天气播报",
    template_title_key: "",
    template_source: "builtin",
    file_path: "batch/weather.json",
    file_name: "weather.json",
    description: "",
    arg_names: ["city"],
    action_count: 1,
    preview_actions: [{ tool_name: "execute_shell_command" }],
    updated_at: "",
    ...overrides,
  };
}

describe("scriptDisplayLabel", () => {
  it("is the bare script name, without the .json suffix", () => {
    // The group header already names the template, and the suffix is how
    // a batch happens to be stored rather than part of its identity.
    expect(scriptDisplayLabel(templateScript())).toBe("weather");
  });

  it("strips the suffix case-insensitively", () => {
    expect(scriptDisplayLabel(templateScript({ file_name: "A.JSON" }))).toBe(
      "A",
    );
  });

  it("leaves a name that has no suffix alone", () => {
    expect(scriptDisplayLabel(templateScript({ file_name: "weather" }))).toBe(
      "weather",
    );
  });
});

describe("scriptQualifiedName", () => {
  it("is <template title>/<script name>", () => {
    expect(scriptQualifiedName(templateScript(), t)).toBe(
      "每日天气播报/weather",
    );
  });

  it("prefers the i18n key over the literal", () => {
    const script = templateScript({
      template_title: "Weather",
      template_title_key: "cronJobs.templates.weather.title",
    });
    expect(scriptQualifiedName(script, tWithWeather)).toBe("天气日报/weather");
  });

  it("falls back to the literal when the key does not resolve", () => {
    const script = templateScript({
      template_title: "Weather",
      template_title_key: "cronJobs.templates.missing.title",
    });
    expect(scriptQualifiedName(script, t)).toBe("Weather/weather");
  });
});

describe("buildScriptOptions", () => {
  it("shows only the job's own scripts while collapsed, and counts the rest", () => {
    const { groups, templateCount } = buildScriptOptions({
      ownScripts: [ownBatch()],
      templateScripts: [
        templateScript(),
        templateScript({ ref: "b/batch/x.json", template: "b" }),
      ],
      t,
      expanded: false,
      labels,
    });
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("我的脚本");
    expect(groups[0].options.map((o) => o.value)).toEqual(["collect"]);
    // The count is what the expander advertises, so it must not depend on
    // whether the groups are currently rendered.
    expect(templateCount).toBe(2);
  });

  it("groups template scripts by their template when expanded", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [
        templateScript(),
        templateScript({
          ref: "weather-report/batch/other.json",
          file_path: "batch/other.json",
          file_name: "other.json",
        }),
        templateScript({
          ref: "usage/batch/scan.json",
          template: "usage",
          template_title: "用量巡检",
          file_path: "batch/scan.json",
          file_name: "scan.json",
        }),
      ],
      t,
      expanded: true,
      labels,
    });
    // Prefixed, because template groups and cron-job groups share the list
    // and their names can easily coincide.
    expect(groups.map((g) => g.label)).toEqual([
      "任务模板 · 每日天气播报",
      "任务模板 · 用量巡检",
    ]);
    // Bare names: the group header above them already says the template.
    expect(groups[0].options.map((o) => o.label)).toEqual(["weather", "other"]);
  });

  it("omits the first group entirely when the job owns nothing", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      t,
      expanded: false,
      labels,
    });
    expect(groups).toEqual([]);
  });

  it("blanks every option's title so no native tooltip fires", () => {
    // rc-select fills the DOM title attribute from a string label, which
    // would pop a browser tooltip on top of the antd one.
    const { groups } = buildScriptOptions({
      ownScripts: [ownBatch()],
      templateScripts: [templateScript()],
      t,
      expanded: true,
      labels,
    });
    const options = groups.flatMap((g) => g.options);
    expect(options).toHaveLength(2);
    for (const option of options) expect(option.title).toBe("");
  });

  it("shows the description on hover, falling back to the identity", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [ownBatch({ description: "采集磁盘用量" })],
      templateScripts: [templateScript()],
      t,
      expanded: true,
      labels,
    });
    expect(groups[0].options[0].tooltip).toBe("采集磁盘用量");
    // None of the shipped template scripts carry a description, so this
    // fallback is the common case rather than the edge one.
    expect(groups[1].options[0].tooltip).toBe("每日天气播报/weather");
  });

  it("makes both the title and the raw ref searchable", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [templateScript()],
      t,
      expanded: true,
      labels,
    });
    const { searchText } = groups[0].options[0];
    expect(searchText).toContain("每日天气播报");
    expect(searchText).toContain("weather.json");
    expect(searchText).toContain("weather-report/batch/weather.json");
  });
});

describe("other jobs' scripts", () => {
  const owned = {
    job_id: "bbbbbbbb-0000-4000-8000-000000000002",
    job_name: "磁盘空间巡检",
    batches: [ownBatch({ name: "scan", description: "看看还剩多少" })],
  };

  it("appears as its own group behind the expander", () => {
    const { groups, templateCount } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [templateScript()],
      jobScripts: [owned],
      t,
      expanded: true,
      labels,
    });
    expect(groups.map((g) => g.label)).toEqual([
      "任务模板 · 每日天气播报",
      "定时任务 · 磁盘空间巡检",
    ]);
    // The expander advertises everything foreign, not just templates.
    expect(templateCount).toBe(2);
  });

  it("carries a copy source rather than a storable value", () => {
    // Nothing here may ever be written into a step: a script belongs to one
    // job, so picking a foreign one has to copy it.
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [templateScript()],
      jobScripts: [owned],
      t,
      expanded: true,
      labels,
    });
    expect(groups[0].options[0].copySource).toEqual({
      from_template: "weather-report",
      file: "batch/weather.json",
    });
    expect(groups[1].options[0].copySource).toEqual({
      from_job_id: owned.job_id,
      file: "scan",
    });
  });

  it("is hidden while collapsed, like template scripts", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [ownBatch()],
      templateScripts: [],
      jobScripts: [owned],
      t,
      expanded: false,
      labels,
    });
    expect(groups.map((g) => g.label)).toEqual(["我的脚本"]);
  });

  it("skips a job that owns no scripts", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      jobScripts: [{ ...owned, batches: [] }],
      t,
      expanded: true,
      labels,
    });
    expect(groups).toEqual([]);
  });
});

describe("findScript", () => {
  it("resolves one of the job's own names", () => {
    const found = findScript("collect", [ownBatch()]);
    expect(found?.kind).toBe("own");
    expect(found?.action_count).toBe(1);
  });

  it("returns null for a dangling value", () => {
    expect(findScript("ghost", [ownBatch()])).toBeNull();
    expect(findScript("gone/batch/a.json", [ownBatch()])).toBeNull();
    expect(findScript("", [ownBatch()])).toBeNull();
  });
});

describe("skill-carried scripts", () => {
  function skill(overrides: Partial<CronSkillInfo> = {}): CronSkillInfo {
    return {
      name: "collector",
      source: "workspace",
      template: "",
      batch_files: ["scripts/collect.json"],
      ...overrides,
    };
  }

  it("stays hidden until expanded, like the other foreign groups", () => {
    const collapsed = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [skill()],
      t,
      expanded: false,
      labels,
    });

    expect(collapsed.groups).toEqual([]);
    // Still counted: the expander advertises what is behind it.
    expect(collapsed.templateCount).toBe(1);
  });

  it("groups per skill and labels rows by their relative path", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [
        skill({ batch_files: ["scripts/phase1.json", "batch/phase1.json"] }),
      ],
      t,
      expanded: true,
      labels,
    });

    expect(groups[0].label).toBe("skill · collector");
    // The subdirectory has to survive into the label: the same stem can
    // exist under both directories, and nothing else distinguishes them.
    expect(groups[0].options.map((o) => o.label)).toEqual([
      "scripts/phase1",
      "batch/phase1",
    ]);
  });

  it("asks to copy from an installed skill without a qualifier", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [skill()],
      t,
      expanded: true,
      labels,
    });

    // No `from_skill_template` key at all — an empty one would read as "a
    // package named ''", and the backend 400s on the orphan qualifier.
    expect(groups[0].options[0].copySource).toEqual({
      from_skill: "collector",
      file: "scripts/collect.json",
    });
  });

  it("qualifies a copy from a template-bundled skill", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [
        skill({ source: "template", template: "workspace-usage" }),
      ],
      t,
      expanded: true,
      labels,
    });

    expect(groups[0].options[0].copySource).toEqual({
      from_skill: "collector",
      from_skill_template: "workspace-usage",
      file: "scripts/collect.json",
    });
  });

  it("keeps two skills' same-named scripts as distinct keys", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [skill({ name: "one" }), skill({ name: "two" })],
      t,
      expanded: true,
      labels,
    });

    const values = groups.flatMap((g) => g.options.map((o) => o.value));
    expect(new Set(values).size).toBe(2);
  });

  it("skips a skill that carries nothing", () => {
    const { groups, templateCount } = buildScriptOptions({
      ownScripts: [],
      templateScripts: [],
      skillScripts: [
        skill({ batch_files: [] }),
        skill({ batch_files: undefined }),
      ],
      t,
      expanded: true,
      labels,
    });

    expect(groups).toEqual([]);
    expect(templateCount).toBe(0);
  });

  it("is optional, so existing callers need no change", () => {
    const { groups } = buildScriptOptions({
      ownScripts: [ownBatch()],
      templateScripts: [],
      t,
      expanded: true,
      labels,
    });

    expect(groups.map((g) => g.label)).toEqual(["我的脚本"]);
  });
});
