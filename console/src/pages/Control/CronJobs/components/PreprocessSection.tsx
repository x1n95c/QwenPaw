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
  CronSkillInfo,
  JobToolBatches,
  TemplateBatchScriptInfo,
  ToolBatchImportCandidate,
  ToolBatchInfo,
} from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { BatchEditorModal, type BatchEditorTarget } from "./BatchEditorModal";
import { BatchPickerModal } from "./BatchPickerModal";
import { BatchStepPreview } from "./BatchStepPreview";
import {
  buildScriptOptions,
  findScript,
  type ScriptSelectOption,
} from "./scriptOptions";
import { useCronSkills } from "./useCronSkills";
import { useTemplateBatchScripts } from "./useTemplateBatchScripts";
import { useWorkspaceJobBatches } from "./useWorkspaceJobBatches";
import { useToolBatches } from "./useToolBatches";
import {
  PREPROCESS_DEFAULTS,
  emptyPreprocessStep,
  type PreprocessStepFormValue,
} from "./constants";
import styles from "../index.module.less";

interface PreprocessSectionProps {
  form: FormInstance;
  /** The job that owns these scripts. See `JobDrawerProps.jobId`. */
  jobId: string;
}

/** Files accepted by a row's import button. */
const UPLOAD_ACCEPT =
  ".json,.zip,application/json,application/zip,application/x-zip-compressed";

/** Mirrors MAX_PREPROCESS_SCRIPTS in app/crons/models.py. */
const MAX_STEPS = 10;

