import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, CheckCircle2, Copy, Database, KeyRound, Loader2, ShieldCheck, UserPlus } from "lucide-react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { copyText, useFocus } from "@/lib/hooks";
import { notify } from "@/lib/notify";
import type { AuthMode, StorageCheck, StorageInput } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Field } from "@/components/ui/Primitives";
import { Checkbox, Radio } from "@/components/ui/Controls";
import PasswordInput from "@/components/ui/PasswordInput";
import { t, tServer } from "@/i18n";

const CODE_KEY = "nc_setup_code";

/** "https://nc.example.com///" → "https://nc.example.com", without a regular expression that could backtrack. */
function withoutTrailingSlashes(s: string): string {
  let end = s.length;
  while (end > 0 && s[end - 1] === "/") end--;
  return s.slice(0, end);
}
const SPRING = { type: "spring", stiffness: 320, damping: 32 } as const;

type BaoSecrets = { root_token: string; recovery_key: string };

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
      <motion.div layout initial={{ opacity: 0, y: 16, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={SPRING}
        className={clsx("card w-full overflow-hidden p-7 shadow-2xl", wide ? "max-w-3xl" : "max-w-md")}>
        <motion.div layout="position" className="mb-6 flex items-center gap-3">
          <motion.img src="/favicon.svg" alt="" className="h-11 w-11 rounded-xl shadow-glow" whileHover={{ rotate: -8, scale: 1.05 }} />
          <div>
            <div className="text-lg font-semibold text-slate-100">Harbor</div>
            <div className="text-xs text-slate-400">{t("Setup wizard")}</div>
          </div>
        </motion.div>
        {children}
      </motion.div>
    </div>
  );
}

/** In the narrow card only the current step is named, the others are numbers. */
function Steps({ current, compact }: { current: number; compact?: boolean }) {
  const names = [t("Setup code"), t("Storage"), t("OpenBao keys"), t("Sign-in")];
  return (
    <motion.ol layout="position" className="mb-6 flex gap-2 text-xs">
      {names.map((n, i) => (
        <li key={n} title={n} className={clsx("relative flex items-center justify-center gap-1 whitespace-nowrap rounded-md border px-2 py-1.5 text-center transition-colors duration-300",
          compact && i !== current ? "flex-none px-3" : "flex-1",
          i < current ? "border-green-500/40 text-green-300" : i === current ? "border-accent text-slate-100" : "border-base-700 text-slate-500")}>
          {i === current && <motion.span layoutId="setup-step" className="absolute inset-0 rounded-md bg-accent/10" transition={SPRING} />}
          <AnimatePresence initial={false}>
            {i < current && (
              <motion.span initial={{ scale: 0, width: 0 }} animate={{ scale: 1, width: "auto" }} exit={{ scale: 0, width: 0 }} transition={SPRING}>
                <Check size={12} />
              </motion.span>
            )}
          </AnimatePresence>
          <span className="relative">{compact && i !== current ? i + 1 : `${i + 1}. ${n}`}</span>
        </li>
      ))}
    </motion.ol>
  );
}

/** Opens and closes its content by height, so the card grows smoothly. */
function Reveal({ show, children }: { show: boolean; children: ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.22, ease: "easeOut" }} className="overflow-hidden">
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function BusyIcon({ busy, icon }: { busy: boolean; icon?: ReactNode }) {
  return (
    busy ? (
      <motion.span key="busy" initial={{ opacity: 0, rotate: -90 }} animate={{ opacity: 1, rotate: 0 }} transition={{ duration: 0.15 }}>
        <Loader2 size={16} className="animate-spin" />
      </motion.span>
    ) : icon ? (
      <motion.span key="idle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>{icon}</motion.span>
    ) : null
  );
}

