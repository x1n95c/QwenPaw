import { useEffect, useState } from "react";
import { Input, Modal } from "@agentscope-ai/design";
import { Alert } from "antd";
import Editor from "@monaco-editor/react";
import { useTranslation } from "react-i18next";
import type { ToolBatchDetail } from "../../../../api/types";
import {
  MAX_BATCH_STEPS,
  validateBatchContent,
  type BatchValidationError,
} from "./batchValidation";
import type { UseToolBatchesResult } from "./useToolBatches";
import styles from "../index.module.less";

export interface BatchEditorTarget {
  mode: "create" | "edit";
  name?: string;
  /**
   * Create mode only: start from this content instead of the example.
   * Used when copying a foreign script in, where the user is editing a
   * real script rather than authoring a new one.
   */
  initialContent?: unknown;
  /** Create mode only: pre-fill the name field. */
  suggestedName?: string;
}

interface BatchEditorModalProps {
  open: boolean;
  /** `null` keeps the modal mounted but idle. */
  target: BatchEditorTarget | null;
  toolBatches: UseToolBatchesResult;
  onCancel: () => void;
  /** Fired after a successful save with the saved script name. */
  onSaved: (name: string) => void;
}

const NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/;

const MONACO_OPTIONS = {
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  fontSize: 12,
  tabSize: 2,
  automaticLayout: true,
};

/**
 * Starter script for newly created batches: a single
 * `execute_shell_command` step with one `${args.*}` placeholder, so the
 * args form below the job's script select has something to show.
 */
const NEW_BATCH_EXAMPLE = {
  description: "",
  actions: [
    {
      tool_name: "execute_shell_command",
      arguments: {
        command: "echo hello ${args.name}",
      },
    },
  ],
};

const VALIDATION_KEY: Record<BatchValidationError["code"], string> = {
  invalid_content: "cronJobs.toolBatches.validation.invalidContent",
  invalid_action: "cronJobs.toolBatches.validation.invalidAction",
  invalid_arguments: "cronJobs.toolBatches.validation.invalidArguments",
  too_many_steps: "cronJobs.toolBatches.validation.tooManySteps",
  forward_step_ref: "cronJobs.toolBatches.validation.forwardStepRef",
  unknown_label: "cronJobs.toolBatches.validation.unknownLabel",
  duplicate_label: "cronJobs.toolBatches.validation.duplicateLabel",
};

function validationParams(
  error: BatchValidationError,
): Record<string, string | number> {
  const step = error.index !== undefined ? error.index + 1 : 1;
  switch (error.code) {
    case "too_many_steps":
      return { count: Number(error.detail) || 0, max: MAX_BATCH_STEPS };
    case "forward_step_ref":
      return { step, ref: error.detail ?? "" };
    case "unknown_label":
    case "duplicate_label":
      return { step, label: error.detail ?? "" };
    case "invalid_action":
    case "invalid_arguments":
      return { step };
    default:
      return {};
  }
}

/**
 * JSON-only editor for one batch script.
 *
 * The graphical step view was dropped on purpose: batch JSON is the
 * source of truth, and complex scripts are better authored by the agent
 * in the chat (the modal says so). Create mode pre-fills a runnable
 * `execute_shell_command` example.
 */
