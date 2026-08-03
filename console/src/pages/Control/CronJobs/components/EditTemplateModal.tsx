import { useEffect } from "react";
import { Form, Input, Modal, Select } from "@agentscope-ai/design";
import { useTranslation } from "react-i18next";
import type {
  CronTemplateInfo,
  UpdateCronTemplateRequest,
} from "../../../../api/types";
import { toTemplateDefinition } from "./packageTemplates";
import {
  templateDescription,
  templateFrequency,
  templateTitle,
} from "./templates";

interface EditTemplateModalProps {
  open: boolean;
  info: CronTemplateInfo | null;
  saving?: boolean;
  onCancel: () => void;
  onSubmit: (name: string, body: UpdateCronTemplateRequest) => Promise<boolean>;
}

interface FormValues {
  title: string;
  description: string;
  frequency: string;
  emoji: string;
  tags: string[];
  version_text: string;
  body: string;
}

const TAG_OPTIONS = ["personal", "team", "reminder", "calendar"];

/**
 * Edit a package's display metadata and docs.
 *
 * Deliberately does *not* touch the job payload, batch scripts or bundled
 * skills: those are edited by exporting the package, changing files, and
 * re-importing — a form is the wrong tool for a JSON batch program. The
 * server preserves everything this modal does not send.
 */
export function EditTemplateModal({
  open,
  info,
  saving,
  onCancel,
  onSubmit,
}: EditTemplateModalProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<FormValues>();

  useEffect(() => {
    if (!open || !info) return;
    // Prefill with the *resolved* text, not the raw literal: a package
    // forked from a shipped one stores i18n keys, and the user should start
    // editing from what they actually see on the card. Submitting a literal
    // then clears the key server-side.
    const def = toTemplateDefinition(info);
    form.setFieldsValue({
      title: templateTitle(def, t),
      description: templateDescription(def, t),
      frequency: templateFrequency(def, t),
      emoji: info.emoji,
      tags: info.tags,
      version_text: info.version_text,
      body: info.content,
    });
  }, [open, info, form, t]);

  const handleOk = async () => {
    if (!info) return;
    const values = await form.validateFields();
    const ok = await onSubmit(info.name, {
      title: values.title?.trim() || "",
      description: values.description?.trim() || "",
      frequency: values.frequency?.trim() || "",
      emoji: values.emoji?.trim() || "",
      tags: values.tags || [],
      version_text: values.version_text?.trim() || "",
      body: values.body ?? "",
    });
    if (ok) onCancel();
  };

  return (
    <Modal
      visible={open}
      title={t("cronJobs.templateEditTitle", { name: info?.name || "" })}
      okText={t("cronJobs.templateEditConfirm")}
      cancelText={t("cronJobs.cancelText")}
      confirmLoading={saving}
      onOk={handleOk}
      onCancel={onCancel}
      width={640}
    >
      <div style={{ marginBottom: 12, opacity: 0.65, fontSize: 12 }}>
        {t("cronJobs.templateEditHint")}
      </div>
      <Form form={form} layout="vertical">
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
          <Input maxLength={4} style={{ width: 100 }} />
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
        <Form.Item name="version_text" label="Version">
          <Input style={{ width: 160 }} placeholder="1.0" />
        </Form.Item>
        <Form.Item name="body" label="TEMPLATE.md">
          <Input.TextArea rows={10} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
