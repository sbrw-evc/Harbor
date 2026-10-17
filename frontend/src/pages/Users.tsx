import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Info, KeyRound, Plus, ShieldOff, Trash2, UserCog } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type UserInput } from "@/lib/api";
import { Fmt, ROLE_LABEL } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { PanelUser, Role } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Field, Modal, Page, PageHeader } from "@/components/ui/Primitives";
import { EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Select } from "@/components/ui/Controls";
import { confirm } from "@/components/ui/Confirm";
import PasswordInput from "@/components/ui/PasswordInput";
import ApiTokens from "@/components/admin/ApiTokens";
import SpaConnection from "@/components/admin/SpaConnection";
import { t } from "@/i18n";

const ROLES: Role[] = ["admin", "operator", "viewer"];

function NewUser({ open, onClose }: { open: boolean; onClose: () => void }) {
  const empty: UserInput = { username: "", display_name: "", email: "", role: "viewer", password: "" };
  const [form, setForm] = useState<UserInput>(empty);
  const create = useAction(() => api.users.create(form), {
    success: (u) => t("User {name} created: the password must be changed at the first sign-in", { name: u.username }),
    invalidate: [["users"]], onSuccess: () => { setForm(empty); onClose(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(undefined); };
  return (
    <Modal open={open} onClose={onClose} title={t("New user")}>
      <form className="space-y-3" onSubmit={submit}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("User name")}><input className="input mono" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label={t("Role")}><Select value={form.role} onChange={(v) => setForm({ ...form, role: v as Role })} options={ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL[r]) }))} /></Field>
          <Field label={t("Name")}><input className="input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <Field label={t("E-mail")}><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        </div>
        <Field label={t("Temporary password")} hint={t("at least 10 characters, mixed case or digits; it is changed at the first sign-in")}>
          <PasswordInput autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn-primary" disabled={!form.username || !form.password || create.isPending}>{t("Create")}</button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPassword({ user, onClose }: { user: PanelUser | null; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const reset = useAction(() => api.users.resetPassword(user!.id, password), {
    success: t("The password is reset; the user changes it at the next sign-in"), invalidate: [["users"]],
    onSuccess: () => { setPassword(""); onClose(); },
  });
  return (
    <Modal open={!!user} onClose={onClose} title={t("New password for {name}", { name: user?.username ?? "" })}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); reset.mutate(undefined); }}>
        <Field label={t("Temporary password")}><PasswordInput autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn-primary" disabled={!password || reset.isPending}>{t("Reset")}</button>
        </div>
      </form>
    </Modal>
  );
}

/** People who may use Network Control: local users (managed here) or records synchronised from Keystone, and the API
 * tokens. */
