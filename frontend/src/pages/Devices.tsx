import { useQuery } from "@tanstack/react-query";
import { useAutoAnimate } from "@formkit/auto-animate/react";
import { Cpu, HardDrive, Plus, Radar, RefreshCw, Router, Search, Server, Shield, Wifi } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { t, tServer } from "@/i18n";
import { api } from "@/lib/api";
import { Fmt } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { useLive } from "@/lib/live";
import { notify } from "@/lib/notify";
import { ROLE_LABEL, STATUS, uptime } from "@/lib/network";
import type { DiscoveryResult } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Kpi, Modal, Page, PageHeader, Segmented, StatusDot } from "@/components/ui/Primitives";
import { SetupGuide } from "@/components/onboarding/Steps";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/Feedback";
import DeviceForm, { type DevicePrefill } from "@/components/devices/DeviceForm";

const EXAMPLE_SUBNET = "192.168.0.0/24"; // NOSONAR: the subnet discovery starts with, changed by the user

export const ROLE_ICON: Record<string, ReactNode> = {
  router: <Router size={16} />, switch: <HardDrive size={16} />, firewall: <Shield size={16} />, ap: <Wifi size={16} />,
  controller: <Radar size={16} />, server: <Server size={16} />, other: <Cpu size={16} />,
};

function Discovery({ open, onClose, onAdd }: { open: boolean; onClose: () => void; onAdd: (p: DevicePrefill) => void }) {
  const [target, setTarget] = useState(EXAMPLE_SUBNET);
  const [community, setCommunity] = useState("public");
  const platforms = useQuery({ queryKey: ["device-platforms"], queryFn: () => api.devices.platforms(), staleTime: Infinity, enabled: open });
  const scan = useAction(() => api.devices.discover(target, community));
  const title = (k: string) => platforms.data?.find((p) => p.key === k)?.title ?? k;
  const rows: DiscoveryResult[] = scan.data ?? [];
  return (
    <Modal open={open} onClose={onClose} title={t("Find devices on the network")} wide>
      <p className="mb-3 text-sm text-slate-400">{t("Checks SSH, the web interface and SNMP of every address and guesses the platform. No credentials are needed.")}</p>
      <div className="flex flex-wrap gap-2">
        <input className="input mono max-w-[220px]" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={t("192.168.0.0/24 or an address")} />
        <input className="input max-w-[160px]" value={community} onChange={(e) => setCommunity(e.target.value)} placeholder="SNMP community" />
        <button className="btn-primary" disabled={scan.isPending} onClick={() => scan.mutate(undefined)}><Search size={14} /> {scan.isPending ? t("Scanning…") : t("Find")}</button>
      </div>
      {scan.isPending && <Skeleton className="mt-4 h-40" />}
      {scan.data && (
        <div className="mt-4 max-h-[55vh] overflow-auto rounded-lg border border-base-700">
          <table className="table-nc">
            <thead><tr><th>{t("Address")}</th><th>{t("Open ports")}</th><th>{t("Looks like")}</th><th>SNMP</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.host}>
                  <td className="mono">{r.host}{r.name && <div className="text-[11px] text-slate-500">{r.name}</div>}</td>
                  <td className="text-xs text-slate-400">{r.ports.join(", ") || "—"}</td>
                  <td>{r.platforms.length ? r.platforms.slice(0, 2).map((p) => <Badge key={p} tone="blue" className="mr-1">{title(p)}</Badge>) : <span className="text-xs text-slate-500">{t("not identified")}</span>}</td>
                  <td className="max-w-[260px] truncate text-xs text-slate-500" title={r.snmp?.descr}>{r.snmp?.vendor || r.snmp?.descr || "—"}</td>
                  <td>{r.known ? <Badge tone="green">{t("already added")}</Badge> :
                    <button className="btn-ghost btn-sm" onClick={() => onAdd({ host: r.host, platform: r.platforms[0] ?? "snmp", name: r.name })}><Plus size={12} /> {t("Add")}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <EmptyState title={t("Nothing found")} hint={t("Check the subnet and that the devices are reachable from the panel host")} />}
        </div>
      )}
    </Modal>
  );
}

