import { useState } from "react";
import { Button, Form, Select, Tooltip } from "@agentscope-ai/design";
import { Alert } from "antd";
import { useTranslation } from "react-i18next";
import { MAX_CRON_SKILLS } from "./constants";
import {
  buildSkillOptions,
  findSkillRefs,
  parseSkillOptionValue,
  type SkillRefFormValue,
  type SkillSelectOption,
} from "./skillOptions";
import { useCronSkills } from "./useCronSkills";
import styles from "../index.module.less";

interface SkillPickerProps {
  /** Package this job came from; its skills lead the list. */
  currentTemplate?: string;
  value?: SkillRefFormValue[];
  onChange?: (value: SkillRefFormValue[]) => void;
}

/**
 * The skill picker, bound through ONE Form.Item.
 *
 * A multi-select rather than a row per skill: a skill carries no arguments,
 * cannot be edited from here, and — unlike a preprocess chain, where one
 * script may write a file the next one reads — has no meaningful order. So
 * a row would hold nothing but a dropdown and a delete button, and its
 * ordinal would imply sequencing that does not exist.
 *
 * The Select's values are packed strings; the form value is always an array
 * of `{name, template?}` refs, because that is what the backend persists.
 */
function SkillPicker({ currentTemplate, value, onChange }: SkillPickerProps) {
  const { t } = useTranslation();
  // Reveal the template groups. Kept outside the form value: it is a view
  // state of the picker, not part of the job.
  const [expanded, setExpanded] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const { skills, loading } = useCronSkills();

  const refs = value || [];
  const { values, missing, missingOptions } = findSkillRefs(refs, skills);
  const { groups, templateCount } = buildSkillOptions({
    skills,
    t,
    // A search must reach into the collapsed groups. Without this, typing
    // a bundled skill's name filters the installed list to nothing and
    // renders "no match" while the hit sits one hidden group away.
    expanded: expanded || Boolean(searchValue.trim()),
    selected: values,
    currentTemplate,
    labels: {
      installed: t("cronJobs.skills.groupInstalled"),
      template: t("cronJobs.skills.groupTemplatePrefix"),
    },
  });

  return (
    <>
      <Select
        mode="multiple"
        showSearch
        allowClear
        loading={loading}
        style={{ width: "100%" }}
        placeholder={t("cronJobs.skills.placeholder")}
        // The dangling group goes last and only exists when something is
        // dangling. Its rows are what stop the packed internal key from
        // being rendered as the tag's text.
        options={
          missingOptions.length > 0
            ? [
                ...groups,
                {
                  label: t("cronJobs.skills.groupMissing"),
                  options: missingOptions,
                },
              ]
            : groups
        }
        value={values}
        maxCount={MAX_CRON_SKILLS}
        onChange={(next: string[]) => {
          setSearchValue("");
          // Unrecognised keys are dropped rather than guessed at: every
          // value here was produced by `skillOptionValue`, so anything
          // else is a bug we should not persist a half-parsed ref for.
          onChange?.(
            next
              .map(parseSkillOptionValue)
              .filter((ref): ref is SkillRefFormValue => ref !== null),
          );
        }}
        onSearch={setSearchValue}
        // The search box is uncontrolled — antd clears it itself on select
        // and on close. This mirror only decides whether the template
        // groups are revealed, so it has to reset at the same two moments
        // or the next open starts filtered.
        onOpenChange={(open: boolean) => {
          if (!open) setSearchValue("");
        }}
        // antd infers the callback parameter from `options`, which here is
        // the *group* type — it has no way to say "a leaf of a grouped
        // list". Both callbacks only ever receive leaves.
        filterOption={(input: string, option?: unknown) =>
          (
            (option as SkillSelectOption | undefined)?.searchText || ""
          ).includes(input.toLowerCase())
        }
        // The description belongs on hover, not inline: the rows stay
        // scannable, and the option's own `title` is blanked so this is the
        // only tooltip that fires.
        optionRender={(option: { data: unknown }) => {
          const data = option.data as SkillSelectOption;
          return (
            <Tooltip
              title={data.tooltip}
              placement="right"
              mouseEnterDelay={0.4}
            >
              <div>{data.label}</div>
            </Tooltip>
          );
        }}
        popupRender={(menu: React.ReactNode) => (
          <>
            {menu}
            {/* preventDefault on mousedown, or the Select blurs and the
                dropdown closes before onClick ever fires. */}
            <div
              className={styles.preprocessScriptExpander}
              onMouseDown={(event) => event.preventDefault()}
            >
              <Button
                type="link"
                size="small"
                onClick={() => setExpanded((prev) => !prev)}
              >
                {expanded
                  ? t("cronJobs.skills.groupTemplatesHide")
                  : t("cronJobs.skills.groupTemplates", {
                      count: templateCount,
                    })}
              </Button>
            </div>
          </>
        )}
        // A workspace with no skills of its own would otherwise render
        // antd's bare "no data" above the expander, hiding that there is
        // more behind it.
        notFoundContent={
          <div className={styles.preprocessScriptEmpty}>
            {t("cronJobs.skills.noMatch")}
          </div>
        }
      />

      {missing.length > 0 && !loading ? (
        // Kept, not dropped: a template package may simply not be
        // installed here, and the job still runs — the agent is told the
        // instructions were unavailable rather than left to invent them.
        <Alert
          type="warning"
          showIcon
          message={t("cronJobs.skills.missing", {
            names: missing.join(", "),
          })}
        />
      ) : null}
    </>
  );
}

/**
 * "Use skill" block in the job drawer, immediately above the preprocess
 * block: the visual order mirrors the prompt order the executor builds —
 * skill instructions, then the data they are applied to, then the request.
 *
 * Agent tasks only. A text task never runs a model, so there is no prompt
 * for a skill to reach; the field is hidden rather than shown as a control
 * that quietly does nothing. The stored value is left alone, so toggling
 * the task type back does not lose the selection.
 */
export function SkillSection() {
  const { t } = useTranslation();

  return (
    <Form.Item
      noStyle
      shouldUpdate={(prev, cur) =>
        prev.task_type !== cur.task_type ||
        prev.meta?.from_template !== cur.meta?.from_template
      }
    >
      {({ getFieldValue }) =>
        getFieldValue("task_type") === "agent" ? (
          <Form.Item
            name="skills"
            label={t("cronJobs.skills.label")}
            tooltip={t("cronJobs.skills.tooltip")}
          >
            {/* Provenance, not a setting: `meta.from_template` is written
                once when a template is applied and only decides which group
                leads the list. */}
            <SkillPicker
              currentTemplate={getFieldValue(["meta", "from_template"])}
            />
          </Form.Item>
        ) : null
      }
    </Form.Item>
  );
}
