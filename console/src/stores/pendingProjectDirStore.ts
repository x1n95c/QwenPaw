import { create } from "zustand";

/** One pending project-directory entry: a path plus an optional label. */
export interface PendingProjectDirEntry {
  path: string;
  label?: string | null;
}

/**
 * Project directories the user picked for a chat that does not exist on
 * the server yet.
 *
 * A brand-new chat has no id until its first message creates it, so there
 * is nothing to persist an override against. The list is held here, sent
 * with the first message as `request_context.pending_project_dirs`, and
 * persisted server-side once the chat exists — which is also what makes
 * the *first* turn run in the chosen directories rather than the agent
 * default. Index 0 is the primary directory.
 *
 * Keyed by the console's local session id so switching between several
 * unsent new chats does not mix their choices up. Deliberately in-memory:
 * stale directories surviving a reload would be worse than losing the
 * pick, because they would silently steer a later conversation.
 */
interface PendingProjectDirsState {
  byLocalId: Record<string, PendingProjectDirEntry[]>;
  /** Project display name chosen before the chat existed, if any. */
  nameByLocalId: Record<string, string | null>;
  setPending: (
    localId: string,
    entries: PendingProjectDirEntry[],
    projectName?: string | null,
  ) => void;
  clearPending: (localId: string) => void;
  getPending: (localId: string) => PendingProjectDirEntry[] | undefined;
  getPendingName: (localId: string) => string | null | undefined;
}

export const usePendingProjectDirStore = create<PendingProjectDirsState>(
  (set, get) => ({
    byLocalId: {},
    nameByLocalId: {},

    setPending: (localId, entries, projectName) =>
      set((state) => ({
        byLocalId: { ...state.byLocalId, [localId]: entries },
        nameByLocalId: {
          ...state.nameByLocalId,
          [localId]: projectName ?? null,
        },
      })),

    clearPending: (localId) =>
      set((state) => {
        if (!(localId in state.byLocalId)) return state;
        const next = { ...state.byLocalId };
        delete next[localId];
        const names = { ...state.nameByLocalId };
        delete names[localId];
        return { byLocalId: next, nameByLocalId: names };
      }),

    getPending: (localId) => get().byLocalId[localId],
    getPendingName: (localId) => get().nameByLocalId[localId],
  }),
);

/** Read the pending directory list outside React (e.g. a submit handler). */
export function getPendingProjectDirs(
  localId: string,
): PendingProjectDirEntry[] | undefined {
  if (!localId) return undefined;
  return usePendingProjectDirStore.getState().getPending(localId);
}

/** Read the pending project name outside React (e.g. a submit handler). */
export function getPendingProjectName(
  localId: string,
): string | null | undefined {
  if (!localId) return undefined;
  return usePendingProjectDirStore.getState().getPendingName(localId);
}

/** Drop the pending list once the server has taken ownership of it. */
export function clearPendingProjectDir(localId: string): void {
  if (!localId) return;
  usePendingProjectDirStore.getState().clearPending(localId);
}
