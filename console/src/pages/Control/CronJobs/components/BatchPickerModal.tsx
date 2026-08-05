import { useState } from "react";
import { Button, Modal, Tag, Tooltip } from "@agentscope-ai/design";
import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { BatchEditorTarget } from "./BatchEditorModal";
import { BatchStepPreview } from "./BatchStepPreview";
import type { UseToolBatchesResult } from "./useToolBatches";
import styles from "../index.module.less";

interface BatchPickerModalProps {
  open: boolean;
  /** Pool state owned by the preprocess section, so both stay in sync. */
  toolBatches: UseToolBatchesResult;
  onCancel: () => void;
  /** Open the shared editor (owned by the parent so it stacks above us). */
  onOpenEditor: (target: BatchEditorTarget) => void;
}

/**
 * Manage the shared batch-script pool: edit or delete existing scripts.
 *
 * Creating and importing scripts lives in the preprocess section itself
 * (the 新建 / 导入 buttons), so this modal stays a pure manager.
 */
export function BatchPickerModal({
  open,
  toolBatches,
  onCancel,
  onOpenEditor,
}: BatchPickerModalProps) {
  const { t } = useTranslation();
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const { batches, loading, busy, deleteBatch } = toolBatches;

  const handleDeleteConfirmed = async (name: string) => {
    await deleteBatch(name);
    setConfirmDelete(null);
  };

  return (
    <>
      <Modal
        visible={open}
        title={t("cronJobs.toolBatches.pickerTitle")}
        width={680}
        onCancel={onCancel}
        footer={<Button onClick={onCancel}>{t("common.close")}</Button>}
      >
        {loading && !batches.length ? (
          <div className={styles.batchPickerEmpty}>{t("common.loading")}</div>
        ) : null}

        {!loading && !batches.length ? (
          <div className={styles.batchPickerEmpty}>
            {t("cronJobs.toolBatches.emptyPool")}
          </div>
        ) : null}

        <div className={styles.templateCardList}>
          {batches.map((batch) => (
            <div key={batch.name} className={styles.templateCard}>
              {/* Header is its own row: `.templateCard` is a column, so the
                  action buttons have to sit in a nested row to end up
                  beside the name rather than under it. */}
              <div className={styles.batchCardHeader}>
                <div className={styles.batchCardMain}>
                  <div className={styles.templateCardTitle} title={batch.name}>
                    {batch.name}
                  </div>
                  {batch.description ? (
                    <div
                      className={styles.templateCardDescription}
                      title={batch.description}
                    >
                      {batch.description}
                    </div>
                  ) : null}
                  <div className={styles.batchCardTags}>
                    <Tag>
                      {t("cronJobs.toolBatches.stepsCount", {
                        count: batch.action_count,
                      })}
                    </Tag>
                    {batch.arg_names.length > 0 ? (
                      <Tag>
                        {t("cronJobs.toolBatches.argsCount", {
                          count: batch.arg_names.length,
                        })}
                      </Tag>
                    ) : null}
                  </div>
                </div>

                <div className={styles.batchCardActions}>
                  <Tooltip title={t("cronJobs.toolBatches.edit")}>
                    <Button
                      icon={<EditOutlined />}
                      onClick={() =>
                        onOpenEditor({ mode: "edit", name: batch.name })
                      }
                    />
                  </Tooltip>
                  <Tooltip title={t("cronJobs.toolBatches.delete")}>
                    <Button
                      icon={<DeleteOutlined />}
                      danger
                      onClick={() => setConfirmDelete(batch.name)}
                    />
                  </Tooltip>
                </div>
              </div>

              {/* Same preview the job form shows, so a script reads the
                  same way wherever it appears. Collapsed here: the list is
                  for scanning, and N expanded previews would bury it. */}
              <BatchStepPreview
                actions={batch.preview_actions}
                actionCount={batch.action_count}
                title={t("cronJobs.toolBatches.stepsPreview", {
                  count: batch.action_count,
                })}
              />
            </div>
          ))}
        </div>
      </Modal>

      <Modal
        visible={confirmDelete !== null}
        title={t("cronJobs.toolBatches.deleteConfirmTitle")}
        onCancel={() => setConfirmDelete(null)}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              danger
              loading={busy}
              onClick={() =>
                confirmDelete && void handleDeleteConfirmed(confirmDelete)
              }
            >
              {t("cronJobs.toolBatches.deleteConfirmOk")}
            </Button>
          </>
        }
      >
        {t("cronJobs.toolBatches.deleteConfirmBody", {
          name: confirmDelete || "",
        })}
      </Modal>
    </>
  );
}
