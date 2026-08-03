import { useMemo, useRef, useState } from "react";
import { Button, Modal, Select, Tooltip } from "@agentscope-ai/design";
import {
  DeleteOutlined,
  EditOutlined,
  ExportOutlined,
  FileSearchOutlined,
  ImportOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { CronTemplateInfo } from "../../../../api/types";
import type { CronTemplateCategory, CronTemplateDefinition } from "./templates";
import {
  templateDescription,
  templateFrequency,
  templateTitle,
} from "./templates";
import {
  toTemplateDefinition,
  toTemplateDefinitions,
} from "./packageTemplates";
import { EditTemplateModal } from "./EditTemplateModal";
import { TemplateDetailDrawer } from "./TemplateDetailDrawer";
import { useCronTemplates } from "./useCronTemplates";
import styles from "../index.module.less";

interface TemplatePickerModalProps {
  open: boolean;
  timezone: string;
  onCancel: () => void;
  onUseTemplate: (templateValues: Record<string, unknown>) => void;
}

const ZIP_ACCEPT = ".zip,application/zip,application/x-zip-compressed";

export function TemplatePickerModal({
  open,
  timezone,
  onCancel,
  onUseTemplate,
}: TemplatePickerModalProps) {
  const { t } = useTranslation();
  const [category, setCategory] = useState<CronTemplateCategory>("cron");
  const [detailName, setDetailName] = useState<string | null>(null);
  const [editing, setEditing] = useState<CronTemplateInfo | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    templates: packages,
    busy,
    importZip,
    exportTemplate,
    deleteTemplate,
    updateTemplate,
    forkTemplate,
    installSkills,
  } = useCronTemplates();

  const allTemplates = useMemo(
    () => toTemplateDefinitions(packages),
    [packages],
  );

  const filteredTemplates = useMemo(
    () => allTemplates.filter((template) => template.category === category),
    [allTemplates, category],
  );

  const categoryOptions = [
    { label: t("cronJobs.scheduleTypeRecurring"), value: "cron" },
    { label: t("cronJobs.scheduleTypeOnce"), value: "once" },
  ];

  const handleUseTemplate = (template: CronTemplateDefinition) => {
    const templateValues = template.toFormValues(timezone);
    onUseTemplate({
      ...templateValues,
      name: templateTitle(template, t),
      text:
        templateValues.task_type === "agent"
          ? ""
          : (templateValues.text as string) || templateDescription(template, t),
    });
  };

  /** Use a template straight from the detail drawer's footer button. */
  const handleUseFromDetail = (info: CronTemplateInfo) => {
    setDetailName(null);
    handleUseTemplate(toTemplateDefinition(info));
  };

  const handlePickFile = () => fileInputRef.current?.click();

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    // Reset first: picking the same file twice must still fire onChange.
    event.target.value = "";
    if (file) await importZip(file);
  };

  /**
   * Open the edit form, copying a shipped package into the pool first.
   *
   * Builtins are read-only on disk, so editing one has to fork it. Doing
   * that here — rather than making the user press a separate "copy" button
   * and figure out why — keeps Edit meaning the same thing everywhere.
   */
  const handleEdit = async (template: CronTemplateDefinition) => {
    let info = packages.find((p) => p.name === template.packageName);
    if (template.packageSource === "builtin") {
      const forked = await forkTemplate(template.packageName);
      if (!forked) return;
      info = forked;
    }
    if (info) setEditing(info);
  };

  const handleDelete = (template: CronTemplateDefinition) => {
    Modal.confirm({
      title: t("cronJobs.templateDeleteConfirmTitle"),
      content: t("cronJobs.templateDeleteConfirmContent", {
        name: template.packageName,
      }),
      okText: t("cronJobs.deleteText"),
      okType: "primary",
      cancelText: t("cronJobs.cancelText"),
      onOk: () => deleteTemplate(template.packageName),
    });
  };

  return (
    <Modal
      visible={open}
      title={t("cronJobs.templateModalTitle")}
      footer={null}
      width={860}
      onCancel={onCancel}
    >
      <div className={styles.templateModalHeader}>
        <div className={styles.templateModalDesc}>
          {t("cronJobs.templateModalDescription")}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Tooltip title={t("cronJobs.templateImportTooltip")}>
            <Button
              icon={<ImportOutlined />}
              loading={busy}
              onClick={handlePickFile}
            >
              {t("cronJobs.templateImport")}
            </Button>
          </Tooltip>
          <Select<CronTemplateCategory>
            value={category}
            options={categoryOptions}
            style={{ width: 180 }}
            onChange={setCategory}
          />
        </div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept={ZIP_ACCEPT}
        style={{ display: "none" }}
        onChange={handleFileChange}
      />
      <div className={styles.templateGrid}>
        {filteredTemplates.map((template) => {
          const description = templateDescription(template, t);
          const frequency = templateFrequency(template, t);
          const isBuiltin = template.packageSource === "builtin";
          return (
            <div key={template.id} className={styles.templateCard}>
              {/* Contents sit beside the title, so the card reads
                  "what it is + what's in it" on one line and the title no
                  longer needs to spell that out. */}
              <div className={styles.templateTitleRow}>
                <div className={styles.templateTitle}>
                  {template.emoji ? `${template.emoji} ` : ""}
                  {templateTitle(template, t)}
                </div>
                {template.batchFiles.length || template.skills.length ? (
                  <div className={styles.templateTags}>
                    {template.batchFiles.length ? (
                      <span className={styles.templateTag}>
                        {t("cronJobs.templateBatchCount", {
                          count: template.batchFiles.length,
                        })}
                      </span>
                    ) : null}
                    {template.skills.length ? (
                      <span className={styles.templateTag}>
                        {t("cronJobs.templateSkillCount", {
                          count: template.skills.length,
                        })}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
              {/* Clamped to one line; the tooltip carries the full text so a
                  long description cannot push the cards out of alignment. */}
              <Tooltip title={description}>
                <div className={styles.templateDesc}>{description}</div>
              </Tooltip>
              {frequency ? (
                <div className={styles.templateMeta}>{frequency}</div>
              ) : null}
              <div
                className={styles.templateActions}
                style={{ display: "flex", gap: 8, alignItems: "center" }}
              >
                <Button
                  type="primary"
                  onClick={() => handleUseTemplate(template)}
                >
                  {t("cronJobs.useTemplate")}
                </Button>
                <Tooltip title={t("cronJobs.templateViewDetailTooltip")}>
                  <Button
                    icon={<FileSearchOutlined />}
                    onClick={() => setDetailName(template.packageName)}
                  />
                </Tooltip>
                <Tooltip title={t("cronJobs.templateExportTooltip")}>
                  <Button
                    icon={<ExportOutlined />}
                    loading={busy}
                    onClick={() => exportTemplate(template.packageName)}
                  />
                </Tooltip>
                {template.skills.length ? (
                  <Button
                    loading={busy}
                    onClick={() => installSkills(template.packageName)}
                  >
                    {t("cronJobs.templateInstallSkills")}
                  </Button>
                ) : null}
                <Tooltip
                  title={
                    isBuiltin
                      ? t("cronJobs.templateEditBuiltinTooltip")
                      : t("cronJobs.templateEdit")
                  }
                >
                  <Button
                    icon={<EditOutlined />}
                    loading={busy}
                    onClick={() => handleEdit(template)}
                  />
                </Tooltip>
                {isBuiltin ? null : (
                  <Tooltip title={t("cronJobs.templateDeleteTooltip")}>
                    <Button
                      icon={<DeleteOutlined />}
                      loading={busy}
                      onClick={() => handleDelete(template)}
                    />
                  </Tooltip>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <TemplateDetailDrawer
        open={detailName !== null}
        packageName={detailName}
        onClose={() => setDetailName(null)}
        onUseTemplate={handleUseFromDetail}
      />

      <EditTemplateModal
        open={editing !== null}
        info={editing}
        saving={busy}
        onCancel={() => setEditing(null)}
        onSubmit={updateTemplate}
      />
    </Modal>
  );
}
