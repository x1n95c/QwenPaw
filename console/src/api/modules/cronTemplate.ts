import { request } from "../request";
import { getApiUrl } from "../config";
import { buildAuthHeaders } from "../authHeaders";
import type {
  CreateCronTemplateRequest,
  CronTemplateFileContent,
  CronTemplateImportResult,
  CronTemplateInfo,
  InstallTemplateBatchesRequest,
  InstallTemplateBatchesResult,
  InstallTemplateSkillsRequest,
  InstallTemplateSkillsResult,
  UpdateCronTemplateRequest,
} from "../types";

/**
 * Upload a template zip.
 *
 * Uses fetch directly rather than request(): request() forces
 * Content-Type: application/json on POST, which would break the
 * multipart boundary. Mirrors _uploadZip in modules/skill.ts.
 */
async function uploadTemplateZip(
  file: File,
  options?: {
    target_name?: string;
    rename_map?: Record<string, string>;
    overwrite?: boolean;
  },
): Promise<CronTemplateImportResult> {
  const formData = new FormData();
  formData.append("file", file);

  const params = new URLSearchParams();
  if (options?.target_name) {
    params.set("target_name", options.target_name);
  }
  if (options?.rename_map && Object.keys(options.rename_map).length) {
    params.set("rename_map", JSON.stringify(options.rename_map));
  }
  if (options?.overwrite !== undefined) {
    params.set("overwrite", String(options.overwrite));
  }
  const qs = params.toString();
  const url = getApiUrl(`/cron-templates/upload${qs ? `?${qs}` : ""}`);

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

  return (await response.json()) as CronTemplateImportResult;
}

/**
 * Download a template package as a zip Blob.
 *
 * request() parses JSON or text, so binary responses need raw fetch.
 */
async function downloadTemplateZip(
  name: string,
): Promise<{ blob: Blob; filename: string }> {
  const url = getApiUrl(`/cron-templates/${encodeURIComponent(name)}/export`);
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

export const cronTemplateApi = {
  listCronTemplates: (includeBuiltin = true) =>
    request<CronTemplateInfo[]>(
      `/cron-templates?include_builtin=${includeBuiltin}`,
    ),

  getCronTemplate: (name: string) =>
    request<CronTemplateInfo>(`/cron-templates/${encodeURIComponent(name)}`),

  createCronTemplate: (body: CreateCronTemplateRequest) =>
    request<CronTemplateInfo>("/cron-templates", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateCronTemplate: (name: string, body: UpdateCronTemplateRequest) =>
    request<CronTemplateInfo>(`/cron-templates/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  deleteCronTemplate: (name: string) =>
    request<{ deleted: boolean; name: string }>(
      `/cron-templates/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  forkCronTemplate: (name: string) =>
    request<CronTemplateInfo>(
      `/cron-templates/${encodeURIComponent(name)}/fork`,
      { method: "POST" },
    ),

  readCronTemplateFile: (name: string, path: string) =>
    request<CronTemplateFileContent>(
      `/cron-templates/${encodeURIComponent(name)}/files/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    ),

  installCronTemplateSkills: (
    name: string,
    body: InstallTemplateSkillsRequest = {},
  ) =>
    request<InstallTemplateSkillsResult>(
      `/cron-templates/${encodeURIComponent(name)}/install-skills`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  /**
   * Copy the package's bundled batch/*.json scripts into the shared
   * script pool, so jobs can reference them by name without depending
   * on the package staying installed.
   */
  installCronTemplateBatches: (
    name: string,
    body: InstallTemplateBatchesRequest = {},
  ) =>
    request<InstallTemplateBatchesResult>(
      `/cron-templates/${encodeURIComponent(name)}/install-batches`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  uploadCronTemplateZip: uploadTemplateZip,
  downloadCronTemplateZip: downloadTemplateZip,
};
