import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, CheckCircle2, Copy, Database, KeyRound, Loader2, ShieldCheck, UserPlus } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { copyText } from "@/lib/hooks";
import type { AuthMode, StorageCheck, StorageInput } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Field } from "@/components/ui/Primitives";
import { Checkbox, Radio } from "@/components/ui/Controls";
import PasswordInput from "@/components/ui/PasswordInput";
import { t, tServer } from "@/i18n";

const CODE_KEY = "nc_setup_code";

function readCode(): string {
  try {
    return sessionStorage.getItem(CODE_KEY) ?? "";
  } catch {
    return "";
  }
}

function keepCode(code: string) {
  try {
    sessionStorage.setItem(CODE_KEY, code);
  } catch {
    /* storage is off */
  }
}

function Shell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className={clsx("card w-full p-7 shadow-2xl", wide ? "max-w-3xl" : "max-w-md")}>
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 shadow-glow">
            <Activity className="text-white" size={22} />
          </div>
          <div>
            <div className="text-lg font-semibold text-slate-100">Network Control</div>
            <div className="text-xs text-slate-400">{t("Setup wizard")}</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {message}
    </div>
  );
}

function Steps({ current }: { current: number }) {
  const names = [t("Setup code"), t("Storage"), t("Sign-in")];
  return (
    <ol className="mb-6 flex gap-2 text-xs">
      {names.map((n, i) => (
        <li key={n} className={clsx("flex-1 rounded-md border px-2 py-1.5 text-center",
          i < current ? "border-green-500/40 text-green-300" : i === current ? "border-accent text-slate-100" : "border-base-700 text-slate-500")}>
          {i + 1}. {n}
        </li>
      ))}
    </ol>
  );
}

function CodeStep({ onDone }: { onDone: (code: string) => void }) {
  const [code, setCode] = useState(readCode);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.setup.verify(code);
      keepCode(code);
      onDone(code);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-sm text-slate-300">{t("Network Control is not set up yet. Enter the one-time setup code: install.sh printed it, and it is in the log of the backend.")}</p>
      <pre className="mono overflow-x-auto rounded-lg bg-base-900 px-3 py-2 text-xs text-slate-300">docker compose logs backend | grep "setup code"</pre>
      <Field label={t("Setup code")}>
        <input className="input mono text-center text-lg tracking-[.25em]" autoFocus value={code} maxLength={20}
          onChange={(e) => setCode(e.target.value.toUpperCase())} />
      </Field>
      <ErrorBox message={error} />
      <button className="btn-primary w-full" disabled={code.trim().length < 12 || busy}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />} {t("Continue")}
      </button>
    </form>
  );
}

