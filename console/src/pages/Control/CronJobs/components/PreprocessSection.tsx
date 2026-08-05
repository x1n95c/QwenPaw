import { useRef, useState } from "react";
import {
  Button,
  Checkbox,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Switch,
  Tag,
  Tooltip,
} from "@agentscope-ai/design";
import {
  DeleteOutlined,
  EditOutlined,
  ImportOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Alert } from "antd";
import type { FormInstance } from "antd";
import { useTranslation } from "react-i18next";
import type {
  ToolBatchImportCandidate,
  ToolBatchInfo,
} from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { BatchEditorModal, type BatchEditorTarget } from "./BatchEditorModal";
import { BatchPickerModal } from "./BatchPickerModal";
import { BatchStepPreview } from "./BatchStepPreview";
import { useToolBatches } from "./useToolBatches";
import {
  PREPROCESS_DEFAULTS,
  emptyPreprocessStep,
  type PreprocessStepFormValue,
} from "./constants";
import styles from "../index.module.less";

interface PreprocessSectionProps {
  form: FormInstance;
}

/** Files accepted by the 导入脚本 button. */
const UPLOAD_ACCEPT =
  ".json,.zip,application/json,application/zip,application/x-zip-compressed";

/** Mirrors MAX_PREPROCESS_SCRIPTS in app/crons/models.py. */
const MAX_STEPS = 10;

interface StepsEditorProps {
  batches: ToolBatchInfo[];
  loading: boolean;
  onEditScript: (name: string) => void;
  value?: PreprocessStepFormValue[];
  onChange?: (value: PreprocessStepFormValue[]) => void;
}

/**
 * The ordered script list, bound through ONE Form.Item.
 *
 * Every arg key is dynamic (it follows the selected script's `${args.*}`
 * placeholders) and so is the row count, which antd cannot express with
 * static `name` paths — one Form.Item per key would leave orphan keys from
 * a previous script in the form store after a switch. So the whole array
 * moves through a single field and every emit rebuilds it from scratch.
 */
