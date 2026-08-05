import { useEffect } from "react";
import { Checkbox, Form, Input, Modal, Select } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import api from "../../../../api";
import type { CronJobSpecOutput } from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { buildCreateTemplateRequest } from "./jobToTemplate";
import { useCronTemplates } from "./useCronTemplates";

interface SaveAsTemplateModalProps {
  open: boolean;
  job: CronJobSpecOutput | null;
  onCancel: () => void;
  onSaved?: () => void;
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
}: SaveAsTemplateModalProps) {
  const { t } = useTranslation();
  const { message } = useAppMessage();
  const [form] = Form.useForm<FormValues>();
  const { createTemplate, busy } = useCronTemplates();

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
    if (!job) return;
    const values = await form.validateFields();

    // A template must be self-contained: every pool script the job's
    // preprocess chain references is packaged under batch/ so an importer
    // can install them. A missing script aborts the save — shipping a
    // dangling reference defeats the point of packaging.
    let batchFiles: Record<string, string> | undefined;
    let batchEntry: string | undefined;
    const scriptNames = (job.preprocess?.steps || [])
      .map((step) => step.script?.trim())
      .filter((name): name is string => Boolean(name));
    if (job.preprocess?.enabled && scriptNames.length > 0) {
      const collected: Record<string, string> = {};
      for (const name of scriptNames) {
        // Sequential on purpose: the first missing script should abort
        // with its own name rather than race several failures.
        try {
          const detail = await api.getToolBatch(name);
          collected[`${name}.json`] = JSON.stringify(detail.content, null, 2);
        } catch {
          message.error(t("cronJobs.saveAsTemplateBatchMissing", { name }));
          return;
        }
      }
      batchFiles = collected;
      // The entry names the first script; the rest travel alongside and
      // are installed together by install-batches.
      batchEntry = `batch/${scriptNames[0]}.json`;
    }

    const ok = await createTemplate(
      buildCreateTemplateRequest(job, {
        name: values.name.trim(),
        title: values.title?.trim() || job.name,
        description: values.description?.trim() || "",
        frequency: values.frequency?.trim() || "",
        emoji: values.emoji?.trim() || "",
        tags: values.tags || [],
        includeDispatchTarget: Boolean(values.includeDispatchTarget),
        batchFiles,
        batchEntry,
      }),
    );
    if (ok) {
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
