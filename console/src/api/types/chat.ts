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

/** Where a chat's project directory comes from, highest precedence first. */
export type ProjectDirSource =
  | "fork"
  | "mode"
  | "request"
  | "session"
  | "agent"
  | "workspace_fallback";

/** A chat's effective project directory, plus its provenance. */
export interface ChatProjectDir {
  /** The directory relative paths and shell commands resolve from. */
  project_dir: string;
  /** "session" = this chat overrides; "agent" = inherited. */
  source: ProjectDirSource;
  /** The agent-level default, for showing what would be inherited. */
  agent_project_dir?: string | null;
  /**
   * False when the configured path is missing. Surface this as an
   * "unavailable" state — do NOT silently fall back to another directory.
   */
  exists: boolean;
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