function StepsEditor({
  batches,
  loading,
  onEditScript,
  value,
  onChange,
}: StepsEditorProps) {
  const { t } = useTranslation();
  // Always render at least one row: an empty list would leave the user
  // with nothing to click after enabling the block.
  const steps = value?.length ? value : [emptyPreprocessStep()];

  const emit = (next: PreprocessStepFormValue[]) => onChange?.(next);

  const replaceStep = (
    index: number,
    patch: Partial<PreprocessStepFormValue>,
  ) => emit(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const options = batches.map((batch) => ({
    label: batch.description
      ? `${batch.name} — ${batch.description}`
      : batch.name,
    value: batch.name,
  }));

  return (
    <div className={styles.preprocessSteps}>
      {steps.map((step, index) => {
        const selected = batches.find((b) => b.name === step.script);
        const missing = Boolean(step.script) && !loading && !selected;
        return (
          <div key={index} className={styles.preprocessStep}>
            <div className={styles.preprocessStepHead}>
              {/* The ordinal is the whole point of a chain: scripts run
                  top to bottom, and a later one may depend on an earlier
                  one having already written a file. */}
              <span className={styles.preprocessStepOrdinal}>{index + 1}</span>
              <Select
                showSearch
                allowClear
                loading={loading}
                className={styles.preprocessStepSelect}
                placeholder={t("cronJobs.preprocess.scriptPlaceholder")}
                options={options}
                value={step.script || undefined}
                onChange={(next) =>
                  // Fresh script → fresh placeholder set; never carry the
                  // previous script's keys across.
                  replaceStep(index, { script: next || "", args: {} })
                }
                filterOption={(input, option) =>
                  (option?.label?.toString() || "")
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
              />
              <Tooltip
                title={
                  step.script
                    ? t("cronJobs.preprocess.editCurrentScriptTooltip")
                    : t("cronJobs.preprocess.editCurrentScriptDisabled")
                }
              >
                <Button
                  icon={<EditOutlined />}
                  disabled={!step.script}
                  onClick={() => step.script && onEditScript(step.script)}
                />
              </Tooltip>
              <Tooltip
                title={
                  steps.length >= MAX_STEPS
                    ? t("cronJobs.preprocess.addStepMax", { max: MAX_STEPS })
                    : t("cronJobs.preprocess.addStepTooltip")
                }
              >
                <Button
                  icon={<PlusOutlined />}
                  disabled={steps.length >= MAX_STEPS}
                  onClick={() =>
                    emit([
                      ...steps.slice(0, index + 1),
                      emptyPreprocessStep(),
                      ...steps.slice(index + 1),
                    ])
                  }
                />
              </Tooltip>
              <Tooltip title={t("cronJobs.preprocess.removeStepTooltip")}>
                <Button
                  icon={<DeleteOutlined />}
                  // Removing the only row would leave nothing to click;
                  // clearing the selection is what "no script" means.
                  disabled={steps.length <= 1}
                  onClick={() => emit(steps.filter((_, i) => i !== index))}
                />
              </Tooltip>
            </div>

            {missing ? (
              <Alert
                type="warning"
                showIcon
                message={t("cronJobs.preprocess.scriptMissing", {
                  name: step.script,
                })}
              />
            ) : null}

            {selected ? (
              <BatchStepPreview
                actions={selected.preview_actions}
                actionCount={selected.action_count}
                title={t("cronJobs.toolBatches.stepsPreview", {
                  count: selected.action_count,
                })}
              />
            ) : null}

            {selected && selected.arg_names.length > 0 ? (
              <div className={styles.preprocessStepArgs}>
                {selected.arg_names.map((name) => (
                  <div key={name} className={styles.preprocessArgRow}>
                    <span className={styles.preprocessArgName}>
                      {`\${args.${name}}`}
                    </span>
                    <Input
                      value={step.args?.[name] ?? ""}
                      placeholder={name}
                      onChange={(event) => {
                        // Rebuilt from the script's declared names only,
                        // so a renamed placeholder cannot leave an orphan.
                        const next: Record<string, string> = {};
                        for (const key of selected.arg_names) {
                          next[key] =
                            key === name
                              ? event.target.value
                              : step.args?.[key] ?? "";
                        }
                        replaceStep(index, { args: next });
                      }}
                    />
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Preprocess block inside the job drawer, between the task type and the
 * message content: the scripts collect data first, then the task body
 * consumes it.
 *
 * The enable switch is the only thing shown until it is on — a job without
 * a preprocess should not have to scroll past its settings.
 */
export function PreprocessSection({ form }: PreprocessSectionProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const toolBatches = useToolBatches();
  const { batches, loading } = toolBatches;
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editorTarget, setEditorTarget] = useState<BatchEditorTarget | null>(
    null,
  );
  // A zip with several scripts needs a pick step before anything is
  // written; the server returns candidates and waits for `select`.
  const [pendingImport, setPendingImport] = useState<{
    file: File;
    candidates: ToolBatchImportCandidate[];
  } | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const enabled = Form.useWatch(["preprocess", "enabled"], form);

  /** Put a newly created/imported script into the first empty row. */
  const useScript = (name: string) => {
    const steps: PreprocessStepFormValue[] =
      form.getFieldValue(["preprocess", "steps"]) || [];
    const blank = steps.findIndex((step) => !step?.script);
    const next = steps.length ? [...steps] : [emptyPreprocessStep()];
    const target = blank >= 0 ? blank : next.length;
    next[target] = { script: name, args: {} };
    form.setFieldValue(["preprocess", "steps"], next);
  };

  const handleUploadChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    // Reset first: picking the same file twice must still fire onChange.
    event.target.value = "";
    if (!file) return;

    if (file.name.toLowerCase().endsWith(".json")) {
      // Single script: create it in the pool directly and select it.
      try {
        const content = JSON.parse(await file.text());
        const name = file.name.replace(/\.json$/i, "");
        const ok = await toolBatches.createBatch({ name, content });
        if (ok) useScript(name);
      } catch (error) {
        message.error(
          error instanceof SyntaxError
            ? t("cronJobs.toolBatches.jsonParseError", {
                message: error.message,
              })
            : t("cronJobs.toolBatches.importFailed"),
        );
      }
      return;
    }

    // Zip: a single script is imported directly; several scripts come
    // back as candidates for the pick step below.
    const result = await toolBatches.importZip(file);
    if (result?.candidates?.length) {
      // Pre-tick files that are usable and do not collide. A broken file
      // is listed but never pre-selected: selecting it fails the whole
      // import, which is the opposite of helpful as a default.
      setSelectedFiles(
        result.candidates
          .filter((c) => c.valid !== false && !c.exists)
          .map((c) => c.file_name),
      );
      setPendingImport({ file, candidates: result.candidates });
    }
  };

  const toggleImportFile = (fileName: string, checked: boolean) => {
    setSelectedFiles((prev) =>
      checked ? [...prev, fileName] : prev.filter((f) => f !== fileName),
    );
  };

  const handleImportConfirm = async () => {
    if (!pendingImport || selectedFiles.length === 0) return;
    const result = await toolBatches.importZip(pendingImport.file, {
      select: selectedFiles,
    });
    if (result && !result.candidates?.length) {
      setPendingImport(null);
      setSelectedFiles([]);
    }
  };

  return (
    <>
      <Form.Item
        name={["preprocess", "enabled"]}
        label={t("cronJobs.preprocess.enabled")}
        valuePropName="checked"
        tooltip={t("cronJobs.preprocess.enabledTooltip")}
      >
        <Switch />
      </Form.Item>

      {enabled ? (
        <div className={styles.preprocessBody}>
          <Form.Item
            name={["preprocess", "steps"]}
            label={t("cronJobs.preprocess.scripts")}
            tooltip={t("cronJobs.preprocess.scriptsTooltip")}
          >
            <StepsEditor
              batches={batches}
              loading={loading}
              onEditScript={(name) => setEditorTarget({ mode: "edit", name })}
            />
          </Form.Item>

          {/* Pool-level actions sit below the list: they act on the shared
              pool rather than on any one row. */}
          <div className={styles.preprocessPoolActions}>
            <Button onClick={() => setPickerOpen(true)}>
              {t("cronJobs.preprocess.manageScripts")}
            </Button>
            <Tooltip title={t("cronJobs.preprocess.newScriptTooltip")}>
              <Button
                icon={<PlusOutlined />}
                onClick={() => setEditorTarget({ mode: "create" })}
              >
                {t("cronJobs.preprocess.newScript")}
              </Button>
            </Tooltip>
            <Tooltip title={t("cronJobs.preprocess.importScriptTooltip")}>
              <Button
                icon={<ImportOutlined />}
                onClick={() => uploadInputRef.current?.click()}
              >
                {t("cronJobs.preprocess.importScript")}
              </Button>
            </Tooltip>
          </div>
          <input
            ref={uploadInputRef}
            type="file"
            accept={UPLOAD_ACCEPT}
            style={{ display: "none" }}
            onChange={(event) => void handleUploadChange(event)}
          />

          <Collapse
            ghost
            className={styles.preprocessAdvanced}
            items={[
              {
                key: "advanced",
                label: t("cronJobs.preprocess.advanced"),
                children: (
                  <>
                    <Form.Item
                      name={["preprocess", "last_only"]}
                      label={t("cronJobs.preprocess.lastOnly")}
                      valuePropName="checked"
                      tooltip={t("cronJobs.preprocess.lastOnlyTooltip")}
                    >
                      <Switch />
                    </Form.Item>
                    <Form.Item
                      name={["preprocess", "on_failure"]}
                      label={t("cronJobs.preprocess.onFailure")}
                      tooltip={t("cronJobs.preprocess.onFailureTooltip")}
                    >
                      <Select
                        options={[
                          {
                            label: t("cronJobs.preprocess.onFailureContinue"),
                            value: "continue",
                          },
                          {
                            label: t("cronJobs.preprocess.onFailureAbort"),
                            value: "abort",
                          },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item
                      name={["preprocess", "timeout_seconds"]}
                      label={t("cronJobs.preprocess.timeoutSeconds")}
                      tooltip={t("cronJobs.preprocess.timeoutSecondsTooltip")}
                    >
                      <InputNumber
                        min={1}
                        style={{ width: "100%" }}
                        placeholder={String(
                          PREPROCESS_DEFAULTS.timeout_seconds,
                        )}
                      />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
        </div>
      ) : null}

      <BatchPickerModal
        open={pickerOpen}
        toolBatches={toolBatches}
        onCancel={() => setPickerOpen(false)}
        onOpenEditor={setEditorTarget}
      />

      <Modal
        visible={pendingImport !== null}
        title={t("cronJobs.toolBatches.importSelectTitle")}
        width={560}
        onCancel={() => {
          setPendingImport(null);
          setSelectedFiles([]);
        }}
        footer={
          <>
            <Button
              onClick={() => {
                setPendingImport(null);
                setSelectedFiles([]);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              disabled={selectedFiles.length === 0}
              loading={toolBatches.busy}
              onClick={() => void handleImportConfirm()}
            >
              {t("cronJobs.toolBatches.importConfirm", {
                count: selectedFiles.length,
              })}
            </Button>
          </>
        }
      >
        <div className={styles.templateImportHint}>
          {t("cronJobs.toolBatches.importSelectHint")}
        </div>
        {pendingImport?.candidates.map((candidate) => {
          const broken = candidate.valid === false;
          return (
            <div key={candidate.file_name} className={styles.templateImportRow}>
              <Checkbox
                checked={selectedFiles.includes(candidate.file_name)}
                disabled={broken}
                onChange={(event) =>
                  toggleImportFile(candidate.file_name, event.target.checked)
                }
              >
                {candidate.name}
                <span className={styles.templateImportFileName}>
                  {candidate.file_name}
                </span>
              </Checkbox>
              {broken ? (
                <Tag color="error" title={candidate.error}>
                  {t("cronJobs.toolBatches.importInvalid")}
                </Tag>
              ) : null}
              {candidate.exists && !broken ? (
                <Tag color="warning">
                  {t("cronJobs.toolBatches.importExists")}
                </Tag>
              ) : null}
            </div>
          );
        })}
      </Modal>

      <BatchEditorModal
        open={editorTarget !== null}
        target={editorTarget}
        toolBatches={toolBatches}
        onCancel={() => setEditorTarget(null)}
        onSaved={useScript}
      />
    </>
  );
}
