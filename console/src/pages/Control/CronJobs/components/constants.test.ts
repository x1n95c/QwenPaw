import { describe, it, expect } from "vitest";
import { DEFAULT_FORM_VALUES, normalizePreprocessValue } from "./constants";

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

  it("stringifies non-string arg values so inputs stay controlled", () => {
    const value = normalizePreprocessValue({
      steps: [{ script: "a", args: { n: 5, flag: true } }],
    });
    expect(value.steps[0].args).toEqual({ n: "5", flag: "true" });
  });

  it("drops inline-actions steps rather than showing an empty row", () => {
    // The UI only edits pool scripts; a blank row would look like the
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
