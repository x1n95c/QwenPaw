import { describe, it, expect } from "vitest";
import { MAX_CRON_SKILLS } from "./constants";
import { resolveTemplateSkills } from "./templateSkills";

const TEMPLATE = {
  packageName: "workspace-usage",
  skills: ["disk-usage-advisor", "extra-helper"],
};

describe("resolveTemplateSkills", () => {
  it("prefers what the package declares", () => {
    // A package may bundle three skills and mean for the job to reference
    // only one; that intent cannot be recovered from the directory listing.
    const refs = resolveTemplateSkills(
      { skills: [{ name: "disk-usage-advisor", template: "workspace-usage" }] },
      TEMPLATE,
    );

    expect(refs).toEqual([
      { name: "disk-usage-advisor", template: "workspace-usage" },
    ]);
  });

  it("keeps a declared ref pointing at another package", () => {
    // Nothing requires a template to reference only its own skills.
    const refs = resolveTemplateSkills(
      { skills: [{ name: "writer", template: "other-pkg" }] },
      TEMPLATE,
    );

    expect(refs).toEqual([{ name: "writer", template: "other-pkg" }]);
  });

  it("falls back to every bundled skill", () => {
    // For imported third-party packages: `buildCreateTemplateRequest` never
    // packages skills, so no user-saved template has a `skills/` directory.
    expect(resolveTemplateSkills({}, TEMPLATE)).toEqual([
      { name: "disk-usage-advisor", template: "workspace-usage" },
      { name: "extra-helper", template: "workspace-usage" },
    ]);
  });

  it("qualifies the fallback refs with the package that ships them", () => {
    // Without `template` the ref would resolve against the workspace's
    // installed skills, which is exactly what this change stopped needing.
    const refs = resolveTemplateSkills({}, TEMPLATE);

    expect(refs.every((ref) => ref.template === "workspace-usage")).toBe(true);
  });

  it("is empty for a package that bundles nothing", () => {
    expect(
      resolveTemplateSkills({}, { packageName: "plain", skills: [] }),
    ).toEqual([]);
  });

  it("is empty with no template at all", () => {
    // The drawer's "new job" path passes no template.
    expect(resolveTemplateSkills({})).toEqual([]);
  });

  it("normalizes a hand-edited declaration", () => {
    const refs = resolveTemplateSkills(
      {
        skills: [
          { name: "  a  " },
          { name: "" },
          { name: "a" },
          "not-an-object",
        ],
      },
      TEMPLATE,
    );

    expect(refs).toEqual([{ name: "a" }]);
  });

  it("falls back when the declaration normalizes to nothing", () => {
    // An all-junk `skills` key must not silently disable the bundled
    // skills the package's own prompt is written against.
    expect(resolveTemplateSkills({ skills: [{ name: "" }] }, TEMPLATE)).toEqual(
      [
        { name: "disk-usage-advisor", template: "workspace-usage" },
        { name: "extra-helper", template: "workspace-usage" },
      ],
    );
  });

  it("caps the fallback at MAX_CRON_SKILLS", () => {
    const many = Array.from({ length: MAX_CRON_SKILLS + 3 }, (_, i) => `s${i}`);

    expect(
      resolveTemplateSkills({}, { packageName: "big", skills: many }),
    ).toHaveLength(MAX_CRON_SKILLS);
  });

  it("ignores a non-array skills key", () => {
    expect(resolveTemplateSkills({ skills: "advisor" }, TEMPLATE)).toEqual([
      { name: "disk-usage-advisor", template: "workspace-usage" },
      { name: "extra-helper", template: "workspace-usage" },
    ]);
  });
});
