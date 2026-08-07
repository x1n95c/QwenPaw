import { request } from "../request";
import type { CronSkillInfo } from "../types/cronSkill";

export const cronSkillApi = {
  /**
   * Every skill a job in this workspace could attach, in one request.
   *
   * One endpoint rather than one per source: the job form's picker offers
   * installed skills and template-bundled skills together, and two
   * requests would mean two loading states for a distinction the user does
   * not care about while choosing.
   */
  listCronSkills: (includeBuiltin = true) =>
    request<CronSkillInfo[]>(`/cron-skills?include_builtin=${includeBuiltin}`),
};
