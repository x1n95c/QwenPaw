import { Collapse } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import { summarizeStep } from "./batchValidation";
import styles from "../index.module.less";

interface BatchStepPreviewProps {
  /**
   * The actions to render. Callers pass `ToolBatchInfo.preview_actions`,
   * which the backend already caps — the extra `slice` here is only a
   * guard so this component's own contract holds for any input.
   */
  actions: unknown[];
  /** Total steps in the script, used for the "N more" line. */
  actionCount: number;
  /** Header text; differs between the job form and the script manager. */
  title: string;
  defaultExpanded?: boolean;
}

/**
 * Number of steps rendered. Mirrors `PREVIEW_ACTION_LIMIT` in
 * `app/tool_batches/store.py`, which is what bounds the payload; this
 * constant only documents the rendered row count for tests.
 */
export const BATCH_PREVIEW_STEP_LIMIT = 2;

/**
 * Collapsible read-only preview of a batch script's leading steps.
 *
 * Shared by the job form (what will run before the task body) and the
 * script manager (what a stored script does), so both describe a script
 * the same way. Deliberately shows only the first couple of steps: this
 * is an "is this the script I meant" check, not a program listing — the
 * editor is one click away for the full JSON.
 */
export function BatchStepPreview({
  actions,
  actionCount,
  title,
  defaultExpanded = false,
}: BatchStepPreviewProps) {
  const { t } = useTranslation();
  const shown = actions.slice(0, BATCH_PREVIEW_STEP_LIMIT);
  if (shown.length === 0) return null;

  const remaining = actionCount - shown.length;

  return (
    <Collapse
      ghost
      className={styles.batchStepPreview}
      defaultActiveKey={defaultExpanded ? ["steps"] : undefined}
      items={[
        {
          key: "steps",
          label: <span className={styles.batchStepPreviewTitle}>{title}</span>,
          children: (
            <div className={styles.batchStepList}>
              {shown.map((step, index) => {
                const summary = summarizeStep(step);
                return (
                  <div key={index} className={styles.batchStepRow}>
                    <span className={styles.batchStepIndex}>{index + 1}</span>
                    <div className={styles.batchStepBody}>
                      <span className={styles.batchStepTool}>
                        {summary.toolName}
                      </span>
                      {summary.params.map(([key, value]) => (
                        <div key={key} className={styles.batchStepParam}>
                          <span className={styles.batchStepParamKey}>
                            {key}
                          </span>
                          <span
                            className={styles.batchStepParamValue}
                            title={value}
                          >
                            {value}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {remaining > 0 ? (
                <div className={styles.batchStepMore}>
                  {t("cronJobs.preprocess.stepsMore", { count: remaining })}
                </div>
              ) : null}
            </div>
          ),
        },
      ]}
    />
  );
}
