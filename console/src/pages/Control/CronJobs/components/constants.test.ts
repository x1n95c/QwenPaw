import { describe, it, expect } from "vitest";
import {
  DEFAULT_FORM_VALUES,
  MAX_CRON_SKILLS,
  normalizePreprocessValue,
  normalizeSkillRefs,
} from "./constants";

describe("DEFAULT_FORM_VALUES", () => {
  it("has all required top-level keys", () => {
    const keys = [
      "enabled",
      "save_result_to_inbox",
      "scheduleType",
      "schedule",
      "onceRunAt",
      "cronType",
      "task_type",
      "request",
      "preprocess",
      "dispatch",
      "runtime",
    ];
    for (const key of keys) {
      expect(DEFAULT_FORM_VALUES).toHaveProperty(key);
    }
  });

  it("defaults preprocess to a disabled, empty chain", () => {
    expect(DEFAULT_FORM_VALUES.preprocess).toEqual({
      enabled: false,
      steps: [],
      last_only: true,
      on_failure: "continue",
      timeout_seconds: 120,
    });
  });

  it("default schedule.type is 'cron' and schedule.cron is '0 9 * * *'", () => {
    expect(DEFAULT_FORM_VALUES.schedule.type).toBe("cron");
    expect(DEFAULT_FORM_VALUES.schedule.cron).toBe("0 9 * * *");
  });

  it("delivers cron results by default", () => {
    expect(DEFAULT_FORM_VALUES.dispatch.silent).toBe(false);
  });
});

describe("normalizePreprocessValue", () => {
  it("reads the current steps array", () => {
    const value = normalizePreprocessValue({
      enabled: true,
      steps: [{ script: "a", args: { x: "1" } }, { script: "b" }],
    });
    expect(value.enabled).toBe(true);
    expect(value.steps).toEqual([
      { script: "a", args: { x: "1" } },
      { script: "b", args: {} },
    ]);
  });

  it("folds the legacy single-script shape into one step", () => {
    // Jobs created before chaining existed, and every job the CLI makes,
    // still arrive in this shape.
    const value = normalizePreprocessValue({
      enabled: true,
      script: "collect",
      args: { path: "/tmp" },
    });
    expect(value.steps).toEqual([
      { script: "collect", args: { path: "/tmp" } },
    ]);
  });

  it("passes a template reference through untouched", () => {
    // `<template>/batch/<file>.json` is just as valid a script value as a
    // script name, and the backend resolver is what tells them apart.
    const value = normalizePreprocessValue({
      enabled: true,
      steps: [{ script: " weather-report/batch/weather.json " }],
    });
    expect(value.steps).toEqual([
      { script: "weather-report/batch/weather.json", args: {} },
    ]);
  });

  it("stringifies non-string arg values so inputs stay controlled", () => {
    const value = normalizePreprocessValue({
      steps: [{ script: "a", args: { n: 5, flag: true } }],
    });
    expect(value.steps[0].args).toEqual({ n: "5", flag: "true" });
  });

  it("drops inline-actions steps rather than showing an empty row", () => {
    // The UI only edits the job's own scripts; a blank row would look like the
    // user had not picked one yet, and saving would silently drop it.
    const value = normalizePreprocessValue({
      steps: [{ actions: [{ tool_name: "x" }] }, { script: "a" }],
    });
    expect(value.steps).toEqual([{ script: "a", args: {} }]);
  });

  it("falls back to the defaults for junk input", () => {
    for (const junk of [null, undefined, "x", 5]) {
      expect(normalizePreprocessValue(junk).steps).toEqual([]);
    }
  });

  it("keeps a positive timeout and rejects a bad one", () => {
    expect(
      normalizePreprocessValue({ script: "a", timeout_seconds: 30 })
        .timeout_seconds,
    ).toBe(30);
    for (const bad of [0, -1, "abc", null]) {
      expect(
        normalizePreprocessValue({ script: "a", timeout_seconds: bad })
          .timeout_seconds,
      ).toBe(120);
    }
  });
});

describe("normalizeSkillRefs", () => {
  it("defaults to an empty list", () => {
    // Present in DEFAULT_FORM_VALUES so the drawer form always overwrites
    // the previously edited job's selection instead of inheriting it.
    expect(DEFAULT_FORM_VALUES.skills).toEqual([]);
  });

  it("keeps both ref shapes", () => {
    expect(
      normalizeSkillRefs([
        { name: "advisor" },
        { name: "bundled", template: "pkg" },
      ]),
    ).toEqual([{ name: "advisor" }, { name: "bundled", template: "pkg" }]);
  });

  it("omits an empty template rather than passing it through", () => {
    // `template: ""` would read as "a package named ''" on the way back to
    // the backend, which resolves nowhere.
    expect(normalizeSkillRefs([{ name: "a", template: "  " }])).toEqual([
      { name: "a" },
    ]);
  });

  it("trims and drops entries with no usable name", () => {
    expect(
      normalizeSkillRefs([
        { name: "  advisor  " },
        { name: "   " },
        { name: 42 },
        { template: "pkg" },
        null,
        "advisor",
      ]),
    ).toEqual([{ name: "advisor" }]);
  });

  it("dedupes, keeping the first occurrence", () => {
    expect(
      normalizeSkillRefs([
        { name: "a" },
        { name: "a" },
        { name: "a", template: "pkg" },
      ]),
    ).toEqual([{ name: "a" }, { name: "a", template: "pkg" }]);
  });

  it("caps the list at MAX_CRON_SKILLS", () => {
    // The Select can only enforce the cap on new picks, so a job that
    // already exceeds it would otherwise be unsavable with no visible
    // reason.
    const many = Array.from({ length: MAX_CRON_SKILLS + 3 }, (_, i) => ({
      name: `s${i}`,
    }));

    expect(normalizeSkillRefs(many)).toHaveLength(MAX_CRON_SKILLS);
  });

  it("treats anything that is not an array as empty", () => {
    for (const input of [undefined, null, "advisor", 7, { name: "a" }]) {
      expect(normalizeSkillRefs(input)).toEqual([]);
    }
  });
});
