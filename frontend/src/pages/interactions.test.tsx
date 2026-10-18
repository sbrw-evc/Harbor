import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { backend } from "@/test/backend";
import { open, rendering, sleep, useApp } from "@/test/app";
import { notify } from "@/lib/notify";

useApp();

const sent = (method: string, path: RegExp) => backend.sent.filter((s) => s.method === method && path.test(s.path));

describe("VPN clients", () => {
  it("creates a client from the form", async () => {
    await open("/clients");
    fireEvent.click(screen.getByRole("button", { name: /New client/ }));
    const dialog = await screen.findByRole("dialog");
    const inputs = within(dialog).getAllByRole("textbox");
    fireEvent.change(inputs[0], { target: { value: "Laptop" } });
    fireEvent.change(inputs[1], { target: { value: "Bob" } });
    fireEvent.click(within(dialog).getAllByRole("button").find((b) => b.getAttribute("type") === "submit")!);
    await waitFor(() => expect(sent("POST", /^\/api\/clients$/).length).toBe(1));
    expect(sent("POST", /^\/api\/clients$/)[0].body).toMatchObject({ name: "Laptop", owner: "Bob" });
  });

  it("opens the config of a client", async () => {
    await open("/clients");
    fireEvent.click(screen.getAllByTitle("Config").find((b) => !(b as HTMLButtonElement).disabled)!);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).queryAllByText(/AmneziaWG|WireGuard/).length).toBeGreaterThan(0));
    for (const b of within(dialog).getAllByRole("button")) {
      if (/link|копир|Copy|\.conf|Download/i.test(b.textContent ?? "")) fireEvent.click(b);
    }
    await act(() => sleep(200));
    expect(rendering.errors).toEqual([]);
  });

  it("acts on a client from its page", async () => {
    await open("/clients/1");
    for (const name of [/Block/, /Reissue/]) {
      const b = screen.queryAllByRole("button", { name }).find((x) => !(x as HTMLButtonElement).disabled);
      if (b) fireEvent.click(b);
      await act(() => sleep(100));
      const confirm = screen.queryAllByRole("button", { name: /Confirm|Block|Reissue/ }).pop();
      if (confirm && screen.queryByRole("dialog")) fireEvent.click(confirm);
      await act(() => sleep(100));
    }
    expect(rendering.errors).toEqual([]);
  });
});

describe("the frame of the panel", () => {
  it("opens the notifications and the account menu", async () => {
    notify.success("Saved", "the settings");
    notify.error("Failed", "boom");
    await open("/");
    fireEvent.click(screen.getByTitle("Notifications"));
    expect(await screen.findByText("Saved")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Mark all as read"));
    fireEvent.click(screen.getByTitle("Clear"));
    fireEvent.click(screen.getAllByTitle("Close").pop()!);

    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    const menu = await screen.findByRole("menu");
    for (const b of within(menu).getAllByRole("menuitem").slice(0, 6)) {
      if (!/Sign out/.test(b.textContent ?? "")) fireEvent.click(b);
      await act(() => sleep(50));
    }
    expect(rendering.errors).toEqual([]);
  });

  it("collapses the menu and searches with the palette", async () => {
    await open("/");
    fireEvent.click(screen.getByRole("button", { name: /Collapse the menu/ }));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const search = await screen.findByPlaceholderText(/Search pages and clients/);
    fireEvent.change(search, { target: { value: "cli" } });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await act(() => sleep(200));
    expect(rendering.errors).toEqual([]);
  });
});

describe("signing in", () => {
  it("signs a local user in with a second factor", async () => {
    backend.overrides.set("/api/auth/me", new Error("Sign in"));
    backend.overrides.set("/api/auth/config", { mode: "local", spa: false, local: true });
    backend.overrides.set("POST /api/auth/login", { needs_2fa: true });
    await open("/login");
    const user = screen.getAllByRole("textbox")[0];
    fireEvent.change(user, { target: { value: "admin" } });
    fireEvent.change(document.querySelector("input[type=password]")!, { target: { value: "Correct-Horse-9" } });
    fireEvent.click(screen.getByRole("button", { name: /Sign in/ }));
    await waitFor(() => expect(sent("POST", /\/api\/auth\/login/).length).toBe(1));
    expect(rendering.errors).toEqual([]);
  });
});

describe("the entry point", () => {
  it("mounts the application into #root", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    window.history.pushState({}, "", "/");
    await import("@/main");
    await waitFor(() => expect(document.getElementById("root")!.textContent!.length).toBeGreaterThan(20), { timeout: 4000 });
  });
});
