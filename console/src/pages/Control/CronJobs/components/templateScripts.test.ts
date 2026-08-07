import { describe, it, expect, vi } from "vitest";
import { copyTemplateScripts } from "./templateScripts";
import { remapPreprocessScripts } from "./packageTemplates";

/** A copy that always succeeds, taking the requested basename verbatim. */
function fakeCopy() {
  return vi.fn(async ({ file }: { file: string }) => ({
    name: file
      .split("/")
      .pop()!
      .replace(/\.json$/i, ""),
  }));
}

describe("copyTemplateScripts", () => {
  it("copies every bundled script, not only the declared ones", async () => {
    // The workspace-usage shape: two platform variants the *agent* picks
    // between at run time, so the package declares no preprocess at all.
    // Copying only declared scripts left the new job's list empty and the
    // user had to go browsing other packages to fetch them back.
    const copy = fakeCopy();
    const { landed, failed } = await copyTemplateScripts({
      template: {
        packageName: "workspace-usage",
        batchFiles: ["batch/scan-unix.json", "batch/scan-windows.json"],
      },
      declared: [],
      copy,
    });

    expect(copy).toHaveBeenCalledTimes(2);
    expect(landed).toEqual({
      "scan-unix": "scan-unix",
      "scan-windows": "scan-windows",
    });
    expect(failed).toEqual({ declared: [], extra: [] });
  });

  it("gives a declared script the basename ahead of a same-named extra", async () => {
    // `batch_files` arrives sorted by full path, so the nested copy comes
    // first. Copying in that order would map the step onto the legacy file
    // and land the real one as `weather-2` — the job would run the wrong
    // script and nothing would say so.
    const taken = new Set<string>();
    const copy = vi.fn(async ({ file }: { file: string }) => {
      const stem = file
        .split("/")
        .pop()!
        .replace(/\.json$/i, "");
      let name = stem;
      for (let n = 2; taken.has(name); n++) name = `${stem}-${n}`;
      taken.add(name);
      return { name };
    });

    const { landed } = await copyTemplateScripts({
      template: {
        packageName: "p",
        batchFiles: ["batch/legacy/weather.json", "batch/weather.json"],
      },
      declared: ["weather"],
      copy,
    });

    expect(copy.mock.calls.map((call) => call[0].file)).toEqual([
      "batch/weather.json",
      "batch/legacy/weather.json",
    ]);
    expect(landed).toEqual({ weather: "weather" });
    expect(taken).toEqual(new Set(["weather", "weather-2"]));
  });

  it("remaps the step when the server renames the copy", async () => {
    const copy = vi.fn(async () => ({ name: "weather-2" }));
    const { landed } = await copyTemplateScripts({
      template: { packageName: "p", batchFiles: ["batch/weather.json"] },
      declared: ["weather"],
      copy,
    });

    const values = remapPreprocessScripts(
      { preprocess: { enabled: true, steps: [{ script: "weather" }] } },
      landed,
    );
    expect(values).toEqual({
      preprocess: { enabled: true, steps: [{ script: "weather-2" }] },
    });
  });

  it("keeps going when an extra fails, and reports it apart", async () => {
    const copy = vi.fn(async ({ file }: { file: string }) => {
      if (file === "batch/broken.json") throw new Error("nope");
      return {
        name: file
          .split("/")
          .pop()!
          .replace(/\.json$/i, ""),
      };
    });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { landed, failed } = await copyTemplateScripts({
      template: {
        packageName: "p",
        batchFiles: ["batch/broken.json", "batch/fine.json"],
      },
      declared: [],
      copy,
    });

    expect(landed).toEqual({ fine: "fine" });
    // Split by consequence: a declared failure breaks the chain, an extra
    // one does not, and the user is told different things.
    expect(failed).toEqual({ declared: [], extra: ["broken"] });
  });

  it("reports a failed declared script as declared", async () => {
    const copy = vi.fn(async () => {
      throw new Error("nope");
    });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { landed, failed } = await copyTemplateScripts({
      template: { packageName: "p", batchFiles: ["batch/weather.json"] },
      declared: ["weather"],
      copy,
    });

    expect(landed).toEqual({});
    expect(failed).toEqual({ declared: ["weather"], extra: [] });
  });

  it("makes no request for a package that bundles nothing", async () => {
    const copy = fakeCopy();
    const { landed } = await copyTemplateScripts({
      template: { packageName: "p", batchFiles: [] },
      declared: [],
      copy,
    });

    expect(copy).not.toHaveBeenCalled();
    const values = { preprocess: { steps: [{ script: "x" }] } };
    expect(remapPreprocessScripts(values, landed)).toEqual(values);
  });
});