export default function Users() {
  const me = useAuth((s) => s.user);
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.users.list() });
  const spa = useQuery({ queryKey: ["spa"], queryFn: () => api.users.spa() });
  const local = spa.data?.mode === "local";
  const issuer = spa.data?.issuer;
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<PanelUser | null>(null);
  const update = useAction((a: { id: number; role?: Role; is_active?: boolean }) => api.users.update(a.id, a), { invalidate: [["users"]] });
  const totpOff = useAction((id: number) => api.users.disableTotp(id), { success: t("Two-factor authentication is off"), invalidate: [["users"]] });
  const remove = useAction((id: number) => api.users.remove(id), { success: t("User deleted"), invalidate: [["users"]] });

  const askRemove = async (u: PanelUser) => {
    if (await confirm({ title: t("Delete {name}?", { name: u.username }), message: t("The user, their sessions, API tokens, password and TOTP secret are deleted."), confirmLabel: t("Delete"), danger: true }))
      remove.mutate(u.id);
  };

  return (
    <Page>
      <PageHeader title={t("Users and access")} icon={<UserCog size={20} />}
        subtitle={local ? t("Users of this panel: passwords and two-factor codes are kept in OpenBao.")
          : t("People, their roles and sign-in are managed in SPA; Network Control synchronises them every 5 minutes.")}
        actions={local ? <button className="btn-primary" onClick={() => setCreating(true)}><Plus size={15} /> {t("New user")}</button>
          : issuer && <a className="btn-ghost" href={issuer} target="_blank" rel="noreferrer"><ExternalLink size={15} /> {t("Manage in SPA")}</a>} />

      {!local && spa.data && (
        <div className="flex items-start gap-3 rounded-lg border border-blue-500/30 bg-blue-500/10 px-4 py-3 text-sm text-blue-100">
          <Info size={18} className="mt-0.5 shrink-0" />
          <div>{t("Creating users, passwords, passkeys, roles and enabling or disabling people are managed in SPA. A person disabled in SPA loses their sessions and API tokens here at the next synchronization.")}</div>
        </div>
      )}

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card bodyClass="p-0">
          <table className="table-nc">
            <thead><tr><th>{t("User")}</th><th>{t("Role")}</th><th>{local ? "2FA" : t("Groups")}</th><th>{t("Status")}</th><th>{t("Last sign-in")}</th>{local && <th />}</tr></thead>
            <tbody>
              {users.isLoading && <tr><td colSpan={6}><Skeleton className="h-24" /></td></tr>}
              {users.data?.map((u) => {
                const self = u.id === me?.id;
                const editable = local && u.source === "local";
                return (
                  <tr key={u.id} className={u.is_active ? undefined : "opacity-60"}>
                    <td>
                      <div className="font-medium text-slate-100">{u.display_name || u.username} {self && <Badge tone="blue">{t("you")}</Badge>}</div>
                      <div className="mono text-slate-500">{u.username}{u.email && <span className="text-slate-600"> · {u.email}</span>}</div>
                    </td>
                    <td>{editable && !self ? (
                      <Select size="sm" value={u.role} onChange={(v) => update.mutate({ id: u.id, role: v as Role })} options={ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL[r]) }))} />
                    ) : <Badge tone={u.role === "admin" ? "red" : u.role === "operator" ? "amber" : "blue"}>{t(ROLE_LABEL[u.role])}</Badge>}</td>
                    <td className="text-xs text-slate-400">{local ? (u.totp_enabled ? <Badge tone="green">{t("on")}</Badge> : "—") : u.groups.length ? u.groups.join(", ") : "—"}</td>
                    <td className="space-x-1">
                      {u.is_active ? <Badge tone="green">{t("active")}</Badge> : <Badge tone="red">{t("disabled")}</Badge>}
                      {u.locked && <Badge tone="amber">{t("locked")}</Badge>}
                      {u.must_change_password && <Badge tone="amber">{t("temporary password")}</Badge>}
                      {!local && !u.external_id && <Badge tone="amber">{t("not in SPA")}</Badge>}
                    </td>
                    <td className="text-xs text-slate-400">{Fmt.ago(u.last_login_at)}</td>
                    {local && <td className="whitespace-nowrap text-right">{editable && <>
                      {!self && <button className="btn-ghost btn-sm" onClick={() => update.mutate({ id: u.id, is_active: !u.is_active })}>{u.is_active ? t("Disable") : t("Enable")}</button>}
                      <button className="btn-ghost btn-sm" title={t("Reset the password")} onClick={() => setResetting(u)}><KeyRound size={12} /></button>
                      {u.totp_enabled && !self && <button className="btn-ghost btn-sm" title={t("Turn off two-factor authentication")} onClick={() => totpOff.mutate(u.id)}><ShieldOff size={12} /></button>}
                      {!self && <button className="btn-ghost btn-sm text-red-300" title={t("Delete")} onClick={() => askRemove(u)}><Trash2 size={12} /></button>}
                    </>}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {users.data && !users.data.length && <EmptyState title={t("No users yet")} hint={local ? undefined : t("They appear after the first synchronization with SPA")} />}
        </Card>
        <SpaConnection />
      </div>

      <ApiTokens />
      <NewUser open={creating} onClose={() => setCreating(false)} />
      <ResetPassword user={resetting} onClose={() => setResetting(null)} />
    </Page>
  );
}
