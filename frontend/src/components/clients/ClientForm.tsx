import { useState, type FormEvent } from "react";
import clsx from "clsx";
import { useFocus } from "@/lib/hooks";
import type { ClientInput } from "@/lib/api";
import type { ServerKind, VpnClient, VpnServer } from "@/lib/types";
import { k, t } from "@/i18n";
import { Field, Segmented } from "@/components/ui/Primitives";
import { NumberInput } from "@/components/ui/Controls";

const GB = 1024 ** 3;
/** Labels are English keys, drawn with t(). */
const VALIDITY = [
  { label: "∞", days: null }, { label: k("7 d"), days: 7 }, { label: k("30 d"), days: 30 }, { label: k("90 d"), days: 90 }, { label: k("1 year"), days: 365 },
];

function toLocalInput(iso: string | null | undefined) {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function ClientForm({ initial, submitLabel, busy, onSubmit, servers }: {
  initial?: Partial<VpnClient>; submitLabel: string; busy?: boolean; onSubmit: (d: ClientInput) => void; servers?: VpnServer[];
}) {
  const [server, setServer] = useState<ServerKind>("amnezia");
  const current = servers?.find((s) => s.kind === server);
  const [name, setName] = useState(initial?.name ?? "");
  const nameInput = useFocus<HTMLInputElement>();
  const [owner, setOwner] = useState(initial?.owner ?? "");
  const [contact, setContact] = useState(initial?.contact ?? "");
  const [tags, setTags] = useState((initial?.tags ?? []).join(", "));
  const [note, setNote] = useState(initial?.note ?? "");
  const [expires, setExpires] = useState(toLocalInput(initial?.expires_at));
  const [quota, setQuota] = useState(initial?.quota_bytes ? String(+(initial.quota_bytes / GB).toFixed(2)) : "");

  const preset = (days: number | null) => setExpires(days === null ? "" : toLocalInput(new Date(Date.now() + days * 86400000).toISOString()));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      name: name.trim(), owner: owner.trim(), contact: contact.trim(), note,
      tags: tags.split(",").map((tg) => tg.trim()).filter(Boolean),
      expires_at: expires ? new Date(expires).toISOString() : null,
      quota_gb: quota ? Number(quota) : null,
      ...(servers ? { server } : {}),
    });
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {servers && servers.length > 1 && (
        <Field label={t("VPN server")} hint={current && !current.ready ? (server === "opnsense"
          ? t("WireGuard on OPNsense is not set up — enter the address and the API key on the “VPN on OPNsense” page")
          : t("WireGuard on MikroTik is not set up — set it up on the “VPN on MikroTik” page"))
          : server === "mikrotik" ? t("WireGuard on the home router: access to the local network, and sites from the list go through Amnezia")
          : server === "opnsense" ? t("WireGuard on the OPNsense firewall: access to the local network by its rules")
          : t("AmneziaWG on a VPS with traffic obfuscation")}>
          <Segmented layoutId="client-server" value={server} onChange={setServer}
            options={servers.map((s) => ({ value: s.kind, label: s.title }))} />
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Name *")}><input ref={nameInput} className="input" required value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Anna's iPhone")} /></Field>
        <Field label={t("Owner")}><input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder={t("Anna")} /></Field>
        <Field label={t("Contact")}><input className="input" value={contact} onChange={(e) => setContact(e.target.value)} placeholder="@telegram / email" /></Field>
        <Field label={t("Tags")} hint={t("comma-separated: family, work")}><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
      </div>
      <Field label={t("Access period")} hint={t("when it ends, the client is blocked automatically")}>
        <div className="flex flex-wrap items-center gap-2">
          <input type="datetime-local" className="input max-w-[230px]" value={expires} onChange={(e) => setExpires(e.target.value)} />
          {VALIDITY.map((v) => (
            <button type="button" key={v.label} onClick={() => preset(v.days)}
              className={clsx("chip", (v.days === null && !expires) && "chip-on")}>{t(v.label)}</button>
          ))}
        </div>
      </Field>
      <Field label={t("Monthly quota, GB")} hint={t("empty — no limit; when exceeded, blocked until the 1st of the month")}>
        <NumberInput className="max-w-[200px]" value={quota ? Number(quota) : null} min={0} step={0.5} allowEmpty placeholder="∞"
          onChange={(v) => setQuota(v === null ? "" : String(v))} />
      </Field>
      <Field label={t("Note")}><textarea className="input min-h-[70px]" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      <div className="flex justify-end"><button type="submit" className="btn-primary" disabled={busy || !name.trim() || (current && !current.ready)}>{submitLabel}</button></div>
    </form>
  );
}
