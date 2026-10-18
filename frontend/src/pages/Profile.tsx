import { useQuery } from "@tanstack/react-query";
import { ExternalLink, KeyRound, LogOut, Monitor, ShieldCheck, UserCircle } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "@/lib/api";
import { Fmt, ROLE_LABEL } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { useAuth } from "@/store/auth";
import { Badge, Card, Field, Page, PageHeader } from "@/components/ui/Primitives";
import PasswordInput from "@/components/ui/PasswordInput";
import { t } from "@/i18n";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-base-700/60 py-2 last:border-0">
      <dt className="text-sm text-slate-400">{label}</dt>
      <dd className="min-w-0 truncate text-right text-sm text-slate-200">{children}</dd>
    </div>
  );
}

/** The local mode: the password and the TOTP of the signed-in person (both kept in OpenBao). */
function LocalSecurity() {
  const { user, bootstrap } = useAuth();
  const [pw, setPw] = useState({ current: "", next: "", repeat: "" });
  const [totp, setTotp] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [code, setCode] = useState("");
  const change = useAction(() => api.auth.changePassword(pw.current, pw.next), {
    success: t("The password is changed; your other sessions are ended"), invalidate: [["sessions"]],
    onSuccess: () => { setPw({ current: "", next: "", repeat: "" }); void bootstrap(); },
  });
  const setup = useAction(() => api.auth.totpSetup(), { onSuccess: (r) => setTotp(r) });
  const enable = useAction(() => api.auth.totpEnable(code), {
    success: t("Two-factor authentication is on"), onSuccess: () => { setTotp(null); setCode(""); void bootstrap(); },
  });
  const disable = useAction(() => api.auth.totpDisable(), { success: t("Two-factor authentication is off"), onSuccess: () => bootstrap() });
  const submit = (e: FormEvent) => { e.preventDefault(); change.mutate(undefined); };
  const mismatch = pw.repeat !== "" && pw.next !== pw.repeat;

  return (
    <>
      <Card title={<span className="flex items-center gap-2"><KeyRound size={14} /> {t("Password")}</span>}>
        <form onSubmit={submit} className="space-y-3">
          {user?.must_change_password && <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">{t("Set your own password to go on working with the panel.")}</div>}
          <Field label={t("Current password")}><PasswordInput autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
          <Field label={t("New password")} hint={t("at least 10 characters, mixed case or digits")}><PasswordInput autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
          <Field label={t("Repeat")} hint={mismatch ? <span className="text-red-300">{t("the passwords differ")}</span> : undefined}><PasswordInput autoComplete="new-password" value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} /></Field>
          <button type="submit" className="btn-primary" disabled={!pw.current || !pw.next || mismatch || change.isPending}>{t("Change the password")}</button>
        </form>
      </Card>
      <Card title={<span className="flex items-center gap-2"><ShieldCheck size={14} /> {t("Two-factor authentication")}</span>}>
        {user?.totp_enabled ? (
          <div className="space-y-3">
            <Badge tone="green"><ShieldCheck size={12} /> {t("on")}</Badge>
            <p className="text-sm text-slate-400">{t("A code from the authenticator app is asked at every sign-in. The secret is kept in OpenBao.")}</p>
            <button className="btn-danger" disabled={disable.isPending} onClick={() => disable.mutate(undefined)}>{t("Turn off")}</button>
          </div>
        ) : totp ? (
          <div className="grid gap-4 sm:grid-cols-[180px_1fr]">
            <div className="qr" dangerouslySetInnerHTML={{ __html: totp.qr_svg }} />
            <div className="space-y-3">
              <p className="text-sm text-slate-300">{t("Scan the QR code with Google Authenticator, Aegis or 1Password and enter the code.")}</p>
              <div className="mono break-all text-xs text-slate-500">{totp.secret}</div>
              <input className="input text-center font-mono text-xl tracking-[.4em]" maxLength={6} inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              <button className="btn-primary" disabled={code.length < 6 || enable.isPending} onClick={() => enable.mutate(undefined)}>{t("Confirm and turn on")}</button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">{t("Protect the sign-in with one-time codes (TOTP).")}</p>
            <button className="btn-primary" disabled={setup.isPending} onClick={() => setup.mutate(undefined)}>{t("Set up")}</button>
          </div>
        )}
      </Card>
    </>
  );
}

export default function Profile() {
  const user = useAuth((s) => s.user);
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.auth.sessions() });
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api.auth.config() });
  const revoke = useAction((id: string) => api.auth.revokeSession(id), { invalidate: [["sessions"]] });
  const account = config.data?.account_url;
  const local = config.data?.mode === "local";

  return (
    <Page>
      <PageHeader title={t("Profile")} icon={<UserCircle size={20} />}
        subtitle={<>{user?.display_name || user?.username} · {t(ROLE_LABEL[user?.role ?? "viewer"])}</>} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={<span className="flex items-center gap-2"><UserCircle size={14} /> {t("Account")}</span>}>
          <dl>
            <Row label={t("Username")}><span className="mono">{user?.username}</span></Row>
            <Row label={t("Display name")}>{user?.display_name || "—"}</Row>
            <Row label={t("E-mail")}>{user?.email || "—"}</Row>
            <Row label={t("Role")}>{t(ROLE_LABEL[user?.role ?? "viewer"])}</Row>
            <Row label={t("Groups")}>{user?.groups.length ? user.groups.join(", ") : "—"}</Row>
            <Row label={t("Last sign-in")}>{Fmt.dateTime(user?.last_login_at)}</Row>
          </dl>
        </Card>

        {local ? <LocalSecurity /> : <Card title={<span className="flex items-center gap-2"><ShieldCheck size={14} /> {t("Account and security")}</span>}>
          <div className="space-y-3">
            <p className="text-sm text-slate-400">
              {t("Your account, password, passkeys and two-factor authentication are managed in SPA — Single Point of Authorization. Your role in Harbor is given there by an SPA administrator.")}
            </p>
            {account ? (
              <a className="btn-primary" href={account} target="_blank" rel="noreferrer"><ExternalLink size={15} /> {t("Open account and security in SPA")}</a>
            ) : (
              <p className="text-xs text-slate-500">{t("The address of the SPA account page is not known yet.")}</p>
            )}
          </div>
        </Card>}
      </div>

      <Card title={<span className="flex items-center gap-2"><Monitor size={14} /> {t("Active sessions")}</span>} bodyClass="p-0">
        <table className="table-nc">
          <thead><tr><th>{t("Device")}</th><th>IP</th><th>{t("Created")}</th><th>{t("Activity")}</th><th /></tr></thead>
          <tbody>
            {sessions.data?.map((s) => (
              <tr key={s.id}>
                <td className="max-w-[360px] truncate text-slate-300" title={s.user_agent}>{s.user_agent || "—"} {s.current && <Badge tone="blue">{t("current")}</Badge>}</td>
                <td className="mono text-slate-400">{s.ip}</td>
                <td className="text-xs text-slate-400">{Fmt.dateTime(s.created_at)}</td>
                <td className="text-xs text-slate-400">{Fmt.ago(s.last_seen_at)}</td>
                <td>{!s.current && <button className="btn-ghost btn-sm" onClick={() => revoke.mutate(s.id)}><LogOut size={12} /> {t("End")}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Page>
  );
}
