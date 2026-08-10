import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/common_setup";

import { ProjectDirSelector } from "./index";
import { usePendingProjectDirStore } from "@/stores/pendingProjectDirStore";

// i18n is not initialised in the test env, so react-i18next's t() returns
// the key itself. Queries below therefore match on key names (e.g.
// "projectDir.chooseFolder") rather than on translated copy.
const MANAGE = /projectDir\.manageAria/;
const CHOOSE_FOLDER = /projectDir\.chooseFolder/;
const RESTORE = /projectDir\.restoreDefault/;
const MAKE_PRIMARY = /projectDir\.makePrimary/;
const REMOVE = "projectDir.remove";
const PROJECT_NAME = "projectDir.projectNameLabel";
const DIR_NAME = "projectDir.renameAria";

/** The backend chat id the console would resolve for a created chat. */
const CHAT_UUID = "df160662-28f8-432d-94cf-d53d97c44894";
const resolveReal = () => CHAT_UUID;
const resolveNone = () => null;

const mocks = vi.hoisted(() => ({
  getProjectDirs: vi.fn(),
  setProjectDirs: vi.fn(),
  clearProjectDirs: vi.fn(),
  getAgentProject: vi.fn(),
  pickDirectory: vi.fn(),
  isNativePickerAvailable: vi.fn(),
  // Must live inside vi.hoisted: the vi.mock factory below is hoisted to
  // the top of the file and cannot reach ordinary module-level consts.
  cancelled: Symbol("pick-cancelled"),
}));

vi.mock("@/utils/pickDirectory", () => ({
  PICK_CANCELLED: mocks.cancelled,
  pickDirectory: mocks.pickDirectory,
  isNativeDirectoryPickerAvailable: mocks.isNativePickerAvailable,
}));

vi.mock("@/api/modules/codingProject", () => ({
  codingProjectApi: { get: mocks.getAgentProject },
}));

vi.mock("@/api/modules/chat", () => ({
  chatApi: {
    getProjectDirs: mocks.getProjectDirs,
    setProjectDirs: mocks.setProjectDirs,
    clearProjectDirs: mocks.clearProjectDirs,
  },
}));

const AGENT_DEFAULT = {
  project_dirs: [{ path: "/repos/agent-default", label: null, exists: true }],
  source: "agent" as const,
  agent_project_dirs: [
    { path: "/repos/agent-default", label: null, exists: true },
  ],
  project_name: "agent-default",
  project_name_is_custom: false,
};

const SESSION_OVERRIDE = {
  project_dirs: [{ path: "/repos/session-pick", label: null, exists: true }],
  source: "session" as const,
  agent_project_dirs: [
    { path: "/repos/agent-default", label: null, exists: true },
  ],
  project_name: "session-pick",
  project_name_is_custom: false,
};

const MULTI_LIST = {
  project_dirs: [
    { path: "/repos/main-app", label: null, exists: true },
    { path: "/repos/backend", label: "backend API", exists: true },
  ],
  source: "session" as const,
  agent_project_dirs: [],
  project_name: "My App",
  project_name_is_custom: true,
};