function CheckLine({ label, r }: { label: string; r?: { ok: boolean; error?: string; version?: string } }) {
  if (!r) return null;
  return (
    <div className={clsx("flex items-start gap-2 text-sm", r.ok ? "text-green-300" : "text-red-300")}>
      {r.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
      <span>{label}: {r.ok ? (r.version ? t("works, version {v}", { v: r.version }) : t("works")) : tServer(r.error ?? "")}</span>
    </div>
  );
}

function StorageStep({ code, onDone }: { code: string; onDone: () => void }) {
  const state = useQuery({ queryKey: ["setup"], queryFn: () => api.setup.state() });
  const d = state.data?.defaults;
  const [form, setForm] = useState<StorageInput | null>(null);
  const [check, setCheck] = useState<StorageCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "check" | "save">("");
  const [shown, setShown] = useState<{ root_token: string; recovery_key: string } | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (d && !form) setForm({
      postgres: { ...d.postgres, password: "" },
      clickhouse: { ...d.clickhouse, password: "" },
      openbao: { addr: d.openbao.addr, mode: "bundled", root_token: "", role_id: "", secret_id: "", mount: "amnezia" },
    });
  }, [d, form]);

  if (!form) return <div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> {t("Loading…")}</div>;

  const pg = (k: keyof StorageInput["postgres"], v: string | number) => setForm({ ...form, postgres: { ...form.postgres, [k]: v } });
  const ch = (k: keyof StorageInput["clickhouse"], v: string) => setForm({ ...form, clickhouse: { ...form.clickhouse, [k]: v } });
  const bao = (k: keyof StorageInput["openbao"], v: string) => setForm({ ...form, openbao: { ...form.openbao, [k]: v } });

  const run = async (what: "check" | "save") => {
    setBusy(what);
    setError(null);
    try {
      if (what === "check") setCheck(await api.setup.checkStorage(code, form));
      else {
        const r = await api.setup.storage(code, form);
        if (r.openbao) setShown(r.openbao);
        else onDone();
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  };

  if (shown) return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 text-sm text-amber-200"><ShieldCheck size={16} className="mt-0.5 shrink-0" />
        {t("OpenBao is initialised. Save its root token and recovery key now, in a password manager: they are shown once and kept nowhere. They are needed to restore OpenBao or to administer it by hand.")}</div>
      {([["Root token", shown.root_token], ["Recovery key", shown.recovery_key]] as const).map(([label, value]) => (
        <Field key={label} label={t(label)}>
          <div className="flex gap-2">
            <input className="input mono" readOnly value={value} />
            <button type="button" className="btn-ghost" onClick={() => copyText(value)}><Copy size={14} /></button>
          </div>
        </Field>
      ))}
      <Checkbox checked={saved} onChange={setSaved} label={t("I have saved the root token and the recovery key")} />
      <button className="btn-primary w-full" disabled={!saved} onClick={onDone}>{t("Continue")}</button>
    </div>
  );

  return (
    <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); run("save"); }}>
      <p className="text-sm text-slate-400">{t("Where Network Control keeps its data. The values of the bundled services are filled in; install.sh printed their passwords. Passwords are kept in OpenBao, not on disk.")}</p>
      <fieldset className="space-y-3">
        <legend className="mb-2 flex items-center gap-2 font-medium text-slate-200"><Database size={15} /> PostgreSQL (TimescaleDB)</legend>
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="sm:col-span-2"><Field label={t("Host")}><input className="input mono" value={form.postgres.host} onChange={(e) => pg("host", e.target.value)} /></Field></div>
          <Field label={t("Port")}><input className="input mono" inputMode="numeric" value={form.postgres.port} onChange={(e) => pg("port", Number(e.target.value) || 0)} /></Field>
          <Field label={t("Database")}><input className="input mono" value={form.postgres.database} onChange={(e) => pg("database", e.target.value)} /></Field>
          <div className="sm:col-span-2"><Field label={t("User")}><input className="input mono" value={form.postgres.user} onChange={(e) => pg("user", e.target.value)} /></Field></div>
          <div className="sm:col-span-2"><Field label={t("Password")}><PasswordInput value={form.postgres.password} onChange={(e) => pg("password", e.target.value)} /></Field></div>
        </div>
      </fieldset>
      <fieldset className="space-y-3">
        <legend className="mb-2 flex items-center gap-2 font-medium text-slate-200"><Database size={15} /> ClickHouse</legend>
        <div className="grid gap-3 sm:grid-cols-4">
          <div className="sm:col-span-2"><Field label={t("Address")}><input className="input mono" value={form.clickhouse.url} onChange={(e) => ch("url", e.target.value)} /></Field></div>
          <div className="sm:col-span-2"><Field label={t("Database")}><input className="input mono" value={form.clickhouse.database} onChange={(e) => ch("database", e.target.value)} /></Field></div>
          <div className="sm:col-span-2"><Field label={t("User")}><input className="input mono" value={form.clickhouse.user} onChange={(e) => ch("user", e.target.value)} /></Field></div>
          <div className="sm:col-span-2"><Field label={t("Password")}><PasswordInput value={form.clickhouse.password} onChange={(e) => ch("password", e.target.value)} /></Field></div>
        </div>
      </fieldset>
      <fieldset className="space-y-3">
        <legend className="mb-2 flex items-center gap-2 font-medium text-slate-200"><KeyRound size={15} /> OpenBao</legend>
        <Field label={t("Address")}><input className="input mono" value={form.openbao.addr} onChange={(e) => bao("addr", e.target.value)} /></Field>
        <Radio checked={form.openbao.mode === "bundled"} onChange={() => bao("mode", "bundled")} label={t("The OpenBao of this installation: initialise it now")}
          hint={t("If it was initialised before, enter its root token.")} />
        <Radio checked={form.openbao.mode === "external"} onChange={() => bao("mode", "external")} label={t("An external OpenBao")}
          hint={t("An AppRole with read, write and list rights on a KV v2 mount.")} />
        {form.openbao.mode === "bundled" ? (
          <Field label={t("Root token (only if initialised before)")}><PasswordInput value={form.openbao.root_token ?? ""} onChange={(e) => bao("root_token", e.target.value)} /></Field>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="role_id"><input className="input mono" value={form.openbao.role_id ?? ""} onChange={(e) => bao("role_id", e.target.value)} /></Field>
            <Field label="secret_id"><PasswordInput value={form.openbao.secret_id ?? ""} onChange={(e) => bao("secret_id", e.target.value)} /></Field>
            <Field label={t("KV mount")}><input className="input mono" value={form.openbao.mount ?? ""} onChange={(e) => bao("mount", e.target.value)} /></Field>
          </div>
        )}
      </fieldset>
      {check && (
        <div className="space-y-1.5 rounded-lg border border-base-700 px-3 py-2">
          <CheckLine label="PostgreSQL" r={check.postgres} />
          {check.postgres.ok && !check.postgres.timescaledb && <div className="pl-6 text-xs text-amber-300">{t("TimescaleDB is not available: time series are kept in ordinary tables.")}</div>}
          <CheckLine label="ClickHouse" r={check.clickhouse} />
          <CheckLine label="OpenBao" r={check.openbao} />
        </div>
      )}
      <ErrorBox message={error} />
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => run("check")}>
          {busy === "check" && <Loader2 size={14} className="animate-spin" />} {t("Check")}
        </button>
        <button className="btn-primary" disabled={!!busy || !form.postgres.password || !form.clickhouse.password}>
          {busy === "save" && <Loader2 size={14} className="animate-spin" />} {t("Save and continue")}
        </button>
      </div>
    </form>
  );
}

