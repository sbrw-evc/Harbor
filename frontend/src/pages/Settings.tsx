import { useQuery } from "@tanstack/react-query";
import { Bell, Cable, Cloud, Router, Save, Send, Server, Settings as SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "@/lib/api";
import { copyText, useAction } from "@/lib/hooks";
import { notify } from "@/lib/notify";
import type { Settings } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Field, Page, PageHeader } from "@/components/ui/Primitives";
import { SetupGuide } from "@/components/onboarding/Steps";
import PasswordInput from "@/components/ui/PasswordInput";
import { Checkbox, NumberInput, Select } from "@/components/ui/Controls";
import { Skeleton } from "@/components/ui/Feedback";
import SpaConnection from "@/components/admin/SpaConnection";
import { k, t, tServer } from "@/i18n";

type Def = { key: string; label: string; hint?: string; type: "text" | "number" | "bool" | "select"; options?: [string, string][] };
// The infrastructure the panel manages (it was .env before the setup wizard). A change applies within seconds.
const INFRA_GROUPS: { title: string; anchor?: string; fields: Def[] }[] = [
  { title: k("Router (MikroTik)"), anchor: "infrastructure", fields: [
    { key: "router_host", label: k("Address"), hint: k("reached over SSH with the key of the panel"), type: "text" },
    { key: "router_ssh_user", label: k("SSH user"), type: "text" },
  ] },
  { title: k("Amnezia server (VPS)"), fields: [
    { key: "awg_host", label: k("Address"), hint: k("empty — no Amnezia server"), type: "text" },
    { key: "awg_ssh_user", label: k("SSH user"), type: "text" },
    { key: "awg_container", label: k("AmneziaWG container"), type: "text" },
    { key: "awg_iface", label: k("Interface"), type: "text" },
    { key: "awg_bin", label: k("Utility"), type: "text" },
    { key: "awg_dir", label: k("Config folder in the container"), type: "text" },
  ] },
  { title: k("Gateway and LAN"), fields: [
    { key: "gateway_ip", label: k("Address of the gateway (this host)"), type: "text" },
    { key: "lan_cidr", label: k("Home network"), type: "text" },
    { key: "lan_if", label: k("LAN interface of the gateway"), type: "text" },
    { key: "tunnel_net", label: k("Network of the tunnel"), type: "text" },
    { key: "port_forwards", label: k("Port forwards to the tunnel"), hint: k("e.g. 8443=192.168.0.10:443, space-separated"), type: "text" },
  ] },
  { title: k("WireGuard on MikroTik"), fields: [
    { key: "mt_wg_iface", label: k("Interface"), type: "text" },
    { key: "mt_wg_network", label: k("Client network"), type: "text" },
    { key: "mt_wg_port", label: k("Port"), type: "number" },
  ] },
  { title: k("Panel"), fields: [
    { key: "public_base_url", label: k("Address of the panel"), hint: k("for config links and Keystone, e.g. https://nc.example.com"), type: "text" },
    { key: "spa_public_url", label: k("Address for Keystone redirects"), hint: k("empty — the address of the panel"), type: "text" },
    { key: "session_ttl_hours", label: k("Session lifetime, h"), type: "number" },
    { key: "cookie_secure", label: k("Cookies only over HTTPS"), type: "bool" },
    { key: "flows_ttl_days", label: k("Keep flows in ClickHouse, days"), type: "number" },
  ] },
];

