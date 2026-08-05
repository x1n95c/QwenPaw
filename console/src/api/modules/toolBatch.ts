import { request } from "../request";
import { getApiUrl } from "../config";
import { buildAuthHeaders } from "../authHeaders";
import type {
  CreateToolBatchRequest,
  ToolBatchDetail,
  ToolBatchImportResult,
  ToolBatchInfo,
  UpdateToolBatchRequest,
} from "../types";

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
async function uploadToolBatchZip(
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
  const url = getApiUrl(`/tool-batches/upload${qs ? `?${qs}` : ""}`);

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
async function downloadToolBatchZip(
  name: string,
): Promise<{ blob: Blob; filename: string }> {
  const url = getApiUrl(`/tool-batches/${encodeURIComponent(name)}/export`);
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
  listToolBatches: () => request<ToolBatchInfo[]>("/tool-batches"),

  getToolBatch: (name: string) =>
    request<ToolBatchDetail>(`/tool-batches/${encodeURIComponent(name)}`),

  createToolBatch: (body: CreateToolBatchRequest) =>
    request<ToolBatchInfo>("/tool-batches", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateToolBatch: (name: string, body: UpdateToolBatchRequest) =>
    request<ToolBatchInfo>(`/tool-batches/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  deleteToolBatch: (name: string) =>
    request<{ deleted: boolean; name: string }>(
      `/tool-batches/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  uploadToolBatchZip: uploadToolBatchZip,
  downloadToolBatchZip: downloadToolBatchZip,
};
