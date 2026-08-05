import { describe, it, expect } from "vitest";
import {
  ARG_REF_PATTERN,
  MAX_BATCH_STEPS,
  expandDottedArgKeys,
  extractActions,
  extractArgNames,
  summarizeStep,
  validateBatchContent,
} from "./batchValidation";

describe("extractArgNames", () => {
  it("finds placeholders in nested argument strings", () => {
    const content = {
      actions: [
        {
          tool_name: "execute_shell_command",
          arguments: {
            command: "ls ${args.folder} | grep ${args.pattern}",
            nested: { deep: ["${args.limit}"] },
          },
        },
      ],
    };
    expect(extractArgNames(content)).toEqual(["folder", "limit", "pattern"]);
  });

  it("dedupes repeated placeholders and sorts the result", () => {
    const content = [
      { tool_name: "a", arguments: { x: "${args.b} ${args.a}" } },
      { tool_name: "b", arguments: { y: "${args.b}" } },
    ];
    expect(extractArgNames(content)).toEqual(["a", "b"]);
  });

  it("accepts dotted placeholder names like the backend pattern", () => {
    expect(extractArgNames({ v: "${args.user.name-1}" })).toEqual([
      "user.name-1",
    ]);
  });

  it("ignores non-placeholder dollar syntax", () => {
    expect(extractArgNames({ v: "$args.foo ${args} ${steps.0.text}" })).toEqual(
      [],
    );
  });

  it("returns an empty list for empty or malformed content", () => {
    expect(extractArgNames(null)).toEqual([]);
    expect(extractArgNames("plain text")).toEqual([]);
    expect(extractArgNames(42)).toEqual([]);
  });

  it("matches the backend _ARG_REF_INLINE_PATTERN semantics", () => {
    // The backend pattern is \${args.([A-Za-z0-9_.-]+)} — verify the
    // exported pattern source stays in lockstep with it.
    expect(ARG_REF_PATTERN.source).toBe("\\$\\{args\\.([A-Za-z0-9_.-]+)\\}");
    expect(ARG_REF_PATTERN.flags).toContain("g");
  });
});

describe("extractActions", () => {
  it("returns the array itself for the bare-array shape", () => {
    const content = [{ tool_name: "a", arguments: {} }];
    expect(extractActions(content)).toBe(content);
  });

  it("unwraps the {actions} object shape", () => {
    const actions = [{ tool_name: "a", arguments: {} }];
    expect(extractActions({ actions, description: "d" })).toBe(actions);
  });

  it("returns null when neither shape matches", () => {
    expect(extractActions({ steps: [] })).toBeNull();
    expect(extractActions("not json")).toBeNull();
    expect(extractActions(null)).toBeNull();
  });
});

describe("expandDottedArgKeys", () => {
  it("leaves plain keys untouched", () => {
    expect(expandDottedArgKeys({ city: "hz", n: 1 })).toEqual({
      city: "hz",
      n: 1,
    });
  });

  it("expands a dotted key into a nested object", () => {
    expect(expandDottedArgKeys({ "out.dir": "/tmp" })).toEqual({
      out: { dir: "/tmp" },
    });
  });

  it("expands deep keys", () => {
    expect(expandDottedArgKeys({ "a.b.c": 1 })).toEqual({
      a: { b: { c: 1 } },
    });
  });

  it("merges dotted keys sharing a prefix and keeps plain keys", () => {
    expect(
      expandDottedArgKeys({
        "out.dir": "/tmp",
        "out.name": "report",
        plain: "p",
      }),
    ).toEqual({ out: { dir: "/tmp", name: "report" }, plain: "p" });
  });

  it("merges into an existing object value", () => {
    expect(
      expandDottedArgKeys({ out: { keep: true }, "out.dir": "/tmp" }),
    ).toEqual({ out: { keep: true, dir: "/tmp" } });
  });

  it("returns an empty object for empty input", () => {
    expect(expandDottedArgKeys({})).toEqual({});
  });
});