describe("ProjectDirSelector", () => {
  beforeEach(() => {
    mocks.getProjectDirs.mockResolvedValue(AGENT_DEFAULT);
    mocks.setProjectDirs.mockResolvedValue(SESSION_OVERRIDE);
    mocks.clearProjectDirs.mockResolvedValue(AGENT_DEFAULT);
    mocks.getAgentProject.mockResolvedValue({
      path: "/repos/agent-default",
      name: "agent-default",
      is_workspace_default: false,
      workspace_dir: "/home/me/.qwenpaw/workspaces/default",
      exists: true,
    });
    mocks.isNativePickerAvailable.mockResolvedValue(true);
    mocks.pickDirectory.mockResolvedValue(mocks.cancelled);
    usePendingProjectDirStore.setState({ byLocalId: {}, nameByLocalId: {} });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  /** Render bound to a created chat and wait for the first load. */
  const renderReal = async (props = {}) => {
    const user = userEvent.setup();
    renderWithProviders(
      <ProjectDirSelector
        chatId="1783998367022-50vjoj1"
        resolveChatId={resolveReal}
        {...props}
      />,
    );
    await waitFor(() =>
      expect(mocks.getProjectDirs).toHaveBeenCalledWith(CHAT_UUID),
    );
    return user;
  };

  const openPanel = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole("button", { name: MANAGE }));
  };

  describe("chat id resolution", () => {
    it("reads the chat using the backend id, not the routing id", async () => {
      // The console keeps a local `<ms>-<rand>` session id for the whole
      // life of a chat; the backend endpoint only accepts the UUID. Using
      // the routing id made every chat look uncreated, which is why a
      // bound directory kept showing the agent default.
      await renderReal();

      expect(mocks.getProjectDirs).toHaveBeenCalledWith(CHAT_UUID);
      expect(mocks.getProjectDirs).not.toHaveBeenCalledWith(
        "1783998367022-50vjoj1",
      );
    });

    it("falls back to the agent default while the chat is local", async () => {
      renderWithProviders(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          localSessionId="local-1"
          resolveChatId={resolveNone}
        />,
      );

      await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());
      expect(mocks.getProjectDirs).not.toHaveBeenCalled();
    });

    it("re-reads once the backend id becomes known", async () => {
      // This is the fix for "it took effect but the card kept showing the
      // default": the routing id never changes, so only the resolver
      // flipping from null can trigger the refetch.
      let resolved: string | null = null;
      const { rerender } = renderWithProviders(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          localSessionId="local-1"
          resolveChatId={() => resolved}
        />,
      );
      await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());

      resolved = CHAT_UUID;
      rerender(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          localSessionId="local-1"
          refreshKey={1}
          resolveChatId={() => resolved}
        />,
      );

      await waitFor(() =>
        expect(mocks.getProjectDirs).toHaveBeenCalledWith(CHAT_UUID),
      );
    });

    it("re-reads when refreshKey changes for an existing chat", async () => {
      const { rerender } = renderWithProviders(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          resolveChatId={resolveReal}
        />,
      );
      await waitFor(() => expect(mocks.getProjectDirs).toHaveBeenCalledTimes(1));

      rerender(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          refreshKey={2}
          resolveChatId={resolveReal}
        />,
      );

      await waitFor(() =>
        expect(mocks.getProjectDirs).toHaveBeenCalledTimes(2),
      );
    });
  });

  describe("the collapsed card", () => {
    it("names what it is and shows the project name", async () => {
      await renderReal();

      expect(screen.getByText(/projectDir\.title/)).toBeInTheDocument();
      expect(await screen.findByLabelText(PROJECT_NAME)).toHaveValue(
        "agent-default",
      );
    });

    it("shows the project name in an editable field while collapsed", async () => {
      // Naming a project is the common case; it must not require opening
      // the panel first.
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      await renderReal();

      const field = await screen.findByLabelText(PROJECT_NAME);
      expect(field).toHaveValue("My App");
      expect(field).toBeEnabled();
    });

    it("commits a renamed project with the whole list", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      const field = await screen.findByLabelText(PROJECT_NAME);

      await user.clear(field);
      await user.type(field, "Renamed{Enter}");

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          [
            { path: "/repos/main-app", label: null },
            { path: "/repos/backend", label: "backend API" },
          ],
          "Renamed",
        ),
      );
    });

    it("clearing the name lets it track the primary directory again", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      const field = await screen.findByLabelText(PROJECT_NAME);

      await user.clear(field);
      await user.keyboard("{Enter}");

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          expect.any(Array),
          null,
        ),
      );
    });

    it("Escape reverts the name without saving", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      const field = await screen.findByLabelText(PROJECT_NAME);

      await user.clear(field);
      await user.type(field, "Discarded{Escape}");

      await waitFor(() => expect(field).toHaveValue("My App"));
      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("typing a name equal to the derived one stores nothing", async () => {
      // Otherwise the name would stop following the directory for no
      // visible reason.
      const user = await renderReal();
      const field = await screen.findByLabelText(PROJECT_NAME);

      await user.clear(field);
      await user.type(field, "agent-default{Enter}");

      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("shows the count only when several directories are bound", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      await renderReal();

      expect(await screen.findByText("·2")).toBeInTheDocument();
    });

    it("says unbound rather than showing the workspace path", async () => {
      mocks.getProjectDirs.mockResolvedValue({
        project_dirs: [],
        source: "workspace_fallback",
        agent_project_dirs: [],
      });
      await renderReal();

      expect(
        await screen.findByText(/projectDir\.unboundShort/),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/\.qwenpaw\/workspaces/),
      ).not.toBeInTheDocument();
    });

    it("flags a missing primary instead of hiding it", async () => {
      mocks.getProjectDirs.mockResolvedValue({
        project_dirs: [{ path: "/repos/gone", label: null, exists: false }],
        source: "agent",
        agent_project_dirs: [],
        project_name: "gone",
      });
      const { container } = renderWithProviders(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          resolveChatId={resolveReal}
        />,
      );

      await waitFor(() =>
        expect(
          container.querySelector('[data-missing="true"]'),
        ).toBeInTheDocument(),
      );
    });
  });

  describe("the directory list", () => {
    it("renders every entry, marking only the first as primary", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      await openPanel(user);

      const names = await screen.findAllByLabelText(DIR_NAME);
      expect(names.map((el) => (el as HTMLInputElement).value)).toEqual([
        "main-app",
        // A directory label *is* its display name, replacing the basename.
        "backend API",
      ]);
      expect(screen.getByText("/repos/backend")).toBeInTheDocument();
      expect(screen.getAllByText(/projectDir\.primaryTag/)).toHaveLength(1);
      expect(
        screen.getAllByRole("button", { name: MAKE_PRIMARY }),
      ).toHaveLength(1);
    });

    it("make-primary moves the entry to the front", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: MAKE_PRIMARY }),
      );

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          [
            { path: "/repos/backend", label: "backend API" },
            { path: "/repos/main-app", label: null },
          ],
          "My App",
        ),
      );
    });

    it("renaming a directory commits its label", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      await openPanel(user);
      const fields = await screen.findAllByLabelText(DIR_NAME);

      await user.clear(fields[0]);
      await user.type(fields[0], "Frontend{Enter}");

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          [
            { path: "/repos/main-app", label: "Frontend" },
            { path: "/repos/backend", label: "backend API" },
          ],
          "My App",
        ),
      );
    });

    it("leaving a directory name untouched saves nothing", async () => {
      // Every row is a live field, so focus alone must not write.
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      await openPanel(user);
      const fields = await screen.findAllByLabelText(DIR_NAME);

      await user.click(fields[0]);
      await user.click(fields[1]);

      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("remove drops the entry", async () => {
      mocks.getProjectDirs.mockResolvedValue(MULTI_LIST);
      const user = await renderReal();
      await openPanel(user);

      const removes = await screen.findAllByRole("button", { name: REMOVE });
      await user.click(removes[1]);

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          [{ path: "/repos/main-app", label: null }],
          "My App",
        ),
      );
    });

    it("removing the last entry clears the override", async () => {
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: REMOVE }),
      );

      await waitFor(() =>
        expect(mocks.clearProjectDirs).toHaveBeenCalledWith(CHAT_UUID),
      );
    });
  });

  describe("adding a directory", () => {
    it("has no absolute-path field or in-app browser", async () => {
      // Both were replaced by the OS dialog.
      const user = await renderReal();
      await openPanel(user);

      expect(
        screen.queryByLabelText("projectDir.inputAria"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /projectDir\.browse$/ }),
      ).not.toBeInTheDocument();
    });

    it("adds the folder chosen from the OS dialog", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/picked");
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      await waitFor(() =>
        expect(mocks.setProjectDirs).toHaveBeenCalledWith(
          CHAT_UUID,
          [
            { path: "/repos/agent-default", label: null },
            { path: "/repos/picked", label: null },
          ],
          null,
        ),
      );
    });

    it("cancelling the dialog changes nothing", async () => {
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      await waitFor(() => expect(mocks.pickDirectory).toHaveBeenCalled());
      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("rejects a duplicate path client-side", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/agent-default");
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      expect(
        await screen.findByText(/projectDir\.duplicate/),
      ).toBeInTheDocument();
      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("explains itself when no dialog can be opened", async () => {
      // A remote or headless host has no way to show one, and a button
      // that always fails is worse than a sentence saying why.
      mocks.isNativePickerAvailable.mockResolvedValue(false);
      const user = await renderReal();
      await openPanel(user);

      expect(
        await screen.findByText(/projectDir\.pickerUnavailable/),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: CHOOSE_FOLDER }),
      ).not.toBeInTheDocument();
    });

    it("surfaces a dialog failure instead of failing silently", async () => {
      mocks.pickDirectory.mockRejectedValue(new Error("no display"));
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      expect(await screen.findByText("no display")).toBeInTheDocument();
    });

    it("keeps the panel open and shows a save failure", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/picked");
      mocks.setProjectDirs.mockRejectedValue(new Error("Not a directory"));
      const user = await renderReal();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      expect(await screen.findByText("Not a directory")).toBeInTheDocument();
    });
  });

  describe("restore default", () => {
    it("clears the override", async () => {
      mocks.getProjectDirs.mockResolvedValue(SESSION_OVERRIDE);
      const user = await renderReal();
      await openPanel(user);

      await user.click(await screen.findByRole("button", { name: RESTORE }));

      await waitFor(() =>
        expect(mocks.clearProjectDirs).toHaveBeenCalledWith(CHAT_UUID),
      );
    });

    it("is disabled when there is nothing to restore", async () => {
      const user = await renderReal();
      await openPanel(user);

      expect(
        await screen.findByRole("button", { name: RESTORE }),
      ).toBeDisabled();
    });
  });

  describe("a chat that does not exist yet", () => {
    const renderPending = async () => {
      const user = userEvent.setup();
      renderWithProviders(
        <ProjectDirSelector
          chatId="1783998367022-50vjoj1"
          localSessionId="local-1"
          resolveChatId={resolveNone}
        />,
      );
      await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());
      return user;
    };

    it("stores the pick locally instead of calling the chat API", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/picked");
      const user = await renderPending();
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      await waitFor(() =>
        expect(
          usePendingProjectDirStore.getState().byLocalId["local-1"],
        ).toEqual([
          { path: "/repos/agent-default", label: null },
          { path: "/repos/picked", label: null },
        ]),
      );
      expect(mocks.setProjectDirs).not.toHaveBeenCalled();
    });

    it("keeps a name typed before the first message", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/picked");
      const user = await renderPending();
      await openPanel(user);
      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );
      await waitFor(() =>
        expect(
          usePendingProjectDirStore.getState().byLocalId["local-1"],
        ).toBeTruthy(),
      );

      // Set in one shot rather than typing: the add above re-renders
      // asynchronously, and a multi-keystroke interaction gets its draft
      // reset mid-way. The change→blur pair is what the component
      // actually reacts to.
      const field = await screen.findByLabelText(PROJECT_NAME);
      fireEvent.change(field, { target: { value: "Before Send" } });
      fireEvent.blur(field);

      await waitFor(() =>
        expect(
          usePendingProjectDirStore.getState().nameByLocalId["local-1"],
        ).toBe("Before Send"),
      );
    });

    it("refuses to store a pick with no local session id", async () => {
      mocks.pickDirectory.mockResolvedValue("/repos/picked");
      const user = userEvent.setup();
      renderWithProviders(
        <ProjectDirSelector chatId={null} resolveChatId={resolveNone} />,
      );
      await waitFor(() => expect(mocks.getAgentProject).toHaveBeenCalled());
      await openPanel(user);

      await user.click(
        await screen.findByRole("button", { name: CHOOSE_FOLDER }),
      );

      expect(
        await screen.findByText(/projectDir\.noSessionYet/),
      ).toBeInTheDocument();
    });
  });
});