interface StepsEditorProps {
  batches: ToolBatchInfo[];
  templateScripts: TemplateBatchScriptInfo[];
  /** Other cron jobs' scripts, browse-only: picking one copies it in. */
  jobScripts: JobToolBatches[];
  /** Skills that carry batch JSON, browse-only: picking one copies it in. */
  skillScripts: CronSkillInfo[];
  loading: boolean;
  /** Open the editor for the script selected in row `index`. */
  onEditScript: (index: number, script: string) => void;
  /** Author a new script and land it in row `index`. */
  onCreateScript: (index: number) => void;
  /** Import a script file and land it in row `index`. */
  onImportScript: (index: number) => void;
  /** Open the script manager. Acts outside this chain. */
  onManageScripts: () => void;
  /**
   * Copy a foreign script into this job and put the resulting local name in
   * row `index`. The source descriptor is transient and never stored.
   */
  onAdoptScript: (
    index: number,
    source: NonNullable<ScriptSelectOption["copySource"]>,
  ) => Promise<void>;
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
  templateScripts,
  jobScripts,
  skillScripts,
  loading,
  onEditScript,
  onCreateScript,
  onImportScript,
  onManageScripts,
  onAdoptScript,
  value,
  onChange,
}: StepsEditorProps) {
  const { t } = useTranslation();
  // Reveal the template groups. Kept outside the form value: it is a view
  // state of the picker, not part of the job.
  const [expanded, setExpanded] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  // Always render at least one row: an empty list would leave the user
  // with nothing to click after enabling the block.
  const steps = value?.length ? value : [emptyPreprocessStep()];

  const emit = (next: PreprocessStepFormValue[]) => onChange?.(next);

  const replaceStep = (
    index: number,
    patch: Partial<PreprocessStepFormValue>,
  ) => emit(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)));

  const { groups, templateCount } = buildScriptOptions({
    ownScripts: batches,
    templateScripts,
    jobScripts,
    skillScripts,
    t,
    // A search must reach into the collapsed groups. Without this, typing
    // "weather" filters this job's own scripts to nothing and renders "no
    // data" while the match sits one hidden group away — the feature would be invisible to
    // anyone who types instead of scrolls.
    expanded: expanded || Boolean(searchValue.trim()),
    labels: {
      own: t("cronJobs.preprocess.scriptGroupPool"),
      template: t("cronJobs.preprocess.scriptGroupTemplatePrefix"),
      job: t("cronJobs.preprocess.scriptGroupJobPrefix"),
      skill: t("cronJobs.preprocess.scriptGroupSkillPrefix"),
    },
  });

  return (
    <div className={styles.preprocessSteps}>
      {steps.map((step, index) => {
        const selected = findScript(step.script, batches);
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
                options={groups}
                value={step.script || undefined}
                onChange={(next, option) => {
                  setSearchValue("");
                  const picked = option as unknown as
                    | ScriptSelectOption
                    | undefined;
                  if (picked?.copySource) {
                    // A foreign script — from a template or another job —
                    // is copied in, never referenced: the step must end up
                    // holding a name that exists in *this* job's directory.
                    // The server decides the final name (it may already be
                    // taken), so the row is filled from its answer rather
                    // than from `next`.
                    void onAdoptScript(index, picked.copySource);
                    return;
                  }
                  // Fresh script → fresh placeholder set; never carry the
                  // previous script's keys across.
                  replaceStep(index, { script: next || "", args: {} });
                }}
                onSearch={setSearchValue}
                // The search box is uncontrolled — antd clears it itself on
                // select and on close, and replicating those rules here
                // would only drift. This mirror exists to decide whether
                // the template groups are revealed, so it has to be reset
                // at the same two moments or the next row opens filtered.
                onOpenChange={(open) => {
                  if (!open) setSearchValue("");
                }}
                // antd infers the callback parameter from `options`, which
                // here is the *group* type — it has no way to say "a leaf
                // of a grouped list". Both callbacks only ever receive
                // leaves, so narrowing is the honest thing to do.
                filterOption={(input, option) =>
                  (
                    (option as unknown as ScriptSelectOption | undefined)
                      ?.searchText || ""
                  ).includes(input.toLowerCase())
                }
                // The description belongs on hover, not inline: the rows
                // stay scannable, and the option's own `title` is blanked
                // so this is the only tooltip that fires.
                optionRender={(option) => {
                  const data = option.data as unknown as ScriptSelectOption;
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
                popupRender={(menu) => (
                  <>
                    {menu}
                    {/* preventDefault on mousedown, or the Select blurs and
                        the dropdown closes before onClick ever fires. */}
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
                          ? t("cronJobs.preprocess.scriptGroupTemplatesHide")
                          : t("cronJobs.preprocess.scriptGroupTemplates", {
                              count: templateCount,
                            })}
                      </Button>
                    </div>
                  </>
                )}
                // A job with no scripts of its own would otherwise render
                // antd's bare "no data" above the expander, hiding that
                // there is more behind it.
                notFoundContent={
                  <div className={styles.preprocessScriptEmpty}>
                    {t("cronJobs.preprocess.scriptNoMatch")}
                  </div>
                }
              />
              {/* The first button is what this row needs *next*: an empty
                  row needs a script written, a filled one needs it
                  changed. One slot, two jobs, so the row never shows a
                  button that does nothing. */}
              <Tooltip
                title={
                  step.script
                    ? t("cronJobs.preprocess.editCurrentScriptTooltip")
                    : t("cronJobs.preprocess.newScriptTooltip")
                }
              >
                <Button
                  icon={step.script ? <EditOutlined /> : <PlusOutlined />}
                  onClick={() =>
                    step.script
                      ? onEditScript(index, step.script)
                      : onCreateScript(index)
                  }
                />
              </Tooltip>
              <Tooltip title={t("cronJobs.preprocess.importScriptTooltip")}>
                <Button
                  icon={<ImportOutlined />}
                  onClick={() => onImportScript(index)}
                />
              </Tooltip>
              <Tooltip
                title={
                  steps.length > 1
                    ? t("cronJobs.preprocess.removeStepTooltip")
                    : t("cronJobs.preprocess.clearStepTooltip")
                }
              >
                <Button
                  icon={<DeleteOutlined />}
                  // Never disabled. Dropping the last row would leave
                  // nothing to click, so on a single row this clears the
                  // selection instead — which is what "remove" means when
                  // there is only one.
                  onClick={() =>
                    steps.length > 1
                      ? emit(steps.filter((_, i) => i !== index))
                      : replaceStep(index, { script: "", args: {} })
                  }
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

      {/* Adding another step belongs below the list, not on a row: it acts
          on the chain rather than on any one script. Managing the scripts sits
          opposite it — same line, but it is the only thing here that
          reaches outside this job. */}
      <div className={styles.preprocessChainActions}>
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
            onClick={() => emit([...steps, emptyPreprocessStep()])}
          >
            {t("cronJobs.preprocess.addStep")}
          </Button>
        </Tooltip>
        <Button onClick={onManageScripts}>
          {t("cronJobs.preprocess.manageScripts")}
        </Button>
      </div>
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
export function PreprocessSection({ form, jobId }: PreprocessSectionProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const toolBatches = useToolBatches(jobId);
  const { batches, loading } = toolBatches;
  const templateScripts = useTemplateBatchScripts();
  // Other jobs' scripts, browse-only. Excludes this job, which is already
  // listed as "my scripts".
  const workspaceJobScripts = useWorkspaceJobBatches(jobId);
  // Skills carry batch JSON of their own — the layout `make-skill` teaches
  // agents to write. Same list the "use skill" picker renders; only the
  // ones actually carrying a script produce a group.
  const cronSkills = useCronSkills();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editorTarget, setEditorTarget] = useState<BatchEditorTarget | null>(
    null,
  );
  // Which row the open editor should write its result back to. `null`
  // means "the first empty row", which is what creating from the manager
  // actions below wants.
  const [editorRow, setEditorRow] = useState<number | null>(null);
  // A zip with several scripts needs a pick step before anything is
  // written; the server returns candidates and waits for `select`.
  const [pendingImport, setPendingImport] = useState<{
    file: File;
    candidates: ToolBatchImportCandidate[];
  } | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const enabled = Form.useWatch(["preprocess", "enabled"], form);

  /**
   * Point a row at a newly created / imported script.
   *
   * Named `assignScript`, not `useScript`: a `use` prefix makes eslint's
   * `react-hooks/rules-of-hooks` treat it as a hook and reject every call
   * from inside an event handler.
   *
   * Targets `editorRow` when one is set — copying a template script has to
   * replace the row it was copied from, not land in some other blank one —
   * and otherwise fills the first empty row.
   */
  const assignScript = (name: string) => {
    const steps: PreprocessStepFormValue[] =
      form.getFieldValue(["preprocess", "steps"]) || [];
    const next = steps.length ? [...steps] : [emptyPreprocessStep()];
    let target = editorRow;
    if (target === null || target < 0 || target >= next.length) {
      const blank = next.findIndex((step) => !step?.script);
      target = blank >= 0 ? blank : next.length;
    }
    next[target] = { script: name, args: {} };
    form.setFieldValue(["preprocess", "steps"], next);
    setEditorRow(null);
  };

  /**
   * Copy a foreign script into this job and point a row at the result.
   *
   * The source descriptor is transient: it names a script in a template
   * package or in another job, and is never stored. The server resolves it,
   * writes a copy into this job's directory, and answers with the name that
   * actually landed (which differs from the requested one when that was
   * taken). Only that name reaches the form, so a step can never hold a
   * value pointing outside the job that runs it.
   */
  const handleAdoptScript = async (
    index: number,
    source: NonNullable<ScriptSelectOption["copySource"]>,
  ) => {
    setEditorRow(index);
    const landed = await toolBatches.copyBatch(source);
    if (landed) assignScript(landed);
    else setEditorRow(null);
  };

  /**
   * The edit button on a step. Every script a step names belongs to this
   * job, so editing is always in place.
   */
  const handleEditScript = (index: number, script: string) => {
    setEditorRow(index);
    setEditorTarget({ mode: "edit", name: script });
  };

  const handleUploadChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    // Reset first: picking the same file twice must still fire onChange.
    event.target.value = "";
    if (!file) return;

    if (file.name.toLowerCase().endsWith(".json")) {
      // Single script: create it in this job directly and select it.
      try {
        const content = JSON.parse(await file.text());
        const name = file.name.replace(/\.json$/i, "");
        const ok = await toolBatches.createBatch({ name, content });
        if (ok) assignScript(name);
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
              templateScripts={templateScripts.scripts}
              jobScripts={workspaceJobScripts.groups}
              skillScripts={cronSkills.skills}
              // Every list feeds the same picker, so the "script not found"
              // warning must wait for all of them or it flashes on first
              // paint.
              loading={loading || templateScripts.loading || cronSkills.loading}
              onEditScript={handleEditScript}
              onCreateScript={(index) => {
                setEditorRow(index);
                setEditorTarget({ mode: "create" });
              }}
              onImportScript={(index) => {
                setEditorRow(index);
                uploadInputRef.current?.click();
              }}
              onManageScripts={() => setPickerOpen(true)}
              onAdoptScript={handleAdoptScript}
            />
          </Form.Item>

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
        onOpenEditor={(target) => {
          setEditorRow(null);
          setEditorTarget(target);
        }}
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
        onCancel={() => {
          setEditorTarget(null);
          setEditorRow(null);
        }}
        onSaved={assignScript}
      />
    </>
  );
}
