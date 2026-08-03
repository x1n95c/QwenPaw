import { describe, expect, it } from "vitest";
import {
  templateDescription,
  templateFrequency,
  templateTitle,
} from "./templates";

/**
 * i18n stub: knows two keys, echoes anything else back the way i18next
 * does for a missing translation. That echo behaviour is the whole reason
 * the resolvers fall through to the literal.
 */
const DICT: Record<string, string> = {
  "cronJobs.templates.x.title": "科技早报",
  "cronJobs.templates.x.description": "每天汇总科技新闻",
};
const t = (key: string) => DICT[key] ?? key;

describe("templateTitle", () => {
  it("prefers the i18n key when it resolves", () => {
    expect(
      templateTitle(
        {
          titleKey: "cronJobs.templates.x.title",
          title: "literal fallback",
          packageName: "pkg",
        },
        t,
      ),
    ).toBe("科技早报");
  });

  it("falls back to the literal when the key has no translation", () => {
    expect(
      templateTitle(
        {
          titleKey: "cronJobs.templates.missing.title",
          title: "literal fallback",
          packageName: "pkg",
        },
        t,
      ),
    ).toBe("literal fallback");
  });

  it("uses the literal when no key is set (user-authored package)", () => {
    expect(
      templateTitle({ titleKey: "", title: "我的模板", packageName: "pkg" }, t),
    ).toBe("我的模板");
  });

  it("falls back to the package name when nothing else is available", () => {
    expect(
      templateTitle({ titleKey: "", title: "", packageName: "my-pkg" }, t),
    ).toBe("my-pkg");
  });
});

describe("templateDescription", () => {
  it("prefers the i18n key", () => {
    expect(
      templateDescription(
        {
          descriptionKey: "cronJobs.templates.x.description",
          description: "en fallback",
        },
        t,
      ),
    ).toBe("每天汇总科技新闻");
  });

  it("falls back to the literal on a missing translation", () => {
    expect(
      templateDescription(
        {
          descriptionKey: "cronJobs.templates.gone.description",
          description: "en fallback",
        },
        t,
      ),
    ).toBe("en fallback");
  });

  it("returns empty string when neither is set", () => {
    expect(
      templateDescription({ descriptionKey: "", description: "" }, t),
    ).toBe("");
  });
});

describe("templateFrequency", () => {
  it("falls back to the literal", () => {
    expect(
      templateFrequency({ frequencyKey: "nope", frequency: "每天 09:00" }, t),
    ).toBe("每天 09:00");
  });

  it("returns empty string when neither is set", () => {
    expect(templateFrequency({ frequencyKey: "", frequency: "" }, t)).toBe("");
  });
});
