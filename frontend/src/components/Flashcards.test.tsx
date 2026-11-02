import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Flashcard } from "../api";
import type { SSEEvent } from "../sse";
import { useStore } from "../store";
import { mockBackend } from "../test/backend";
import { deckOf, flashcard, renderWithStore } from "../test/fixtures";
import { deckStats, Flashcards, studyOrder } from "./Flashcards";

const FORMULA = flashcard();
const POINT = flashcard({
  id: "f2", kind: "point", front: "What are support vectors?", back: "Points with functional margin exactly one.",
  source_quote: "Support vectors are the points with functional margin exactly one.", source_page: 3,
});
const LATER = flashcard({ id: "f3", kind: "point", front: "What does Slater's condition give?", back: "Strong duality.", due: false, box: 3 });

function ViewerProbe() {
  const { viewer } = useStore();
  return viewer ? <div data-testid="viewer-target">{`${viewer.pdfName}:${viewer.page}:${viewer.bboxes.length}`}</div> : null;
}

const stream = (events: SSEEvent[], error?: Error) =>
  vi.fn(async (_id: string, _body: unknown, onEvent: (e: SSEEvent) => void) => {
    for (const e of events) {
      await Promise.resolve();
      onEvent(e);
    }
    if (error) throw error;
  });

const open = (cards: Flashcard[], over: Record<string, unknown> = {}, nextDue: string | null = null) => {
  const mocks = mockBackend({ flashcards: vi.fn().mockResolvedValue(deckOf(cards, nextDue)), ...over });
  renderWithStore(<><Flashcards /><ViewerProbe /></>);
  return mocks;
};

const flip = () => fireEvent.click(screen.getByTestId("flashcard"));

/** The button is enabled once the documents have loaded. */
const clickWhenReady = async (name: RegExp) => {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
};

describe("deck helpers", () => {
  it("counts cards by kind, due state and box", () => {
    expect(deckStats([FORMULA, POINT, LATER])).toEqual({
      total: 3, due: 2, formulas: 1, points: 2, byBox: { 1: 2, 2: 0, 3: 1, 4: 0, 5: 0 },
    });
    expect(deckStats([]).total).toBe(0);
  });

  it("orders due cards first, then the weakest box, then the order of the notes", () => {
    const box2 = flashcard({ id: "b2", box: 2 });
    const box1 = flashcard({ id: "b1", box: 1 });
    const box1b = flashcard({ id: "b1b", box: 1 });
    expect(studyOrder([LATER, box2, box1, box1b]).map((c) => c.id)).toEqual(["b1", "b1b", "b2", "f3"]);
  });
});