function AccountStep({ code }: { code: string }) {
  const bootstrap = useAuth((s) => s.bootstrap);
  const [mode, setMode] = useState<AuthMode>("local");
  const [base, setBase] = useState(window.location.origin);
  const [form, setForm] = useState({ username: "admin", display_name: "", email: "", password: "", repeat: "", key: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hint = useQuery({ queryKey: ["setup-keystone", base], queryFn: () => api.setup.keystone(code, base), enabled: mode === "keystone" });
  const mismatch = form.repeat !== "" && form.password !== form.repeat;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.setup.account(code, mode === "local"
        ? { mode, public_base_url: base, username: form.username, display_name: form.display_name, email: form.email, password: form.password }
        : { mode, public_base_url: base, key: form.key });
      try {
        sessionStorage.removeItem(CODE_KEY);
      } catch {
        /* storage is off */
      }
      if (mode === "local") {
        await bootstrap();
        window.location.replace("/");
      } else window.location.assign(r.redirect);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="space-y-5" onSubmit={submit}>
      <Field label={t("Address of the panel")} hint={t("How people and Keystone reach it, e.g. https://nc.example.com")}>
        <input className="input mono" value={base} onChange={(e) => setBase(e.target.value.replace(/\/+$/, ""))} />
      </Field>
      <div className="space-y-2">
        <div className="text-sm font-medium text-slate-200">{t("How people sign in")}</div>
        <Radio checked={mode === "local"} onChange={() => setMode("local")} label={t("Users of Network Control")}
          hint={t("A standalone installation: passwords and two-factor codes are kept in OpenBao. Keystone can be connected later, with the users moved there.")} />
        <Radio checked={mode === "keystone"} onChange={() => setMode("keystone")} label={t("Keystone (single sign-on)")}
          hint={t("People and roles are managed in Keystone; the panel synchronises them.")} />
      </div>
      {mode === "local" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("Administrator's user name")}><input className="input mono" autoComplete="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label={t("Name")}><input className="input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <div className="sm:col-span-2"><Field label={t("E-mail")} hint={t("optional; Keystone matches people by it when they are moved")}><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field></div>
          <Field label={t("Password")} hint={t("at least 10 characters, mixed case or digits")}><PasswordInput autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label={t("Repeat")} hint={mismatch ? <span className="text-red-300">{t("the passwords differ")}</span> : undefined}><PasswordInput autoComplete="new-password" value={form.repeat} onChange={(e) => setForm({ ...form, repeat: e.target.value })} /></Field>
        </div>
      ) : (
        <div className="space-y-3 text-sm text-slate-300">
          <ol className="list-decimal space-y-2 pl-5">
            <li>{t("In Keystone open Applications → Connect an application, type Network Control, address of the panel:")} <code className="mono break-all text-slate-100">{base}</code>
              {hint.data && <div className="mt-1 space-y-0.5 text-xs text-slate-500">
                <div>{t("Redirect address:")} <code className="mono break-all">{hint.data.redirect_uri}</code></div>
                <div>{t("Address after sign-out:")} <code className="mono break-all">{hint.data.post_logout_uri}</code></div>
              </div>}
            </li>
            <li>{t("Give yourself the role admin: without an administrator in Keystone the panel refuses the connection.")}</li>
            <li>{t("Paste the connection key Keystone shows once:")}</li>
          </ol>
          <textarea className="input mono h-24 text-xs" placeholder="spa1.…" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value.trim() })} />
        </div>
      )}
      <ErrorBox message={error} />
      <button className="btn-primary w-full" disabled={busy || (mode === "local" ? !form.username || !form.password || form.password !== form.repeat : !form.key)}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <UserPlus size={16} />} {mode === "local" ? t("Create the administrator and sign in") : t("Connect Keystone and sign in")}
      </button>
    </form>
  );
}

/** The setup wizard: the setup code, the storage, the sign-in. After it the "First steps" of the overview take over. */
export default function SetupPage() {
  const state = useQuery({ queryKey: ["setup"], queryFn: () => api.setup.state(), refetchInterval: (q) => (q.state.data?.stage === "storage" ? false : 5000) });
  const [code, setCode] = useState<string | null>(null);
  const stage = state.data?.stage;

  if (stage === "done") return <Navigate to="/" replace />;
  if (!state.data) return <Shell><div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> {t("Loading…")}</div></Shell>;
  const step = !code ? 0 : stage === "storage" ? 1 : 2;
  return (
    <Shell wide={step > 0}>
      <Steps current={step} />
      {step === 0 && <CodeStep onDone={(c) => { setCode(c); state.refetch(); }} />}
      {step === 1 && <StorageStep code={code!} onDone={() => state.refetch()} />}
      {step === 2 && <AccountStep code={code!} />}
    </Shell>
  );
}
