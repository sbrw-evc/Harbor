import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRightLeft, ExternalLink, Fingerprint, KeyRound, RefreshCw, Trash2, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { Fmt } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { ImportReport, SpaStatus } from "@/lib/types";
import { Badge, Card } from "@/components/ui/Primitives";
import { Checkbox } from "@/components/ui/Controls";
import { confirm } from "@/components/ui/Confirm";
import { Skeleton } from "@/components/ui/Feedback";
import { ConnectInstructions } from "@/pages/Login";
import { k, t, tServer } from "@/i18n";

const KEY_SOURCE: Record<string, string> = {
  env: k("SPA_CONNECTION_KEY"), file: k("SPA_CONNECTION_KEY_FILE"), openbao: k("OpenBao: spa/connection_key"),
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-base-700/60 py-2 last:border-0">
      <dt className="text-sm text-slate-400">{label}</dt>
      <dd className="min-w-0 text-right text-sm text-slate-200">{children}</dd>
    </div>
  );
}

function KeyForm() {
  const [key, setKey] = useState("");
  const save = useAction(() => api.users.setKey(key), {
    success: (r) => t("Connected to {issuer}", { issuer: r.issuer }), invalidate: [["spa"], ["auth-config"]], onSuccess: () => setKey(""),
  });
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
      <textarea className="input mono h-20 text-xs" placeholder="spa1.…" value={key} onChange={(e) => setKey(e.target.value.trim())} />
      <button type="submit" className="btn-primary btn-sm" disabled={!key.startsWith("spa1.") || save.isPending}><KeyRound size={13} /> {t("Save the key")}</button>
      <p className="text-xs text-slate-500">{t("The key is kept in OpenBao and not shown again.")}</p>
    </form>
  );
}

/** The local mode with a Keystone connection: move the local users there, then switch the sign-in. */
function MoveToKeystone({ s }: { s: SpaStatus }) {
  const [report, setReport] = useState<ImportReport | null>(null);
  const [purge, setPurge] = useState(true);
  const move = useAction(() => api.users.importLocal(), {
    success: (r) => t("Moved: {n}", { n: r.imported.length }), invalidate: [["spa"]], onSuccess: setReport,
  });
  const switchMode = useAction(() => api.users.switchToKeystone(purge), {
    success: t("Sign-in is through Keystone now"), invalidate: [["spa"], ["users"], ["auth-config"]],
    onSuccess: () => window.location.assign("/login"),
  });
  const removeKey = useAction(() => api.users.removeKey(), { success: t("The connection key is removed"), invalidate: [["spa"]] });
  const askSwitch = async () => {
    if (await confirm({
      title: t("Sign in through Keystone?"),
      message: t("People sign in through Keystone from now on. Local users are bound to the Keystone accounts with the same user name; the others are disabled. You will be signed out."),
      confirmLabel: t("Switch"),
    })) switchMode.mutate(undefined);
  };
  const reason = (r: string) => ({ username_taken: t("the user name is taken in Keystone"), email_taken: t("the e-mail is taken in Keystone"),
    invalid_hash: t("unknown password format"), invalid_role: t("unknown role"), invalid_username: t("invalid user name") } as Record<string, string>)[r] ?? r;

  return (
    <div className="space-y-3 rounded-lg border border-base-700 p-3">
      <div className="flex items-center gap-2 font-medium text-slate-200"><ArrowRightLeft size={15} /> {t("Moving to Keystone")}</div>
      <ol className="list-decimal space-y-1.5 pl-5 text-sm text-slate-300">
        <li>{t("In Keystone, open this application and allow moving users.")} {s.user_import
          ? <Badge tone="green">{t("allowed")}</Badge> : <Badge tone="amber">{t("not allowed yet")}</Badge>}</li>
        <li>{t("Move the local users: their passwords and roles come along; two-factor authentication is set up again in Keystone.")}
          <div className="mt-1.5"><button className="btn-ghost btn-sm" disabled={!s.user_import || move.isPending} onClick={() => move.mutate(undefined)}><Users size={13} /> {t("Move the local users")}</button></div>
        </li>
        <li>{t("Switch the sign-in to Keystone: somebody must have the administrator role of this application there.")}
          <div className="mt-1.5 space-y-2">
            <Checkbox checked={purge} onChange={setPurge} label={t("Delete the local passwords and TOTP secrets from OpenBao")} />
            <button className="btn-primary btn-sm" disabled={switchMode.isPending} onClick={askSwitch}>{t("Switch to Keystone")}</button>
          </div>
        </li>
      </ol>
      {report && (
        <div className="space-y-1 text-xs">
          {report.imported.length > 0 && <div className="text-green-300">{t("Moved:")} {report.imported.map((x) => x.username).join(", ")}</div>}
          {report.conflicts.map((x) => <div key={x.username} className="text-amber-300">{x.username}: {reason(x.reason)}</div>)}
          {report.rejected.map((x) => <div key={x.username} className="text-red-300">{x.username}: {reason(x.reason)}</div>)}
        </div>
      )}
      {s.last_import && !report && <div className="text-xs text-slate-500">{t("Last move {when}: {n} moved, {c} conflicts", { when: Fmt.ago(s.last_import.at), n: s.last_import.imported, c: s.last_import.conflicts })}</div>}
      <button className="btn-ghost btn-sm text-red-300" disabled={removeKey.isPending} onClick={() => removeKey.mutate(undefined)}><Trash2 size={12} /> {t("Remove the connection key")}</button>
    </div>
  );
}