const GROUPS: { title: string; anchor?: string; fields: Def[] }[] = [
  { title: k("Client configs"), fields: [
    { key: "client_dns", label: k("DNS for clients"), hint: k("comma-separated"), type: "text" },
    { key: "endpoint_host", label: k("Server address in configs"), hint: k("empty — the address of the Amnezia server"), type: "text" },
    { key: "share_link_hours", label: k("Config link lifetime, h"), type: "number" },
  ] },
  { title: k("Default policies"), fields: [
    { key: "default_quota_gb", label: k("Default quota, GB/month"), hint: k("empty — unlimited"), type: "number" },
    { key: "default_valid_days", label: k("Default access period, days"), hint: k("empty — no limit"), type: "number" },
    { key: "quota_warn_percent", label: k("Warn when the quota is used, %"), type: "number" },
    { key: "expiry_warn_days", label: k("Warn before access ends, days"), type: "number" },
  ] },
  { title: k("Monitoring"), fields: [
    { key: "poll_seconds", label: k("Server polling interval, s"), type: "number" },
    { key: "retention_days", label: k("Keep statistics, days"), type: "number" },
    { key: "gateway_stale_minutes", label: k("Alert when the gateway is silent longer than, min"), type: "number" },
    { key: "timezone", label: k("Time zone of reports"), type: "text" },
  ] },
  { title: k("Notifications"), anchor: "notifications", fields: [
    { key: "notify_enabled", label: k("Send notifications"), type: "bool" },
    { key: "notify_min_severity", label: k("Minimum severity"), type: "select",
      options: [["info", k("Info")], ["warning", k("Warning")], ["critical", k("Critical")]] },
    { key: "notify_telegram_chat_id", label: k("Telegram chat id"), hint: k("ID of a user, group or channel"), type: "text" },
  ] },
  { title: k("Network and devices"), fields: [
    { key: "endpoint_offline_minutes", label: k("A client is offline after, min"), type: "number" },
    { key: "endpoint_retention_days", label: k("Forget inactive clients after, days"), type: "number" },
    { key: "flow_collector_host", label: k("Collector address for flow export"), hint: k("empty — the address of the gateway"), type: "text" },
    { key: "flow_collector_port", label: k("NetFlow / IPFIX port"), type: "number" },
  ] },
  { title: k("Traffic analysis"), fields: [
    { key: "threat_enabled", label: k("Check addresses against threat lists"), type: "bool" },
    { key: "threat_refresh_hours", label: k("Refresh threat lists every, h"), type: "number" },
    { key: "threat_exclude", label: k("Do not flag networks"), hint: k("your public addresses, space-separated"), type: "text" },
    { key: "rdns_enabled", label: k("Resolve names of remote hosts (PTR)"), type: "bool" },
  ] },
  { title: k("Backup"), fields: [
    { key: "backup_enabled", label: k("Daily backup"), type: "bool" },
    { key: "backup_keep", label: k("Backups to keep"), type: "number" },
  ] },
];

/** The SSH key of the panel, the checks of the router and the VPS, the client of the gateway, the storage. */
function InfraCard({ form }: { form: Settings }) {
  const q = useQuery({ queryKey: ["infra"], queryFn: () => api.ops.infra() });
  const check = useAction((target: "router" | "vps") => api.ops.checkInfra(target), {
    onSuccess: (r) => (r.ok ? notify.success(t("Connected: {detail}", { detail: r.detail || "OK" })) : notify.error(t("No connection"), tServer(r.detail))),
  });
  const gateway = useAction(() => api.ops.gatewayClient(), { success: t("The gateway client is created; the tunnel starts in a few seconds"), invalidate: [["infra"]] });
  const i = q.data;
  const key = i?.ssh_public_key ?? "";
  const mikrotik = key ? `/user ssh-keys add user=${form.router_ssh_user || "admin"} key="${key}"` : "";
  return (
    <Card title={<span className="flex items-center gap-2"><Server size={14} /> {t("Access and storage")}</span>}>
      {!i ? <Skeleton className="h-40" /> : (
        <div className="space-y-4 text-sm">
          <div className="space-y-1.5">
            <div className="text-slate-400">{t("SSH key of the panel: add it to the router and to the VPS")}</div>
            <button className="mono w-full break-all rounded-lg bg-base-900 px-3 py-2 text-left text-[11px] text-slate-300 hover:text-fg" title={t("Copy to the clipboard")} onClick={() => copyText(key)}>{key}</button>
            <div className="text-xs text-slate-500">{t("On MikroTik:")}</div>
            <button className="mono w-full break-all rounded-lg bg-base-900 px-3 py-2 text-left text-[11px] text-slate-300 hover:text-fg" title={t("Copy to the clipboard")} onClick={() => copyText(mikrotik)}>{mikrotik}</button>
            <div className="text-xs text-slate-500">{t("On the VPS: add the line to /root/.ssh/authorized_keys")}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-ghost btn-sm" disabled={!form.router_host || check.isPending} onClick={() => check.mutate("router")}><Router size={13} /> {t("Check the router")}</button>
            <button className="btn-ghost btn-sm" disabled={!form.awg_host || check.isPending} onClick={() => check.mutate("vps")}><Cloud size={13} /> {t("Check the VPS")}</button>
            <button className="btn-ghost btn-sm" disabled={!form.awg_host || gateway.isPending} onClick={() => gateway.mutate(undefined)}><Cable size={13} /> {i.gateway_client ? t("Recreate the gateway client") : t("Create the gateway client")}</button>
          </div>
          <dl className="space-y-1 text-xs">
            <div className="flex justify-between gap-3"><dt className="text-slate-500">PostgreSQL</dt><dd className="mono truncate text-slate-300">{i.storage.postgres} {i.storage.timescaledb && <Badge tone="green">TimescaleDB</Badge>}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-slate-500">ClickHouse</dt><dd className="mono truncate text-slate-300">{i.storage.clickhouse}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-slate-500">OpenBao</dt><dd className="mono truncate text-slate-300">{i.storage.openbao}</dd></div>
          </dl>
        </div>
      )}
    </Card>
  );
}

