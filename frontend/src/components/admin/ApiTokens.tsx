import { useQuery } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { Fmt, ROLE_LABEL } from "@/lib/format";
import { copyText, useAction } from "@/lib/hooks";
import type { ApiTokenInfo, Role } from "@/lib/types";
import { Badge, Card, Field, Modal } from "@/components/ui/Primitives";
import { confirm } from "@/components/ui/Confirm";
import { NumberInput, Select } from "@/components/ui/Controls";
import { t } from "@/i18n";

export default function ApiTokens() {
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.admin.tokens() });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", role: "viewer" as Role, days: "90" });
  const [issued, setIssued] = useState<ApiTokenInfo | null>(null);
  const create = useAction((d: typeof form) => api.admin.createToken({ name: d.name, role: d.role, days: d.days ? Number(d.days) : null }), {
    invalidate: [["tokens"]], onSuccess: (t) => { setOpen(false); setIssued(t); },
  });
  const revoke = useAction((id: number) => api.admin.revokeToken(id), { success: t("Token revoked"), invalidate: [["tokens"]] });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(form); };

  return (
    <Card title={<span className="flex items-center gap-2"><KeyRound size={14} /> {t("API tokens")}</span>}
      actions={<button className="btn-ghost btn-sm" onClick={() => { setForm({ name: "", role: "viewer", days: "90" }); setOpen(true); }}><Plus size={14} /> {t("Issue")}</button>}
      bodyClass="p-0">
      <table className="table-nc">
        <thead><tr><th>{t("Name")}</th><th>{t("Prefix")}</th><th>{t("Role")}</th><th>{t("Expires")}</th><th>{t("Last used")}</th><th>{t("Created by")}</th><th /></tr></thead>
        <tbody>
          {tokens.data?.map((tok) => (
            <tr key={tok.id}>
              <td className="text-slate-200">{tok.name}</td>
              <td className="mono text-slate-400">{tok.prefix}…</td>
              <td><Badge tone={tok.role === "admin" ? "red" : tok.role === "operator" ? "amber" : "blue"}>{t(ROLE_LABEL[tok.role])}</Badge></td>
              <td className="text-xs text-slate-400">{tok.expires_at ? Fmt.dateTime(tok.expires_at) : t("no limit")}</td>
              <td className="text-xs text-slate-400">{Fmt.ago(tok.last_used_at)}</td>
              <td className="text-xs text-slate-400">{tok.created_by}</td>
              <td><button className="btn-danger btn-sm" onClick={async () => {
                if (await confirm({ title: t("Revoke the token?"), message: t("Integrations that use “{name}” will stop working.", { name: tok.name }), danger: true, confirmLabel: t("Revoke") }))
                  revoke.mutate(tok.id);
              }}><Trash2 size={12} /></button></td>
            </tr>
          ))}
          {tokens.data && !tokens.data.length && <tr><td colSpan={7} className="py-6 text-center text-slate-500">{t("No tokens. Use them for integrations:")} <span className="mono">Authorization: Bearer ac_…</span>, {t("documentation")} — <a className="text-blue-300" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></td></tr>}
        </tbody>
      </table>

      <Modal open={open} onClose={() => setOpen(false)} title={t("New API token")}>
        <form onSubmit={submit} className="space-y-4">
          <Field label={t("Name")}><input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Home Assistant" /></Field>
          <Field label={t("Role")}>
            <Select value={form.role} onChange={(v: Role) => setForm({ ...form, role: v })} options={(["viewer", "operator", "admin"] as Role[]).map((r) => ({ value: r, label: t(ROLE_LABEL[r]) }))} />
          </Field>
          <Field label={t("Validity, days")} hint={t("empty — no limit")}>
            <NumberInput value={form.days ? Number(form.days) : null} min={1} max={3650} allowEmpty placeholder="∞"
              onChange={(v) => setForm({ ...form, days: v === null ? "" : String(v) })} />
          </Field>
          <p className="text-xs text-slate-500">{t("The token belongs to you: it never has more rights than your role and stops working when SPA disables your account.")}</p>
          <div className="flex justify-end"><button type="submit" className="btn-primary" disabled={create.isPending}>{t("Issue")}</button></div>
        </form>
      </Modal>
      <Modal open={!!issued} onClose={() => setIssued(null)} title={t("Token issued")}>
        <p className="text-sm text-slate-300">{t("Copy the token now — it will not be shown again.")}</p>
        <div className="mt-3 flex gap-2">
          <input readOnly className="input mono" value={issued?.token ?? ""} />
          <button className="btn-primary" onClick={() => copyText(issued?.token ?? "")}><Copy size={14} /></button>
        </div>
      </Modal>
    </Card>
  );
}
