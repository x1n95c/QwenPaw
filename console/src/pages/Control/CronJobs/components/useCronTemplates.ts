/**
 * Load and mutate backend template packages.
 *
 * Kept separate from useCronJobs: templates are a shared pool that outlives
 * any single job list, and the picker needs them even when the job table is
 * still loading.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import api from "../../../../api";
import type {
  CreateCronTemplateRequest,
  CronTemplateImportConflict,
  CronTemplateInfo,
  UpdateCronTemplateRequest,
} from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { parseErrorDetail } from "../../../../utils/error";

/**
 * Pull the structured bits out of a failed request.
 *
 * The backend answers 409 with either `{conflicts: [...]}` (zip import) or a
 * single `{suggested_name}` (create), and 422 with scanner findings. Surface
 * all three as text the user can act on instead of "request failed".
 */
function describeError(error: unknown, fallback: string): string {
  const detail = parseErrorDetail(error) as
    | {
        conflicts?: CronTemplateImportConflict[];
        suggested_name?: string;
        message?: string;
        detail?: string;
        type?: string;
        findings?: { title?: string; severity?: string }[];
      }
    | string
    | undefined;

  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    if (detail.conflicts?.length) {
      return detail.conflicts
        .map((c) => `${c.message} (${c.suggested_name})`)
        .join("; ");
    }
    if (detail.type === "security_scan_failed") {
      const titles = (detail.findings || [])
        .map((f) => f.title)
        .filter(Boolean)
        .slice(0, 3)
        .join("; ");
      return titles ? `${detail.detail || fallback}: ${titles}` : fallback;
    }
    if (detail.suggested_name) {
      return `${detail.message || fallback} → ${detail.suggested_name}`;
    }
    if (detail.message) return detail.message;
    if (detail.detail) return detail.detail;
  }
  if (error instanceof Error && error.message) {
    const idx = error.message.indexOf(" - ");
    return idx >= 0 ? error.message.slice(0, idx) : error.message;
  }
  return fallback;
}

/** Trigger a browser download for an exported package. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function useCronTemplates() {
  const [templates, setTemplates] = useState<CronTemplateInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { message } = useAppMessage();
  const { t } = useTranslation();

  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listCronTemplates();
      setTemplates(data || []);
    } catch (error) {
      console.error("Failed to load cron templates", error);
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  const importZip = useCallback(
    async (file: File, options?: { overwrite?: boolean }) => {
      setBusy(true);
      try {
        const result = await api.uploadCronTemplateZip(file, {
          overwrite: options?.overwrite,
        });
        message.success(
          t("cronJobs.templateImported", { count: result.count }),
        );
        await fetchTemplates();
        return true;
      } catch (error) {
        console.error("Failed to import cron template", error);
        message.error(describeError(error, t("cronJobs.templateImportFailed")));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchTemplates, message, t],
  );

  const exportTemplate = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const { blob, filename } = await api.downloadCronTemplateZip(name);
        saveBlob(blob, filename);
        message.success(t("cronJobs.templateExported", { name }));
        return true;
      } catch (error) {
        console.error("Failed to export cron template", error);
        message.error(describeError(error, t("cronJobs.templateExportFailed")));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [message, t],
  );

  const deleteTemplate = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        await api.deleteCronTemplate(name);
        message.success(t("cronJobs.templateDeleted", { name }));
        await fetchTemplates();
        return true;
      } catch (error) {
        console.error("Failed to delete cron template", error);
        message.error(describeError(error, t("cronJobs.templateDeleteFailed")));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchTemplates, message, t],
  );

  const createTemplate = useCallback(
    async (body: CreateCronTemplateRequest) => {
      setBusy(true);
      try {
        await api.createCronTemplate(body);
        message.success(t("cronJobs.templateSaved", { name: body.name }));
        await fetchTemplates();
        return true;
      } catch (error) {
        console.error("Failed to save cron template", error);
        message.error(describeError(error, t("cronJobs.templateSaveFailed")));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchTemplates, message, t],
  );

  const updateTemplate = useCallback(
    async (name: string, body: UpdateCronTemplateRequest) => {
      setBusy(true);
      try {
        await api.updateCronTemplate(name, body);
        message.success(t("cronJobs.templateUpdated", { name }));
        await fetchTemplates();
        return true;
      } catch (error) {
        console.error("Failed to update cron template", error);
        message.error(describeError(error, t("cronJobs.templateUpdateFailed")));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchTemplates, message, t],
  );

  /**
   * Copy a shipped package into the pool so it becomes editable.
   *
   * Returns the new package (not a boolean) because the caller needs it to
   * open the edit form on the copy rather than on the read-only original.
   */
  const forkTemplate = useCallback(
    async (name: string): Promise<CronTemplateInfo | null> => {
      setBusy(true);
      try {
        const forked = await api.forkCronTemplate(name);
        message.success(t("cronJobs.templateForked", { name }));
        await fetchTemplates();
        return forked;
      } catch (error) {
        console.error("Failed to fork cron template", error);
        message.error(describeError(error, t("cronJobs.templateForkFailed")));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fetchTemplates, message, t],
  );

  const installSkills = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const result = await api.installCronTemplateSkills(name, {
          target: "pool",
        });
        if (result.installed.length) {
          message.success(
            t("cronJobs.templateSkillsInstalled", {
              names: result.installed.join(", "),
            }),
          );
        } else {
          message.info(
            t("cronJobs.templateSkillsAlreadyInstalled", {
              names: result.skipped.join(", "),
            }),
          );
        }
        return true;
      } catch (error) {
        console.error("Failed to install template skills", error);
        message.error(
          describeError(error, t("cronJobs.templateSkillsInstallFailed")),
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [message, t],
  );

  return {
    templates,
    loading,
    busy,
    fetchTemplates,
    importZip,
    exportTemplate,
    deleteTemplate,
    createTemplate,
    updateTemplate,
    forkTemplate,
    installSkills,
  };
}
