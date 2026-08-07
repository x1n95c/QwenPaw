/**
 * Load and mutate one cron job's own batch scripts.
 *
 * Scripts belong to the job that runs them
 * (`<workspace>/cron_jobs/<job_id>/batch/`), so every call is scoped by
 * `jobId` and a job can only ever see its own. Reaching another job's or a
 * template's script goes through `copyBatch`, which duplicates it — the two
 * copies are then independent, which is the point.
 *
 * `jobId` is minted by the drawer when it opens, so this works before the
 * job has ever been saved.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import api from "../../../../api";
import type {
  CopyToolBatchRequest,
  CreateToolBatchRequest,
  ToolBatchDetail,
  ToolBatchImportConflict,
  ToolBatchImportResult,
  ToolBatchInfo,
  UpdateToolBatchRequest,
} from "../../../../api/types";
import { useAppMessage } from "../../../../hooks/useAppMessage";
import { parseErrorDetail } from "../../../../utils/error";

/**
 * Pull the structured bits out of a failed request.
 *
 * The backend answers 409 with `{message, conflicts: [...]}` (name
 * collisions with suggested renames) and 422 with scanner findings.
 * Surface both as text the user can act on instead of "request failed".
 */
function describeError(error: unknown, fallback: string): string {
  const detail = parseErrorDetail(error) as
    | {
        conflicts?: ToolBatchImportConflict[];
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
      const list = detail.conflicts
        .map((c) => `${c.file_name} → ${c.name} (${c.suggested_name})`)
        .join("; ");
      return detail.message ? `${detail.message}: ${list}` : list;
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

/** Trigger a browser download for an exported script zip. */
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

export interface UseToolBatchesResult {
  batches: ToolBatchInfo[];
  loading: boolean;
  busy: boolean;
  fetchBatches: () => Promise<void>;
  getBatch: (name: string) => Promise<ToolBatchDetail | null>;
  importZip: (
    file: File,
    options?: {
      select?: string[];
      renameMap?: Record<string, string>;
      overwrite?: boolean;
    },
  ) => Promise<ToolBatchImportResult | null>;
  exportBatch: (name: string) => Promise<boolean>;
  deleteBatch: (name: string) => Promise<boolean>;
  createBatch: (body: CreateToolBatchRequest) => Promise<boolean>;
  updateBatch: (name: string, body: UpdateToolBatchRequest) => Promise<boolean>;
  /** Duplicate a foreign script in; resolves to the name that landed. */
  copyBatch: (body: CopyToolBatchRequest) => Promise<string | null>;
}

export function useToolBatches(jobId: string): UseToolBatchesResult {
  const [batches, setBatches] = useState<ToolBatchInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { message } = useAppMessage();
  const { t } = useTranslation();

  const fetchBatches = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listJobBatches(jobId);
      setBatches(data || []);
    } catch (error) {
      console.error("Failed to load tool batches", error);
      setBatches([]);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    fetchBatches();
  }, [fetchBatches]);

  /** Load one script with its content for the editor; never throws. */
  const getBatch = useCallback(
    async (name: string): Promise<ToolBatchDetail | null> => {
      setBusy(true);
      try {
        return await api.getJobBatch(jobId, name);
      } catch (error) {
        console.error("Failed to load tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.loadFailed")),
        );
        return null;
      } finally {
        setBusy(false);
      }
    },
    [jobId, message, t],
  );

  /**
   * Upload a zip. Returns the raw result so the caller can run the
   * candidate-selection step when the server writes nothing and answers
   * with `{imported: [], candidates: [...]}`; null on failure (the error
   * toast has already been shown).
   */
  const importZip = useCallback(
    async (
      file: File,
      options?: {
        select?: string[];
        renameMap?: Record<string, string>;
        overwrite?: boolean;
      },
    ): Promise<ToolBatchImportResult | null> => {
      setBusy(true);
      try {
        const result = await api.uploadJobBatchZip(jobId, file, {
          select: options?.select,
          rename_map: options?.renameMap,
          overwrite: options?.overwrite,
        });
        if (result.candidates?.length) {
          // Multi-candidate zip: nothing written yet; no success toast.
          return result;
        }
        message.success(
          t("cronJobs.toolBatches.imported", {
            count: result.imported.length,
          }),
        );
        await fetchBatches();
        return result;
      } catch (error) {
        console.error("Failed to import tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.importFailed")),
        );
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fetchBatches, jobId, message, t],
  );

  const exportBatch = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        const { blob, filename } = await api.downloadJobBatchZip(jobId, name);
        saveBlob(blob, filename);
        message.success(t("cronJobs.toolBatches.exported", { name }));
        return true;
      } catch (error) {
        console.error("Failed to export tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.exportFailed")),
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [jobId, message, t],
  );

  const deleteBatch = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        await api.deleteJobBatch(jobId, name);
        message.success(t("cronJobs.toolBatches.deleted", { name }));
        await fetchBatches();
        return true;
      } catch (error) {
        console.error("Failed to delete tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.deleteFailed")),
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchBatches, jobId, message, t],
  );

  const createBatch = useCallback(
    async (body: CreateToolBatchRequest) => {
      setBusy(true);
      try {
        await api.createJobBatch(jobId, body);
        message.success(t("cronJobs.toolBatches.saved", { name: body.name }));
        await fetchBatches();
        return true;
      } catch (error) {
        console.error("Failed to save tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.saveFailed")),
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchBatches, jobId, message, t],
  );

  const updateBatch = useCallback(
    async (name: string, body: UpdateToolBatchRequest) => {
      setBusy(true);
      try {
        await api.updateJobBatch(jobId, name, body);
        message.success(t("cronJobs.toolBatches.updated", { name }));
        await fetchBatches();
        return true;
      } catch (error) {
        console.error("Failed to update tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.updateFailed")),
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fetchBatches, jobId, message, t],
  );

  /**
   * Duplicate another job's or a template's script into this job.
   *
   * Returns the name that actually landed — the server picks a free
   * variant when the preferred one is taken — or `null` on failure. The
   * caller writes the returned name into the step, so a foreign
   * identifier never reaches the form.
   */
  const copyBatch = useCallback(
    async (body: CopyToolBatchRequest): Promise<string | null> => {
      setBusy(true);
      try {
        const info = await api.copyJobBatch(jobId, body);
        await fetchBatches();
        return info.name;
      } catch (error) {
        console.error("Failed to copy tool batch", error);
        message.error(
          describeError(error, t("cronJobs.toolBatches.copyFailed")),
        );
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fetchBatches, jobId, message, t],
  );

  return {
    batches,
    loading,
    busy,
    fetchBatches,
    getBatch,
    importZip,
    exportBatch,
    deleteBatch,
    createBatch,
    updateBatch,
    copyBatch,
  };
}
