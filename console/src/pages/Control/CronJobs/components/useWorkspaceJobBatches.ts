/**
 * Load the batch scripts owned by the *other* cron jobs in this workspace.
 *
 * Scripts belong to one job, so these are browse-only: picking one copies
 * it into the current job. Kept separate from `useToolBatches`, which is
 * this job's own list and is the only one that can be written to.
 */

import { useCallback, useEffect, useState } from "react";
import api from "../../../../api";
import type { JobToolBatches } from "../../../../api/types";

export interface UseWorkspaceJobBatchesResult {
  groups: JobToolBatches[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useWorkspaceJobBatches(
  excludeJobId: string,
): UseWorkspaceJobBatchesResult {
  const [groups, setGroups] = useState<JobToolBatches[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setGroups((await api.listWorkspaceJobBatches(excludeJobId)) || []);
    } catch (error) {
      // No toast: this is an optional extra in the picker, and the job's
      // own list next to it is still perfectly usable.
      console.error("Failed to load other jobs' batch scripts", error);
      setGroups([]);
    } finally {
      setLoading(false);
    }
  }, [excludeJobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { groups, loading, refresh };
}