function TelegramCard({ isAdmin }: { isAdmin: boolean }) {
  const status = useQuery({ queryKey: ["notifications"], queryFn: () => api.admin.notifications() });
  const [token, setToken] = useState("");
  const save = useAction((t: string) => api.admin.setTelegramToken(t), { success: (r) => t("Bot @{bot} connected", { bot: r.bot }), invalidate: [["notifications"]], onSuccess: () => setToken("") });
  const test = useAction(() => api.admin.testNotification(), { success: t("Test message sent") });
  return (
    <Card title={<span className="flex items-center gap-2"><Bell size={14} /> {t("Telegram bot")}</span>}>
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm">
          {status.data?.configured ? <Badge tone="green">{t("configured")}{status.data.bot ? ` · @${status.data.bot}` : ""}</Badge>
            : status.data?.bot ? <Badge tone="amber">@{status.data.bot} · {t("enter the chat id")}</Badge> : <Badge>{t("not configured")}</Badge>}
        </div>
        <p className="text-xs text-slate-500">{t("Create a bot with @BotFather and paste its token. It is kept in OpenBao and not shown again.")}</p>
        {isAdmin && <>
          <div className="flex gap-2">
            <PasswordInput className="mono" placeholder="123456:ABC-DEF…" value={token} onChange={(e) => setToken(e.target.value)} />
            <button className="btn-ghost" disabled={!token || save.isPending} onClick={() => save.mutate(token)}><Save size={14} /></button>
          </div>
          <button className="btn-ghost btn-sm" disabled={!status.data?.configured || test.isPending} onClick={() => test.mutate(undefined)}><Send size={12} /> {t("Test message")}</button>
        </>}
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const isAdmin = useAuth((s) => s.can("admin"));
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.ops.settings(), refetchInterval: false, refetchOnWindowFocus: false });
  const [form, setForm] = useState<Settings>({});
  useEffect(() => { if (q.data) setForm(q.data); }, [q.data]);
  const save = useAction((s: Settings) => api.ops.saveSettings(s), { success: t("Settings saved"), invalidate: [["settings"]] });
  const spaSync = useAction(() => api.users.sync(), { success: t("Synchronization done"), invalidate: [["spa"], ["users"]] });
  const { hash } = useLocation();
  // Steps of the checklist and of the guide link to a card of this page (#spa, #notifications).
  useEffect(() => {
    if (q.data && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [q.data, hash]);

  const set = (d: Def, v: string | boolean) =>
    setForm({ ...form, [d.key]: d.type === "number" ? (v === "" ? null : Number(v)) : v });

  const input = (d: Def) => {
    if (d.type === "bool") return <Checkbox disabled={!isAdmin} checked={!!form[d.key]} onChange={(v) => set(d, v)} label={t(d.label)} />;
    const control = d.type === "select"
      ? <Select disabled={!isAdmin} value={String(form[d.key] ?? "")} onChange={(v) => set(d, v)}
          options={(d.options ?? []).map(([v, l]) => ({ value: v, label: t(l) }))} />
      : d.type === "number"
        ? <NumberInput disabled={!isAdmin} allowEmpty placeholder="—" value={form[d.key] === null || form[d.key] === "" ? null : Number(form[d.key])}
            min={0} onChange={(v) => setForm({ ...form, [d.key]: v })} />
        : <input className="input" disabled={!isAdmin} value={String(form[d.key] ?? "")} onChange={(e) => set(d, e.target.value)} />;
    return <Field label={t(d.label)} hint={d.hint ? t(d.hint) : undefined}>{control}</Field>;
  };

  return (
    <Page>
      <PageHeader title={t("Settings")} icon={<SettingsIcon size={20} />} subtitle={isAdmin ? t("Panel parameters and business rules") : t("View only")}
        actions={isAdmin && <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate(form)}><Save size={16} /> {t("Save")}</button>} />
      <SetupGuide page="settings" actions={{ sync: () => spaSync.mutate(undefined) }} />
      {!q.data ? <Skeleton className="h-96" /> : (
        <div className="grid gap-4 xl:grid-cols-3">
          {isAdmin && INFRA_GROUPS.map((g) => (
            <div key={g.title} id={g.anchor} className="scroll-mt-20">
              <Card title={t(g.title)}>
                <div className="space-y-4">{g.fields.map((d) => <div key={d.key}>{input(d)}</div>)}</div>
              </Card>
            </div>
          ))}
          {isAdmin && <InfraCard form={form} />}
          {GROUPS.map((g) => (
            <div key={g.title} id={g.anchor} className="scroll-mt-20">
              <Card title={t(g.title)}>
                <div className="space-y-4">{g.fields.map((d) => <div key={d.key}>{input(d)}</div>)}</div>
              </Card>
            </div>
          ))}
          <TelegramCard isAdmin={isAdmin} />
          {isAdmin && <div id="spa" className="scroll-mt-20 xl:col-span-2"><SpaConnection /></div>}
        </div>
      )}
    </Page>
  );
}
