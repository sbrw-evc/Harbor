import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowLeft, Ban, Download, KeyRound, Pencil, Play, Power, RefreshCw, Router, Save, Terminal, Trash2, Unlock, Waypoints,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { t, tServer } from "@/i18n";
import { api } from "@/lib/api";
import { Fmt } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { useLive } from "@/lib/live";
import { notify } from "@/lib/notify";
import { ACTION_LABEL, ROLE_LABEL, SOURCE_LABEL, STATUS, bps, speed, uptime } from "@/lib/network";
import type { DeviceAction, DeviceInterface, NetworkDevice, NetworkEndpoint } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Field, Modal, Page, PageHeader, Segmented, StatusDot, UsageBar } from "@/components/ui/Primitives";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/Feedback";
import { confirm } from "@/components/ui/Confirm";
import { NumberInput, Select } from "@/components/ui/Controls";
import DeviceForm from "@/components/devices/DeviceForm";
import { ROLE_ICON } from "./Devices";

type Tab = "interfaces" | "neighbors" | "endpoints" | "children" | "manage";

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex justify-between gap-3 border-b border-base-700/40 py-1.5 text-sm"><span className="text-slate-500">{label}</span><span className="truncate text-right text-slate-200">{value || "—"}</span></div>;
}

function Interfaces({ d, can }: { d: NetworkDevice; can: boolean }) {
  const [edit, setEdit] = useState<DeviceInterface | null>(null);
  const [desc, setDesc] = useState("");
  const act = useAction(({ params }: { params: Record<string, unknown> }) => api.devices.action(d.id, "interface", params),
    { success: t("Interface changed"), invalidate: [["device", d.id]], onSuccess: () => setEdit(null) });
  const toggle = async (i: DeviceInterface) => {
    const enable = !i.admin_up;
    if (await confirm({ title: enable ? t("Enable {name}?", { name: i.name }) : t("Disable {name}?", { name: i.name }), message: enable ? t("The port will be enabled.") :
      t("The port will be administratively disabled. If the panel reaches the device through it, the connection will be lost."), danger: !enable, confirmLabel: enable ? t("Enable") : t("Disable") }))
      act.mutate({ params: { name: i.name, enabled: enable } });
  };
  const ifs = d.interfaces ?? [];
  if (!ifs.length) return <EmptyState title={t("No interfaces received")} hint={t("The device has not been polled yet or the driver does not return interfaces")} />;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="table-nc min-w-[900px]">
          <thead><tr><th>{t("Interface")}</th><th>{t("State")}</th><th>{t("Speed")}</th><th>{t("In")}</th><th>{t("Out")}</th><th>{t("Utilization")}</th><th>{t("IP addresses")}</th><th /></tr></thead>
          <tbody>
            {ifs.map((i) => (
              <tr key={i.name}>
                <td><div className="font-medium text-slate-100">{i.name}</div><div className="max-w-[240px] truncate text-[11px] text-slate-500">{i.description || i.type}{i.mac ? ` · ${i.mac}` : ""}</div></td>
                <td>{i.admin_up === false ? <Badge>{t("disabled")}</Badge> : i.oper_up ? <Badge tone="green">up</Badge> : <Badge tone="amber">down</Badge>}</td>
                <td className="text-xs text-slate-400">{speed(i.speed_mbps)}</td>
                <td className="mono">{i.rx_bps != null ? bps(i.rx_bps) : "—"}</td>
                <td className="mono">{i.tx_bps != null ? bps(i.tx_bps) : "—"}</td>
                <td className="w-32">{i.utilization != null ? <UsageBar percent={i.utilization} /> : <span className="text-xs text-slate-600">—</span>}</td>
                <td className="mono text-xs">{i.addresses.join(", ")}</td>
                <td className="whitespace-nowrap">
                  {can && <>
                    <button className="btn-ghost btn-sm mr-1" title={t("Description")} onClick={() => { setEdit(i); setDesc(i.description); }}><Pencil size={12} /></button>
                    <button className="btn-ghost btn-sm" title={i.admin_up === false ? t("Enable") : t("Disable")} onClick={() => toggle(i)}><Power size={12} className={i.admin_up === false ? "text-emerald-300" : "text-red-300"} /></button>
                  </>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={t("Description of {name}", { name: edit?.name ?? "" })}>
        <input className="input" value={desc} maxLength={120} onChange={(e) => setDesc(e.target.value)} />
        <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={act.isPending} onClick={() => edit && act.mutate({ params: { name: edit.name, description: desc } })}><Save size={14} /> {t("Save")}</button></div>
      </Modal>
    </>
  );
}

export function EndpointTable({ rows, showDevice, canBlock, onEdit }: { rows: NetworkEndpoint[]; showDevice?: boolean; canBlock?: boolean; onEdit?: (e: NetworkEndpoint) => void }) {
  const block = useAction(({ mac, blocked }: { mac: string; blocked: boolean }) => api.devices.blockEndpoint(mac, blocked),
    { success: (r) => tServer(r.output), invalidate: [["endpoints"], ["device"]] });
  return (
    <div className="overflow-x-auto">
      <table className="table-nc min-w-[860px]">
        <thead><tr><th>{t("Client")}</th><th>IP</th><th>{t("MAC / vendor")}</th>{showDevice && <th>{t("Device")}</th>}<th>{t("Connection")}</th><th>{t("Seen")}</th>{(canBlock || onEdit) && <th />}</tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.mac} className={e.blocked ? "opacity-60" : ""}>
              <td><div className="flex items-center gap-2"><StatusDot on={e.online} pulse={false} /><span className="text-slate-100">{e.name || e.vendor || "—"}</span>{e.blocked && <Badge tone="red">{t("blocked")}</Badge>}</div>
                {e.alias && e.hostname && <div className="text-[11px] text-slate-500">{e.hostname}</div>}</td>
              <td className="mono">{e.ip || "—"}</td>
              <td><div className="mono">{e.mac}</div><div className="max-w-[200px] truncate text-[11px] text-slate-500">{e.vendor}</div></td>
              {showDevice && <td>{e.device_id ? <Link className="text-blue-300 hover:underline" to={`/devices/${e.device_id}`}>{e.device_name}</Link> : "—"}</td>}
              <td className="text-xs text-slate-400">{SOURCE_LABEL[e.source] ? t(SOURCE_LABEL[e.source]) : e.source}{e.interface || e.port ? ` · ${e.port || e.interface}` : ""}
                {e.ssid && <div>{e.ssid}{e.signal != null ? ` · ${e.signal} dBm` : ""}</div>}{e.vlan != null && <div>VLAN {e.vlan}</div>}</td>
              <td className="text-xs text-slate-400">{Fmt.ago(e.last_seen)}</td>
              {(canBlock || onEdit) && <td className="whitespace-nowrap">
                {onEdit && <button className="btn-ghost btn-sm mr-1" title={t("Name and note")} onClick={() => onEdit(e)}><Pencil size={12} /></button>}
                {canBlock && <button className="btn-ghost btn-sm" title={e.blocked ? t("Unblock the client") : t("Block on the controller")} disabled={block.isPending}
                  onClick={async () => { if (e.blocked || await confirm({ title: t("Block {name}?", { name: e.name || e.mac }), message: t("The client will be blocked on the controller / router (UniFi, Omada, Keenetic)."), danger: true, confirmLabel: t("Block the client") })) block.mutate({ mac: e.mac, blocked: !e.blocked }); }}>
                  {e.blocked ? <Unlock size={12} /> : <Ban size={12} />}</button>}
              </td>}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <EmptyState title={t("No clients")} hint={t("Clients come from ARP, DHCP, Wi-Fi and the MAC tables of the devices")} />}
    </div>
  );
}

function ServerList({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return <input className="input mono" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />;
}

function Manage({ d }: { d: NetworkDevice }) {
  const isAdmin = useAuth((s) => s.can("admin"));
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.ops.settings() });
  const caps = new Set(d.capabilities);
  const [name, setName] = useState(d.facts.hostname || d.name);
  const [dns, setDns] = useState("1.1.1.1, 8.8.8.8");
  const [ntp, setNtp] = useState("pool.ntp.org");
  const [flow, setFlow] = useState({ collector: "", port: 2055, protocol: "netflow9", interfaces: "" });
  const [cmd, setCmd] = useState("");
  const [output, setOutput] = useState<string | null>(null);
  const act = useAction(({ action, params }: { action: DeviceAction; params: Record<string, unknown> }) => api.devices.action(d.id, action, params), {
    success: (r) => (r.output && r.output.length < 200 ? tServer(r.output) : t("Done")), invalidate: [["device", d.id]],
    onSuccess: (r) => setOutput(r.output || null),
  });
  const run = (action: DeviceAction, params: Record<string, unknown> = {}) => act.mutate({ action, params });
  const list = (s: string) => s.split(/[\s,;]+/).filter(Boolean);
  const collector = flow.collector || String(settings.data?.flow_collector_host || "");

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {caps.has("hostname") && <Card title={t(ACTION_LABEL.hostname)}><div className="flex gap-2"><input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-primary" disabled={act.isPending} onClick={() => run("hostname", { name })}><Save size={14} /></button></div></Card>}
      {caps.has("dns") && <Card title={t(ACTION_LABEL.dns)}><div className="flex gap-2"><ServerList value={dns} onChange={setDns} placeholder="1.1.1.1, 8.8.8.8" />
        <button className="btn-primary" disabled={act.isPending} onClick={() => run("dns", { servers: list(dns) })}><Save size={14} /></button></div></Card>}
      {caps.has("ntp") && <Card title={t(ACTION_LABEL.ntp)}><div className="flex gap-2"><ServerList value={ntp} onChange={setNtp} placeholder="pool.ntp.org" />
        <button className="btn-primary" disabled={act.isPending} onClick={() => run("ntp", { servers: list(ntp) })}><Save size={14} /></button></div></Card>}
      {caps.has("flow_export") && (
        <Card title={<span className="flex items-center gap-2"><Waypoints size={14} /> {t(ACTION_LABEL.flow_export)}</span>}>
          <p className="mb-3 text-xs text-slate-500">{t("Sets the device to send flows to the panel collector — after that the traffic of the device appears in Flow analysis.")}</p>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label={t("Collector")}><input className="input mono" value={collector} onChange={(e) => setFlow({ ...flow, collector: e.target.value })} placeholder={t("GATEWAY_IP by default")} /></Field>
            <Field label={t("Port")}><NumberInput value={flow.port} min={1} max={65535} onChange={(v) => setFlow({ ...flow, port: v ?? 2055 })} /></Field>
            <Field label={t("Protocol")}><Select value={flow.protocol} onChange={(v) => setFlow({ ...flow, protocol: v, port: v === "sflow" ? 6343 : flow.port === 6343 ? 2055 : flow.port })}
              options={[{ value: "netflow9", label: "NetFlow v9" }, { value: "ipfix", label: "IPFIX" }, { value: "netflow5", label: "NetFlow v5" }, { value: "sflow", label: "sFlow" }]} /></Field>
          </div>
          {d.platform === "cisco_ios" && <div className="mt-3"><Field label={t("Interfaces to monitor")} hint={t("comma-separated, e.g. Gi1/0/1, Vlan10")}><input className="input mono" value={flow.interfaces} onChange={(e) => setFlow({ ...flow, interfaces: e.target.value })} /></Field></div>}
          <div className="mt-3 flex gap-2">
            <button className="btn-primary" disabled={act.isPending} onClick={() => run("flow_export", { ...flow, collector, interfaces: list(flow.interfaces), enabled: true })}><Play size={14} /> {t("Enable export")}</button>
            <button className="btn-ghost" disabled={act.isPending} onClick={() => run("flow_export", { ...flow, collector, enabled: false })}>{t("Disable")}</button>
          </div>
        </Card>
      )}
      {caps.has("reboot") && isAdmin && d.platform !== "omada" && d.platform !== "unifi" && d.platform !== "meraki" && (
        <Card title={t(ACTION_LABEL.reboot)}>
          <p className="mb-3 text-sm text-slate-400">{t("The device will be unavailable for a few minutes; unsaved configuration is saved before the reboot where supported.")}</p>
          <button className="btn-danger" disabled={act.isPending} onClick={async () => {
            if (await confirm({ title: t("Reboot {name}?", { name: d.name }), message: t("All clients connected through the device will lose connectivity until it boots."), danger: true, confirmLabel: t("Reboot") })) run("reboot");
          }}><Power size={14} /> {t("Reboot")}</button>
        </Card>
      )}
      {caps.has("command") && isAdmin && (
        <Card title={<span className="flex items-center gap-2"><Terminal size={14} /> {t(ACTION_LABEL.command)}</span>} className="xl:col-span-2">
          <div className="flex gap-2">
            <input className="input mono" value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder={d.platform === "mikrotik" ? "/ip route print" : "show ip route"}
              onKeyDown={(e) => e.key === "Enter" && cmd && run("command", { command: cmd })} />
            <button className="btn-primary" disabled={!cmd || act.isPending} onClick={() => run("command", { command: cmd })}><Play size={14} /> {t("Run")}</button>
          </div>
          <p className="mt-1 text-[11px] text-slate-500">{t("The command runs as the device account and is recorded in the audit log.")}</p>
        </Card>
      )}
      {output !== null && (
        <Card title={t("Device response")} className="xl:col-span-2" actions={<button className="btn-ghost btn-sm" onClick={() => setOutput(null)}>{t("Close")}</button>}>
          <pre className="mono max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg bg-base-950/70 p-3 text-slate-300">{output || t("(empty)")}</pre>
        </Card>
      )}
      {!caps.size && <Card><EmptyState title={t("Management is not available")} hint={t("The platform or the chosen transport supports monitoring only")} /></Card>}
    </div>
  );
}