function CodeStep({ onDone }: { onDone: (code: string) => void }) {
  const [code, setCode] = useState(readCode);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(0);
  const input = useFocus<HTMLInputElement>(shake + 1);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.setup.verify(code);
      keepCode(code);
      notify.success(t("The setup code is accepted"));
      onDone(code);
    } catch (err) {
      notify.error(t("The setup code is not accepted"), (err as Error).message);
      setShake((s) => s + 1);
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-sm text-slate-300">{t("Harbor is not set up yet. Enter the one-time setup code: install.sh printed it, and it is in the log of the backend.")}</p>
      <pre className="mono overflow-x-auto rounded-lg bg-base-900 px-3 py-2 text-xs text-slate-300">docker compose logs backend | grep "setup code"</pre>
      <Field label={t("Setup code")}>
        <motion.input key={shake} ref={input} className="input mono text-center text-lg tracking-[.25em]" value={code} maxLength={20}
          animate={shake ? { x: [0, -8, 8, -6, 6, -3, 0] } : undefined} transition={{ duration: 0.4 }}
          onChange={(e) => setCode(e.target.value.toUpperCase())} />
      </Field>
      <motion.button whileTap={{ scale: 0.98 }} className="btn-primary w-full" disabled={code.trim().length < 12 || busy}>
        <BusyIcon busy={busy} icon={<KeyRound size={16} />} /> {t("Continue")}
      </motion.button>
    </form>
  );
}

type CheckResult = { ok: boolean; error?: string; version?: string };

function checkText(r: CheckResult): string {
  return r.ok ? (r.version ? t("works, version {v}", { v: r.version }) : t("works")) : tServer(r.error ?? "");
}

function CheckLine({ label, r, i }: { label: string; r?: CheckResult; i: number }) {
  if (!r) return null;
  return (
    <motion.div initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }}
      className={clsx("flex items-start gap-2 text-sm", r.ok ? "text-green-300" : "text-red-300")}>
      {r.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
      <span>{label}: {checkText(r)}</span>
    </motion.div>
  );
}

function StorageStep({ code, onDone }: { code: string; onDone: (secrets: BaoSecrets | null) => void }) {
  const state = useQuery({ queryKey: ["setup"], queryFn: () => api.setup.state() });
  const d = state.data?.defaults;
  const [form, setForm] = useState<StorageInput | null>(null);
  const [check, setCheck] = useState<StorageCheck | null>(null);
  const [busy, setBusy] = useState<"" | "check" | "save">("");

  useEffect(() => {
    if (d && !form) setForm({
      postgres: { ...d.postgres, password: "" },
      clickhouse: { ...d.clickhouse, password: "" },
      openbao: { addr: d.openbao.addr, mode: "bundled", root_token: "", role_id: "", secret_id: "", mount: "amnezia" },
    });
  }, [d, form]);

  if (!form) return <div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> {t("Loading…")}</div>;

  // A change makes the last check stale: its results go away.
  const edit = (next: StorageInput) => { setForm(next); setCheck(null); };
  const pg = (k: keyof StorageInput["postgres"], v: string | number) => edit({ ...form, postgres: { ...form.postgres, [k]: v } });
  const ch = (k: keyof StorageInput["clickhouse"], v: string) => edit({ ...form, clickhouse: { ...form.clickhouse, [k]: v } });
  const bao = (k: keyof StorageInput["openbao"], v: string) => edit({ ...form, openbao: { ...form.openbao, [k]: v } });

  const run = async (what: "check" | "save") => {
    setBusy(what);
    try {
      if (what === "check") {
        const r = await api.setup.checkStorage(code, form);
        setCheck(r);
        ([["PostgreSQL", r.postgres], ["ClickHouse", r.clickhouse], ["OpenBao", r.openbao]] as const).forEach(([name, res]) =>
          res.ok ? notify.success(name, checkText(res)) : notify.error(t("{name} does not answer", { name }), checkText(res)));
        if (r.postgres.ok && !r.postgres.timescaledb) notify.warning("TimescaleDB", t("TimescaleDB is not available: time series are kept in ordinary tables."));
        setBusy("");
      } else {
        const r = await api.setup.storage(code, form);
        notify.success(t("The storage is saved"), t("Harbor keeps its data in PostgreSQL, ClickHouse and OpenBao."));
        if (r.openbao) notify.warning(t("OpenBao is initialised"), t("Save its root token and recovery key: they are shown once."));
        onDone(r.openbao);
      }
    } catch (err) {
      notify.error(what === "check" ? t("The check failed") : t("The storage is not saved"), (err as Error).message);
      setBusy("");
    }
  };

  return (
    <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); void run("save"); }}>
      <p className="text-sm text-slate-400">{t("Where Harbor keeps its data. The values of the bundled services are filled in; install.sh printed their passwords. Passwords are kept in OpenBao, not on disk.")}</p>
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
        <motion.div key={form.openbao.mode} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
          {form.openbao.mode === "bundled" ? (
            <Field label={t("Root token (only if initialised before)")}><PasswordInput value={form.openbao.root_token ?? ""} onChange={(e) => bao("root_token", e.target.value)} /></Field>
          ) : (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="role_id"><input className="input mono" value={form.openbao.role_id ?? ""} onChange={(e) => bao("role_id", e.target.value)} /></Field>
              <Field label="secret_id"><PasswordInput value={form.openbao.secret_id ?? ""} onChange={(e) => bao("secret_id", e.target.value)} /></Field>
              <Field label={t("KV mount")}><input className="input mono" value={form.openbao.mount ?? ""} onChange={(e) => bao("mount", e.target.value)} /></Field>
            </div>
          )}
        </motion.div>
      </fieldset>
      <Reveal show={!!check}>
        {check && (
          <div className="space-y-1.5 rounded-lg border border-base-700 px-3 py-2">
            <CheckLine i={0} label="PostgreSQL" r={check.postgres} />
            {check.postgres.ok && !check.postgres.timescaledb && <div className="pl-6 text-xs text-amber-300">{t("TimescaleDB is not available: time series are kept in ordinary tables.")}</div>}
            <CheckLine i={1} label="ClickHouse" r={check.clickhouse} />
            <CheckLine i={2} label="OpenBao" r={check.openbao} />
          </div>
        )}
      </Reveal>
      <div className="flex justify-end gap-2">
        <motion.button whileTap={{ scale: 0.97 }} type="button" className="btn-ghost" disabled={!!busy} onClick={() => run("check")}>
          <BusyIcon busy={busy === "check"} /> {t("Check")}
        </motion.button>
        <motion.button whileTap={{ scale: 0.97 }} className="btn-primary" disabled={!!busy || !form.postgres.password || !form.clickhouse.password}>
          <BusyIcon busy={busy === "save"} /> {t("Save and continue")}
        </motion.button>
      </div>
    </form>
  );
}

