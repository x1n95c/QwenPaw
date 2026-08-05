/**
 * The preview is a "did I pick the right script" glance, not a listing.
 * These tests pin the two properties that make it that: the rendered row
 * count is bounded, and whatever it hides is still accounted for.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BATCH_PREVIEW_STEP_LIMIT, BatchStepPreview } from "./BatchStepPreview";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key}:${JSON.stringify(params)}` : key,
  }),
}));

const action = (tool: string, args: Record<string, unknown> = {}) => ({
  tool_name: tool,
  arguments: args,
});

describe("BatchStepPreview", () => {
  it("renders one row per action with its parameters", () => {
    render(
      <BatchStepPreview
        actions={[action("read_file", { file_path: "/tmp/a.txt" })]}
        actionCount={1}
        title="steps"
      />,
    );
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("file_path")).toBeInTheDocument();
    expect(screen.getByText("/tmp/a.txt")).toBeInTheDocument();
  });

  it("never renders more than the step limit", () => {
    const actions = Array.from({ length: 6 }, (_, i) => action(`tool_${i}`));
    render(
      <BatchStepPreview actions={actions} actionCount={6} title="steps" />,
    );
    for (let i = 0; i < BATCH_PREVIEW_STEP_LIMIT; i += 1) {
      expect(screen.getByText(`tool_${i}`)).toBeInTheDocument();
    }
    expect(
      screen.queryByText(`tool_${BATCH_PREVIEW_STEP_LIMIT}`),
    ).not.toBeInTheDocument();
  });

  it("accounts for the steps it does not show", () => {
    render(
      <BatchStepPreview
        actions={[action("a"), action("b")]}
        actionCount={7}
        title="steps"
      />,
    );
    // 7 total - 2 shown; a silent truncation would read as "that is the
    // whole script", which is exactly the wrong impression here.
    expect(screen.getByText(/stepsMore:{"count":5}/)).toBeInTheDocument();
  });

  it("omits the remainder line when everything is shown", () => {
    render(
      <BatchStepPreview actions={[action("a")]} actionCount={1} title="s" />,
    );
    expect(screen.queryByText(/stepsMore/)).not.toBeInTheDocument();
  });

  it("renders nothing when there are no actions", () => {
    const { container } = render(
      <BatchStepPreview actions={[]} actionCount={0} title="s" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("summarizes control-flow steps by their own fields", () => {
    render(
      <BatchStepPreview
        actions={[
          { tool_name: "goto", arguments: { label: "again", condition: "x" } },
        ]}
        actionCount={1}
        title="s"
      />,
    );
    expect(screen.getByText("goto")).toBeInTheDocument();
    expect(screen.getByText("label")).toBeInTheDocument();
    expect(screen.getByText("again")).toBeInTheDocument();
  });
});