export default function DeviceDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const isAdmin = useAuth((s) => s.can("admin"));
  const canOperate = useAuth((s) => s.can("operator"));
  const [tab, setTab] = useState<Tab>("interfaces");
  const [editing, setEditing] = useState(false);
  const q = useQuery({ queryKey: ["device", id], queryFn: () => api.devices.get(id), refetchInterval: 30_000 });
  useLive("devices", (e: { id: string }) => { if (e.id === id) void q.refetch(); }, [id]);
  const poll = useAction(() => api.devices.poll(id), { invalidate: [["device", id]],
    onSuccess: (r) => (r.ok ? notify.success(t("Poll complete")) : notify.error(t("The device did not respond"), r.error ? tServer(r.error) : undefined)) });
  const remove = useAction(() => api.devices.remove(id), { success: t("Device deleted"), invalidate: [["devices"]], onSuccess: () => nav("/devices") });
  const resetKey = useAction(() => api.devices.resetHostKey(id), { success: t("Key reset — a new one will be saved on the next connection"), invalidate: [["device", id]] });
  const childReboot = useAction((mac: string) => api.devices.action(id, "reboot", { mac }), { success: t("Reboot started") });

  const d = q.data;
  if (q.error) return <ErrorState error={q.error} />;
  if (!d) return <PageLoader />;
  const st = STATUS[d.status];
  const f = d.facts;
  const downloadBackup = async () => {
    const r = await fetch(api.devices.backupUrl(id), { credentials: "same-origin" });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail;
      notify.error(t("Could not get the backup"), typeof detail === "string" ? tServer(detail) : detail);
      return;
    }
    const blob = new Blob([await r.text()], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${d.name}-${new Date().toISOString().slice(0, 10)}.cfg`;
    a.click();
  };

  return (
    <Page>
      <PageHeader title={d.name} icon={ROLE_ICON[d.role] ?? <Router size={20} />}
        subtitle={<span className="flex flex-wrap items-center gap-2"><Link to="/devices" className="flex items-center gap-1 text-blue-300 hover:underline"><ArrowLeft size={12} /> {t("Devices")}</Link>
          <Badge tone={st.tone}>{t(st.label)}</Badge> <span>{d.platform_title} · {d.host}</span></span>}
        actions={<>
          {canOperate && <button className="btn-ghost" disabled={poll.isPending} onClick={() => poll.mutate(undefined)}><RefreshCw size={14} className={poll.isPending ? "animate-spin" : ""} /> {t("Poll")}</button>}
          {isAdmin && d.capabilities.includes("backup") && <button className="btn-ghost" onClick={downloadBackup}><Download size={14} /> {t("Configuration")}</button>}
          {isAdmin && <button className="btn-ghost" onClick={() => setEditing(true)}><Pencil size={14} /> {t("Edit")}</button>}
          {isAdmin && d.ssh_host_key && <button className="btn-ghost" title={t("Reset the saved SSH server key")} onClick={async () => {
            if (await confirm({ title: t("Reset the SSH key?"), message: t("Do this only if the device was replaced or reinstalled: the new key will be accepted without verification."), confirmLabel: t("Reset") })) resetKey.mutate(undefined);
          }}><KeyRound size={14} /></button>}
          {isAdmin && <button className="btn-danger" onClick={async () => {
            if (await confirm({ title: t("Delete {name}?", { name: d.name }), message: t("The device will be removed from the system together with its credentials in OpenBao. Nothing changes on the device itself."), danger: true, confirmLabel: t("Delete") })) remove.mutate(undefined);
          }}><Trash2 size={14} /></button>}
        </>} />

      {d.last_error && <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="card border-amber-500/40 p-3 text-sm text-amber-200">{t("Last poll: {error}", { error: tServer(d.last_error) })}</motion.div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title={t("Device")}>
          <Fact label={t("Vendor")} value={f.vendor || d.vendor} /><Fact label={t("Model")} value={f.model} />
          <Fact label={t("OS")} value={[f.os, f.version].filter(Boolean).join(" ")} /><Fact label={t("Serial number")} value={f.serial} />
          <Fact label="MAC" value={f.mac && <span className="mono">{f.mac}</span>} /><Fact label={t("Role")} value={ROLE_LABEL[d.role] ? t(ROLE_LABEL[d.role]) : d.role} />
        </Card>
        <Card title={t("State")}>
          <Fact label={t("Uptime")} value={uptime(f.uptime_seconds)} />
          {f.cpu_percent != null && <div className="py-1.5"><div className="mb-1 text-xs text-slate-500">CPU</div><UsageBar percent={f.cpu_percent} /></div>}
          {f.memory_percent != null && <div className="py-1.5"><div className="mb-1 text-xs text-slate-500">{t("Memory")}</div><UsageBar percent={f.memory_percent} /></div>}
          <Fact label={t("Last poll")} value={Fmt.ago(d.last_poll_at)} /><Fact label={t("Interval")} value={t("{n} s", { n: d.poll_seconds })} />
        </Card>
        <Card title={t("Connection")}>
          <Fact label={t("Address")} value={<span className="mono">{d.host}{d.port ? `:${d.port}` : ""}</span>} /><Fact label={t("Transport")} value={d.transport} />
          <Fact label={t("Location")} value={d.location} />{d.site && <Fact label={t("Site")} value={d.site} />}
          <Fact label={t("Flows exported from")} value={d.flow_exporter_ip || d.host} />
          {!!d.tags.length && <div className="mt-2 flex flex-wrap gap-1">{d.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>}
          {Object.entries(f.extra ?? {}).filter(([, v]) => v !== null && v !== "" && typeof v !== "object").map(([k, v]) => <Fact key={k} label={k} value={String(v)} />)}
        </Card>
      </div>

      <Segmented layoutId="dev-tab" value={tab} onChange={setTab} options={[
        { value: "interfaces", label: `${t("Interfaces")} ${d.interfaces?.length ?? 0}` }, { value: "neighbors", label: `${t("Neighbours")} ${d.neighbors?.length ?? 0}` },
        { value: "endpoints", label: `${t("Clients")} ${d.endpoints?.length ?? 0}` },
        ...(d.children?.length ? [{ value: "children" as Tab, label: `${t("Devices")} ${d.children.length}` }] : []),
        { value: "manage", label: t("Management") },
      ]} />
      {tab === "interfaces" && <Card bodyClass="p-0"><Interfaces d={d} can={canOperate && d.capabilities.includes("interface")} /></Card>}
      {tab === "neighbors" && (
        <Card bodyClass="p-0">
          <table className="table-nc">
            <thead><tr><th>{t("Local port")}</th><th>{t("Neighbour")}</th><th>{t("Neighbour port")}</th><th>{t("Address")}</th><th>{t("Platform")}</th><th>{t("Protocol")}</th></tr></thead>
            <tbody>{(d.neighbors ?? []).map((n, i) => (
              <tr key={i}><td className="mono">{n.local_interface}</td><td className="text-slate-100">{n.remote_name || "—"}</td><td className="mono">{n.remote_interface}</td>
                <td className="mono text-xs">{n.remote_ip}{n.remote_mac && <div className="text-slate-500">{n.remote_mac}</div>}</td>
                <td className="max-w-[260px] truncate text-xs text-slate-400" title={n.remote_platform}>{n.remote_platform}</td><td><Badge>{n.protocol.toUpperCase()}</Badge></td></tr>
            ))}</tbody>
          </table>
          {!d.neighbors?.length && <EmptyState title={t("No neighbours found")} hint={t("Enable LLDP / CDP / MNDP on the devices")} />}
        </Card>
      )}
      {tab === "endpoints" && <Card bodyClass="p-0"><EndpointTable rows={d.endpoints ?? []} canBlock={canOperate && d.capabilities.includes("endpoint_block")} /></Card>}
      {tab === "children" && (
        <Card bodyClass="p-0">
          <table className="table-nc">
            <thead><tr><th>{t("Device")}</th><th>{t("Type")}</th><th>{t("Address")}</th><th>{t("Firmware")}</th><th>{t("Client count")}</th><th>Uplink</th><th /></tr></thead>
            <tbody>{(d.children ?? []).map((c) => (
              <tr key={c.mac}><td><div className="flex items-center gap-2"><StatusDot on={c.online} pulse={false} /><span className="text-slate-100">{c.name}</span></div><div className="text-[11px] text-slate-500">{c.model}</div></td>
                <td>{ROLE_LABEL[c.kind] ? t(ROLE_LABEL[c.kind]) : c.kind}</td><td className="mono text-xs">{c.ip}<div className="text-slate-500">{c.mac}</div></td><td className="text-xs">{c.version}</td>
                <td>{c.clients ?? "—"}</td><td className="mono text-xs">{c.uplink_mac}{c.uplink_port && ` · ${c.uplink_port}`}</td>
                <td>{isAdmin && d.capabilities.includes("reboot") && <button className="btn-ghost btn-sm" title={t("Reboot")} onClick={async () => {
                  if (await confirm({ title: t("Reboot {name}?", { name: c.name }), message: t("The clients of the device will lose connectivity for a while."), danger: true, confirmLabel: t("Reboot") })) childReboot.mutate(c.mac);
                }}><Power size={12} /></button>}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      )}
      {tab === "manage" && (canOperate ? <Manage d={d} /> : <Card><EmptyState title={t("Not enough permissions")} hint={t("Management is available to operators and administrators")} /></Card>)}
      <DeviceForm open={editing} device={d} onClose={() => setEditing(false)} />
    </Page>
  );
}
