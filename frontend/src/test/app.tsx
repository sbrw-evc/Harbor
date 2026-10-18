// The whole application in a test: signed in as the administrator of the fixtures (backend.ts), with the browser
// parts jsdom lacks stubbed. Charts and maps are stubbed in setup.ts.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";
import { backend, FakeEventSource, installBackend } from "./backend";

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
export const rendering = { errors: [] as unknown[] };
const realError = console.error;

/** Installs the fake backend and catches the errors React reports while rendering; call at the top of a test file. */
export function useApp() {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    window.scrollTo = vi.fn() as never;
    backend.reset();
    installBackend();
    rendering.errors = [];
    console.error = (...a: unknown[]) => {
      if (String(a[0]).includes("The above error occurred") || a[0] instanceof Error) rendering.errors.push(a[0]);
    };
  });
  afterEach(() => {
    console.error = realError;
    vi.unstubAllGlobals();
  });
}

/** Opens a path of the application; resolves when it has settled without a rendering error. */
export async function open(path: string) {
  window.history.pushState({}, "", path);
  const { default: App } = await import("@/App");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } } });
  const view = render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(30), { timeout: 4000 });
  await act(() => sleep(400));
  expect(rendering.errors).toEqual([]);
  return view;
}