describe("Flashcards deck", () => {
  it("makes a deck from the streamed events and shows it as it grows", async () => {
    let stored: Flashcard[] = [];
    const replay = stream([
      { event: "deck", data: { sections: 2, skipped: 0 } },
      { event: "progress", data: { index: 1, total: 2, section: "3 Optimal Margin" } },
      { event: "cards", data: { section: "3 Optimal Margin", cards: [FORMULA] } },
      { event: "cards", data: { section: "4 Kernels", cards: [POINT] } },
      { event: "done", data: { created: 2, sections: 2 } },
    ]);
    const generate = vi.fn(async (id: string, body: unknown, onEvent: (e: SSEEvent) => void) => {
      await replay(id, body, onEvent);
      stored = [FORMULA, POINT];
    });
    const mocks = open([], {
      flashcards: vi.fn(async () => deckOf(stored)),
      generateFlashcards: generate,
    });
    await clickWhenReady(/Make flashcards/);
    await waitFor(() => expect(generate).toHaveBeenCalled());
    expect(generate.mock.calls[0].slice(0, 2)).toEqual(["c1", { regenerate: false }]);
    expect(await screen.findByText("Made 2 new cards.")).toBeInTheDocument();
    const summary = screen.getByLabelText("Deck summary");
    expect(summary).toHaveTextContent("2cards");
    expect(summary).toHaveTextContent("1formulas");
    expect(summary).toHaveTextContent("1key points");
    expect(summary).toHaveTextContent("2due now");
    await waitFor(() => expect(mocks.flashcards.mock.calls.length).toBeGreaterThan(1));
  });

  it("needs processed notes before it can make cards", async () => {
    open([], { listDocuments: vi.fn().mockResolvedValue([]) });
    expect(await screen.findByText("Upload and process notes in the Library first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Make flashcards/ })).toBeDisabled();
  });

  it("shows progress while generating and lets you stop", async () => {
    let finish: () => void = () => undefined;
    let signal: AbortSignal | undefined;
    const generate = vi.fn((_id: string, _body: unknown, onEvent: (e: SSEEvent) => void, s?: AbortSignal) => {
      signal = s;
      onEvent({ event: "progress", data: { index: 2, total: 5, section: "4 Kernels" } });
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    open([], { generateFlashcards: generate });
    await clickWhenReady(/Make flashcards/);
    expect(await screen.findByText(/Section 2 of 5/)).toBeInTheDocument();
    expect(screen.getByText("· 4 Kernels", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(signal?.aborted).toBe(true);
    finish();
  });

  it("reports warnings and a model failure from the stream", async () => {
    open([], {
      generateFlashcards: stream([
        { event: "deck", data: { sections: 1, skipped: 0 } },
        { event: "warning", data: { message: "4 Kernels: the model's reply was not valid JSON." } },
        { event: "error", data: { message: "Ollama is not running" } },
        { event: "done", data: { created: 0, sections: 0 } },
      ]),
    });
    await clickWhenReady(/Make flashcards/);
    expect(await screen.findByRole("alert")).toHaveTextContent("Ollama is not running");
    expect(screen.getByText("1 section had problems")).toBeInTheDocument();
    expect(screen.getByText(/not valid JSON/)).toBeInTheDocument();
  });

  it("shows a request that fails outright", async () => {
    open([], { generateFlashcards: stream([], new Error("Could not reach the server")) });
    await clickWhenReady(/Make flashcards/);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });

  it("says so when every section already has cards", async () => {
    const generate = stream([
      { event: "deck", data: { sections: 0, skipped: 4 } },
      { event: "done", data: { created: 0, sections: 0 } },
    ]);
    open([FORMULA], { generateFlashcards: generate });
    await clickWhenReady(/Add cards for new notes/);
    expect(await screen.findByText(/already covers every section/)).toBeInTheDocument();
    expect(generate.mock.calls[0][1]).toEqual({ regenerate: false });
  });

  it("asks before a rebuild replaces cards and their progress", async () => {
    const generate = stream([{ event: "done", data: { created: 1, sections: 1 } }]);
    open([FORMULA], { generateFlashcards: generate });
    await clickWhenReady(/Rebuild deck/);
    const confirm = screen.getByRole("alertdialog", { name: "Rebuild deck" });
    expect(confirm).toHaveTextContent("review progress are replaced");
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(generate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Rebuild deck/ }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Rebuild" }));
    await waitFor(() => expect(generate.mock.calls[0][1]).toEqual({ regenerate: true }));
  });

  it("reports a deck that cannot be loaded", async () => {
    let serverDown = true;
    open([], {
      flashcards: vi.fn(async () => {
        if (serverDown) throw new Error("Server error");
        return deckOf([FORMULA]);
      }),
    });
    expect(screen.queryByRole("button", { name: /Make flashcards/ })).toBeNull();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load flashcards: Server error");
    expect(screen.queryByRole("button", { name: /Make flashcards/ })).toBeNull(); // never offer this on a failed load
    serverDown = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Deck summary")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("filters by kind and browses cards with a link to their page", async () => {
    open([FORMULA, POINT]);
    fireEvent.click(await screen.findByRole("button", { name: "Browse 2 cards" }));
    expect(screen.getAllByTestId("card-tile")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Key points" }));
    const tiles = screen.getAllByTestId("card-tile");
    expect(tiles).toHaveLength(1);
    expect(tiles[0]).toHaveTextContent("What are support vectors?");
    expect(screen.getByRole("button", { name: /Study 1 due/ })).toBeInTheDocument();
    fireEvent.click(within(tiles[0]).getByRole("button", { name: /Lecture 04 SVMs · p. 3/ }));
    expect(screen.getByTestId("viewer-target")).toHaveTextContent("Lecture_04_SVMs.pdf:3:1");
    fireEvent.click(screen.getByRole("button", { name: "Hide cards" }));
    expect(screen.queryByTestId("card-tile")).toBeNull();
  });

  it("says when you are caught up and still lets you study everything", async () => {
    open([LATER], {}, "2026-11-20T09:00:00+00:00");
    expect(await screen.findByTestId("caught-up")).toHaveTextContent("All caught up. Next review:");
    fireEvent.click(screen.getByRole("button", { name: /Study all 1/ }));
    expect(await screen.findByText("What does Slater's condition give?")).toBeInTheDocument();
  });

  it("studies only due cards unless you ask for all", async () => {
    open([FORMULA, LATER]);
    fireEvent.click(await screen.findByRole("button", { name: /Study 1 due/ }));
    expect(screen.getByText("Card 1 of 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "End session" }));
    fireEvent.click(await screen.findByRole("button", { name: "Study all 2" }));
    expect(screen.getByText("Card 1 of 2")).toBeInTheDocument();
  });
});

describe("Flashcards study session", () => {
  const start = async (cards: Flashcard[], over: Record<string, unknown> = {}) => {
    const mocks = open(cards, {
      reviewFlashcard: vi.fn(async (id: string) => cards.find((c) => c.id === id)),
      ...over,
    });
    fireEvent.click(await screen.findByRole("button", { name: /Study \d+ due/ }));
    return mocks;
  };

  it("hides the answer until the card is flipped", async () => {
    await start([FORMULA]);
    const card = screen.getByTestId("flashcard");
    expect(card).toHaveAccessibleName("Show the answer");
    expect(screen.getByTestId("card-back")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: /Got it/ })).toBeNull();
    flip();
    expect(card).toHaveClass("flipped");
    expect(card).toHaveAccessibleName("Show the question");
    expect(screen.getByTestId("card-back")).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByTestId("card-back").querySelector(".katex")).not.toBeNull();
    expect(screen.getByText(/The margin is the distance/)).toBeInTheDocument();
    flip();
    expect(card).not.toHaveClass("flipped");
  });

  it("grades cards, brings forgotten ones back and summarises the session", async () => {
    const mocks = await start([FORMULA, POINT]);
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Again/ }));
    await waitFor(() => expect(mocks.reviewFlashcard).toHaveBeenCalledWith("f1", "again"));
    expect(await screen.findByText("Card 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("What are support vectors?")).toBeInTheDocument();
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Got it/ }));
    expect(await screen.findByText("Card 3 of 3")).toBeInTheDocument();
    expect(screen.getByText("State the geometric margin.")).toBeInTheDocument();
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Got it/ }));
    const done = await screen.findByTestId("session-done");
    expect(done).toHaveTextContent("2 known · 1 to review again");
    expect(mocks.reviewFlashcard).toHaveBeenCalledTimes(3);
  });

  it("can repeat a finished session and go back to the deck", async () => {
    await start([FORMULA]);
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Got it/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Study these again/ }));
    expect(screen.getByText("Card 1 of 1")).toBeInTheDocument();
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Got it/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Back to deck" }));
    expect(await screen.findByLabelText("Deck summary")).toBeInTheDocument();
  });

  it("works from the keyboard, and grades only a flipped card", async () => {
    const mocks = await start([FORMULA, POINT]);
    fireEvent.keyDown(window, { key: "2" });
    fireEvent.keyDown(window, { key: "1" });
    expect(mocks.reviewFlashcard).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: " " });
    expect(screen.getByTestId("flashcard")).toHaveClass("flipped");
    fireEvent.keyDown(window, { key: "2" });
    await waitFor(() => expect(mocks.reviewFlashcard).toHaveBeenCalledWith("f1", "good"));
    expect(await screen.findByText("Card 2 of 2")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByTestId("flashcard"), { key: "Enter" });
    expect(screen.getByTestId("flashcard")).toHaveClass("flipped");
    fireEvent.keyDown(window, { key: "1" });
    await waitFor(() => expect(mocks.reviewFlashcard).toHaveBeenCalledWith("f2", "again"));
  });

  it("leaves space on a focused button to that button", async () => {
    await start([FORMULA]);
    const show = screen.getByRole("button", { name: /Show answer/ });
    show.focus();
    fireEvent.keyDown(show, { key: " " });
    expect(screen.getByTestId("flashcard")).not.toHaveClass("flipped");
    fireEvent.click(show);
    expect(screen.getByTestId("flashcard")).toHaveClass("flipped");
  });

  it("keeps the card when a review fails", async () => {
    await start([FORMULA], { reviewFlashcard: vi.fn().mockRejectedValue(new Error("Server unavailable")) });
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Got it/ }));
    expect(await screen.findByText("Server unavailable")).toBeInTheDocument();
    expect(screen.getByText("Card 1 of 1")).toBeInTheDocument();
    expect(screen.getByText("0 known · 0 again")).toBeInTheDocument();
  });

  it("opens the source page from the back of the card", async () => {
    await start([POINT]);
    flip();
    fireEvent.click(screen.getByRole("button", { name: /Lecture 04 SVMs · p. 3/ }));
    expect(screen.getByTestId("viewer-target")).toHaveTextContent("Lecture_04_SVMs.pdf:3:1");
  });
});
