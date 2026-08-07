import { describe, it, expect } from "vitest";
import type { CronSkillInfo } from "../../../../api/types";
import {
  buildSkillOptions,
  findSkillRefs,
  parseSkillOptionValue,
  skillOptionValue,
} from "./skillOptions";

/** `t` echoing the key back, which is what a missing translation does. */
const t = (key: string) => key;

function installed(name: string, description = ""): CronSkillInfo {
  return { name, source: "workspace", template: "", description };
}

function bundled(
  name: string,
  template: string,
  extra: Partial<CronSkillInfo> = {},
): CronSkillInfo {
  return { name, source: "template", template, ...extra };
}

const LABELS = { installed: "已安装", template: "任务模板" };

describe("skillOptionValue / parseSkillOptionValue", () => {
  it("round-trips an installed skill", () => {
    const ref = { name: "advisor" };
    expect(parseSkillOptionValue(skillOptionValue(ref))).toEqual(ref);
  });

  it("round-trips a template-bundled skill", () => {
    const ref = { name: "advisor", template: "workspace-usage" };
    expect(parseSkillOptionValue(skillOptionValue(ref))).toEqual(ref);
  });

  it("keeps the two sources distinct for the same name", () => {
    // They can coexist in one dropdown, so a shared key would make one
    // shadow the other and the wrong ref would be stored.
    expect(skillOptionValue({ name: "a" })).not.toBe(
      skillOptionValue({ name: "a", template: "pkg" }),
    );
  });

  it("survives a skill name containing a slash-like tail", () => {
    // The package name cannot contain `/`, so the first separator is the
    // boundary and everything after it is the skill name.
    expect(parseSkillOptionValue("tpl:pkg/a/b")).toEqual({
      name: "a/b",
      template: "pkg",
    });
  });

  it("rejects anything it did not produce", () => {
    for (const value of ["", "advisor", "ws:", "tpl:", "tpl:pkg", "tpl:/a"]) {
      expect(parseSkillOptionValue(value)).toBeNull();
    }
  });
});

