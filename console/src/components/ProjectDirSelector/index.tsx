import {
  AlertTriangle,
  ChevronDown,
  Folder,
  FolderOpen,
  LoaderCircle,
  RotateCcw,
  X,
} from "lucide-react";
import { Button, Input, Popover } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { chatApi } from "../../api/modules/chat";
import { codingProjectApi } from "../../api/modules/codingProject";
import {
  usePendingProjectDirStore,
  type PendingProjectDirEntry,
} from "../../stores/pendingProjectDirStore";
import {
  isNativeDirectoryPickerAvailable,
  pickDirectory,
  PICK_CANCELLED,
} from "../../utils/pickDirectory";
import type { ChatProjectDirEntry, ChatProjectDirs } from "../../api/types";
import styles from "./index.module.less";

/** Last path segment, so the pill stays short. Handles both separators. */
function basename(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

/** Case-insensitive path compare, matching the server's dedupe rule. */
function samePath(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

interface ProjectDirSelectorProps {
  /**
   * The console's routing id for the conversation. This is NOT the backend
   * chat id: the console keeps a locally generated `<ms>-<rand>` session id
   * for the whole life of a chat and stashes the backend UUID alongside it,
   * so the UUID has to be looked up (see `resolveChatId` below) rather than
   * guessed from the shape of this value.
   */
  chatId: string | null | undefined;
  /**
   * The console's local session id for an unsent new chat. A pending
   * directory list is keyed by it so several unsent new chats keep
   * separate picks, and it rides along with the first message instead of
   * being persisted now.
   */
  localSessionId?: string | null;
  /**
   * Maps the console's session id to the backend chat UUID, returning null
   * while the chat has not been created yet.
   *
   * Injected rather than imported so this component does not depend on the
   * Chat page's sessionApi (and so tests can drive both states). Without
   * it the component stays in "not created yet" mode, which is the safe
   * default: picks are held locally and sent with the next message.
   */
  resolveChatId?: (sessionId: string) => string | null;
  /**
   * Bumped by the parent when the backend id becomes known, so the panel
   * re-reads the now-persisted value instead of showing the agent default
   * it loaded while the chat was still local.
   */
  refreshKey?: number;
}

/**
 * Shows the project directories the next message will operate in, and lets
 * the user manage the list: add, remove, and pick which one is PRIMARY.
 *
 * The list is ordered — index 0 is the primary directory (relative paths
 * and shell commands resolve there); the rest are extra directories the
 * agent can reach by absolute path. Every mutation PUTs the whole list,
 * so there are no incremental endpoints to race.
 *
 * Sits above the composer because it describes where the *next message*
 * will run — context for the whole input rather than one more button in
 * the action bar.
 */
export function ProjectDirSelector({
  chatId,
  localSessionId,
  resolveChatId,
  refreshKey = 0,
}: ProjectDirSelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<ChatProjectDirs | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // In-flight name edits, keyed by path. Absent means "show whatever the
  // entry currently says"; present means the user has typed something that
  // is not committed yet.
  const [nameDrafts, setNameDrafts] = useState<Record<string, string>>({});
  // Uncommitted edit of the *project* name (undefined = not editing).
  const [projectNameDraft, setProjectNameDraft] = useState<
    string | undefined
  >();
  // Undefined until probed, so the button does not flash in and out.
  const [nativePicker, setNativePicker] = useState<boolean | undefined>();

  // The backend chat UUID, or null while the chat exists only locally.
  // Looked up rather than inferred: the routing id keeps its local
  // `<ms>-<rand>` shape for the chat's whole life, so judging by shape
  // made every real chat look uncreated — which is why a bound directory
  // used to keep showing the agent default after it had taken effect.
  const realChatId = chatId ? (resolveChatId?.(chatId) ?? null) : null;
  const isRealChat = Boolean(realChatId);
  const pendingKey = localSessionId || "";
  const pending = usePendingProjectDirStore((st) =>
    pendingKey ? st.byLocalId[pendingKey] : undefined,
  );
  const pendingName = usePendingProjectDirStore((st) =>
    pendingKey ? st.nameByLocalId[pendingKey] : undefined,
  );
  const setPending = usePendingProjectDirStore((st) => st.setPending);
  const clearPending = usePendingProjectDirStore((st) => st.clearPending);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (realChatId) {
        setInfo(await chatApi.getProjectDirs(realChatId));
      } else {
        // No chat exists yet, so there is no per-chat value to read. Show
        // what the agent would use (its primary), so the pill is
        // informative before the first message rather than absent. The
        // agent workspace fallback is NOT shown: an unbound chat simply
        // renders the empty state.
        const agent = await codingProjectApi.get();
        if (agent.is_workspace_default) {
          setInfo({ project_dirs: [], source: "workspace_fallback" });
        } else {
          setInfo({
            project_dirs: [
              { path: agent.path, exists: agent.exists ?? true },
            ],
            source: "agent",
            agent_project_dirs: [
              { path: agent.path, exists: agent.exists ?? true },
            ],
          });
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [realChatId]);

  // Refresh when the chat changes, and again once its backend id resolves
  // (`realChatId` flips from null) or the parent signals that the first
  // message has been sent — otherwise the card would keep showing the
  // agent default it read while the chat was still local.
  useEffect(() => {
    setInfo(null);
    setError(null);
    void load();
  }, [chatId, realChatId, refreshKey, load]);

  // A pending pick wins the display: it is what the next message will use.
  const entries: ChatProjectDirEntry[] =
    pending?.map((entry) => ({
      path: entry.path,
      label: entry.label,
      exists: true,
    })) ??
    info?.project_dirs ??
    [];
  const isPending = Boolean(pending) && !isRealChat;
  const isSession = Boolean(pending) || info?.source === "session";
  const primary = entries[0];
  // A pending path has not been server-checked yet, so do not claim it is
  // missing; the router validates it when the first message arrives.
  const primaryMissing = pending ? false : primary ? !primary.exists : false;

  // The project's own name, falling back to the primary directory's so the
  // card always has something to show. `projectNameDraft` holds an
  // uncommitted edit; `undefined` means "not editing".
  const derivedName = primary
    ? primary.label || basename(primary.path)
    : undefined;
  const storedName = pendingName ?? info?.project_name ?? derivedName;
  const projectName = projectNameDraft ?? storedName;
  // The name as *persisted*: null when it is merely derived from the
  // primary directory. Every list mutation resends it, so without this a
  // reorder or a removal would wipe a name the user had set.
  const customName =
    pendingName ?? (info?.project_name_is_custom ? info.project_name : null) ??
    null;

  /**
   * Commit a new full list, and optionally a new project name. For a real
   * chat this PUTs; for an unsent chat it updates the pending store.
   * `null` clears the override entirely (restore-default / removing the
   * last entry).
   */
  const commit = useCallback(
    async (
      next: PendingProjectDirEntry[] | null,
      name?: string | null,
    ) => {
      // `undefined` means "leave the name alone", so it resolves to what
      // is stored; `null` clears it.
      const nextName = name === undefined ? customName : name;
      if (!isRealChat || !realChatId) {
        if (!pendingKey) {
          setError(t("projectDir.noSessionYet"));
          return;
        }
        if (next && next.length > 0) {
          setPending(pendingKey, next, nextName);
        } else {
          clearPending(pendingKey);
        }
        return;
      }
      setSaving(true);
      setError(null);
      try {
        if (next && next.length > 0) {
          setInfo(
            await chatApi.setProjectDirs(realChatId, next, nextName),
          );
        } else {
          setInfo(await chatApi.clearProjectDirs(realChatId));
        }
        clearPending(pendingKey);
      } catch (err) {
        // Surface the server's 422 (e.g. "Not a directory") instead of
        // closing the panel as if it had worked.
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [
      realChatId,
      isRealChat,
      pendingKey,
      customName,
      setPending,
      clearPending,
      t,
    ],
  );

  /** Commit a rename of the project itself (not of a directory). */
  const commitProjectName = (raw: string) => {
    const name = raw.trim();
    setProjectNameDraft(undefined);
    // Blank, or unchanged from what the primary directory already implies:
    // store nothing so the name keeps tracking the directory.
    const nextName = !name || name === derivedName ? null : name;
    if (customName === nextName) return;
    if (entries.length === 0) return;
    void commit(toPayload(entries), nextName);
  };

  const toPayload = (list: ChatProjectDirEntry[]): PendingProjectDirEntry[] =>
    list.map((entry) => ({ path: entry.path, label: entry.label ?? null }));

  const makePrimary = (index: number) => {
    if (index <= 0 || index >= entries.length) return;
    const next = [...entries];
    const [moved] = next.splice(index, 1);
    next.unshift(moved);
    void commit(toPayload(next));
  };

  const removeAt = (index: number) => {
    const next = entries.filter((_, i) => i !== index);
    // Removing the last entry is the same as restoring the default: the
    // chat goes back to inheriting the agent list.
    void commit(next.length > 0 ? toPayload(next) : null);
  };

  const setNameDraft = (path: string, value: string) =>
    setNameDrafts((prev) => ({ ...prev, [path]: value }));

  const clearNameDraft = (path: string) =>
    setNameDrafts((prev) => {
      if (!(path in prev)) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });

  /**
   * Rename by editing the entry's label. A blank name clears the label so
   * the row falls back to the folder's own basename, which is how a rename
   * is undone.
   */
  const renameAt = (index: number, rawName: string) => {
    const entry = entries[index];
    if (!entry) return;
    const name = rawName.trim();
    const nextLabel = name && name !== basename(entry.path) ? name : null;
    if ((entry.label ?? null) === nextLabel) return;
    const next = toPayload(entries);
    next[index] = { ...next[index], label: nextLabel };
    void commit(next);
  };

  // Reset half-typed names when the panel closes: reopening should show
  // what is actually bound, not a stale edit.
  useEffect(() => {
    if (open) return;
    setNameDrafts({});
    setProjectNameDraft(undefined);
  }, [open]);

  // Probe once the panel is first opened, not on mount: an unopened
  // selector should cost no requests.
  useEffect(() => {
    if (!open || nativePicker !== undefined) return;
    let alive = true;
    void isNativeDirectoryPickerAvailable().then((ok) => {
      if (alive) setNativePicker(ok);
    });
    return () => {
      alive = false;
    };
  }, [open, nativePicker]);

  const addPath = async (path: string) => {
    if (entries.some((entry) => samePath(entry.path, path))) {
      setError(t("projectDir.duplicate"));
      return;
    }
    // Added unnamed: renaming is a separate, optional gesture on the row.
    await commit([...toPayload(entries), { path, label: null }]);
  };

  /** Open the OS folder chooser and add whatever comes back. */
  const chooseNative = async () => {
    setError(null);
    try {
      const picked = await pickDirectory({
        title: t("projectDir.pickTitle"),
        defaultPath: primary?.path,
      });
      if (picked === PICK_CANCELLED) return;
      await addPath(picked);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNativePicker(false);
    }
  };

  const restoreDefault = async () => {
    await commit(null);
    setOpen(false);
  };

  const panel = (
    <div className={styles.panel}>
      <div>
        <div className={styles.panelTitle}>{t("projectDir.title")}</div>
        <div className={styles.panelHint}>{t("projectDir.listHint")}</div>
      </div>

      {entries.length > 0 ? (
        <ul className={styles.dirList}>
          {entries.map((entry, index) => {
            const isPrimary = index === 0;
            // An uncommitted edit wins; otherwise the label, and finally
            // the folder's own name. A label *is* the display name here,
            // so clearing the field falls back to the basename.
            const displayName =
              nameDrafts[entry.path] ?? (entry.label || basename(entry.path));
            return (
              <li
                className={styles.dirRow}
                data-missing={!pending && !entry.exists}
                data-primary={isPrimary}
                key={entry.path}
              >
                <div className={styles.dirMain}>
                  <span className={styles.dirName}>
                    <Folder size={12} />
                    {/* Always a real input, never a span that turns into
                        one on double-click: an affordance you have to
                        discover is one most people never find. Borderless
                        until hover/focus so a list of them still reads as
                        a list rather than a form. */}
                    <Input
                      aria-label={t("projectDir.renameAria")}
                      className={styles.nameInput}
                      disabled={saving}
                      maxLength={50}
                      onBlur={() => {
                        renameAt(index, displayName);
                        clearNameDraft(entry.path);
                      }}
                      onChange={(e) =>
                        setNameDraft(entry.path, e.target.value)
                      }
                      onKeyDown={(e) => {
                        if (e.key !== "Escape") return;
                        // Revert, and keep the key from also closing the
                        // popover out from under the user. Deliberately no
                        // blur(): it fires synchronously, so onBlur would
                        // still see the pre-revert value in its closure and
                        // save the text we just discarded. Leaving focus in
                        // place also matches how revert normally behaves —
                        // and a later blur is harmless, because the value
                        // then equals what is already stored.
                        e.stopPropagation();
                        clearNameDraft(entry.path);
                      }}
                      onPressEnter={(e) =>
                        (e.target as HTMLInputElement).blur()
                      }
                      size="small"
                      title={t("projectDir.renameHint")}
                      value={displayName}
                      variant="borderless"
                    />
                    {!pending && !entry.exists ? (
                      <span className={styles.missingTag}>
                        <AlertTriangle size={10} />
                        {t("projectDir.unavailable")}
                      </span>
                    ) : null}
                  </span>
                  <span className={styles.dirPath} title={entry.path}>
                    {entry.path}
                  </span>
                </div>
                <div className={styles.dirActions}>
                  {/* One slot, two states, same width: the label reads
                      "Primary" on the current one and "Make primary" on
                      the others, so nothing shifts when the choice moves.
                      The primary label stays lit; the others appear on
                      hover so the list reads quietly at rest. */}
                  {isPrimary ? (
                    <span className={styles.primaryLabel}>
                      {t("projectDir.primaryTag")}
                    </span>
                  ) : (
                    <Button
                      className={styles.makePrimaryBtn}
                      disabled={saving}
                      onClick={() => makePrimary(index)}
                      size="small"
                      type="text"
                    >
                      {t("projectDir.makePrimary")}
                    </Button>
                  )}
                  <Button
                    aria-label={t("projectDir.remove")}
                    disabled={saving}
                    icon={<X size={13} />}
                    onClick={() => removeAt(index)}
                    size="small"
                    title={t("projectDir.remove")}
                    type="text"
                  />
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className={styles.emptyState}>
          {loading ? (
            <LoaderCircle size={14} />
          ) : (
            t("projectDir.unbound")
          )}
        </div>
      )}

      <div className={styles.addSection}>
        {nativePicker === false ? (
          // Nothing can open a dialog here (remote console / headless
          // host), so say why instead of showing a button that fails.
          <div className={styles.panelHint}>
            {t("projectDir.pickerUnavailable")}
          </div>
        ) : (
          <Button
            block
            disabled={saving || nativePicker === undefined}
            icon={<FolderOpen size={14} />}
            loading={saving}
            onClick={() => void chooseNative()}
            size="small"
            type="primary"
          >
            {t("projectDir.chooseFolder")}
          </Button>
        )}
      </div>

      {error ? <div className={styles.errorNotice}>{error}</div> : null}

      <div className={styles.actions}>
        <Button
          disabled={saving || (!isSession && !pending)}
          icon={<RotateCcw size={12} />}
          onClick={() => void restoreDefault()}
          size="small"
        >
          {t("projectDir.restoreDefault")}
        </Button>
      </div>
    </div>
  );

  // The trigger uses a native `title` rather than an antd <Tooltip>:
  // nesting Tooltip and Popover around the same child makes both attach
  // handlers to it, and the hover-opened tooltip swallows the click that
  // should open the panel. The full paths also appear inside the panel.
  //
  // Popover requires exactly one child, so keep this comment out of the
  // JSX body — a `{/* ... */}` there turns children into an array.
  return (
    <div
      className={styles.card}
      data-missing={primaryMissing}
      data-pending={isPending}
      data-source={isPending ? "pending" : (info?.source ?? "")}
    >
      <span className={styles.cardLabel}>{t("projectDir.title")}</span>

      {loading && !info ? (
        <LoaderCircle className={styles.spin} size={13} />
      ) : primaryMissing ? (
        <AlertTriangle size={13} />
      ) : (
        <Folder size={13} />
      )}

      {entries.length > 0 ? (
        // Editable while collapsed: naming a project is the common case and
        // should not require opening the panel first.
        <Input
          aria-label={t("projectDir.projectNameLabel")}
          className={styles.cardNameInput}
          disabled={saving}
          maxLength={60}
          onBlur={(e) => commitProjectName(e.target.value)}
          onChange={(e) => setProjectNameDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.stopPropagation();
            setProjectNameDraft(undefined);
          }}
          onPressEnter={(e) => (e.target as HTMLInputElement).blur()}
          placeholder={derivedName}
          size="small"
          title={primary?.path}
          value={projectName ?? ""}
          variant="borderless"
        />
      ) : (
        <span className={styles.cardUnbound}>
          {t("projectDir.unboundShort")}
        </span>
      )}

      {entries.length > 1 ? (
        <span className={styles.countTag} title={t("projectDir.countTitle")}>
          ·{entries.length}
        </span>
      ) : null}

      <span className={styles.sourceTag}>
        {isPending
          ? t("projectDir.tagPending")
          : isSession
            ? t("projectDir.tagSession")
            : t("projectDir.tagInherited")}
      </span>

      {/* The chevron is the popover trigger, not the whole card: the name
          field lives in the card and must be typeable without opening the
          panel. Popover also takes exactly one child. */}
      <Popover
        arrow={false}
        content={panel}
        onOpenChange={setOpen}
        open={open}
        overlayClassName={styles.popover}
        placement="topLeft"
        trigger="click"
      >
        <button
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label={t("projectDir.manageAria")}
          className={styles.cardToggle}
          title={t("projectDir.manageAria")}
          type="button"
        >
          <ChevronDown size={13} />
        </button>
      </Popover>
    </div>
  );
}
