import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SetupPage from "./Setup";

const setup = vi.hoisted(() => ({
  state: vi.fn(), verify: vi.fn(), checkStorage: vi.fn(), storage: vi.fn(), account: vi.fn(), keystone: vi.fn(),
}));
const notify = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const bootstrap = vi.hoisted(() => vi.fn(() => Promise.resolve()));

vi.mock("@/lib/api", () => ({ api: { setup } }));
vi.mock("@/lib/notify", () => ({ notify }));
vi.mock("@/store/auth", () => ({ useAuth: (pick: (s: { bootstrap: () => Promise<void> }) => unknown) => pick({ bootstrap }) }));

const DEFAULTS = {
  postgres: { host: "postgres", port: 5432, database: "amnezia", user: "amnezia" },
  clickhouse: { url: "http://clickhouse:8123", user: "amnezia", database: "flows" },
  openbao: { addr: "http://openbao:8200", mode: "bundled" },
};
const STORAGE = { stage: "storage", defaults: DEFAULTS, error: null };
const ACCOUNT = { stage: "account", defaults: null, error: null };
const SECRETS = { root_token: "s.root", recovery_key: "recovery==" };

const location = { origin: "https://nc.test", replace: vi.fn(), assign: vi.fn() };

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/setup"]}>
        <Routes>
          <Route path="/setup" element={<SetupPage />} />
          <Route path="/" element={<div>overview</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const group = (name: string, i = 0) => screen.getAllByRole("group", { name })[i];
const input = (name: string, i = 0) => group(name, i).querySelector("input, textarea") as HTMLInputElement;
const type = (el: Element, value: string) => fireEvent.change(el, { target: { value } });

/** Through the setup code to the storage step. */
async function toStorage() {
  setup.state.mockResolvedValue(STORAGE);
  setup.verify.mockResolvedValue({ ok: true, stage: "storage" });
  renderWizard();
  type(await screen.findByRole("textbox"), "abcd-efgh-ijkl");
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  await screen.findByText("PostgreSQL (TimescaleDB)");
  type(input("Password", 0), "pg-pass");
  type(input("Password", 1), "ch-pass");
}

/** Saves the storage; the server answers with these keys of OpenBao. */
async function save(openbao: typeof SECRETS | null) {
  setup.storage.mockResolvedValue({ stage: "account", openbao });
  fireEvent.click(screen.getByRole("button", { name: /Save and continue/ }));
  await waitFor(() => expect(setup.storage).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("location", { ...window.location, ...location });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the setup code", () => {
  it("is refused with a notification, then accepted", async () => {
    setup.state.mockResolvedValue(STORAGE);
    setup.verify.mockRejectedValueOnce(new Error("Wrong setup code")).mockResolvedValueOnce({ ok: true });
    renderWizard();
    const code = await screen.findByRole("textbox");
    type(code, "wrongcode123");
    expect((code as HTMLInputElement).value).toBe("WRONGCODE123");
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The setup code is not accepted", "Wrong setup code"));
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await screen.findByText("PostgreSQL (TimescaleDB)");
    expect(notify.success).toHaveBeenCalledWith("The setup code is accepted");
    expect(sessionStorage.getItem("nc_setup_code")).toBe("WRONGCODE123");
  });

  it("shows the overview once the wizard is done", async () => {
    setup.state.mockResolvedValue({ stage: "done", defaults: null, error: null });
    renderWizard();
    expect(await screen.findByText("overview")).toBeTruthy();
  });
});

describe("the storage step", () => {
  it("reports every check as a notification and forgets the results on a change", async () => {
    await toStorage();
    setup.checkStorage.mockResolvedValue({
      postgres: { ok: true, version: "16.6", timescaledb: false },
      clickhouse: { ok: false, error: "Authentication failed" },
      openbao: { ok: true },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    await screen.findByText(/PostgreSQL: works, version 16.6/);
    expect(notify.success).toHaveBeenCalledWith("PostgreSQL", "works, version 16.6");
    expect(notify.error).toHaveBeenCalledWith("ClickHouse does not answer", "Authentication failed");
    expect(notify.success).toHaveBeenCalledWith("OpenBao", "works");
    expect(notify.warning).toHaveBeenCalledWith("TimescaleDB", expect.any(String));
    type(input("Host"), "db");
    await waitFor(() => expect(screen.queryByText(/PostgreSQL: works/)).toBeNull());
    type(input("Port"), "6432");
    type(input("Database", 1), "flows2");
    type(input("Address", 0), "http://ch:8123");
    type(input("User", 0), "pg");
    type(input("User", 1), "ch");

    setup.checkStorage.mockRejectedValueOnce(new Error("timeout"));
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The check failed", "timeout"));
  });

  it("takes an external OpenBao and goes straight to the sign-in", async () => {
    await toStorage();
    fireEvent.click(screen.getByRole("radio", { name: /An external OpenBao/ }));
    type(input("role_id"), "role");
    type(input("secret_id"), "secret");
    type(input("KV mount"), "kv");
    type(input("Address", 1), "http://bao:8200");
    await save(null);
    expect(setup.storage.mock.calls[0][1].openbao).toMatchObject({ mode: "external", role_id: "role", secret_id: "secret", mount: "kv" });
    await screen.findByText("How people sign in");
    expect(notify.success).toHaveBeenCalledWith("The storage is saved", expect.any(String));
  });

  it("reports a failed save and stays", async () => {
    await toStorage();
    fireEvent.click(screen.getByRole("radio", { name: /initialise it now/ }));
    type(input("Root token (only if initialised before)"), "s.old");
    setup.storage.mockRejectedValueOnce(new Error("OpenBao is sealed"));
    fireEvent.click(screen.getByRole("button", { name: /Save and continue/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The storage is not saved", "OpenBao is sealed"));
    expect(screen.getByText("PostgreSQL (TimescaleDB)")).toBeTruthy();
  });
});

describe("the keys of OpenBao", () => {
  it("stay until they are confirmed, although the server reads the stage behind", async () => {
    await toStorage();
    // Another worker of the backend still answers "storage" right after the save.
    await save(SECRETS);
    expect(await screen.findByDisplayValue("s.root")).toBeTruthy();
    expect(screen.getByDisplayValue("recovery==")).toBeTruthy();
    expect(notify.warning).toHaveBeenCalledWith("OpenBao is initialised", expect.any(String));
    setup.state.mockResolvedValue(ACCOUNT);
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.getByDisplayValue("s.root")).toBeTruthy();

    const leave = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(true);

    fireEvent.focus(screen.getByDisplayValue("s.root"));
    fireEvent.click(within(group("Root token")).getByRole("button"));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("s.root"));
    fireEvent.click(screen.getByRole("button", { name: /Copy both/ }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Root token: s.root\nRecovery key: recovery=="));
    fireEvent.click(screen.getByRole("button", { name: /Save to a file/ }));
    const calls = vi.mocked(URL.createObjectURL).mock.calls;
    const file = calls[calls.length - 1][0] as Blob;
    const text = await new Promise<string>((done) => {
      const reader = new FileReader();
      reader.onload = () => done(String(reader.result));
      reader.readAsText(file);
    });
    expect(text).toContain("Root token: s.root\nRecovery key: recovery==");
    expect(notify.success).toHaveBeenCalledWith("The keys are saved to a file", expect.any(String));

    const next = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    const confirmed = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(confirmed);
    expect(confirmed.defaultPrevented).toBe(false);
    fireEvent.click(next);
    await screen.findByText("How people sign in");
  });

  it("warn when OpenBao returned no recovery key", async () => {
    await toStorage();
    await save({ root_token: "s.root", recovery_key: "" });
    expect(await screen.findByText(/OpenBao did not return it/)).toBeTruthy();
  });
});

describe("the sign-in step", () => {
  async function toAccount() {
    await toStorage();
    setup.state.mockResolvedValue(ACCOUNT);
    await save(null);
    await screen.findByText("How people sign in");
  }

  it("creates the administrator and signs in", async () => {
    await toAccount();
    type(input("Address of the panel"), "https://nc.example.com/");
    type(input("Name"), "Root");
    type(input("E-mail"), "root@example.com");
    type(input("Password"), "Correct-Horse-9");
    type(input("Repeat"), "Correct-Horse-8");
    expect(await screen.findByText("the passwords differ")).toBeTruthy();
    type(input("Repeat"), "Correct-Horse-9");

    setup.account.mockRejectedValueOnce(new Error("The password is too weak"));
    fireEvent.click(screen.getByRole("button", { name: /Create the administrator/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The administrator is not created", "The password is too weak"));

    setup.account.mockResolvedValueOnce({ user: { role: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: /Create the administrator/ }));
    await waitFor(() => expect(location.replace).toHaveBeenCalledWith("/"));
    expect(setup.account.mock.calls[1][1]).toMatchObject({
      mode: "local", public_base_url: "https://nc.example.com", username: "admin", display_name: "Root", email: "root@example.com",
    });
    expect(bootstrap).toHaveBeenCalled();
    expect(notify.success).toHaveBeenCalledWith("The administrator admin is created", expect.any(String));
  });

  it("connects Keystone and goes there to sign in", async () => {
    await toAccount();
    setup.keystone.mockResolvedValue({ redirect_uri: "https://nc.test/api/auth/callback", post_logout_uri: "https://nc.test/login" });
    fireEvent.click(screen.getByRole("radio", { name: /Keystone \(single sign-on\)/ }));
    expect(await screen.findByText("https://nc.test/api/auth/callback")).toBeTruthy();
    type(screen.getByPlaceholderText("spa1.…"), " spa1.key ");

    setup.account.mockRejectedValueOnce(new Error("Nobody is admin in Keystone"));
    fireEvent.click(screen.getByRole("button", { name: /Connect Keystone/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("Keystone is not connected", "Nobody is admin in Keystone"));

    setup.account.mockResolvedValueOnce({ redirect: "https://keystone.test/authorize" });
    fireEvent.click(screen.getByRole("button", { name: /Connect Keystone/ }));
    await waitFor(() => expect(location.assign).toHaveBeenCalledWith("https://keystone.test/authorize"));
    expect(setup.account.mock.calls[1][1]).toMatchObject({ mode: "keystone", key: "spa1.key" });
    expect(notify.success).toHaveBeenCalledWith("Keystone is connected", expect.any(String));
  });
});

describe("going back", () => {
  it("returns from the storage to the setup code and from the sign-in to the keys, keeping what was typed", async () => {
    await toStorage();
    fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
    const codeInput = await screen.findByRole("textbox");
    expect((codeInput as HTMLInputElement).value).toBe("ABCD-EFGH-IJKL");
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await waitFor(() => expect(input("Password", 0).value).toBe("pg-pass"));  // the storage form kept its passwords
    fireEvent.click(screen.getByRole("button", { name: /Go to the step Setup code/ }));
    expect(await screen.findByRole("textbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Go to the step Storage/ }));  // forward, without the code again
    expect(input("Password", 1).value).toBe("ch-pass");

    await save(SECRETS);
    await screen.findByDisplayValue("s.root");
    expect(screen.queryByRole("button", { name: /^Back$/ })).toBeNull();  // the saved storage cannot be edited again
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("How people sign in");
    type(input("Name"), "Root");
    fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
    expect(screen.getByDisplayValue("recovery==")).toBeTruthy();
    expect(screen.getByRole("checkbox").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(input("Name").value).toBe("Root"));
    expect(screen.queryByRole("button", { name: /Go to the step Storage/ })).toBeNull();
  });
});