describe("buildSkillOptions", () => {
  it("shows installed skills without expanding", () => {
    const { groups } = buildSkillOptions({
      skills: [installed("advisor"), bundled("bundled", "pkg")],
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("已安装");
    expect(groups[0].options.map((o) => o.label)).toEqual(["advisor"]);
  });

  it("reveals one group per template when expanded", () => {
    const { groups } = buildSkillOptions({
      skills: [
        installed("advisor"),
        bundled("a", "pkg-one", { template_title: "One" }),
        bundled("b", "pkg-two", { template_title: "Two" }),
        bundled("c", "pkg-one", { template_title: "One" }),
      ],
      t,
      expanded: true,
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual([
      "已安装",
      "任务模板 · One",
      "任务模板 · Two",
    ]);
    expect(groups[1].options.map((o) => o.label)).toEqual(["a", "c"]);
  });

  it("counts every bundled skill even while collapsed", () => {
    // The count is what the expander button offers, so it has to be the
    // total behind it rather than what is currently rendered.
    const { templateCount } = buildSkillOptions({
      skills: [installed("x"), bundled("a", "p"), bundled("b", "p")],
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(templateCount).toBe(2);
  });

  it("omits the installed group when there are none", () => {
    const { groups } = buildSkillOptions({
      skills: [bundled("a", "p")],
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(groups).toEqual([]);
  });

  it("resolves a package title i18n-key first", () => {
    const withKey = buildSkillOptions({
      skills: [
        bundled("a", "p", {
          template_title: "Literal",
          template_title_key: "cronJobs.templates.x.title",
        }),
      ],
      t: (key) => (key === "cronJobs.templates.x.title" ? "译后标题" : key),
      expanded: true,
      labels: LABELS,
    });

    expect(withKey.groups[0].label).toBe("任务模板 · 译后标题");
  });

  it("falls back to the literal title when the key does not resolve", () => {
    // `t` echoing the key back must not put `cronJobs.templates.x.title`
    // on screen.
    const { groups } = buildSkillOptions({
      skills: [
        bundled("a", "p", {
          template_title: "Literal",
          template_title_key: "cronJobs.templates.x.title",
        }),
      ],
      t,
      expanded: true,
      labels: LABELS,
    });

    expect(groups[0].label).toBe("任务模板 · Literal");
  });

  describe("searchText", () => {
    const optionFor = (skill: CronSkillInfo) =>
      buildSkillOptions({
        skills: [skill],
        t,
        expanded: true,
        labels: LABELS,
      }).groups[0].options[0];

    it("matches the frontmatter title, which is not the label", () => {
      const option = optionFor(
        bundled("disk-usage-advisor", "p", {
          display_name: "Disk Usage Advisor",
        }),
      );

      expect(option.label).toBe("disk-usage-advisor");
      expect(option.searchText).toContain("disk usage advisor");
    });

    it("matches the package title and the description", () => {
      const option = optionFor(
        bundled("a", "p", {
          template_title: "Space Check",
          description: "Advises on disk usage",
        }),
      );

      expect(option.searchText).toContain("space check");
      expect(option.searchText).toContain("advises on disk usage");
    });

    it("is lowercased, since filterOption lowercases its input", () => {
      const option = optionFor(installed("Advisor", "MIXED Case"));

      expect(option.searchText).toBe(option.searchText.toLowerCase());
    });
  });

  describe("tooltip", () => {
    it("prefers the description", () => {
      const { groups } = buildSkillOptions({
        skills: [installed("a", "what it does")],
        t,
        expanded: false,
        labels: LABELS,
      });

      expect(groups[0].options[0].tooltip).toBe("what it does");
    });

    it("falls back to the qualified name for a bundled skill", () => {
      // Hovering an undocumented skill should still say which package it
      // came from — the group header is not visible on a selected tag.
      const { groups } = buildSkillOptions({
        skills: [bundled("a", "p", { template_title: "Space Check" })],
        t,
        expanded: true,
        labels: LABELS,
      });

      expect(groups[0].options[0].tooltip).toBe("Space Check/a");
    });

    it("falls back to the bare name for an installed skill", () => {
      const { groups } = buildSkillOptions({
        skills: [installed("a")],
        t,
        expanded: false,
        labels: LABELS,
      });

      expect(groups[0].options[0].tooltip).toBe("a");
    });
  });

  it("blanks the option title so only our Tooltip fires", () => {
    const { groups } = buildSkillOptions({
      skills: [installed("a", "desc")],
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(groups[0].options[0].title).toBe("");
  });
});

describe("findSkillRefs", () => {
  it("maps stored refs onto Select values", () => {
    const { values, missing } = findSkillRefs(
      [{ name: "advisor" }, { name: "bundled", template: "pkg" }],
      [installed("advisor"), bundled("bundled", "pkg")],
    );

    expect(values).toEqual(["ws:advisor", "tpl:pkg/bundled"]);
    expect(missing).toEqual([]);
  });

  it("still emits a value for a ref nothing can resolve", () => {
    // Losing it would mean the field silently stops showing part of what
    // the job actually runs with.
    const { values, missing } = findSkillRefs(
      [{ name: "gone" }, { name: "also-gone", template: "absent-pkg" }],
      [installed("advisor")],
    );

    expect(values).toEqual(["ws:gone", "tpl:absent-pkg/also-gone"]);
    expect(missing).toEqual(["gone", "absent-pkg/also-gone"]);
  });

  it("leaves the placeholder enabled so its tag stays removable", () => {
    // antd makes the tag of a *disabled* option non-closable, which would
    // leave a stale reference impossible to clear from the form.
    const { missingOptions } = findSkillRefs([{ name: "gone" }], []);

    expect(missingOptions[0]).not.toHaveProperty("disabled");
  });

  it("gives a dangling ref a readable placeholder option", () => {
    // Without one, rc-select prints the raw value — and the raw value is a
    // packed internal key, so the field would show the user
    // `tpl:absent-pkg/also-gone`.
    const { missingOptions } = findSkillRefs(
      [{ name: "also-gone", template: "absent-pkg" }],
      [],
    );

    expect(missingOptions).toEqual([
      {
        value: "tpl:absent-pkg/also-gone",
        label: "absent-pkg/also-gone",
        title: "",
        searchText: "absent-pkg/also-gone",
        tooltip: "absent-pkg/also-gone",
        source: "template",
      },
    ]);
  });

  it("emits no placeholder options when everything resolves", () => {
    const { missingOptions } = findSkillRefs(
      [{ name: "advisor" }],
      [installed("advisor")],
    );

    expect(missingOptions).toEqual([]);
  });

  it("does not confuse the two sources", () => {
    // An installed `a` must not satisfy a ref for `pkg`'s `a`.
    const { missing } = findSkillRefs(
      [{ name: "a", template: "pkg" }],
      [installed("a")],
    );

    expect(missing).toEqual(["pkg/a"]);
  });

  it("reports nothing for a job with no skills", () => {
    expect(findSkillRefs([], [installed("a")])).toEqual({
      values: [],
      missing: [],
      missingOptions: [],
    });
  });
});

describe("a selected bundled skill stays visible while collapsed", () => {
  it("emits its group even without expanding", () => {
    // rc-select takes a tag's text from the matching option. Hiding the
    // option leaves the tag showing whatever label it cached first, which
    // for a job loaded before the skill list arrived is the qualified
    // fallback rather than the skill's own name.
    const { groups } = buildSkillOptions({
      skills: [
        bundled("advisor", "workspace-usage", { template_title: "空间" }),
      ],
      t,
      expanded: false,
      selected: ["tpl:workspace-usage/advisor"],
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual(["任务模板 · 空间"]);
    expect(groups[0].options[0].label).toBe("advisor");
  });

  it("still hides the ones that are not selected", () => {
    const { groups } = buildSkillOptions({
      skills: [
        bundled("picked", "pkg", { template_title: "P" }),
        bundled("not-picked", "pkg", { template_title: "P" }),
      ],
      t,
      expanded: false,
      selected: ["tpl:pkg/picked"],
      labels: LABELS,
    });

    expect(groups[0].options.map((o) => o.label)).toEqual(["picked"]);
  });

  it("counts every bundled skill regardless of what is shown", () => {
    const { templateCount } = buildSkillOptions({
      skills: [bundled("a", "p"), bundled("b", "p")],
      t,
      expanded: false,
      selected: ["tpl:p/a"],
      labels: LABELS,
    });

    expect(templateCount).toBe(2);
  });

  it("shows everything once expanded, selected or not", () => {
    const { groups } = buildSkillOptions({
      skills: [bundled("a", "p"), bundled("b", "p")],
      t,
      expanded: true,
      selected: ["tpl:p/a"],
      labels: LABELS,
    });

    expect(groups[0].options.map((o) => o.label)).toEqual(["a", "b"]);
  });

  it("defaults `selected` to nothing, so old callers behave as before", () => {
    const { groups } = buildSkillOptions({
      skills: [bundled("a", "p")],
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(groups).toEqual([]);
  });
});

describe("three tiers: current template, installed, other templates", () => {
  const SKILLS = [
    installed("pdf-toolkit"),
    bundled("weather-report", "weather-report", { template_title: "天气" }),
    bundled("advisor", "workspace-usage", { template_title: "空间" }),
    bundled("writer", "daily-brief", { template_title: "简报" }),
  ];

  it("leads with the package the job came from", () => {
    // Applying a template is the one moment when a particular package's
    // skills are obviously the relevant ones; making the user expand to
    // reach them would be backwards.
    const { groups } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: false,
      currentTemplate: "workspace-usage",
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual(["任务模板 · 空间", "已安装"]);
    expect(groups[0].options.map((o) => o.label)).toEqual(["advisor"]);
  });

  it("keeps the other packages behind the expander", () => {
    const { groups } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: true,
      currentTemplate: "workspace-usage",
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual([
      "任务模板 · 空间",
      "已安装",
      "任务模板 · 天气",
      "任务模板 · 简报",
    ]);
  });

  it("counts only what the expander actually reveals", () => {
    // The current package's skills are already on screen, so counting them
    // would overstate what is hidden.
    const { templateCount } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: false,
      currentTemplate: "workspace-usage",
      labels: LABELS,
    });

    expect(templateCount).toBe(2);
  });

  it("falls back to installed-first when there is no origin package", () => {
    // A job written from scratch, or one created before provenance was
    // recorded.
    const { groups } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: false,
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual(["已安装"]);
  });

  it("ignores an origin package that ships no skills", () => {
    const { groups, templateCount } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: false,
      currentTemplate: "diet-plan",
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual(["已安装"]);
    expect(templateCount).toBe(3);
  });

  it("does not duplicate the origin package into the hidden tier", () => {
    const { groups } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: true,
      currentTemplate: "weather-report",
      labels: LABELS,
    });

    const weather = groups.filter((g) => g.label === "任务模板 · 天气");
    expect(weather).toHaveLength(1);
  });

  it("still surfaces a selected skill from another package while collapsed", () => {
    const { groups } = buildSkillOptions({
      skills: SKILLS,
      t,
      expanded: false,
      currentTemplate: "workspace-usage",
      selected: ["tpl:daily-brief/writer"],
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual([
      "任务模板 · 空间",
      "已安装",
      "任务模板 · 简报",
    ]);
  });

  it("shows the origin tier even with no installed skills", () => {
    const { groups } = buildSkillOptions({
      skills: [
        bundled("advisor", "workspace-usage", { template_title: "空间" }),
      ],
      t,
      expanded: false,
      currentTemplate: "workspace-usage",
      labels: LABELS,
    });

    expect(groups.map((g) => g.label)).toEqual(["任务模板 · 空间"]);
  });
});
