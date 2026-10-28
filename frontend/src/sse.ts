/** Server-sent events over fetch (EventSource cannot POST a JSON body). */

export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function errorFromResponse(res: Response): Promise<ApiError> {
  let body: unknown = null;
  let message = `${res.status} ${res.statusText}`.trim();
  try {
    body = await res.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail) && detail[0]?.msg) message = String(detail[0].msg);
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, message, body);
}

/** Incremental parser: feed it text chunks, get back complete events. */
export class SSEParser {
  private buffer = "";

  push(chunk: string): SSEEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: SSEEvent[] = [];
    let idx: number;
    while ((idx = this.buffer.indexOf("\n\n")) >= 0) {
      const raw = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const parsed = parseBlock(raw);
      if (parsed) events.push(parsed);
    }
    return events;
  }

  flush(): SSEEvent[] {
    const rest = this.buffer.trim();
    this.buffer = "";
    const parsed = rest ? parseBlock(rest) : null;
    return parsed ? [parsed] : [];
  }
}

function parseBlock(raw: string): SSEEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  const text = data.join("\n");
  try {
    return { event, data: JSON.parse(text) };
  } catch {
    return { event, data: text };
  }
}

export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (e: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await errorFromResponse(res);
  if (!res.body) throw new ApiError(500, "The server returned an empty stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const ev of parser.push(decoder.decode(value, { stream: true }))) onEvent(ev);
  }
  for (const ev of parser.flush()) onEvent(ev);
}
