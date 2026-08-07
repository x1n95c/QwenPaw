import { describe, it, expect, vi } from "vitest";
import { collectJobScripts } from "./saveAsTemplateScripts";

function fakes(names: string[]) {
  return {
    list: vi.fn(async () => names.map((name) => ({ name }))),
    get: vi.fn(async (_jobId: string, name: string) => ({
      content: { actions: [{ tool_name: name }] },
    })),
  };
}

describe("collectJobScripts", () => {
  it("packages every script the job owns even with preprocess off", async () => {
    // The save-side twin of the apply-side bug: gating packaging on
    // `enabled` shipped a template with no scripts at all, so the round
    // trip silently lost them.
    const { list, get } = fakes(["collect", "notify"]);
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: { enabled: false, steps: [] },
      list,
      get,
    });

    expect(result).toMatchObject({ ok: true });
    if (!result.ok) return;
    expect(Object.keys(result.batchFiles).sort()).toEqual([
      "collect.json",
      "notify.json",
    ]);
    expect(JSON.parse(result.batchFiles["collect.json"])).toEqual({
      actions: [{ tool_name: "collect" }],
    });
  });

  it("sets batch_entry for exactly one declared script", async () => {
    const { list, get } = fakes(["collect", "notify"]);
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: { enabled: true, steps: [{ script: "collect" }] },
      list,
      get,
    });
    expect(result).toMatchObject({
      ok: true,
      batchEntry: "batch/collect.json",
    });
  });

  it("omits batch_entry for zero or several declared scripts", async () => {
    const none = await collectJobScripts({
      jobId: "j1",
      preprocess: null,
      ...fakes(["collect"]),
    });
    expect(none).toMatchObject({ ok: true, batchEntry: undefined });

    const several = await collectJobScripts({
      jobId: "j1",
      preprocess: {
        enabled: true,
        steps: [{ script: "collect" }, { script: "notify" }],
      },
      ...fakes(["collect", "notify"]),
    });
    expect(several).toMatchObject({ ok: true, batchEntry: undefined });
  });

  it("omits batch_entry when the declared script is not owned", async () => {
    // The backend rejects a package whose entry points nowhere, so a stale
    // disabled chain must not be allowed to name one.
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: { enabled: false, steps: [{ script: "gone" }] },
      ...fakes(["collect"]),
    });
    expect(result).toMatchObject({ ok: true, batchEntry: undefined });
  });

  it("aborts naming the missing script when the chain is live", async () => {
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: { enabled: true, steps: [{ script: "gone" }] },
      ...fakes(["collect"]),
    });
    expect(result).toEqual({ ok: false, missing: "gone" });
  });

  it("does not abort on a disabled chain naming a deleted script", async () => {
    // Otherwise a job carrying a stale switched-off chain could never be
    // saved as a template again — a new failure mode, for a reference that
    // never runs.
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: { enabled: false, steps: [{ script: "gone" }] },
      ...fakes(["collect"]),
    });
    expect(result).toMatchObject({ ok: true });
    if (!result.ok) return;
    expect(Object.keys(result.batchFiles)).toEqual(["collect.json"]);
  });

  it("never renames: listing names are unique by construction", async () => {
    const { list, get } = fakes(["a", "a-2", "b"]);
    const result = await collectJobScripts({
      jobId: "j1",
      preprocess: null,
      list,
      get,
    });
    expect(result).toMatchObject({ ok: true });
    if (!result.ok) return;
    expect(Object.keys(result.batchFiles).sort()).toEqual([
      "a-2.json",
      "a.json",
      "b.json",
    ]);
  });
});
