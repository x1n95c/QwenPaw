import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/common_setup";

import { ProjectDirSelector } from "./index";
import { usePendingProjectDirStore } from "@/stores/pendingProjectDirStore";

// i18n is not initialised in the test env, so react-i18next's t() returns
// the key itself. Queries below therefore match on key names (e.g.
// "projectDir.apply") rather than on translated copy.
const APPLY = /projectDir\.apply/;
const INHERIT = /projectDir\.inherit/;

const APPLY_BTN = /projectDir\.apply/;
const BROWSE = /projectDir\.browse$/;
const HOME = /projectDir\.browseHome/;

/** Listing mock that echoes the requested path, so navigation is observable. */
const pathAwareListing = (path: string) => ({
  current: path === "~" ? "/Users/me" : path,
  parent: "/Users",
  dirs: [
    { name: "repos", path: `${path === "~" ? "/Users/me" : path}/repos` },
    { name: "docs", path: `${path === "~" ? "/Users/me" : path}/docs` },
  ],
  selectable: true,
});

const mocks = vi.hoisted(() => ({
  getProjectDir: vi.fn(),
  setProjectDir: vi.fn(),
  clearProjectDir: vi.fn(),
  browseDirs: vi.fn(),
  getAgentProject: vi.fn(),
}));

vi.mock("@/api/modules/codingProject", () => ({
  codingProjectApi: {
    browseDirs: mocks.browseDirs,
    get: mocks.getAgentProject,
  },
}));

vi.mock("@/api/modules/chat", () => ({
  chatApi: {
    getProjectDir: mocks.getProjectDir,
    setProjectDir: mocks.setProjectDir,
    clearProjectDir: mocks.clearProjectDir,
  },
}));

const AGENT_DEFAULT = {
  project_dir: "/repos/agent-default",
  source: "agent" as const,
  agent_project_dir: "/repos/agent-default",
  exists: true,
};

const SESSION_OVERRIDE = {
  project_dir: "/repos/session-pick",
  source: "session" as const,
  agent_project_dir: "/repos/agent-default",
  exists: true,
};

