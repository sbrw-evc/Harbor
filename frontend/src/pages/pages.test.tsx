import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { backend, FakeEventSource, installBackend } from "@/test/backend";

// Charts and maps draw on a canvas jsdom does not have: their options are still built by the pages.
vi.mock("echarts-for-react", () => ({ default: () => <div data-testid="chart" /> }));
vi.mock("react-leaflet", () => {
  const Pass = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const LayersControl = Object.assign(Pass, { BaseLayer: Pass, Overlay: Pass });
  return { MapContainer: Pass, TileLayer: () => null, CircleMarker: Pass, Popup: Pass, Tooltip: Pass, LayersControl,
           useMap: () => ({ fitBounds: () => {}, setView: () => {} }) };
});

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let errors: unknown[] = [];
const realError = console.error;

beforeAll(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  window.scrollTo = vi.fn() as never;
});

afterAll(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  backend.reset();
  installBackend();
  errors = [];
  console.error = (...a: unknown[]) => {
    if (String(a[0]).includes("The above error occurred") || a[0] instanceof Error) errors.push(a[0]);
  };
});

afterEach(() => {
  console.error = realError;
  cleanup();
});

/** The whole application at a path, signed in as the administrator of the fixtures; resolves when it settled. */
export async function open(path: string) {
  window.history.pushState({}, "", path);
  const { default: App } = await import("@/App");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(80), { timeout: 4000 });
  await act(() => sleep(400));
  expect(errors).toEqual([]);
}

const PAGES: [string, RegExp][] = [
  ["/", /Overview|First steps/],
  ["/clients", /VPN clients/],
  ["/clients/1", /Traffic|Config/],
  ["/analytics", /Analytics/],
  ["/map", /map/i],
  ["/flows", /Flow/],
  ["/devices", /Network devices/],
  ["/devices/1", /Interfaces|Poll|gw/],
  ["/endpoints", /Network clients|Clients/],
  ["/network-map", /Network map/],
  ["/backups", /Backups/],
  ["/alerts", /Alerts/],
  ["/routing", /Routing/],
  ["/mikrotik-vpn", /VPN on MikroTik/],
  ["/opnsense-vpn", /VPN on OPNsense/],
  ["/server", /Amnezia|Server/],
  ["/audit", /Audit/],
  ["/users", /Users/],
  ["/settings", /Settings/],
  ["/profile", /Profile|Password/],
];

describe("every page renders with the data of the real backend", () => {
  it.each(PAGES)("%s", async (path, title) => {
    await open(path);
    await waitFor(() => expect(screen.queryAllByText(title).length, document.body.textContent!.slice(0, 600)).toBeGreaterThan(0),
      { timeout: 5000 });
    await act(() => sleep(300));
    expect(errors).toEqual([]);
  });
});

describe("outside the panel", () => {
  it("shows the sign-in page to somebody signed out", async () => {
    backend.overrides.set("/api/auth/me", new Error("Sign in"));
    window.history.pushState({}, "", "/login");
    const { default: App } = await import("@/App");
    render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>);
    expect(await screen.findByRole("button", { name: /Sign in/ })).toBeTruthy();
  });

  it("shows a shared config", async () => {
    await open("/s/token");
    expect(screen.getAllByText(/AmneziaWG|WireGuard|config/i).length).toBeGreaterThan(0);
  });
});
