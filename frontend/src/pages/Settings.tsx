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
const field = (type: Def["type"]) => (key: string, label: string, hint?: string): Def => ({ key, label, hint, type });
const text = field("text");
const num = field("number");
const bool = field("bool");
const select = (key: string, label: string, options: [string, string][]): Def => ({ key, label, type: "select", options });
// The infrastructure the panel manages (it was .env before the setup wizard). A change applies within seconds.
const INFRA_GROUPS: { title: string; anchor?: string; fields: Def[] }[] = [
  { title: k("Router (MikroTik)"), anchor: "infrastructure", fields: [
    text("router_host", k("Address"), k("reached over SSH with the key of the panel")),
    text("router_ssh_user", k("SSH user")),
  ] },
  { title: k("Amnezia server (VPS)"), fields: [
    text("awg_host", k("Address"), k("empty — no Amnezia server")),
    text("awg_ssh_user", k("SSH user")),
    text("awg_container", k("AmneziaWG container")),
    text("awg_iface", k("Interface")),
    text("awg_bin", k("Utility")),
    text("awg_dir", k("Config folder in the container")),
  ] },
  { title: k("Gateway and LAN"), fields: [
    text("gateway_ip", k("Address of the gateway (this host)")),
    text("lan_cidr", k("Home network")),
    text("lan_if", k("LAN interface of the gateway")),
    text("tunnel_net", k("Network of the tunnel")),
    text("port_forwards", k("Port forwards to the tunnel"), k("e.g. 8443=192.168.0.10:443, space-separated")),
  ] },
  { title: k("WireGuard on MikroTik"), fields: [
    text("mt_wg_iface", k("Interface")),
    text("mt_wg_network", k("Client network")),
    num("mt_wg_port", k("Port")),
  ] },
  { title: k("WireGuard on OPNsense"), fields: [
    text("opn_host", k("Address"), k("reached over the REST API; empty — no OPNsense server; the API key is entered on the “VPN on OPNsense” page")),
    text("opn_wg_instance", k("Instance"), k("the name of the instance or its device, e.g. wg0")),
    bool("opn_verify_tls", k("Check the TLS certificate"), k("off for the self-signed certificate of OPNsense")),
  ] },
  { title: k("Panel"), fields: [
    text("public_base_url", k("Address of the panel"), k("for config links and Keystone, e.g. https://nc.example.com")),
    text("spa_public_url", k("Address for Keystone redirects"), k("empty — the address of the panel")),
    num("session_ttl_hours", k("Session lifetime, h")),
    bool("cookie_secure", k("Cookies only over HTTPS")),
    num("flows_ttl_days", k("Keep flows in ClickHouse, days")),
  ] },
];

const GROUPS: { title: string; anchor?: string; fields: Def[] }[] = [
  { title: k("Client configs"), fields: [
    text("client_dns", k("DNS for clients"), k("comma-separated")),
    text("endpoint_host", k("Server address in configs"), k("empty — the address of the Amnezia server")),
    num("share_link_hours", k("Config link lifetime, h")),
  ] },
  { title: k("Default policies"), fields: [
    num("default_quota_gb", k("Default quota, GB/month"), k("empty — unlimited")),
    num("default_valid_days", k("Default access period, days"), k("empty — no limit")),
    num("quota_warn_percent", k("Warn when the quota is used, %")),
    num("expiry_warn_days", k("Warn before access ends, days")),
  ] },
  { title: k("Monitoring"), fields: [
    num("poll_seconds", k("Server polling interval, s")),
    num("retention_days", k("Keep statistics, days")),
    num("gateway_stale_minutes", k("Alert when the gateway is silent longer than, min")),
    text("timezone", k("Time zone of reports")),
  ] },
  { title: k("Notifications"), anchor: "notifications", fields: [
    bool("notify_enabled", k("Send notifications")),
    select("notify_min_severity", k("Minimum severity"), [["info", k("Info")], ["warning", k("Warning")], ["critical", k("Critical")]]),
    text("notify_telegram_chat_id", k("Telegram chat id"), k("ID of a user, group or channel")),
  ] },
  { title: k("Network and devices"), fields: [
    num("endpoint_offline_minutes", k("A client is offline after, min")),
    num("endpoint_retention_days", k("Forget inactive clients after, days")),
    text("flow_collector_host", k("Collector address for flow export"), k("empty — the address of the gateway")),
    num("flow_collector_port", k("NetFlow / IPFIX port")),
  ] },
  { title: k("Traffic analysis"), fields: [
    bool("threat_enabled", k("Check addresses against threat lists")),
    num("threat_refresh_hours", k("Refresh threat lists every, h")),
    text("threat_exclude", k("Do not flag networks"), k("your public addresses, space-separated")),
    bool("rdns_enabled", k("Resolve names of remote hosts (PTR)")),
  ] },
  { title: k("Backup"), fields: [
    bool("backup_enabled", k("Daily backup")),
    num("backup_keep", k("Backups to keep")),
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