/** The root token and the recovery key of a freshly initialised OpenBao. The step stays until the user confirms that
 * they are saved: the stage of the server has moved on already, so nothing else may close it. */
function SecretsStep({ secrets, onDone }: { secrets: BaoSecrets; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const savedRef = useRef(saved);
  savedRef.current = saved;

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (savedRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const rows = [[t("Root token"), secrets.root_token], [t("Recovery key"), secrets.recovery_key]] as const;
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
        <ShieldCheck size={16} className="mt-0.5 shrink-0" />
        {t("OpenBao is initialised. Save its root token and recovery key now, in a password manager: they are shown once and kept nowhere. They are needed to restore OpenBao or to administer it by hand.")}
      </div>
      {rows.map(([label, value], i) => (
        <motion.div key={label} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.08 }}>
          <Field label={label}>
            {value ? (
              <div className="flex gap-2">
                <input className="input mono" readOnly value={value} onFocus={(e) => e.currentTarget.select()} />
                <motion.button whileTap={{ scale: 0.92 }} type="button" className="btn-ghost" title={t("Copy")} onClick={() => copyText(value, t("{what} is copied", { what: label }))}>
                  <Copy size={14} />
                </motion.button>
              </div>
            ) : (
              <div className="flex items-start gap-2 text-sm text-red-300"><AlertTriangle size={15} className="mt-0.5 shrink-0" />
                {t("OpenBao did not return it. Check the log of OpenBao before going on.")}</div>
            )}
          </Field>
        </motion.div>
      ))}
      <motion.button whileTap={{ scale: 0.98 }} type="button" className="btn-ghost w-full"
        onClick={() => copyText(rows.map(([l, v]) => `${l}: ${v}`).join("\n"), t("Both keys are copied"))}>
        <Copy size={14} /> {t("Copy both")}
      </motion.button>
      <Checkbox checked={saved} onChange={setSaved} label={t("I have saved the root token and the recovery key")} />
      <motion.button whileTap={{ scale: 0.98 }} className="btn-primary w-full" disabled={!saved} onClick={onDone}>{t("Continue")}</motion.button>
    </div>
  );
}