/** The Keystone (SPA) connection on the settings and users pages: the key, the move of local users, the sign-in mode,
 * the synchronisation. */
export default function SpaConnection() {
  const q = useQuery({ queryKey: ["spa"], queryFn: () => api.users.spa(), refetchInterval: 30_000 });
  const toLocal = useAction(() => api.users.switchToLocal(), {
    success: t("Sign-in is local now"), invalidate: [["spa"], ["users"], ["auth-config"]],
  });
  const askLocal = async () => {
    if (await confirm({ title: t("Back to local users?"), danger: true, confirmLabel: t("Switch"),
      message: t("Only people with a local password can sign in then. Keystone stays connected; its people are no longer synchronised.") }))
      toLocal.mutate(undefined);
  };
  const sync = useAction(() => api.users.sync(), {
    success: (r) => r.unchanged ? t("Nothing changed in SPA") : t("Synchronized: {created} created, {updated} updated, {disabled} disabled", {
      created: r.created ?? 0, updated: r.updated ?? 0, disabled: r.disabled ?? 0 }),
    invalidate: [["spa"], ["users"], ["tokens"]],
  });
  const s = q.data;

  return (
    <Card title={<span className="flex items-center gap-2"><Fingerprint size={14} /> {t("SPA — Single Point of Authorization")}</span>}
      actions={s?.connected && s.mode === "keystone" && (
        <button className="btn-ghost btn-sm" disabled={sync.isPending} onClick={() => sync.mutate(undefined)}>
          <RefreshCw size={13} className={sync.isPending ? "animate-spin" : undefined} /> {t("Synchronize now")}
        </button>
      )}>
      {!s ? <Skeleton className="h-40" /> : !s.connected ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            {t("Sign-in:")} <Badge tone={s.mode === "local" ? "blue" : "amber"}>{s.mode === "local" ? t("users of this panel") : "Keystone"}</Badge>
          </div>
          <div className="flex items-center gap-2 font-medium text-amber-300"><AlertTriangle size={16} /> {t("This application is not connected to SPA")}</div>
          <ConnectInstructions error={s.key_error} />
          <KeyForm />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            {t("Sign-in:")} <Badge tone={s.mode === "local" ? "blue" : "green"}>{s.mode === "local" ? t("users of this panel") : "Keystone"}</Badge>
          </div>
          {s.error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {tServer(s.error)}
            </div>
          )}
          <dl>
            <Row label={t("Issuer")}><a className="inline-flex items-center gap-1 text-accent hover:underline" href={s.issuer ?? "#"} target="_blank" rel="noreferrer"><span className="mono break-all">{s.issuer}</span> <ExternalLink size={12} /></a></Row>
            <Row label={t("Application")}>{s.application ?? "—"} <span className="mono text-xs text-slate-500">{s.client_id}</span></Row>
            <Row label={t("Connection key")}>{s.key_source ? t(KEY_SOURCE[s.key_source]) : "—"}</Row>
            <Row label={t("Last synchronization")}>
              {s.last_sync_at ? <span title={Fmt.dateTime(s.last_sync_at)}>{Fmt.ago(s.last_sync_at)}</span> : <Badge tone="amber">{t("not yet")}</Badge>}
              <div className="text-xs text-slate-500">{t("every {n} min", { n: Math.round(s.sync_seconds / 60) })}</div>
            </Row>
            <Row label={t("Users")}>
              {t("{active} active of {total}", { active: s.counts.active ?? 0, total: s.counts.users ?? 0 })}
            </Row>
            {s.last_result && !s.last_result.unchanged && (s.last_result.created || s.last_result.updated || s.last_result.disabled) ? (
              <Row label={t("Last changes")}>
                {t("{created} created, {updated} updated, {disabled} disabled", {
                  created: s.last_result.created ?? 0, updated: s.last_result.updated ?? 0, disabled: s.last_result.disabled ?? 0 })}
              </Row>
            ) : null}
            <Row label={t("Role catalogue")}>
              <div className="flex flex-wrap justify-end gap-1">{s.roles.map((r) => <Badge key={r.id} tone="slate">{r.id}</Badge>)}</div>
              <div className="text-xs text-slate-500">{s.roles_published_at ? t("published {when}", { when: Fmt.ago(s.roles_published_at) }) : t("not published yet")}</div>
            </Row>
          </dl>
          {s.mode === "local" ? <MoveToKeystone s={s} /> : (
            <button className="btn-ghost btn-sm" disabled={toLocal.isPending} onClick={askLocal}>{t("Back to local users")}</button>
          )}
        </div>
      )}
    </Card>
  );
}