export default function Devices() {
  const nav = useNavigate();
  const isAdmin = useAuth((s) => s.can("admin"));
  const [q, setQ] = useState("");
  const [role, setRole] = useState("all");
  const [form, setForm] = useState<{ open: boolean; prefill?: DevicePrefill }>({ open: false });
  const [discover, setDiscover] = useState(false);
  const [tbody] = useAutoAnimate<HTMLTableSectionElement>();
  const list = useQuery({ queryKey: ["devices"], queryFn: () => api.devices.list(), refetchInterval: 30_000 });
  useLive("devices", () => list.refetch(), []);
  const poll = useAction((id: string) => api.devices.poll(id), { invalidate: [["devices"]],
    onSuccess: (r) => (r.ok ? notify.success(t("Poll complete")) : notify.error(t("The device did not respond"), r.error ? tServer(r.error) : undefined)) });

  const rows = useMemo(() => (list.data ?? []).filter((d) => (role === "all" || d.role === role) &&
    (!q || [d.name, d.host, d.vendor, d.facts.model, d.location, d.platform_title].join(" ").toLowerCase().includes(q.toLowerCase()))), [list.data, q, role]);
  const total = list.data?.length ?? 0;
  const up = list.data?.filter((d) => d.status === "up").length ?? 0;
  const ports = list.data?.reduce((a, d) => a + d.counts.interfaces_up, 0) ?? 0;

  return (
    <Page>
      <PageHeader title={t("Network devices")} icon={<Router size={20} />}
        subtitle={t("Routers, switches, firewalls, access points and controllers managed by the panel")}
        actions={isAdmin && <>
          <button className="btn-ghost" onClick={() => setDiscover(true)}><Radar size={16} /> {t("Find on the network")}</button>
          <button className="btn-primary" onClick={() => setForm({ open: true })}><Plus size={16} /> {t("Add")}</button>
        </>} />
      <SetupGuide page="devices" actions={{ create: () => setForm({ open: true }), discover: () => setDiscover(true) }} />
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Kpi label={t("Total devices")} value={total} icon={<Router size={16} />} />
        <Kpi label={t("Up")} value={up} icon={<StatusDot on pulse={false} />} tone="green" delay={0.05} hint={total - up ? t("unreachable: {n}", { n: total - up }) : t("all respond")} />
        <Kpi label={t("Active ports")} value={ports} icon={<HardDrive size={16} />} tone="violet" delay={0.1} />
        <Kpi label={t("Managed APs / switches")} value={list.data?.reduce((a, d) => a + d.counts.children, 0) ?? 0} icon={<Wifi size={16} />} tone="amber" delay={0.15} hint={t("via controllers")} />
      </div>
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-[220px] flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input className="input pl-9" placeholder={t("Name, address, model, location…")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Segmented layoutId="dev-role" value={role} onChange={setRole} options={[{ value: "all", label: t("All") },
          ...["router", "switch", "firewall", "ap", "controller", "server"].map((r) => ({ value: r, label: t(ROLE_LABEL[r]) }))]} />
      </div>
      {list.error && <ErrorState error={list.error} />}
      <Card bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="table-nc min-w-[960px]">
            <thead><tr><th>{t("Device")}</th><th>{t("Platform")}</th><th>{t("Model / version")}</th><th>{t("Address")}</th><th>{t("Load")}</th><th>{t("Ports up")}</th><th>{t("Polling")}</th><th /></tr></thead>
            <tbody ref={tbody}>
              {list.isLoading && Array.from({ length: 4 }, (_, i) => <tr key={i}><td colSpan={8}><Skeleton className="h-8" /></td></tr>)}
              {rows.map((d) => {
                const st = STATUS[d.status];
                return (
                  <tr key={d.id} className="cursor-pointer" onClick={() => nav(`/devices/${d.id}`)}>
                    <td>
                      <div className="flex items-center gap-3">
                        <div className="relative rounded-lg bg-base-800 p-2 text-blue-300">{ROLE_ICON[d.role] ?? ROLE_ICON.other}
                          <span className="absolute -right-1 -top-1"><StatusDot on={d.status === "up"} warn={d.status === "error"} pulse={false} /></span></div>
                        <div className="min-w-0">
                          <div className="truncate font-medium text-slate-100">{d.name}</div>
                          <div className="text-[11px] text-slate-500">{ROLE_LABEL[d.role] ? t(ROLE_LABEL[d.role]) : d.role}{d.location ? ` · ${d.location}` : ""}</div>
                        </div>
                      </div>
                    </td>
                    <td><div className="text-sm text-slate-200">{d.vendor || "—"}</div><div className="text-[11px] text-slate-500">{d.platform_title}</div></td>
                    <td><div className="text-sm text-slate-200">{d.facts.model || "—"}</div><div className="text-[11px] text-slate-500">{[d.facts.os, d.facts.version].filter(Boolean).join(" ")}</div></td>
                    <td className="mono">{d.host}<div className="text-[11px] text-slate-500">{d.transport}</div></td>
                    <td className="text-xs text-slate-400">
                      {d.facts.cpu_percent != null && <div>CPU {Math.round(d.facts.cpu_percent)}%</div>}
                      {d.facts.memory_percent != null && <div>RAM {d.facts.memory_percent}%</div>}
                      {d.facts.uptime_seconds != null && <div>up {uptime(d.facts.uptime_seconds)}</div>}
                    </td>
                    <td className="text-xs text-slate-400">{d.counts.interfaces ? `${d.counts.interfaces_up}/${d.counts.interfaces}` : "—"}{d.counts.children ? <div>{t("devices: {n}", { n: d.counts.children })}</div> : null}</td>
                    <td>
                      <Badge tone={st.tone}>{t(st.label)}</Badge>
                      <div className="mt-0.5 max-w-[200px] truncate text-[11px] text-slate-500" title={d.last_error}>{(d.last_error && tServer(d.last_error)) || (d.last_poll_at ? Fmt.ago(d.last_poll_at) : "")}</div>
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="btn-ghost btn-sm" title={t("Poll now")} disabled={poll.isPending && poll.variables === d.id} onClick={() => poll.mutate(d.id)}>
                        <RefreshCw size={13} className={poll.isPending && poll.variables === d.id ? "animate-spin" : ""} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {list.data && !rows.length && <EmptyState icon={<Router size={22} />} title={total ? t("Nothing found") : t("No devices yet")}
            hint={total ? undefined : t("Add a device manually or find it on the network — MikroTik, Cisco, TP-Link/Omada, UniFi, Keenetic, OpenWrt, OPNsense, pfSense, HP/Aruba, Dell, Zyxel, Juniper, Huawei, FortiGate and any SNMP device are supported")} />}
        </div>
      </Card>
      <DeviceForm open={form.open} prefill={form.prefill} onClose={() => setForm({ open: false })} />
      <Discovery open={discover} onClose={() => setDiscover(false)} onAdd={(p) => { setDiscover(false); setForm({ open: true, prefill: p }); }} />
    </Page>
  );
}
