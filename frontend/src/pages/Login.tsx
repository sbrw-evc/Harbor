import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, ExternalLink, KeyRound, Loader2, LogIn, PlugZap } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import type { PanelUser } from "@/lib/types";
import { clearSignedOut, useAuth, wasSignedOut } from "@/store/auth";
import { Field } from "@/components/ui/Primitives";
import PasswordInput from "@/components/ui/PasswordInput";
import { t, tServer } from "@/i18n";

export const SPA_PROTOCOL_URL = "https://github.com/sbrw-evc/SPA-Single-point-of-authorization-/blob/main/docs/protocol.md";

/** Messages for the `sso_error` codes the callback sends back here. */
function errorText(code: string, description: string | null): { title: string; detail: string | null } {
  if (code === "access_denied")
    return { title: t("You have no role in Harbor. Ask an SPA administrator for access."), detail: null };
  if (code === "invalid_state")
    return { title: t("The sign-in took too long or was started in another browser. Try again."), detail: null };
  if (code === "unavailable") return { title: t("SPA is not available"), detail: description ? tServer(description) : null };
  return { title: t("Sign-in failed"), detail: description ? tServer(description) : code };
}

/** How to connect Harbor to SPA (shown while it is not connected). */
export function ConnectInstructions({ error }: { error?: string | null }) {
  const origin = window.location.origin;
  return (
    <div className="space-y-3 text-sm text-slate-300">
      {error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-200">{tServer(error)}</div>}
      <ol className="list-decimal space-y-2 pl-5">
        <li>
          {t("In SPA, open Applications → Connect an application, choose the type Harbor and enter the address of this application:")}{" "}
          <code className="mono break-all text-slate-100">{origin}</code>
          <div className="mt-1 text-xs text-slate-500">{t("Redirect address:")} <code className="mono break-all">{origin}/api/auth/spa/callback</code></div>
        </li>
        <li>
          {t("Give the roles Administrator, Operator or Viewer to people or groups.")}
          <div className="mt-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            {t("Give yourself the Administrator role before you connect: the first synchronization disables the old local admin and every user SPA does not list.")}
          </div>
        </li>
        <li>
          {t("Paste the connection key (spa1.…) in Settings → Sign-in of this panel.")}
        </li>
      </ol>
      <a className="inline-flex items-center gap-1.5 text-accent hover:underline" href={SPA_PROTOCOL_URL} target="_blank" rel="noreferrer">
        {t("How applications connect to SPA")} <ExternalLink size={13} />
      </a>
    </div>
  );
}

/** The local sign-in: user name and password, then the TOTP code when it is on. */
function LocalSignIn({ from }: { from: string }) {
  const setUser = useAuth((s) => s.setUser);
  const navigate = useNavigate();
  const [form, setForm] = useState({ username: "", password: "" });
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = (u: PanelUser) => {
    clearSignedOut();
    setUser(u);
    navigate(u.must_change_password ? "/profile" : from, { replace: true });
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (needsCode) done((await api.auth.verify2fa(code)).user);
      else {
        const r = await api.auth.login(form.username, form.password);
        if (r.requires_2fa) setNeedsCode(true);
        else if (r.user) done(r.user);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="space-y-4" onSubmit={submit}>
      {needsCode ? (
        <Field label={t("Code from the authenticator app")}>
          <input className="input text-center font-mono text-xl tracking-[.4em]" autoFocus maxLength={6} inputMode="numeric"
            autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
        </Field>
      ) : (
        <>
          <Field label={t("User name")}>
            <input className="input" autoFocus autoComplete="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          </Field>
          <Field label={t("Password")}>
            <PasswordInput autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
        </>
      )}
      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 text-sm text-red-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}
      <button className="btn-primary w-full py-2.5" disabled={busy || (needsCode ? code.length < 6 : !form.username || !form.password)}>
        {busy ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />} {needsCode ? t("Confirm") : t("Sign in")}
      </button>
      {needsCode && <button type="button" className="btn-ghost w-full" onClick={() => { setNeedsCode(false); setCode(""); }}>{t("Back")}</button>}
    </form>
  );
}

export default function LoginPage() {
  const { user, ready } = useAuth();
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const error = params.get("sso_error");
  const description = params.get("sso_error_description");
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api.auth.config(), retry: 1, refetchOnWindowFocus: false });
  const [signedOut] = useState(wasSignedOut);
  const [going, setGoing] = useState(false);
  const local = config.data?.mode === "local";
  const connected = !!config.data?.connected;
  const auto = ready && !user && !local && connected && !error && !signedOut;

  const go = (again = false) => {
    clearSignedOut();
    setGoing(true);
    window.location.assign(api.auth.startUrl(from, again ? { prompt: "login" } : {}));
  };

  useEffect(() => {
    if (auto) {
      setGoing(true);
      window.location.replace(api.auth.startUrl(from));
    }
  }, [auto, from]);

  if (ready && user) return <Navigate to={from} replace />;

  const failure = error && error !== "not_connected" ? errorText(error, description) : null;
  const notConnected = config.isSuccess && !local && !connected;

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
      <motion.div className="absolute -left-40 -top-40 h-[480px] w-[480px] rounded-full bg-blue-600/20 blur-3xl"
        animate={{ x: [0, 60, 0], y: [0, 40, 0] }} transition={{ duration: 14, repeat: Infinity }} />
      <motion.div className="absolute -bottom-40 -right-40 h-[480px] w-[480px] rounded-full bg-indigo-600/20 blur-3xl"
        animate={{ x: [0, -50, 0], y: [0, -30, 0] }} transition={{ duration: 16, repeat: Infinity }} />

      <motion.div initial={{ opacity: 0, y: 20, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 200, damping: 22 }}
        className={`card relative w-full p-7 shadow-2xl ${notConnected ? "max-w-lg" : "max-w-sm"}`}>
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 shadow-glow">
            <Activity className="text-white" size={22} />
          </div>
          <div>
            <div className="text-lg font-semibold text-slate-100">Harbor</div>
            <div className="text-xs text-slate-400">{t("VPN and network control panel")}</div>
          </div>
        </div>

        {config.isLoading || (auto && !failure) ? (
          <div className="flex items-center gap-2 py-6 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin" /> {t("Going to SPA to sign in…")}
          </div>
        ) : config.isError ? (
          <div className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-red-300"><AlertTriangle size={16} className="mt-0.5 shrink-0" /> {t("Harbor is not available. Try again in a minute.")}</div>
            <button className="btn-ghost w-full" onClick={() => config.refetch()}>{t("Try again")}</button>
          </div>
        ) : local ? (
          <div className="space-y-4">
            {signedOut && <p className="text-sm text-slate-400">{t("You have signed out.")}</p>}
            <LocalSignIn from={from} />
          </div>
        ) : notConnected ? (
          <div className="space-y-4">
            <div className="flex items-start gap-2.5">
              <PlugZap size={18} className="mt-0.5 shrink-0 text-amber-400" />
              <div>
                <div className="font-semibold text-slate-100">{t("This application is not connected to SPA")}</div>
                <div className="mt-1 text-sm text-slate-400">{t("Sign-in and access to Harbor are managed in SPA — Single Point of Authorization. To connect:")}</div>
              </div>
            </div>
            <ConnectInstructions error={config.data?.error} />
          </div>
        ) : (
          <div className="space-y-4">
            {failure && (
              <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 text-sm text-red-200">
                <div className="flex items-start gap-2"><AlertTriangle size={16} className="mt-0.5 shrink-0" /> {failure.title}</div>
                {failure.detail && <div className="mt-1 pl-6 text-xs text-red-300/80">{failure.detail}</div>}
              </div>
            )}
            {!failure && signedOut && <p className="text-sm text-slate-400">{t("You have signed out.")}</p>}
            <button type="button" className="btn-primary w-full py-2.5" disabled={going} onClick={() => go()}>
              {going ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />} {t("Sign in with SPA")}
            </button>
            {failure && error === "access_denied" && (
              <button type="button" className="btn-ghost w-full" disabled={going} onClick={() => go(true)}>
                <KeyRound size={15} /> {t("Sign in as another person")}
              </button>
            )}
            <p className="text-center text-xs text-slate-500">{t("Passwords, passkeys and two-factor authentication are managed in SPA.")}</p>
          </div>
        )}
      </motion.div>
    </div>
  );
}
