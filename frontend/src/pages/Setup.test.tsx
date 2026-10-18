import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SetupPage from "./Setup";

const setup = vi.hoisted(() => ({
  state: vi.fn(), verify: vi.fn(), checkStorage: vi.fn(), storageDraft: vi.fn(), storage: vi.fn(), account: vi.fn(), keystone: vi.fn(),
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
const STORAGE = { stage: "storage", defaults: DEFAULTS, storage_editable: true, error: null };
// The storage is a draft on the server: the sign-in step is next, the storage may still be changed.
const ACCOUNT = { stage: "account", defaults: DEFAULTS, storage_editable: true, error: null };
const DONE = { stage: "done", defaults: null, storage_editable: false, error: null };
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

/** Hands the storage to the server, which keeps it as a draft. */
async function save() {
  const before = setup.storage.mock.calls.length;
  setup.storage.mockResolvedValue({ stage: "account" });
  fireEvent.click(screen.getByRole("button", { name: /Save and continue/ }));
  await waitFor(() => expect(setup.storage).toHaveBeenCalledTimes(before + 1));
}

/** Through the storage to the sign-in step. */
async function toAccount() {
  await toStorage();
  setup.state.mockResolvedValue(ACCOUNT);
  await save();
  await screen.findByText("How people sign in");
}

/** Fills the local administrator and presses the button that applies everything. */
async function createAdmin() {
  type(input("Password"), "Correct-Horse-9");
  type(input("Repeat"), "Correct-Horse-9");
  fireEvent.click(screen.getByRole("button", { name: /Create the administrator/ }));
  await waitFor(() => expect(setup.account).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("location", { ...window.location, ...location });
  setup.storageDraft.mockResolvedValue({ draft: null, kept: {} });
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
    setup.state.mockResolvedValue(DONE);
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

  it("takes an external OpenBao and goes to the sign-in, nothing applied yet", async () => {
    await toStorage();
    fireEvent.click(screen.getByRole("radio", { name: /An external OpenBao/ }));
    type(input("role_id"), "role");
    type(input("secret_id"), "secret");
    type(input("KV mount"), "kv");
    type(input("Address", 1), "http://bao:8200");
    await save();
    expect(setup.storage.mock.calls[0][1].openbao).toMatchObject({ mode: "external", role_id: "role", secret_id: "secret", mount: "kv" });
    await screen.findByText("How people sign in");
    expect(notify.success).toHaveBeenCalledWith("The storage is checked", "It is applied with the last step of the wizard.");
    expect(setup.account).not.toHaveBeenCalled();
  });

  it("reports a refused storage and stays", async () => {
    await toStorage();
    fireEvent.click(screen.getByRole("radio", { name: /initialise it now/ }));
    type(input("Root token (only if initialised before)"), "s.old");
    setup.storage.mockRejectedValueOnce(new Error("OpenBao is sealed"));
    fireEvent.click(screen.getByRole("button", { name: /Save and continue/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The storage is not accepted", "OpenBao is sealed"));
    expect(screen.getByText("PostgreSQL (TimescaleDB)")).toBeTruthy();
  });

  it("comes back from the draft of the server, its secrets kept there", async () => {
    // A reload after the storage step: the server has the draft, the browser only the setup code.
    sessionStorage.setItem("nc_setup_code", "ABCDEFGHIJKL");
    setup.state.mockResolvedValue(ACCOUNT);
    setup.verify.mockResolvedValue({ ok: true, stage: "account" });
    setup.storageDraft.mockResolvedValue({
      draft: {
        postgres: { host: "db2", port: 5433, database: "harbor", user: "pg", password: "" },
        clickhouse: { url: "http://ch:8123", user: "ch", password: "", database: "flows" },
        openbao: { addr: "http://openbao:8200", mode: "bundled", root_token: "", role_id: "", secret_id: "", mount: "amnezia" },
      },
      kept: { "postgres.password": true, "clickhouse.password": true, "openbao.root_token": false, "openbao.secret_id": false },
    });
    renderWizard();
    fireEvent.click(await screen.findByRole("button", { name: /Continue/ }));
    await screen.findByText("How people sign in");
    fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
    await waitFor(() => expect(input("Host").value).toBe("db2"));
    expect(input("Password", 0).value).toBe("");
    expect(screen.getAllByText("kept on the server; leave empty to keep it")).toHaveLength(2);
    await save();
    expect(setup.storage.mock.calls[0][1].postgres).toMatchObject({ host: "db2", password: "" });
  });
});

describe("the sign-in step", () => {
  it("applies everything with its button, creates the administrator and signs in", async () => {
    await toAccount();
    type(input("Address of the panel"), "https://nc.example.com/");
    type(input("Display name"), "Root");
    type(input("E-mail"), "root@example.com");
    type(input("Password"), "Correct-Horse-9");
    type(input("Repeat"), "Correct-Horse-8");
    expect(await screen.findByText("the passwords differ")).toBeTruthy();
    type(input("Repeat"), "Correct-Horse-9");

    setup.account.mockRejectedValueOnce(new Error("The password is too weak"));
    fireEvent.click(screen.getByRole("button", { name: /Create the administrator/ }));
    await waitFor(() => expect(notify.error).toHaveBeenCalledWith("The administrator is not created", "The password is too weak"));

    setup.account.mockResolvedValueOnce({ user: { role: "admin" }, redirect: "/", openbao: null });
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

    setup.account.mockResolvedValueOnce({ redirect: "https://keystone.test/authorize", openbao: null });
    fireEvent.click(screen.getByRole("button", { name: /Connect Keystone/ }));
    await waitFor(() => expect(location.assign).toHaveBeenCalledWith("https://keystone.test/authorize"));
    expect(setup.account.mock.calls[1][1]).toMatchObject({ mode: "keystone", key: "spa1.key" });
    expect(notify.success).toHaveBeenCalledWith("Keystone is connected", expect.any(String));
  });
});

describe("the keys of OpenBao", () => {
  it("come after the last step and stay until they are confirmed, although the wizard is done on the server", async () => {
    await toAccount();
    setup.account.mockResolvedValue({ user: { role: "admin" }, redirect: "/", openbao: SECRETS });
    setup.state.mockResolvedValue(DONE);
    await createAdmin();
    expect(await screen.findByDisplayValue("s.root")).toBeTruthy();
    expect(screen.getByDisplayValue("recovery==")).toBeTruthy();
    expect(notify.warning).toHaveBeenCalledWith("OpenBao is initialised", expect.any(String));
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.getByDisplayValue("s.root")).toBeTruthy();
    expect(screen.queryByText("overview")).toBeNull();
    expect(location.replace).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /^Back$/ })).toBeNull();  // everything is applied: no way back

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
    await waitFor(() => expect(location.replace).toHaveBeenCalledWith("/"));
    expect(bootstrap).toHaveBeenCalled();
  });

  it("warn when OpenBao returned no recovery key", async () => {
    await toAccount();
    setup.account.mockResolvedValue({ user: { role: "admin" }, redirect: "/", openbao: { root_token: "s.root", recovery_key: "" } });
    await createAdmin();
    expect(await screen.findByText(/OpenBao did not return it/)).toBeTruthy();
  });
});

describe("going back", () => {
  it("returns through every step before the last one, keeping what was typed", async () => {
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

    setup.state.mockResolvedValue(ACCOUNT);
    await save();
    await screen.findByText("How people sign in");
    type(input("Display name"), "Root");
    // The storage is only a draft on the server: the sign-in step goes back to it.
    fireEvent.click(screen.getByRole("button", { name: /^Back$/ }));
    await waitFor(() => expect(input("Password", 0).value).toBe("pg-pass"));
    type(input("Host"), "db2");
    await save();
    expect(setup.storage.mock.calls[1][1].postgres).toMatchObject({ host: "db2", password: "pg-pass" });
    await waitFor(() => expect(input("Display name").value).toBe("Root"));
    fireEvent.click(screen.getByRole("button", { name: /Go to the step Storage/ }));
    expect(input("Host").value).toBe("db2");
  });
});