describe("ProjectDirSelector", () => {
  beforeEach(() => {
    mocks.getProjectDir.mockResolvedValue(AGENT_DEFAULT);
    mocks.setProjectDir.mockResolvedValue(SESSION_OVERRIDE);
    mocks.clearProjectDir.mockResolvedValue(AGENT_DEFAULT);
    mocks.browseDirs.mockResolvedValue({
      current: "/Users/me",
      parent: "/Users",
      dirs: [
        { name: "repos", path: "/Users/me/repos" },
        { name: "docs", path: "/Users/me/docs" },
      ],
      selectable: true,
    });
    mocks.getAgentProject.mockResolvedValue({
      path: "/repos/agent-default",
      name: "agent-default",
      is_workspace_default: false,
      workspace_dir: "/home/me/.qwenpaw/workspaces/default",
      exists: true,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the agent default for a chat that does not exist yet", async () => {
    // Before the first message there is no chat to read a per-chat value
    // from, but the pill must still say where the next message will run.
    renderWithProviders(
      <ProjectDirSelector chatId={null} localSessionId="local-1" />,
    );

    expect(await screen.findByText("agent-default")).toBeInTheDocument();
    expect(mocks.getAgentProject).toHaveBeenCalled();
    expect(mocks.getProjectDir).not.toHaveBeenCalled();
  });

  it("treats the placeholder 'new' chat as not-yet-created", async () => {
    renderWithProviders(
      <ProjectDirSelector chatId="new" localSessionId="local-1" />,
    );

    await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());
    expect(mocks.getProjectDir).not.toHaveBeenCalled();
  });

  it("shows the basename and an inherited tag for an agent default", async () => {
    renderWithProviders(<ProjectDirSelector chatId="c1" />);

    await waitFor(() =>
      expect(mocks.getProjectDir).toHaveBeenCalledWith("c1"),
    );
    expect(await screen.findByText("agent-default")).toBeInTheDocument();
    const trigger = screen.getByRole("button");
    expect(trigger).toHaveAttribute("data-source", "agent");
  });

  it("marks the trigger as a session override", async () => {
    mocks.getProjectDir.mockResolvedValue(SESSION_OVERRIDE);
    renderWithProviders(<ProjectDirSelector chatId="c1" />);

    expect(await screen.findByText("session-pick")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveAttribute(
        "data-source",
        "session",
      ),
    );
  });

  it("flags a missing directory instead of hiding it", async () => {
    mocks.getProjectDir.mockResolvedValue({
      ...AGENT_DEFAULT,
      exists: false,
    });
    renderWithProviders(<ProjectDirSelector chatId="c1" />);

    await waitFor(() =>
      expect(screen.getByRole("button")).toHaveAttribute(
        "data-missing",
        "true",
      ),
    );
  });

  it("applies a typed path to this chat", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProjectDirSelector chatId="c1" />);
    await screen.findByText("agent-default");

    await user.click(screen.getByRole("button"));
    const input = await screen.findByRole("textbox");
    await user.clear(input);
    await user.type(input, "/repos/session-pick");
    await user.click(screen.getByRole("button", { name: APPLY }));

    await waitFor(() =>
      expect(mocks.setProjectDir).toHaveBeenCalledWith(
        "c1",
        "/repos/session-pick",
      ),
    );
  });

  it("clears the override to inherit the agent default", async () => {
    const user = userEvent.setup();
    mocks.getProjectDir.mockResolvedValue(SESSION_OVERRIDE);
    renderWithProviders(<ProjectDirSelector chatId="c1" />);
    await screen.findByText("session-pick");

    await user.click(screen.getByRole("button"));
    await user.click(screen.getByRole("button", { name: INHERIT }));

    await waitFor(() =>
      expect(mocks.clearProjectDir).toHaveBeenCalledWith("c1"),
    );
  });

  it("keeps the panel open and shows the error when apply fails", async () => {
    const user = userEvent.setup();
    mocks.setProjectDir.mockRejectedValue(
      new Error("Not a directory: /nope"),
    );
    renderWithProviders(<ProjectDirSelector chatId="c1" />);
    await screen.findByText("agent-default");

    await user.click(screen.getByRole("button"));
    const input = await screen.findByRole("textbox");
    await user.clear(input);
    await user.type(input, "/nope");
    await user.click(screen.getByRole("button", { name: APPLY }));

    // The failure must be visible; silently closing would read as success.
    expect(
      await screen.findByText(/Not a directory: \/nope/),
    ).toBeInTheDocument();
  });

  it("refetches when the chat changes", async () => {
    const { rerender } = renderWithProviders(
      <ProjectDirSelector chatId="c1" />,
    );
    await waitFor(() =>
      expect(mocks.getProjectDir).toHaveBeenCalledWith("c1"),
    );

    rerender(<ProjectDirSelector chatId="c2" />);

    await waitFor(() =>
      expect(mocks.getProjectDir).toHaveBeenCalledWith("c2"),
    );
  });

  it("disables inherit when there is nothing to inherit from", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProjectDirSelector chatId="c1" />);
    await screen.findByText("agent-default");

    await user.click(screen.getByRole("button"));

    expect(screen.getByRole("button", { name: INHERIT })).toBeDisabled();
  });

  describe("inline directory browser", () => {
    const openBrowser = async () => {
      const user = userEvent.setup();
      renderWithProviders(<ProjectDirSelector chatId="c1" />);
      await screen.findByText("agent-default");
      await user.click(screen.getByRole("button"));
      await user.click(await screen.findByRole("button", { name: BROWSE }));
      return user;
    };

    it("does not fetch listings until expanded", async () => {
      const user = userEvent.setup();
      renderWithProviders(<ProjectDirSelector chatId="c1" />);
      await screen.findByText("agent-default");
      await user.click(screen.getByRole("button"));

      // Opening the panel to read the current directory must cost nothing.
      expect(mocks.browseDirs).not.toHaveBeenCalled();
    });

    it("lists the home directory when expanded", async () => {
      await openBrowser();

      await waitFor(() => expect(mocks.browseDirs).toHaveBeenCalledWith("~", false));
      expect(await screen.findByText("repos")).toBeInTheDocument();
      expect(screen.getByText("docs")).toBeInTheDocument();
      // Parent entry so the user can walk back up.
      expect(screen.getByText("..")).toBeInTheDocument();
    });

    it("navigating into a folder selects it", async () => {
      mocks.browseDirs.mockImplementation(async (p: string) =>
        pathAwareListing(p),
      );
      const user = await openBrowser();
      await screen.findByText("repos");

      await user.click(screen.getByText("repos"));

      await waitFor(() =>
        expect(mocks.browseDirs).toHaveBeenCalledWith("/Users/me/repos", false),
      );
      // The whole point: no second click needed before "Apply" is usable.
      expect(await screen.findByRole("textbox")).toHaveValue(
        "/Users/me/repos",
      );
      // Selecting is still not committing — that needs "Apply".
      expect(mocks.setProjectDir).not.toHaveBeenCalled();
    });

    it("keeps the current value when the browser is merely expanded", async () => {
      // Otherwise opening the browser would silently repoint the draft at
      // the home directory the listing happens to start from.
      await openBrowser();
      await screen.findByText("repos");

      expect(screen.getByRole("textbox")).toHaveValue("/repos/agent-default");
    });

    it("walking up to the parent selects the parent", async () => {
      mocks.browseDirs.mockImplementation(async (p: string) =>
        pathAwareListing(p),
      );
      const user = await openBrowser();
      await screen.findByText("..");

      await user.click(screen.getByText(".."));

      expect(await screen.findByRole("textbox")).toHaveValue("/Users");
    });

    it("selects the home directory in its server-expanded form", async () => {
      // "~" is only meaningful to the server, so the draft must take the
      // resolved path from the listing rather than the literal tilde.
      const user = await openBrowser();
      await screen.findByText("repos");

      await user.click(screen.getByRole("button", { name: HOME }));

      expect(await screen.findByRole("textbox")).toHaveValue("/Users/me");
    });

    it("applies the browsed folder to the chat", async () => {
      mocks.browseDirs.mockImplementation(async (p: string) =>
        pathAwareListing(p),
      );
      const user = await openBrowser();
      await screen.findByText("repos");

      await user.click(screen.getByText("repos"));
      await waitFor(() =>
        expect(screen.getByRole("textbox")).toHaveValue("/Users/me/repos"),
      );
      await user.click(screen.getByRole("button", { name: APPLY_BTN }));

      await waitFor(() =>
        expect(mocks.setProjectDir).toHaveBeenCalledWith(
          "c1",
          "/Users/me/repos",
        ),
      );
    });

    it("keeps the clicked folder selected even if its listing fails", async () => {
      // The folder came from the server's own listing, so it exists and is a
      // valid choice; only reading *inside* it failed. Surfacing the error
      // while keeping the selection beats silently reverting the click.
      const user = await openBrowser();
      await screen.findByText("repos");
      mocks.browseDirs.mockRejectedValue(new Error("Permission denied"));

      await user.click(screen.getByText("docs"));

      await screen.findByText("Permission denied");
      expect(screen.getByRole("textbox")).toHaveValue("/Users/me/docs");
    });

    it("refetches with show-hidden when toggled", async () => {
      const user = await openBrowser();
      await screen.findByText("repos");
      mocks.browseDirs.mockClear();

      await user.click(
        screen.getByRole("button", { name: /projectDir\.browseHidden/ }),
      );

      await waitFor(() =>
        expect(mocks.browseDirs).toHaveBeenCalledWith("~", true),
      );
    });

    it("shows the browse error without breaking the panel", async () => {
      mocks.browseDirs.mockRejectedValue(new Error("Permission denied"));
      await openBrowser();

      expect(await screen.findByText(/Permission denied/)).toBeInTheDocument();
      // The manual path input must remain usable as a fallback.
      expect(screen.getByRole("textbox")).toBeInTheDocument();
    });

    it("reports an empty folder rather than looking broken", async () => {
      mocks.browseDirs.mockResolvedValue({
        current: "/empty",
        parent: null,
        dirs: [],
        selectable: true,
      });
      await openBrowser();

      expect(
        await screen.findByText(/projectDir\.browseEmpty/),
      ).toBeInTheDocument();
    });
  });

  describe("chat that does not exist yet (pending pick)", () => {
    beforeEach(() => {
      usePendingProjectDirStore.setState({ byLocalId: {} });
    });

    const openPanel = async (localSessionId = "local-1") => {
      const user = userEvent.setup();
      renderWithProviders(
        <ProjectDirSelector chatId={null} localSessionId={localSessionId} />,
      );
      await screen.findByText("agent-default");
      await user.click(screen.getByRole("button"));
      return user;
    };

    it("stores the pick as pending instead of calling the chat API", async () => {
      const user = await openPanel();
      const input = await screen.findByRole("textbox");
      await user.clear(input);
      await user.type(input, "/repos/picked-early");
      await user.click(screen.getByRole("button", { name: APPLY }));

      // There is no chat id, so nothing can be persisted yet.
      expect(mocks.setProjectDir).not.toHaveBeenCalled();
      expect(
        usePendingProjectDirStore.getState().getPending("local-1"),
      ).toBe("/repos/picked-early");
    });

    it("shows the pending pick instead of the agent default", async () => {
      usePendingProjectDirStore.setState({
        byLocalId: { "local-1": "/repos/picked-early" },
      });
      renderWithProviders(
        <ProjectDirSelector chatId={null} localSessionId="local-1" />,
      );

      expect(await screen.findByText("picked-early")).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.getByRole("button")).toHaveAttribute(
          "data-pending",
          "true",
        ),
      );
    });

    it("does not mark a pending pick as missing", async () => {
      // It has not been server-checked yet, so claiming it is broken would
      // be a lie the user cannot act on.
      usePendingProjectDirStore.setState({
        byLocalId: { "local-1": "/repos/not-checked-yet" },
      });
      renderWithProviders(
        <ProjectDirSelector chatId={null} localSessionId="local-1" />,
      );

      await screen.findByText("not-checked-yet");
      expect(screen.getByRole("button")).toHaveAttribute(
        "data-missing",
        "false",
      );
    });

    it("keeps separate picks for separate unsent chats", async () => {
      const user = await openPanel("local-A");
      const input = await screen.findByRole("textbox");
      await user.clear(input);
      await user.type(input, "/repos/a");
      await user.click(screen.getByRole("button", { name: APPLY }));

      const state = usePendingProjectDirStore.getState();
      expect(state.getPending("local-A")).toBe("/repos/a");
      expect(state.getPending("local-B")).toBeUndefined();
    });

    it("inherit drops the pending pick without calling the API", async () => {
      usePendingProjectDirStore.setState({
        byLocalId: { "local-1": "/repos/picked-early" },
      });
      const user = userEvent.setup();
      renderWithProviders(
        <ProjectDirSelector chatId={null} localSessionId="local-1" />,
      );
      await screen.findByText("picked-early");

      await user.click(screen.getByRole("button"));
      await user.click(screen.getByRole("button", { name: INHERIT }));

      expect(mocks.clearProjectDir).not.toHaveBeenCalled();
      expect(
        usePendingProjectDirStore.getState().getPending("local-1"),
      ).toBeUndefined();
    });

    it("refuses to store a pick with no local session id", async () => {
      const user = userEvent.setup();
      renderWithProviders(<ProjectDirSelector chatId={null} />);
      await screen.findByText("agent-default");

      await user.click(screen.getByRole("button"));
      const input = await screen.findByRole("textbox");
      await user.clear(input);
      await user.type(input, "/repos/nowhere");
      await user.click(screen.getByRole("button", { name: APPLY }));

      expect(
        await screen.findByText(/projectDir\.noSessionYet/),
      ).toBeInTheDocument();
    });
  });

  it("treats a local timestamp id as not-yet-created", async () => {
    // The console uses `<ms>-<rand>` until the first message resolves the
    // chat to a server UUID. Calling the per-chat API with it would 404.
    renderWithProviders(
      <ProjectDirSelector
        chatId="1782267071416-qs7yghe"
        localSessionId="1782267071416-qs7yghe"
      />,
    );

    await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());
    expect(mocks.getProjectDir).not.toHaveBeenCalled();
  });

  it("treats a server UUID as a real chat", async () => {
    renderWithProviders(
      <ProjectDirSelector chatId="550e8400-e29b-41d4-a716-446655440000" />,
    );

    await waitFor(() =>
      expect(mocks.getProjectDir).toHaveBeenCalledWith(
        "550e8400-e29b-41d4-a716-446655440000",
      ),
    );
    expect(mocks.getAgentProject).not.toHaveBeenCalled();
  });

  it("stores a pending pick for a local timestamp chat", async () => {
    usePendingProjectDirStore.setState({ byLocalId: {} });
    const user = userEvent.setup();
    renderWithProviders(
      <ProjectDirSelector chatId="1782267071416-qs7yghe" localSessionId="1782267071416-qs7yghe" />,
    );
    await screen.findByText("agent-default");

    await user.click(screen.getByRole("button"));
    const input = await screen.findByRole("textbox");
    await user.clear(input);
    await user.type(input, "/repos/early");
    await user.click(screen.getByRole("button", { name: APPLY }));

    expect(mocks.setProjectDir).not.toHaveBeenCalled();
    expect(
      usePendingProjectDirStore.getState().getPending("1782267071416-qs7yghe"),
    ).toBe("/repos/early");
  });
});
