// The backend of the page tests: fetch answers with what the backend really returned (api-fixtures.json is written
// by backend/tests/test_api.py with API_FIXTURES set), keyed by the path with its ids replaced. Writes succeed and are
// recorded; an answer can be replaced per test.
import { vi } from "vitest";
import fixtures from "./api-fixtures.json";

const ID = /^([0-9a-f]{8}-[0-9a-f-]{27}|\d+|([0-9a-f]{2}:){5}[0-9a-f]{2})$/i;
const NAMED = new Set(["backups", "share", "guides"]);

export function fixturePath(path: string): string {
  const parts = path.split("?")[0].split("/");
  return parts.map((p, i) => (ID.test(p) ? ":id" : i > 0 && NAMED.has(parts[i - 1]) && p ? ":name" : p)).join("/");
}

export interface Sent { method: string; path: string; body: unknown }

export const backend = {
  overrides: new Map<string, unknown>(),
  sent: [] as Sent[],
  /** The answer of a GET; undefined when the real backend was never asked for it. */
  answer(path: string): unknown {
    const key = fixturePath(path);
    if (backend.overrides.has(key)) return backend.overrides.get(key);
    return (fixtures as Record<string, unknown>)[key];
  },
  reset() {
    backend.overrides.clear();
    backend.sent = [];
  },
};

function respond(status: number, body: unknown): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body ?? {});
  return new Response(text, { status, headers: { "content-type": typeof body === "string" ? "text/plain" : "application/json" } });
}

export function installBackend() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET") {
      let body: unknown = init?.body;
      try {
        body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      } catch {
        /* not JSON */
      }
      backend.sent.push({ method, path: url.pathname, body });
      const over = backend.overrides.get(`${method} ${fixturePath(url.pathname)}`);
      if (over instanceof Error) return respond(400, { detail: over.message });
      return respond(200, over ?? { ok: true });
    }
    const data = backend.answer(url.pathname);
    if (data instanceof Error) return respond(400, { detail: data.message });
    return data === undefined ? respond(404, { detail: "Not found" }) : respond(200, data);
  }));
}

/** EventSource of the live channel: never connects in jsdom. */
export class FakeEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  addEventListener() {}
  close() {}
}
