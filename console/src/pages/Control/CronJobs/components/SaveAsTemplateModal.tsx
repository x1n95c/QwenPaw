import { useEffect } from "react";
import { Checkbox, Form, Input, Modal, Select } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import api from "../../../../api";
import type { CronJobSpecOutput } from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { buildCreateTemplateRequest } from "./jobToTemplate";
import { collectJobScripts } from "./saveAsTemplateScripts";
import type { UseCronTemplatesResult } from "./useCronTemplates";

interface SaveAsTemplateModalProps {
  open: boolean;
  job: CronJobSpecOutput | null;
  onCancel: () => void;
  onSaved?: () => void;
  /** Shared with the picker; see `TemplatePickerModalProps`. */
  cronTemplates: UseCronTemplatesResult;
}

interface FormValues {
  name: string;
  title: string;
  description: string;
  frequency: string;
  emoji: string;
  tags: string[];
  includeDispatchTarget: boolean;
}

const TAG_OPTIONS = ["personal", "team", "reminder", "calendar"];

/** Derive a filesystem-safe package name from a job name. */
function slugify(input: string): string {
  const slug = (input || "")
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "my-template";
}

export function SaveAsTemplateModal({
  open,
  job,
  onCancel,
  onSaved,
  cronTemplates,
}: SaveAsTemplateModalProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [form] = Form.useForm<FormValues>();
  const { createTemplate, busy } = cronTemplates;

  useEffect(() => {
    if (!open || !job) return;
    form.setFieldsValue({
      name: slugify(job.name),
      title: job.name,
      description: "",
      frequency: "",
      emoji: "",
      tags: [],
      // Off by default: the dispatch target holds a real user / session id
      // and templates are meant to be shared.
      includeDispatchTarget: false,
    });
  }, [open, job, form]);

  const handleOk = async () => {
    // Without an id there is no scripts directory to read, and the request
    // would come back 400 from the job-id gate rather than say anything
    // useful.
    if (!job?.id) return;
    const values = await form.validateFields();

    // A template packages every script the job owns, not only the ones its
    // preprocess names — applying a template copies every bundled script
    // into the new job, and the round trip has to be lossless in both
    // directions.
    const collected = await collectJobScripts({
      jobId: job.id,
      preprocess: job.preprocess,
      list: (jobId) => api.listJobBatches(jobId),
      get: (jobId, name) => api.getJobBatch(jobId, name),
    });
    if (!collected.ok) {
      message.error(
        t("cronJobs.saveAsTemplateBatchMissing", { name: collected.missing }),
      );
      return;
    }
    const hasScripts = Object.keys(collected.batchFiles).length > 0;

    const ok = await createTemplate(
      buildCreateTemplateRequest(job, {
        name: values.name.trim(),
        title: values.title?.trim() || job.name,
        description: values.description?.trim() || "",
        frequency: values.frequency?.trim() || "",
        emoji: values.emoji?.trim() || "",
        tags: values.tags || [],
        includeDispatchTarget: Boolean(values.includeDispatchTarget),
        batchFiles: hasScripts ? collected.batchFiles : undefined,
        batchEntry: collected.batchEntry,
      }),
    );
    if (ok) {
      // Say where it went. "Saved" alone leaves the user hunting: the
      // picker is split by category, and a 日程任务 saved as a template is
      // not under 循环任务 — looking there finds nothing and reads like the
      // save silently failed.
      message.success(
        t("cronJobs.saveAsTemplateSaved", {
          category: t(
            job.schedule?.type === "once"
              ? "cronJobs.scheduleTypeOnce"
              : "cronJobs.scheduleTypeRecurring",
          ),
        }),
      );
      onSaved?.();
      onCancel();
    }
  };

  return (
    <Modal
      visible={open}
      title={t("cronJobs.saveAsTemplateTitle")}
      okText={t("cronJobs.saveAsTemplateConfirm")}
      cancelText={t("cronJobs.cancelText")}
      confirmLoading={busy}
      onOk={handleOk}
      onCancel={onCancel}
      width={560}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label={t("cronJobs.templatePackageName")}
          extra={t("cronJobs.templatePackageNameHint")}
          rules={[
            {
              required: true,
              message: t("cronJobs.templatePackageNameRequired"),
            },
            {
              // Mirrors the backend's normalize_template_name checks so the
              // user sees the problem before the request round-trips.
              pattern: /^[^/\\]+$/,
              message: t("cronJobs.templatePackageNameInvalid"),
            },
          ]}
        >
          <Input placeholder="my-daily-digest" />
        </Form.Item>
        <Form.Item name="title" label={t("cronJobs.templateTitleLabel")}>
          <Input />
        </Form.Item>
        <Form.Item
          name="description"
          label={t("cronJobs.templateDescriptionLabel")}
        >
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item
          name="frequency"
          label={t("cronJobs.templateFrequencyLabel")}
          extra={t("cronJobs.templateFrequencyHint")}
        >
          <Input placeholder={t("cronJobs.templateFrequencyPlaceholder")} />
        </Form.Item>
        <Form.Item name="emoji" label={t("cronJobs.templateEmojiLabel")}>
          <Input maxLength={4} style={{ width: 100 }} placeholder="📊" />
        </Form.Item>
        <Form.Item name="tags" label={t("cronJobs.templateTagsLabel")}>
          <Select
            mode="multiple"
            allowClear
            options={TAG_OPTIONS.map((tag) => ({
              label: t(
                `cronJobs.templateTag${tag[0].toUpperCase()}${tag.slice(1)}`,
              ),
              value: tag,
            }))}
          />
        </Form.Item>
        <Form.Item name="includeDispatchTarget" valuePropName="checked">
          <Checkbox>{t("cronJobs.templateIncludeTarget")}</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
}
