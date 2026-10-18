import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Router, Users, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAction } from "@/lib/hooks";
import { t } from "@/i18n";
import { useAuth } from "@/store/auth";
import { Badge, Card, Page, PageHeader } from "@/components/ui/Primitives";
import { ErrorState, Skeleton } from "@/components/ui/Feedback";
import { confirm } from "@/components/ui/Confirm";
import { ClientConfigCard, PublicKey, Row, rich } from "@/components/vpn/WireguardServer";

const DEFAULT_NETWORK = "10.9.0.0/24"; // NOSONAR: the default client network, shown until the router reports its own

export default function MikrotikVpn() {
  const isAdmin = useAuth((s) => s.can("admin"));
  const info = useQuery({ queryKey: ["mikrotik-vpn"], queryFn: () => api.ops.mikrotikVpn(), refetchInterval: 30_000 });
  const setup = useAction(() => api.ops.setupMikrotikVpn(), {
    success: t("WireGuard on MikroTik is set up"), invalidate: [["mikrotik-vpn"], ["vpn-servers"], ["routing"]],
  });
  const [host, setHost] = useState("");
  const [dns, setDns] = useState("");
  const [allowed, setAllowed] = useState("");
  const i = info.data;
  useEffect(() => { if (i) { setHost(i.endpoint_host); setDns(i.client_dns); setAllowed(i.allowed_ips); } }, [i?.endpoint_host, i?.client_dns, i?.allowed_ips]);
  const save = useAction(() => api.ops.saveSettings({ mt_endpoint_host: host.trim(), mt_client_dns: dns.trim(), mt_allowed_ips: allowed.trim() || "0.0.0.0/0" }), {
    success: t("Client settings saved — new configs will be issued with them"), invalidate: [["mikrotik-vpn"], ["settings"]],
  });
  const presets = i ? [
    { label: t("All traffic"), value: "0.0.0.0/0" },
    { label: t("Home network only"), value: `${i.lan_cidr}, ${i.network}` },
  ] : [];
  const runSetup = async () => {
    if (await confirm({
      title: i?.configured ? t("Run the setup again?") : t("Set up WireGuard on MikroTik?"),
      message: t("A WireGuard interface, an address, firewall rules for the port and DNS, and mangle rules that send sites from the list through Amnezia will be created (if missing). Existing rules are not changed."),
      confirmLabel: t("Set up"),
    })) setup.mutate(undefined);
  };

  return (
    <Page>
      <PageHeader title={t("VPN on MikroTik")} icon={<Router size={20} />}
        subtitle={t("WireGuard server on the home router: its clients are managed together with Amnezia clients")}
        actions={<>
          <Link to="/clients" className="btn-ghost"><Users size={16} /> {t("Clients")}</Link>
          {isAdmin && <button className={i?.configured ? "btn-ghost" : "btn-primary"} disabled={setup.isPending} onClick={runSetup}>
            <Wrench size={16} /> {i?.configured ? t("Run the setup again") : t("Set up on MikroTik")}
          </button>}
        </>} />
      {info.error && <ErrorState error={info.error} />}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title={t("Interface")}>
          {i ? (
            <div className="space-y-2 text-sm">
              <Row label={t("State")}>
                {!i.configured ? <Badge tone="amber">{t("not set up")}</Badge> : i.running ? <Badge tone="green">{t("running")}</Badge> : <Badge tone="red">{t("stopped")}</Badge>}
              </Row>
              <Row label={t("Interface")}><span className="mono">{i.interface}</span></Row>
              <Row label={t("Port")}><span className="mono">{i.port}/udp</span></Row>
              <Row label={t("Client network")}><span className="mono">{i.network}</span></Row>
              <Row label={t("Router address")}><span className="mono">{i.server_ip}</span></Row>
              <Row label={t("Public address")}><span className="mono">{i.public_address || "—"}</span></Row>
              <Row label={t("Peers")}>{i.peers_total} <span className="text-slate-500">{t("({n} blocked)", { n: i.peers_disabled })}</span></Row>
              <PublicKey value={i.public_key} />
            </div>
          ) : <Skeleton className="h-56" />}
        </Card>

        <ClientConfigCard ready={!!i} isAdmin={isAdmin} saving={save.isPending} onSave={() => save.mutate(undefined)}
          host={{ value: host, onChange: setHost, placeholder: i?.public_address,
            hint: t("empty — the public IP of the router ({ip}); a domain can be set", { ip: i?.public_address || t("not detected") }) }}
          dns={{ value: dns, onChange: setDns, placeholder: i?.server_ip, hint: t("empty — the DNS of the router ({ip})", { ip: i?.server_ip ?? "—" }) }}
          allowed={allowed} onAllowed={setAllowed} presets={presets} />
      </div>

      <Card title={t("How it works")}>
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-300">
          <li>{rich(t("Clients connect to the home router over WireGuard on port {port} and get an address from {network}."), {
            port: <span className="mono">{i?.port ?? 51820}/udp</span>, network: <span className="mono">{i?.network ?? DEFAULT_NETWORK}</span> })}</li>
          <li>{t("They can reach the local network, and the internet goes through the provider; sites from the routing list, as for LAN devices, go through Amnezia.")}</li>
          <li>{t("Creating, blocking, quotas, access periods, traffic, the map and config links work the same as for Amnezia clients — on the “VPN clients” page choose the MikroTik server.")}</li>
          <li>{t("The config is plain WireGuard: it imports into the WireGuard and Amnezia VPN apps (QR or a .conf file).")}</li>
          <li>{rich(t("Objects on the router are marked with the comment {tag}; the rollback is described in the Wiki."), { tag: <span className="mono">amnezia</span> })}</li>
        </ul>
      </Card>
    </Page>
  );
}
