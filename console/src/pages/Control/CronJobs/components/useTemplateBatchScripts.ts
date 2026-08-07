/**
 * Load the batch scripts bundled inside cron template packages.
 *
 * Deliberately not `useCronTemplates()`: that hook pulls every package's
 * full TEMPLATE.md body and file list, and two independent instances of it
 * are already mounted on this page. This endpoint returns only what the
 * script picker renders, and carries the title *and* its i18n key so the
 * label can be resolved key-first without the template list at all.
 */

import { useCallback, useEffect, useState } from "react";
import api from "../../../../api";
import type { TemplateBatchScriptInfo } from "../../../../api/types";

export interface UseTemplateBatchScriptsResult {
  scripts: TemplateBatchScriptInfo[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useTemplateBatchScripts(): UseTemplateBatchScriptsResult {
  const [scripts, setScripts] = useState<TemplateBatchScriptInfo[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listCronTemplateBatchScripts();
      setScripts(data || []);
    } catch (error) {
      // No toast: these are an optional extra in the picker, and the job
      // list next to them is still perfectly usable.
      console.error("Failed to load template batch scripts", error);
      setScripts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { scripts, loading, refresh };
}