function AccountStep({ code }: { code: string }) {
  const bootstrap = useAuth((s) => s.bootstrap);
  const [mode, setMode] = useState<AuthMode>("local");
  const [base, setBase] = useState(window.location.origin);
  const [form, setForm] = useState({ username: "admin", display_name: "", email: "", password: "", repeat: "", key: "" });
  const [busy, setBusy] = useState(false);
  const hint = useQuery({ queryKey: ["setup-keystone", base], queryFn: () => api.setup.keystone(code, base), enabled: mode === "keystone" });
  const mismatch = form.repeat !== "" && form.password !== form.repeat;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
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
        notify.success(t("The administrator {name} is created", { name: form.username }), t("Harbor is set up: signing in…"));
        await bootstrap();
        window.location.replace("/");
      } else {
        notify.success(t("Keystone is connected"), t("Harbor is set up: going to Keystone to sign in…"));
        window.location.assign(r.redirect);
      }
    } catch (err) {
      notify.error(mode === "local" ? t("The administrator is not created") : t("Keystone is not connected"), (err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="space-y-5" onSubmit={submit}>
      <Field label={t("Address of the panel")} hint={t("How people and Keystone reach it, e.g. https://nc.example.com")}>
        <input className="input mono" value={base} onChange={(e) => setBase(withoutTrailingSlashes(e.target.value))} />
      </Field>
      <div className="space-y-2">
        <div className="text-sm font-medium text-slate-200">{t("How people sign in")}</div>
        <Radio checked={mode === "local"} onChange={() => setMode("local")} label={t("Users of Harbor")}
          hint={t("A standalone installation: passwords and two-factor codes are kept in OpenBao. Keystone can be connected later, with the users moved there.")} />
        <Radio checked={mode === "keystone"} onChange={() => setMode("keystone")} label={t("Keystone (single sign-on)")}
          hint={t("People and roles are managed in Keystone; the panel synchronises them.")} />
      </div>
      <motion.div key={mode} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
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
              <li>{t("In Keystone open Applications → Connect an application, type Harbor, address of the panel:")} <code className="mono break-all text-slate-100">{base}</code>
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
      </motion.div>
      <motion.button whileTap={{ scale: 0.98 }} className="btn-primary w-full" disabled={busy || (mode === "local" ? !form.username || !form.password || form.password !== form.repeat : !form.key)}>
        <BusyIcon busy={busy} icon={<UserPlus size={16} />} /> {mode === "local" ? t("Create the administrator and sign in") : t("Connect Keystone and sign in")}
      </motion.button>
    </form>
  );
}

/** The setup wizard: the setup code, the storage, the keys of OpenBao, the sign-in. After it the "First steps" of the
 * overview take over. */
export default function SetupPage() {
  const state = useQuery({ queryKey: ["setup"], queryFn: () => api.setup.state(), refetchInterval: (q) => (q.state.data?.stage === "storage" ? false : 5000) });
  const [code, setCode] = useState<string | null>(null);
  // Kept here, not in the storage step: the stage of the server moves on as soon as the storage is saved, and the keys
  // must stay on the screen until the user confirms they are saved.
  const [secrets, setSecrets] = useState<BaoSecrets | null>(null);
  // The answer of the storage step says the stage moved on; a reading of the stage right after it may still come from a
  // process that has not started on the new storage yet, and must not bring the storage step back.
  const [storageSaved, setStorageSaved] = useState(false);
  const stage = state.data?.stage;

  if (stage === "done") return <Navigate to="/" replace />;
  if (!state.data) return (
    <MotionConfig reducedMotion="user">
      <Shell><div className="flex items-center gap-2 py-6 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" /> {t("Loading…")}</div></Shell>
    </MotionConfig>
  );
  const step = !code ? 0 : stage === "storage" && !storageSaved ? 1 : secrets ? 2 : 3;
  return (
    <MotionConfig reducedMotion="user">
      <Shell wide={step > 0}>
        <Steps current={step} compact={step === 0} />
        {/* Each step slides in when it comes. No exit animation: AnimatePresence mode="wait" around a step with its own
            animated parts kept the previous step on the screen for good. */}
        <motion.div key={step} initial={{ opacity: 0, x: 32 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.25, ease: "easeOut" }}>
          {step === 0 && <CodeStep onDone={(c) => { setCode(c); void state.refetch(); }} />}
          {step === 1 && <StorageStep code={code!} onDone={(s) => { setSecrets(s); setStorageSaved(true); void state.refetch(); }} />}
          {step === 2 && <SecretsStep secrets={secrets!} onDone={() => setSecrets(null)} />}
          {step === 3 && <AccountStep code={code!} />}
        </motion.div>
      </Shell>
    </MotionConfig>
  );
}
