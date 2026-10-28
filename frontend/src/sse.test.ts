import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, SSEParser, errorFromResponse, streamSSE } from "./sse";

describe("SSEParser", () => {
  it("parses events split across chunks", () => {
    const p = new SSEParser();
    expect(p.push('event: token\ndata: {"te')).toEqual([]);
    expect(p.push('xt":"Hi"}\n\nevent: done\ndata: {}\n\n')).toEqual([
      { event: "token", data: { text: "Hi" } },
      { event: "done", data: {} },
    ]);
  });

  it("handles CRLF, comments, default event names and non-JSON data", () => {
    const p = new SSEParser();
    const out = p.push(": keep-alive\r\n\r\ndata: plain text\r\n\r\nevent: x\r\ndata: 1\r\n\r\n");
    expect(out).toEqual([
      { event: "message", data: "plain text" },
      { event: "x", data: 1 },
    ]);
  });

  it("joins multi-line data and flushes a trailing event", () => {
    const p = new SSEParser();
    expect(p.push("event: m\ndata: line1\ndata: line2")).toEqual([]);
    expect(p.flush()).toEqual([{ event: "m", data: "line1\nline2" }]);
    expect(p.flush()).toEqual([]);
  });

  it("ignores blocks without data", () => {
    expect(new SSEParser().push("event: lonely\n\n")).toEqual([]);
  });
});

function streamResponse(chunks: string[], init: ResponseInit = { status: 200 }) {
  const body = new ReadableStream({
    start(controller) {
      const enc = new TextEncoder();
      chunks.forEach((c) => controller.enqueue(enc.encode(c)));
      controller.close();
    },
  });
  return new Response(body, init);
}

describe("streamSSE", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts JSON and emits each event", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse(['event: a\ndata: {"n":1}\n\n', 'event: b\ndata: {"n":2}\n\n']));
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];
    await streamSSE("/api/chat", { q: 1 }, (e) => seen.push(e.event));
    expect(seen).toEqual(["a", "b"]);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ q: 1 });
  });

  it("throws ApiError with the server detail on HTTP errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Course not found" }), { status: 404 })));
    await expect(streamSSE("/x", {}, () => {})).rejects.toMatchObject({ status: 404, message: "Course not found" });
  });

  it("uses the first validation message for 422 errors", async () => {
    const res = new Response(JSON.stringify({ detail: [{ msg: "question must not be blank" }] }), { status: 422 });
    const err = await errorFromResponse(res);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("question must not be blank");
  });

  it("falls back to the status text when the body is not JSON", async () => {
    const err = await errorFromResponse(new Response("oops", { status: 500, statusText: "Server Error" }));
    expect(err.message).toBe("500 Server Error");
  });
});