export function BatchEditorModal({
  open,
  target,
  toolBatches,
  onCancel,
  onSaved,
}: BatchEditorModalProps) {
  const { t } = useTranslation();
  const { createBatch, updateBatch, getBatch, busy } = toolBatches;
  const [name, setName] = useState("");
  const [contentText, setContentText] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [contentIssues, setContentIssues] = useState<BatchValidationError[]>(
    [],
  );
  const [contentError, setContentError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ToolBatchDetail | null>(null);

  const mode = target?.mode || "create";
  const editName = target?.name || "";

  useEffect(() => {
    if (!open || !target) {
      setName("");
      setContentText("");
      setNameError(null);
      setContentIssues([]);
      setContentError(null);
      setDetail(null);
      return;
    }
    if (target.mode === "create") {
      setName(target.suggestedName || "");
      setContentText(
        JSON.stringify(
          target.initialContent === undefined
            ? NEW_BATCH_EXAMPLE
            : target.initialContent,
          null,
          2,
        ),
      );
      setNameError(null);
      setContentIssues([]);
      setContentError(null);
      setDetail(null);
      return;
    }
    // Edit mode: fetch the stored script (list rows have no content).
    // Through the hook rather than `api` directly, so the job scoping lives
    // in one place — a script only exists inside its own job now.
    let cancelled = false;
    getBatch(target.name || "")
      .then((d) => {
        if (!cancelled && d) {
          setDetail(d);
          setName(d.name);
          setContentText(JSON.stringify(d.content, null, 2));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setContentError(t("cronJobs.toolBatches.detailFailed"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, target, t, getBatch]);

  const handleNameChange = (value: string) => {
    setName(value);
    if (nameError) setNameError(null);
  };

  const handleContentChange = (value: string | undefined) => {
    setContentText(value ?? "");
    if (contentIssues.length) setContentIssues([]);
    if (contentError) setContentError(null);
  };

  const handleSave = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(t("cronJobs.toolBatches.nameRequired"));
      return;
    }
    if (!NAME_PATTERN.test(trimmedName)) {
      setNameError(t("cronJobs.toolBatches.nameInvalid"));
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(contentText);
    } catch (error) {
      setContentError(
        error instanceof SyntaxError
          ? t("cronJobs.toolBatches.jsonParseError", {
              message: error.message,
            })
          : t("cronJobs.toolBatches.jsonInvalid"),
      );
      return;
    }

    const result = validateBatchContent(parsed);
    if (!result.ok) {
      setContentIssues(result.errors);
      return;
    }

    const ok =
      mode === "create"
        ? await createBatch({ name: trimmedName, content: parsed })
        : await updateBatch(editName, { content: parsed });
    if (ok) onSaved(mode === "create" ? trimmedName : editName);
  };

  const title =
    mode === "create"
      ? t("cronJobs.toolBatches.createTitle")
      : `${t("cronJobs.toolBatches.editTitle")} · ${editName}`;

  // Edit mode fetches the stored script, so the editor is briefly empty.
  // Saving then would overwrite the real script with a parse error.
  const loadingContent =
    mode === "edit" && detail === null && contentError === null;

  return (
    <Modal
      visible={open}
      title={title}
      width={720}
      okText={t("cronJobs.toolBatches.save")}
      cancelText={t("common.cancel")}
      confirmLoading={busy}
      okButtonProps={{
        disabled: loadingContent || !contentText.trim(),
      }}
      onOk={() => void handleSave()}
      onCancel={onCancel}
      // The persistent "let the agent write it" pointer belongs in the
      // footer's info slot, not stacked as a third Alert above the editor.
      // It only renders when `footer` is left to the default, which is why
      // this modal uses onOk/okText instead of a custom footer.
      info={t("cronJobs.toolBatches.editorAgentHint")}
    >
      <div className={styles.batchEditor}>
        {mode === "create" ? (
          // A plain labelled row rather than an antd Form.Item: there is no
          // <Form> here (the fields are local state), and a Form.Item
          // outside one falls back to horizontal layout, which puts the
          // label in a left column while every other form in the console
          // labels above the field.
          <div className={styles.batchEditorField}>
            <label className={styles.batchEditorLabel} htmlFor="batch-name">
              {t("cronJobs.toolBatches.nameLabel")}
            </label>
            <Input
              id="batch-name"
              value={name}
              status={nameError ? "error" : undefined}
              onChange={(event) => handleNameChange(event.target.value)}
              placeholder={t("cronJobs.toolBatches.namePlaceholder")}
            />
            {nameError ? (
              <div className={styles.batchEditorFieldError}>{nameError}</div>
            ) : null}
          </div>
        ) : null}

        <div className={styles.batchEditorField}>
          <label className={styles.batchEditorLabel}>
            {t("cronJobs.toolBatches.contentLabel")}
          </label>
          <div className={styles.batchEditorJson}>
            <Editor
              height="320px"
              language="json"
              value={contentText}
              onChange={handleContentChange}
              options={MONACO_OPTIONS}
            />
          </div>
          {/* Create mode pre-fills a runnable example, so say so. In edit
              mode — and when creating from a copied template script — the
              content is a real script and the same line would be a lie. */}
          {mode === "create" && target?.initialContent === undefined ? (
            <div className={styles.batchEditorFieldHint}>
              {t("cronJobs.toolBatches.editorExampleHint")}
            </div>
          ) : null}
        </div>

        {contentError ? (
          <Alert type="error" showIcon message={contentError} />
        ) : null}

        {contentIssues.length > 0 ? (
          <Alert
            type="error"
            showIcon
            message={t("cronJobs.toolBatches.validationFailed")}
            description={
              <ul className={styles.batchEditorIssues}>
                {contentIssues.map((issue, index) => (
                  <li key={index}>
                    {t(VALIDATION_KEY[issue.code], validationParams(issue))}
                  </li>
                ))}
              </ul>
            }
          />
        ) : null}
      </div>
    </Modal>
  );
}
