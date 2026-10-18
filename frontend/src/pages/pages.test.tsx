import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { backend } from "@/test/backend";
import { open, rendering, sleep, useApp } from "@/test/app";

useApp();

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
    expect(rendering.errors).toEqual([]);
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

const flowQuery = (graph: string, extra: object = {}) => btoa(JSON.stringify({
  preset: "6h", refresh: 0, dimensions: graph === "sankey" ? ["LocalHost", "RemoteCountry"] : ["Application"], filter: "",
  limit: 10, limitType: "avg", unit: "bps", graph, bidirectional: graph === "lines", previousPeriod: graph === "stacked", ...extra,
}));

describe("flow analysis", () => {
  it.each(["stacked", "stacked100", "lines", "grid", "sankey"])("draws the %s visualisation", async (graph) => {
    await open(`/flows?tab=visualize&q=${encodeURIComponent(flowQuery(graph))}`);
    await waitFor(() => expect(screen.getAllByTestId("chart").length).toBeGreaterThan(0), { timeout: 5000 });
  });

  it.each(["recent", "exporters", "rules", "threats", "overview"])("shows the %s tab", async (tab) => {
    await open(`/flows?tab=${tab}`);
    await act(() => sleep(300));
    expect(rendering.errors).toEqual([]);
  });
});
