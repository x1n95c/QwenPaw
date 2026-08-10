export type ChatStatus = "idle" | "running";

export interface ChatSpec {
  id: string; // Chat UUID identifier
  session_id: string; // Session identifier (channel:user_id format)
  user_id: string; // User identifier
  channel: string; // Channel name, default: "default"
  name?: string; // Chat display name
  created_at: string | null; // Chat creation timestamp (ISO 8601)
  updated_at: string | null; // Chat last update timestamp (ISO 8601)
  meta?: Record<string, unknown>; // Additional metadata
  status?: ChatStatus; // Conversation status: idle or running
  pinned?: boolean; // Whether the chat is pinned to the top
  archived_at?: string | null; // When the chat was archived (ISO 8601), null = active
  archived?: boolean; // Computed: whether the chat is archived
}

export interface Message {
  role: string;
  content: unknown;
  [key: string]: unknown;
}

export interface ChatHistory {
  messages: Message[];
  status?: ChatStatus; // Conversation status: idle or running
}

export interface ChatUpdateRequest {
  name?: string;
  pinned?: boolean;
}

export interface ChatDeleteResponse {
  success: boolean;
  chat_id: string;
}

/** Where a chat's project-directory list comes from, highest first. */
export type ProjectDirSource =
  | "fork"
  | "mode"
  | "request"
  | "session"
  | "agent"
  | "workspace_fallback";

/** One entry of a chat's effective project-directory list. */
export interface ChatProjectDirEntry {
  path: string;
  /** User note, when one was set. */
  label?: string | null;
  /**
   * False when the configured path is missing. Surface this as an
   * "unavailable" state — do NOT silently fall back to another directory.
   */
  exists: boolean;
}

/**
 * A chat's effective project directories, ordered. Index 0 is the PRIMARY
 * directory (relative paths and shell cwd resolve there); the rest are
 * extra directories addressed by absolute path. Empty list = nothing
 * configured (tools fall back to the agent workspace, which is
 * deliberately not listed here).
 */
export interface ChatProjectDirs {
  project_dirs: ChatProjectDirEntry[];
  /** "session" = this chat overrides; "agent" = inherited. */
  source: ProjectDirSource;
  /** The agent-level default list, for showing what would be inherited. */
  agent_project_dirs?: ChatProjectDirEntry[];
  /**
   * Display name for the project as a whole — distinct from the
   * per-directory labels. Already resolved by the server (session
   * override → agent default → primary directory's name), so the UI can
   * render it directly.
   */
  project_name?: string | null;
  /** True when the name was set explicitly rather than derived. */
  project_name_is_custom?: boolean;
}

export interface BatchArchiveResult {
  succeeded: string[];
  failed: Array<{
    chat_id: string;
    reason: "not_found" | "in_progress";
    message: string;
  }>;
}

// Legacy Session type alias for backward compatibility
export type Session = ChatSpec;