describe("validateBatchContent", () => {
  it("accepts a valid script and normalizes alias keys", () => {
    const result = validateBatchContent({
      actions: [
        { tool_name: "execute_shell_command", arguments: { command: "date" } },
        // Executor aliases: `tool` and `args`.
        { tool: "read_file", args: { path: "/tmp/x" } },
      ],
    });
    expect(result.ok).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.actions).toEqual([
      { tool_name: "execute_shell_command", arguments: { command: "date" } },
      { tool_name: "read_file", arguments: { path: "/tmp/x" } },
    ]);
  });

  it("rejects content that is neither container shape", () => {
    const result = validateBatchContent({ steps: [] });
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([{ code: "invalid_content" }]);
  });

  it("rejects steps that are not objects or miss tool_name", () => {
    const result = validateBatchContent([
      "not an object",
      { arguments: { command: "date" } },
      { tool_name: "  ", arguments: {} },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors.map((e) => [e.code, e.index])).toEqual([
      ["invalid_action", 0],
      ["invalid_action", 1],
      ["invalid_action", 2],
    ]);
  });

  it("rejects arguments that are not an object", () => {
    const result = validateBatchContent([
      { tool_name: "a", arguments: "command string" },
      { tool_name: "b", arguments: ["list"] },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors.map((e) => [e.code, e.index])).toEqual([
      ["invalid_arguments", 0],
      ["invalid_arguments", 1],
    ]);
  });

  it(`rejects more than MAX_BATCH_STEPS (${MAX_BATCH_STEPS}) steps`, () => {
    const actions = Array.from({ length: MAX_BATCH_STEPS + 1 }, (_, i) => ({
      tool_name: "a",
      arguments: { i },
    }));
    const result = validateBatchContent({ actions });
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([
      { code: "too_many_steps", detail: String(MAX_BATCH_STEPS + 1) },
    ]);
  });

  it(`accepts exactly MAX_BATCH_STEPS (${MAX_BATCH_STEPS}) steps`, () => {
    const actions = Array.from({ length: MAX_BATCH_STEPS }, () => ({
      tool_name: "a",
      arguments: {},
    }));
    expect(validateBatchContent(actions).ok).toBe(true);
  });

  it("accepts backward step references", () => {
    const result = validateBatchContent([
      { tool_name: "a", arguments: { command: "date" } },
      { tool_name: "b", arguments: { command: "echo ${steps.0.text}" } },
    ]);
    expect(result.ok).toBe(true);
  });

  it("rejects self step references (a step has no result while running)", () => {
    const result = validateBatchContent([
      { tool_name: "a", arguments: { command: "date" } },
      {
        tool_name: "b",
        arguments: { command: "echo ${steps.1.value}" },
      },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors[0].code).toBe("forward_step_ref");
    expect(result.errors[0].index).toBe(1);
  });

  it("rejects forward and self step references", () => {
    const result = validateBatchContent([
      { tool_name: "a", arguments: { command: "echo ${steps.2.text}" } },
      { tool_name: "b", arguments: {} },
      { tool_name: "c", arguments: { command: "echo ${steps.0.text}" } },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([
      { code: "forward_step_ref", index: 0, detail: "${steps.2}" },
    ]);
  });

  it("checks every goto label is defined", () => {
    const result = validateBatchContent({
      actions: [
        { tool_name: "label", arguments: { name: "start" } },
        { tool_name: "goto", arguments: { label: "start" } },
        { tool_name: "goto", arguments: { label: "missing" } },
      ],
    });
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([
      { code: "unknown_label", index: 2, detail: "missing" },
    ]);
  });

  it("rejects duplicate labels like the backend label map", () => {
    const result = validateBatchContent([
      { tool_name: "label", arguments: { name: "loop" } },
      { tool_name: "label", arguments: { name: "loop" } },
    ]);
    expect(result.ok).toBe(false);
    expect(result.errors).toEqual([
      { code: "duplicate_label", index: 1, detail: "loop" },
    ]);
  });

  it("accepts control-flow loops with set_var, label and goto", () => {
    const result = validateBatchContent({
      actions: [
        { tool_name: "set_var", arguments: { expr: "i=1" } },
        { tool_name: "label", arguments: { name: "next" } },
        {
          tool_name: "execute_shell_command",
          arguments: { command: "echo ${vars.i}" },
        },
        { tool_name: "set_var", arguments: { expr: "i=${vars.i}+1" } },
        {
          tool_name: "goto",
          arguments: { label: "next", condition: "${vars.i}<=3" },
        },
      ],
    });
    expect(result.ok).toBe(true);
    expect(result.argNames).toEqual([]);
  });

  it("reports arg names alongside validation errors", () => {
    const result = validateBatchContent({
      actions: [{ tool_name: "a", arguments: { x: "${args.city}" } }],
    });
    expect(result.ok).toBe(true);
    expect(result.argNames).toEqual(["city"]);
  });
});

describe("summarizeStep", () => {
  it("lists tool arguments as key/value rows in order", () => {
    const summary = summarizeStep({
      tool_name: "read_file",
      arguments: { file_path: "/tmp/a.txt", limit: 10 },
    });

    expect(summary.toolName).toBe("read_file");
    expect(summary.params).toEqual([
      ["file_path", "/tmp/a.txt"],
      ["limit", "10"],
    ]);
  });

  it("stringifies non-string argument values", () => {
    const summary = summarizeStep({
      tool_name: "t",
      arguments: { items: ["a", "b"] },
    });

    expect(summary.params).toEqual([["items", '["a","b"]']]);
  });

  it("shows only the label name for label steps", () => {
    const summary = summarizeStep({
      tool_name: "label",
      arguments: { name: "next" },
    });

    expect(summary.toolName).toBe("label");
    expect(summary.params).toEqual([["name", "next"]]);
  });

  it("shows target and condition for goto steps", () => {
    const summary = summarizeStep({
      tool_name: "goto",
      arguments: { label: "next", condition: "${vars.i}<=3" },
    });

    expect(summary.params).toEqual([
      ["label", "next"],
      ["condition", "${vars.i}<=3"],
    ]);
  });

  it("shows the expression for set_var steps", () => {
    const summary = summarizeStep({
      tool_name: "set_var",
      arguments: { expr: "total = 0" },
    });

    expect(summary.params).toEqual([["expr", "total = 0"]]);
  });

  it("tolerates malformed steps", () => {
    expect(summarizeStep(null).toolName).toBe("?");
    expect(summarizeStep({}).toolName).toBe("?");
    expect(summarizeStep({ tool_name: "x" }).params).toEqual([]);
  });
});
