import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { mockBackend } from "./test/backend";
import { course, health, renderWithStore } from "./test/fixtures";

vi.mock("react-pdf", async () => {
  const { useEffect } = await import("react");
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (d: { numPages: number }) => void }) => {
      useEffect(() => onLoadSuccess?.({ numPages: 3 }), [onLoadSuccess]);
      return <div data-testid="pdf-document">{children}</div>;
    },
    Page: ({ pageNumber }: { pageNumber: number }) => <div data-testid="pdf-page">page {pageNumber}</div>,
  };
});

describe("App", () => {
  beforeEach(() => localStorage.clear());

  it("shows the landing page when there are no courses and validates course creation", async () => {
    const created = { ...course, id: "c9", name: "Algorithms" };
    const mocks = mockBackend({
      listCourses: vi.fn().mockResolvedValueOnce([]).mockResolvedValue([created]),
      createCourse: vi.fn().mockResolvedValue(created),
    });
    renderWithStore(<App />, { toasts: false });
    fireEvent.click(await screen.findByRole("button", { name: "Create a course" }));
    const dialog = screen.getByRole("dialog", { name: "Create course" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create course" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Give the course a name");
    fireEvent.change(within(dialog).getByLabelText("Course name"), { target: { value: "Algorithms" } });
    fireEvent.change(within(dialog).getByLabelText("Course code"), { target: { value: "CS161" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create course" }));
    await waitFor(() => expect(mocks.createCourse).toHaveBeenCalledWith("Algorithms", "CS161"));
    expect(await screen.findByRole("tab", { name: /Library/ })).toHaveAttribute("aria-selected", "true");
  });

  it("navigates tabs with the mouse and arrow keys and remembers the tab", async () => {
    mockBackend();
    renderWithStore(<App />, { toasts: false });
    const library = await screen.findByRole("tab", { name: /Library/ });
    fireEvent.click(screen.getByRole("tab", { name: /Quiz/ }));
    expect(screen.getByRole("tab", { name: /Quiz/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: /Quiz/ }), { key: "ArrowRight" });
    expect(library).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(library, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: /Quiz/ })).toHaveAttribute("aria-selected", "true");
    expect(localStorage.getItem("cnr.tab")).toBe("quiz");
  });

  it("opens the split-screen PDF viewer from the library", async () => {
    mockBackend();
    renderWithStore(<App />, { toasts: false });
    fireEvent.click(await screen.findByRole("button", { name: "Open Lecture_04_SVMs.pdf" }));
    const viewer = await screen.findByRole("complementary", { name: "PDF viewer" });
    expect(within(viewer).getByTestId("viewer-page")).toHaveTextContent("1 / 3");
    fireEvent.click(within(viewer).getByRole("button", { name: "Next page" }));
    expect(within(viewer).getByTestId("pdf-page")).toHaveTextContent("page 2");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "PDF viewer" })).toBeNull();
  });

  it("shows model health", async () => {
    mockBackend({ health: vi.fn().mockResolvedValue({ ...health, llm: { ...health.llm, ok: false, detail: "Model not pulled" } }) });
    renderWithStore(<App />, { toasts: false });
    expect(await screen.findByText(/qwen2.5:7b unavailable/)).toBeInTheDocument();
  });

  it("re-checks quickly while the model is down and recovers without a reload", async () => {
    const down = { ...health, llm: { ...health.llm, ok: false, detail: "Ollama not reachable" } };
    const mocks = mockBackend({ health: vi.fn().mockResolvedValueOnce(down).mockResolvedValueOnce(down).mockResolvedValue(health) });
    renderWithStore(<App />, { toasts: false, healthRetryMs: 50 });
    expect(await screen.findByText(/qwen2.5:7b unavailable/)).toBeInTheDocument();
    expect(await screen.findByText(/qwen2.5:7b ready/)).toBeInTheDocument();
    expect(mocks.health).toHaveBeenCalledTimes(3);
  });

  it("does not re-check quickly while the model is ready", async () => {
    const mocks = mockBackend();
    renderWithStore(<App />, { toasts: false, healthRetryMs: 20 });
    expect(await screen.findByText(/qwen2.5:7b ready/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 200));
    expect(mocks.health).toHaveBeenCalledTimes(1);
  });

  it("keeps re-checking while the backend is unreachable and loads the courses once it is back", async () => {
    mockBackend({
      health: vi.fn().mockRejectedValueOnce(new Error("Failed to fetch")).mockResolvedValue(health),
      listCourses: vi.fn().mockRejectedValueOnce(new Error("Failed to fetch")).mockResolvedValue([course]),
    });
    renderWithStore(<App />, { toasts: false, healthRetryMs: 50 });
    expect(await screen.findByText(/Could not reach the server/)).toBeInTheDocument();
    expect(await screen.findByText(/qwen2.5:7b ready/)).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: /Library/ })).toBeInTheDocument();
  });

  it("reports an unreachable backend", async () => {
    mockBackend({ listCourses: vi.fn().mockRejectedValue(new Error("Failed to fetch")), health: vi.fn().mockRejectedValue(new Error("x")) });
    renderWithStore(<App />, { toasts: false });
    expect(await screen.findByText(/Could not reach the server/)).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });

  it("switches and deletes courses", async () => {
    const other = { ...course, id: "c2", name: "Statistics", code: "STAT116" };
    const mocks = mockBackend({ listCourses: vi.fn().mockResolvedValue([course, other]) });
    renderWithStore(<App />, { toasts: false });
    fireEvent.click(await screen.findByRole("button", { name: "Switch course" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Statistics/ }));
    expect(await screen.findByText("STAT116")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch course" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete this course/ }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mocks.deleteCourse).toHaveBeenCalledWith("c2"));
  });
});
