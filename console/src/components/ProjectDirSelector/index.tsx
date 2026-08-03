import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Folder,
  FolderSearch,
  Home,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import { Button, Input, Popover } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { chatApi } from "../../api/modules/chat";
import { codingProjectApi } from "../../api/modules/codingProject";
import { usePendingProjectDirStore } from "../../stores/pendingProjectDirStore";
import { useDirectoryBrowser } from "../DirectoryBrowser/useDirectoryBrowser";
import type { ChatProjectDir } from "../../api/types";
import styles from "./index.module.less";

/**
 * The console gives an unsent chat a local timestamp id (`<ms>-<rand>`)
 * until its first message resolves it to a server UUID. Such an id cannot be
 * used with the per-chat API — the chat does not exist yet — so it must take
 * the pending path instead, or the PUT would 404.
 */
const LOCAL_TIMESTAMP_ID = /^\d+-[a-z0-9]+$/;

function isServerChatId(chatId: string | null | undefined): boolean {
  if (!chatId || chatId === "new") return false;
  return !LOCAL_TIMESTAMP_ID.test(chatId);
}

/** Last path segment, so the pill stays short. Handles both separators. */
function basename(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

interface ProjectDirSelectorProps {
  /**
   * The server-side chat this selector acts on. Undefined/null/"new" while
   * the chat has not been created yet — in that case the selector still
   * renders and stores the choice as *pending* (see localSessionId).
   */
  chatId: string | null | undefined;
  /**
   * The console's local session id for an unsent new chat. A pending
   * directory is keyed by it so several unsent new chats keep separate
   * picks, and it rides along with the first message instead of being
   * persisted now.
   */
  localSessionId?: string | null;
}

/**
 * Shows the directory the next message will operate in, and lets the user
 * bind this chat to a different one.
 *
 * Lives in the Sender action bar (not the page header) because it describes
 * the execution context of the *next message*, which is a property of the
 * composer rather than of the whole conversation.
 */
export function ProjectDirSelector({
  chatId,
  localSessionId,
}: ProjectDirSelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<ChatProjectDir | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);

  // Counts successful listings. `data.current` alone is not enough to drive
  // the draft sync below: navigating to a directory we are already viewing
  // (Home while at home) leaves `current` unchanged, so an effect keyed only
  // on it would never fire.
  const [listingSeq, setListingSeq] = useState(0);

  // Only fetch listings once the browser is actually expanded, so opening
  // the panel to read the current directory costs no requests.
  const browser = useDirectoryBrowser({
    enabled: browsing,
    onLoaded: () => setListingSeq((n) => n + 1),
  });

  const isRealChat = isServerChatId(chatId);
  const pendingKey = localSessionId || "";
  const pending = usePendingProjectDirStore((st) =>
    pendingKey ? st.byLocalId[pendingKey] : undefined,
  );
  const setPending = usePendingProjectDirStore((st) => st.setPending);
  const clearPending = usePendingProjectDirStore((st) => st.clearPending);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (isRealChat && chatId) {
        setInfo(await chatApi.getProjectDir(chatId));
      } else {
        // No chat exists yet, so there is no per-chat value to read. Show
        // what the agent would use, so the pill is informative before the
        // first message rather than absent.
        const agent = await codingProjectApi.get();
        setInfo({
          project_dir: agent.path,
          source: agent.is_workspace_default
            ? "workspace_fallback"
            : "agent",
          agent_project_dir: agent.is_workspace_default ? null : agent.path,
          exists: agent.exists ?? true,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [chatId, isRealChat]);

  // Refresh when the chat changes so the pill never shows a stale directory
  // from the previously open conversation.
  useEffect(() => {
    setInfo(null);
    setError(null);
    // Runs for a not-yet-created chat too: load() falls back to the agent
    // default there, so the pill is never blank.
    void load();
  }, [chatId, isRealChat, load]);

  // Set once the user starts moving around in the browser. Guards the two
  // effects below from fighting each other: the seed must not clobber a
  // browsed pick, and the initial listing must not clobber the seed.
  const browsedRef = useRef(false);

  // Seed the input with the current value each time the panel opens.
  useEffect(() => {
    if (!open) {
      browsedRef.current = false;
      return;
    }
    if (browsedRef.current) return;
    const seed = pending ?? info?.project_dir;
    if (seed) setDraft(seed);
  }, [open, info, pending]);

  /**
   * Navigate *and* select: the folder you are looking at is the one "Apply"
   * will bind. Setting the draft from the clicked entry makes it immediate
   * (the listing already carries absolute paths); the effect below then
   * replaces it with the server's canonical form, which is what expands
   * "~" and resolves any "..".
   */
  // Destructured so the dependency is a plain identifier: `navigate` is
  // referentially stable inside the hook, while `browser` is a fresh object
  // every render.
  const { navigate } = browser;
  const go = useCallback(
    (next: string) => {
      browsedRef.current = true;
      if (next.startsWith("/")) setDraft(next);
      navigate(next);
    },
    [navigate],
  );

  const browsedPath = browser.data?.current;
  useEffect(() => {
    // Only after a user-initiated move — otherwise merely expanding the
    // browser would silently repoint the draft at the home directory.
    if (!browsedPath || !browsedRef.current) return;
    setDraft(browsedPath);
    // listingSeq re-runs this on every successful listing, including ones
    // that land on the same path we were already showing.
  }, [browsedPath, listingSeq]);

  const apply = async () => {
    const next = draft.trim();
    if (!next) return;

    // No chat to attach to yet: remember the choice and let the first
    // message carry it. The server re-validates the path when it persists
    // it, so a bad path surfaces then rather than being silently used.
    if (!isRealChat || !chatId) {
      if (!pendingKey) {
        setError(t("projectDir.noSessionYet"));
        return;
      }
      setPending(pendingKey, next);
      setOpen(false);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      setInfo(await chatApi.setProjectDir(chatId, next));
      clearPending(pendingKey);
      setOpen(false);
    } catch (err) {
      // Surface the server's 422 (e.g. "Not a directory") instead of
      // closing the panel as if it had worked.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const inherit = async () => {
    if (!isRealChat || !chatId) {
      // Nothing is persisted yet, so dropping the pending pick *is* the
      // whole "inherit" operation for an unsent chat.
      clearPending(pendingKey);
      setOpen(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      setInfo(await chatApi.clearProjectDir(chatId));
      clearPending(pendingKey);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // A pending pick wins the display: it is what the next message will use.
  const effectivePath = pending ?? info?.project_dir ?? "";
  const isPending = Boolean(pending) && !isRealChat;
  const isSession = Boolean(pending) || info?.source === "session";
  // A pending path has not been server-checked yet, so do not claim it is
  // missing; the router validates it when the first message arrives.
  const missing = pending ? false : info ? !info.exists : false;
  const label = effectivePath ? basename(effectivePath) : "…";

  const panel = (
    <div className={styles.panel}>
      <div>
        <div className={styles.panelTitle}>{t("projectDir.title")}</div>
        <div className={styles.panelHint}>{t("projectDir.hint")}</div>
      </div>

      <div className={styles.currentBox}>
        <span className={styles.currentLabel}>
          {isPending
            ? t("projectDir.sourcePending")
            : isSession
              ? t("projectDir.sourceSession")
              : t("projectDir.sourceAgent")}
        </span>
        {effectivePath || "—"}
      </div>

      {missing ? (
        <div className={styles.missingNotice}>
          <AlertTriangle size={13} />
          <span>{t("projectDir.unavailable")}</span>
        </div>
      ) : null}

      <div className={styles.browseRow}>
        <Input
          aria-label={t("projectDir.inputAria")}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={() => void apply()}
          placeholder={t("projectDir.placeholder")}
          size="small"
          value={draft}
        />
        <Button
          aria-expanded={browsing}
          icon={<FolderSearch size={13} />}
          onClick={() => setBrowsing((v) => !v)}
          size="small"
          title={t("projectDir.browse")}
          type={browsing ? "primary" : "default"}
        />
      </div>

      {browsing ? (
        <>
          <div className={styles.browseToolbar}>
            <Button
              icon={<Home size={12} />}
              onClick={() => go("~")}
              size="small"
              title={t("projectDir.browseHome")}
              type="text"
            />
            <Button
              // Plain navigate: re-reading a listing is not a selection, so
              // it must not move the draft.
              icon={<RotateCcw size={12} />}
              onClick={() => browser.navigate(browser.path)}
              size="small"
              title={t("projectDir.browseRefresh")}
              type="text"
            />
            <Button
              icon={
                browser.showHidden ? <Eye size={12} /> : <EyeOff size={12} />
              }
              onClick={() => browser.setShowHidden((v) => !v)}
              size="small"
              title={t("projectDir.browseHidden")}
              type={browser.showHidden ? "primary" : "text"}
            />
          </div>

          <div className={styles.browsePath} title={browser.data?.current}>
            {browser.data?.current ?? browser.path}
          </div>

          <div className={styles.browseList}>
            {browser.loading ? (
              <div className={styles.browseEmpty}>
                {t("projectDir.browseLoading")}
              </div>
            ) : browser.error ? (
              <div className={styles.browseEmpty}>{browser.error}</div>
            ) : (
              <>
                {browser.data?.parent ? (
                  <button
                    className={styles.browseItem}
                    onClick={() => go(browser.data!.parent!)}
                    type="button"
                  >
                    <Folder size={13} />
                    <span className={styles.browseItemName}>..</span>
                  </button>
                ) : null}
                {browser.data?.dirs.map((dir) => (
                  <button
                    className={styles.browseItem}
                    key={dir.path}
                    onClick={() => go(dir.path)}
                    type="button"
                  >
                    <Folder size={13} />
                    <span className={styles.browseItemName}>{dir.name}</span>
                    <ChevronRight size={12} />
                  </button>
                ))}
                {!browser.data?.dirs.length && !browser.data?.parent ? (
                  <div className={styles.browseEmpty}>
                    {t("projectDir.browseEmpty")}
                  </div>
                ) : null}
              </>
            )}
          </div>
        </>
      ) : null}

      {error ? <div className={styles.errorNotice}>{error}</div> : null}

      <div className={styles.actions}>
        <Button
          disabled={saving || !draft.trim() || draft.trim() === info?.project_dir}
          loading={saving}
          onClick={() => void apply()}
          size="small"
          type="primary"
        >
          {t("projectDir.apply")}
        </Button>
        <Button
          disabled={saving || (!isSession && !pending)}
          onClick={() => void inherit()}
          size="small"
        >
          {t("projectDir.inherit")}
        </Button>
      </div>

      {info?.agent_project_dir && isSession ? (
        <div className={styles.panelHint}>
          {t("projectDir.agentDefault", { path: info.agent_project_dir })}
        </div>
      ) : null}
    </div>
  );

  // The trigger uses a native `title` rather than an antd <Tooltip>:
  // nesting Tooltip and Popover around the same child makes both attach
  // handlers to it, and the hover-opened tooltip swallows the click that
  // should open the panel. The full path also appears inside the panel.
  //
  // Popover requires exactly one child, so keep this comment out of the
  // JSX body — a `{/* ... */}` there turns children into an array.
  return (
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
        aria-label={t("projectDir.triggerAria", {
          path: effectivePath,
        })}
        className={styles.trigger}
        data-missing={missing}
        data-pending={isPending}
        data-source={isPending ? "pending" : (info?.source ?? "")}
        title={effectivePath}
        type="button"
      >
        {loading && !info ? (
          <LoaderCircle size={13} />
        ) : missing ? (
          <AlertTriangle size={13} />
        ) : (
          <Folder size={13} />
        )}
        <span className={styles.triggerLabel}>{label}</span>
        <span className={styles.sourceTag}>
          {isPending
            ? t("projectDir.tagPending")
            : isSession
              ? t("projectDir.tagSession")
              : t("projectDir.tagInherited")}
        </span>
        <ChevronDown size={12} />
      </button>
    </Popover>
  );
}
