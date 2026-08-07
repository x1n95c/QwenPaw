/**
 * Load the skills a cron job can attach.
 *
 * One request covers both sources — installed workspace skills and skills
 * bundled in template packages — so the picker has a single loading state.
 *
 * Deliberately not `useCronTemplates()`: that hook pulls every package's
 * full TEMPLATE.md body and file list, and `CronTemplateInfo.skills` is
 * only a list of names, with no description to render. Same reasoning as
 * `useTemplateBatchScripts`.
 */

import { useCallback, useEffect, useState } from "react";
import api from "../../../../api";
import type { CronSkillInfo } from "../../../../api/types";

export interface UseCronSkillsResult {
  skills: CronSkillInfo[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useCronSkills(): UseCronSkillsResult {
  const [skills, setSkills] = useState<CronSkillInfo[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listCronSkills();
      setSkills(data || []);
    } catch (error) {
      // No toast: attaching a skill is optional, and the rest of the job
      // form is still perfectly usable without the list.
      console.error("Failed to load cron skills", error);
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { skills, loading, refresh };
}
