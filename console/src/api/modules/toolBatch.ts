import { request } from "../request";
import { getApiUrl } from "../config";
import { buildAuthHeaders } from "../authHeaders";
import type {
  CopyToolBatchRequest,
  CreateToolBatchRequest,
  JobToolBatches,
  ToolBatchDetail,
  ToolBatchImportResult,
  ToolBatchInfo,
  UpdateToolBatchRequest,
} from "../types";

/**
 * Scripts belong to one cron job, at
 * `<workspace>/cron_jobs/<job_id>/batch/`. There is no shared pool: two
 * jobs that want the same recipe hold two independent copies, so every
 * route below is scoped to a job id.
 */
const jobBatches = (jobId: string) =>
  `/cron/jobs/${encodeURIComponent(jobId)}/batches`;

/**
 * Upload a zip of batch scripts.
 *
 * Uses fetch directly rather than request(): request() forces
 * Content-Type: application/json on POST, which would break the
 * multipart boundary. Mirrors uploadTemplateZip in modules/cronTemplate.ts.
 *
 * When the zip contains several candidate files the server writes nothing
 * and answers with `{imported: [], candidates: [...]}`; pick files and
 * re-upload the same zip with `select` to actually import.
 */
async function uploadJobBatchZip(
  jobId: string,
  file: File,
  options?: {
    select?: string[];
    rename_map?: Record<string, string>;
    overwrite?: boolean;
  },
): Promise<ToolBatchImportResult> {
  const formData = new FormData();
  formData.append("file", file);

  const params = new URLSearchParams();
  if (options?.select?.length) {
    params.set("select", options.select.join(","));
  }
  if (options?.rename_map && Object.keys(options.rename_map).length) {
    params.set("rename_map", JSON.stringify(options.rename_map));
  }
  if (options?.overwrite !== undefined) {
    params.set("overwrite", String(options.overwrite));
  }
  const qs = params.toString();
  const url = getApiUrl(`${jobBatches(jobId)}/upload${qs ? `?${qs}` : ""}`);

  const response = await fetch(url, {
    method: "POST",
    headers: buildAuthHeaders(),
    body: formData,
  });

  if (!response.ok) {
    const text = await response.text();
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      // Same shape request.ts produces so parseErrorDetail() still works.
      throw new Error(`${response.status} ${response.statusText} - ${text}`);
    }
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return (await response.json()) as ToolBatchImportResult;
}

/**
 * Download a batch script as a zip Blob.
 *
 * request() parses JSON or text, so binary responses need raw fetch.
 */
async function downloadJobBatchZip(
  jobId: string,
  name: string,
): Promise<{ blob: Blob; filename: string }> {
  const url = getApiUrl(
    `${jobBatches(jobId)}/${encodeURIComponent(name)}/export`,
  );
  const response = await fetch(url, { headers: buildAuthHeaders() });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `Request failed: ${response.status}`);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  return {
    blob: await response.blob(),
    filename: match?.[1] || `${name}.zip`,
  };
}

export const toolBatchApi = {
  listJobBatches: (jobId: string) =>
    request<ToolBatchInfo[]>(jobBatches(jobId)),

  getJobBatch: (jobId: string, name: string) =>
    request<ToolBatchDetail>(
      `${jobBatches(jobId)}/${encodeURIComponent(name)}`,
    ),

  createJobBatch: (jobId: string, body: CreateToolBatchRequest) =>
    request<ToolBatchInfo>(jobBatches(jobId), {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateJobBatch: (jobId: string, name: string, body: UpdateToolBatchRequest) =>
    request<ToolBatchInfo>(`${jobBatches(jobId)}/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  deleteJobBatch: (jobId: string, name: string) =>
    request<{ deleted: boolean; name: string }>(
      `${jobBatches(jobId)}/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  /**
   * Duplicate a script owned by another job or a template into this job.
   *
   * The source is named by fields, not a packed string, so nothing has to
   * be parsed and no foreign identifier can end up stored as a step's
   * script. The response carries the name that actually landed, which may
   * differ from `name` when that one was taken.
   */
  copyJobBatch: (jobId: string, body: CopyToolBatchRequest) =>
    request<ToolBatchInfo>(`${jobBatches(jobId)}/copy`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Other jobs' scripts in this workspace, grouped by job. */
  listWorkspaceJobBatches: (excludeJobId?: string) =>
    request<JobToolBatches[]>(
      `/cron/job-batches${
        excludeJobId
          ? `?exclude_job_id=${encodeURIComponent(excludeJobId)}`
          : ""
      }`,
    ),

  uploadJobBatchZip,
  downloadJobBatchZip,
};
