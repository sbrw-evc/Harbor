import { Save } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import clsx from "clsx";
import { copyText } from "@/lib/hooks";
import { t } from "@/i18n";
import { Card, Field } from "@/components/ui/Primitives";
import { Skeleton } from "@/components/ui/Feedback";

// The parts the pages of the WireGuard servers next to Amnezia (MikroTik, OPNsense) have in common.

/** A translated text with React nodes in place of its {placeholders}. */
export const rich = (s: string, parts: Record<string, ReactNode>) =>
  s.split(/(\{\w+\})/).map((p, i) => {
    const m = /^\{(\w+)\}$/.exec(p);
    return <Fragment key={i}>{m && m[1] in parts ? parts[m[1]] : p}</Fragment>;
  });

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex justify-between gap-3"><span className="text-slate-500">{label}</span><span className="text-right">{children}</span></div>;
}

/** The public key of the server; a click copies it. */
export function PublicKey({ value }: { value?: string | null }) {
  if (!value) return null;
  return <>
    <div className="pt-2 text-slate-500">{t("Public key")}</div>
    <button type="button" className="mono break-all text-left text-[11px] text-slate-300 hover:text-fg" title={t("Copy to the clipboard")}
      onClick={() => void copyText(value)}>{value}</button>
  </>;
}

export interface ConfigField {
  value: string;
  onChange: (v: string) => void;
  hint: string;
  placeholder?: string;
}

/** What the configs of new clients get: the address they connect to, their DNS and what they send into the tunnel. */
export function ClientConfigCard({ ready, isAdmin, saving, onSave, host, dns, allowed, onAllowed, presets }: {
  ready: boolean;
  isAdmin: boolean;
  saving: boolean;
  onSave: () => void;
  host: ConfigField;
  dns: ConfigField;
  allowed: string;
  onAllowed: (v: string) => void;
  presets: { label: string; value: string }[];
}) {
  return (
    <Card className="xl:col-span-2" title={t("Client config settings")}>
      {ready ? (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("Connection address")} hint={host.hint}>
              <input className="input mono" value={host.value} disabled={!isAdmin} onChange={(e) => host.onChange(e.target.value)} placeholder={host.placeholder} />
            </Field>
            <Field label="DNS" hint={dns.hint}>
              <input className="input mono" value={dns.value} disabled={!isAdmin} onChange={(e) => dns.onChange(e.target.value)} placeholder={dns.placeholder} />
            </Field>
          </div>
          <Field label="AllowedIPs" hint={t("what the client sends into the tunnel; changes apply to configs downloaded again")}>
            <div className="space-y-2">
              <input className="input mono" value={allowed} disabled={!isAdmin} onChange={(e) => onAllowed(e.target.value)} />
              <div className="flex flex-wrap gap-2">
                {presets.map((p) => (
                  <button type="button" key={p.value} disabled={!isAdmin} className={clsx("chip", allowed === p.value && "chip-on")} onClick={() => onAllowed(p.value)}>{p.label}</button>
                ))}
              </div>
            </div>
          </Field>
          {isAdmin && <div className="flex justify-end"><button type="submit" className="btn-primary" disabled={saving}><Save size={14} /> {t("Save")}</button></div>}
        </form>
      ) : <Skeleton className="h-56" />}
    </Card>
  );
}
