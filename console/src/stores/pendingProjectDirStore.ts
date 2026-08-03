import { create } from "zustand";

/**
 * A project directory the user picked for a chat that does not exist on the
 * server yet.
 *
 * A brand-new chat has no id until its first message creates it, so there is
 * nothing to persist an override against. The choice is held here, sent with
 * the first message as `request_context.pending_project_dir`, and persisted
 * server-side once the chat exists — which is also what makes the *first*
 * turn run in the chosen directory rather than the agent default.
 *
 * Keyed by the console's local session id so switching between several
 * unsent new chats does not mix their choices up. Deliberately in-memory:
 * a stale directory surviving a reload would be worse than losing the pick,
 * because it would silently steer a later conversation.
 */
interface PendingProjectDirState {
  byLocalId: Record<string, string>;
  setPending: (localId: string, path: string) => void;
  clearPending: (localId: string) => void;
  getPending: (localId: string) => string | undefined;
}

export const usePendingProjectDirStore = create<PendingProjectDirState>(
  (set, get) => ({
    byLocalId: {},

    setPending: (localId, path) =>
      set((state) => ({
        byLocalId: { ...state.byLocalId, [localId]: path },
      })),

    clearPending: (localId) =>
      set((state) => {
        if (!(localId in state.byLocalId)) return state;
        const next = { ...state.byLocalId };
        delete next[localId];
        return { byLocalId: next };
      }),

    getPending: (localId) => get().byLocalId[localId],
  }),
);

/** Read the pending directory outside React (e.g. in a submit handler). */
export function getPendingProjectDir(localId: string): string | undefined {
  if (!localId) return undefined;
  return usePendingProjectDirStore.getState().getPending(localId);
}

/** Drop the pending directory once the server has taken ownership of it. */
export function clearPendingProjectDir(localId: string): void {
  if (!localId) return;
  usePendingProjectDirStore.getState().clearPending(localId);
}
