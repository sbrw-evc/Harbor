import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { BrickWall, KeyRound, Settings as SettingsIcon, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAction } from "@/lib/hooks";
import { t, tServer } from "@/i18n";
import { useAuth } from "@/store/auth";
import { Badge, Card, Field, Page, PageHeader } from "@/components/ui/Primitives";
import { ErrorState, Skeleton } from "@/components/ui/Feedback";
import PasswordInput from "@/components/ui/PasswordInput";
import { confirm } from "@/components/ui/Confirm";
import { ClientConfigCard, PublicKey, Row, rich } from "@/components/vpn/WireguardServer";

const DEFAULT_NETWORK = "10.10.0.0/24"; // NOSONAR: the default client network, shown until OPNsense reports its own

export default function OpnsenseVpn() {
  const isAdmin = useAuth((s) => s.can("admin"));
  const info = useQuery({ queryKey: ["opnsense-vpn"], queryFn: () => api.ops.opnsenseVpn(), refetchInterval: 30_000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.ops.settings() });
  const i = info.data;

  const [host, setHost] = useState("");
  const [dns, setDns] = useState("");
  const [allowed, setAllowed] = useState("");
  useEffect(() => { if (i) { setHost(i.endpoint_host); setDns(i.client_dns); setAllowed(i.allowed_ips); } }, [i?.endpoint_host, i?.client_dns, i?.allowed_ips]);
  const save = useAction(() => api.ops.saveSettings({ opn_endpoint_host: host.trim(), opn_client_dns: dns.trim(), opn_allowed_ips: allowed.trim() || "0.0.0.0/0" }), {
    success: t("Client settings saved — new configs will be issued with them"), invalidate: [["opnsense-vpn"], ["settings"]],
  });

  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const saveKey = useAction(() => api.ops.setOpnsenseKey(key.trim(), secret.trim()), {
    success: t("The OPNsense API key is checked and saved in OpenBao"), invalidate: [["opnsense-vpn"], ["vpn-servers"]],
    onSuccess: () => { setKey(""); setSecret(""); },
  });
  const removeKey = useAction(() => api.ops.removeOpnsenseKey(), {
    success: t("The OPNsense API key is removed"), invalidate: [["opnsense-vpn"], ["vpn-servers"]],
  });
  const askRemove = async () => {
    if (await confirm({
      title: t("Remove the OPNsense API key?"),
      message: t("The panel stops managing the WireGuard peers on OPNsense; the peers themselves stay there."),
      confirmLabel: t("Remove"), danger: true,
    })) removeKey.mutate(undefined);
  };

  const lan = String(settings.data?.lan_cidr ?? "");
  const presets = i?.network ? [
    { label: t("All traffic"), value: "0.0.0.0/0" },
    ...(lan ? [{ label: t("Home network only"), value: `${lan}, ${i.network}` }] : []),
  ] : [];
  const state = !i ? null
    : !i.host ? <Badge tone="amber">{t("no address")}</Badge>
    : !i.credentials ? <Badge tone="amber">{t("no API key")}</Badge>
    : i.error ? <Badge tone="red">{t("unreachable")}</Badge>
    : !i.found ? <Badge tone="amber">{t("instance not found")}</Badge>
    : i.enabled ? <Badge tone="green">{t("running")}</Badge> : <Badge tone="red">{t("disabled")}</Badge>;

  return (
    <Page>
      <PageHeader title={t("VPN on OPNsense")} icon={<BrickWall size={20} />}
        subtitle={t("WireGuard instance on the OPNsense firewall: its peers are managed together with the other VPN clients")}
        actions={<>
          <Link to="/clients" className="btn-ghost"><Users size={16} /> {t("Clients")}</Link>
          {isAdmin && <Link to="/settings#infrastructure" className="btn-ghost"><SettingsIcon size={16} /> {t("Settings")}</Link>}
        </>} />
      {info.error && <ErrorState error={info.error} />}
      {i?.error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{tServer(i.error)}</div>}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title={t("Instance")}>
          {i ? (
            <div className="space-y-2 text-sm">
              <Row label={t("State")}>{state}</Row>
              <Row label="OPNsense"><span className="mono">{i.host || "—"}</span></Row>
              <Row label={t("Instance")}><span className="mono">{i.instance}{i.device && i.device !== i.instance ? ` (${i.device})` : ""}</span></Row>
              <Row label={t("Port")}><span className="mono">{i.port ? `${i.port}/udp` : "—"}</span></Row>
              <Row label={t("Client network")}><span className="mono">{i.network || "—"}</span></Row>
              <Row label={t("Server address")}><span className="mono">{i.server_ip || "—"}</span></Row>
              <Row label={t("Peers")}>{i.peers_total} <span className="text-slate-500">{t("({n} blocked)", { n: i.peers_disabled })}</span></Row>
              <Row label={t("Online")}>{i.peers_online}</Row>
              <PublicKey value={i.public_key} />
            </div>
          ) : <Skeleton className="h-56" />}
        </Card>

        <ClientConfigCard ready={!!i} isAdmin={isAdmin} saving={save.isPending} onSave={() => save.mutate(undefined)}
          host={{ value: host, onChange: setHost, placeholder: i?.public_address ?? "",
            hint: t("empty — the public IP of the router in front ({ip}); set it if the port is forwarded elsewhere", { ip: i?.public_address || t("not detected") }) }}
          dns={{ value: dns, onChange: setDns, placeholder: i?.server_ip ?? "",
            hint: t("empty — the address of OPNsense in the tunnel ({ip})", { ip: i?.server_ip || "—" }) }}
          allowed={allowed} onAllowed={setAllowed} presets={presets} />
      </div>

      {isAdmin && (
        <Card title={t("OPNsense API key")}>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); saveKey.mutate(undefined); }}>
            <p className="text-sm text-slate-400">
              {t("Create a key in System → Access → Users → API keys for a user with access to the WireGuard pages. The key is checked against OPNsense and kept only in OpenBao (opnsense/api), never in the database.")}
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("Key")}><input className="input mono" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={i?.credentials ? t("saved — enter a new one to replace it") : ""} /></Field>
              <Field label={t("Secret")}><PasswordInput className="mono" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {i?.credentials && <button type="button" className="btn-ghost" disabled={removeKey.isPending} onClick={askRemove}><Trash2 size={14} /> {t("Remove")}</button>}
              <button type="submit" className="btn-primary" disabled={saveKey.isPending || !key.trim() || !secret.trim() || !i?.host}><KeyRound size={14} /> {t("Check and save")}</button>
            </div>
            {!i?.host && <p className="text-xs text-amber-300">{t("Set the address of OPNsense in the settings first.")}</p>}
          </form>
        </Card>
      )}

      <Card title={t("How it works")}>
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-300">
          <li>{rich(t("The panel manages the peers of the WireGuard instance {instance} through the OPNsense API; the instance itself, its port, NAT and firewall rules are set up in OPNsense."), {
            instance: <span className="mono">{i?.instance ?? "wg0"}</span> })}</li>
          <li>{rich(t("A new client gets a free address from {network} and a preshared key; changes are applied with a WireGuard reconfigure on OPNsense."), {
            network: <span className="mono">{i?.network ?? DEFAULT_NETWORK}</span> })}</li>
          <li>{t("Creating, blocking, quotas, access periods, traffic, the map and config links work the same as for Amnezia clients — on the “VPN clients” page choose the OPNsense server.")}</li>
          <li>{t("The config is plain WireGuard: it imports into the WireGuard and Amnezia VPN apps (QR or a .conf file).")}</li>
        </ul>
      </Card>
    </Page>
  );
}
