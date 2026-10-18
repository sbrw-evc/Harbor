import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import type { ECharts } from "echarts";
import { AnimatePresence, motion } from "framer-motion";
import { Crosshair, ExternalLink, Lock, Network, Plus, RefreshCw, Search, Unlock, Waypoints, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { k, t, useLang } from "@/i18n";
import { api } from "@/lib/api";
import { chartColors, useTheme } from "@/lib/theme";
import { Fmt } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { useLive } from "@/lib/live";
import { ROLE_LABEL, STATUS, bps } from "@/lib/network";
import type { TopoEdge, TopoNode } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Page, PageHeader, StatusDot } from "@/components/ui/Primitives";
import { Checkbox } from "@/components/ui/Controls";
import { ErrorState, Skeleton } from "@/components/ui/Feedback";
import DeviceForm, { type DevicePrefill } from "@/components/devices/DeviceForm";

const CATEGORIES = [
  { name: k("Internet and VPN"), color: "#a78bfa" }, { name: k("Routers and firewalls"), color: "#3b82f6" },
  { name: k("Switches"), color: "#22d3ee" }, { name: k("Access points and controllers"), color: "#34d399" },
  { name: k("Servers and other"), color: "#f59e0b" }, { name: k("Discovered neighbours"), color: "#64748b" },
  { name: k("Clients online"), color: "#93c5fd" }, { name: k("Clients offline"), color: "#475569" },
];
const EDGE: Record<TopoEdge["kind"], { color: string; type: "solid" | "dashed" | "dotted"; label: string }> = {
  link: { color: "#3b82f6", type: "solid", label: k("link between devices") }, managed: { color: "#64748b", type: "dotted", label: k("managed by a controller") },
  wifi: { color: "#34d399", type: "dashed", label: "Wi-Fi" }, wired: { color: "#475569", type: "solid", label: k("wired connection") },
  wan: { color: "#a78bfa", type: "solid", label: k("internet uplink") }, tunnel: { color: "#f472b6", type: "dashed", label: k("VPN tunnel") },
};
const SYMBOL: Record<string, string> = { internet: "roundRect", vpn: "diamond", router: "roundRect", firewall: "roundRect", gateway: "roundRect",
  switch: "rect", ap: "triangle", controller: "pin", server: "rect", other: "circle" };

function category(n: TopoNode): number {
  if (n.type === "internet" || n.type === "vpn") return 0;
  if (n.type === "neighbor") return 5;
  if (n.type === "endpoint") return n.status === "online" ? 6 : 7;
  if (["router", "firewall", "gateway"].includes(n.role ?? "")) return 1;
  if (n.role === "switch") return 2;
  if (["ap", "controller"].includes(n.role ?? "")) return 3;
  return 4;
}

function nodeSize(n: TopoNode): number {
  if (n.type === "internet") return 46;
  if (n.type === "vpn") return 32;
  if (n.type === "device") return Math.min(52, 30 + Math.sqrt(n.clients ?? 0) * 3);
  if (n.type === "child") return 24 + Math.min(14, Math.sqrt(n.clients ?? 0) * 2);
  if (n.type === "neighbor") return 20;
  return Math.min(22, 8 + Math.sqrt((n.bps ?? 0) / 1e5));
}

function Details({ node, edges, nodes, onClose, onAdd }: { node: TopoNode; edges: TopoEdge[]; nodes: Map<string, TopoNode>; onClose: () => void; onAdd: (p: DevicePrefill) => void }) {
  const isAdmin = useAuth((s) => s.can("admin"));
  const links = edges.filter((e) => e.source === node.id || e.target === node.id);
  const st = node.type === "device" ? STATUS[(node.status ?? "unknown") as keyof typeof STATUS] : null;
  const flowsLink = node.ip && node.type === "endpoint"
    ? `/flows?tab=visualize&q=${btoa(unescape(encodeURIComponent(JSON.stringify({ preset: "1h", refresh: 30, dimensions: ["Application", "RemoteASName"], filter: `LocalAddr = ${node.ip}`, limit: 10, limitType: "avg", unit: "bps", graph: "stacked", bidirectional: true, previousPeriod: false }))))}`
    : null;
  return (
    <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} className="card absolute right-3 top-3 z-10 w-80 max-w-[calc(100%-24px)] p-4 shadow-2xl">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-fg">{node.label}</div>
          <div className="text-xs text-slate-500">{node.type === "endpoint" ? (node.vendor || t("client")) : ROLE_LABEL[node.role ?? ""] ? t(ROLE_LABEL[node.role ?? ""]) : node.type}{node.model ? ` · ${node.model}` : ""}</div>
        </div>
        <button className="p-1 text-slate-500 hover:text-fg" onClick={onClose}><X size={16} /></button>
      </div>
      <div className="mt-3 space-y-1 text-sm">
        {st && <div><Badge tone={st.tone}>{t(st.label)}</Badge></div>}
        {node.type === "endpoint" && <div className="flex items-center gap-2"><StatusDot on={node.status === "online"} pulse={false} /> {node.status === "online" ? t("online now") : t("seen {ago}", { ago: Fmt.ago(node.last_seen) })}{node.blocked && <Badge tone="red">{t("is blocked")}</Badge>}</div>}
        {node.ip && <div className="flex justify-between"><span className="text-slate-500">IP</span><span className="mono">{node.ip}</span></div>}
        {node.mac && <div className="flex justify-between"><span className="text-slate-500">MAC</span><span className="mono">{node.mac}</span></div>}
        {node.ssid && <div className="flex justify-between"><span className="text-slate-500">Wi-Fi</span><span>{node.ssid}{node.signal != null ? ` · ${node.signal} dBm` : ""}</span></div>}
        {!!node.clients && <div className="flex justify-between"><span className="text-slate-500">{t("Client count")}</span><span>{node.clients}</span></div>}
        {!!node.bps && <div className="flex justify-between"><span className="text-slate-500">{t("Traffic (5 min)")}</span><span className="mono">{bps(node.bps)}</span></div>}
        {node.error && <div className="text-xs text-amber-300">{node.error}</div>}
      </div>
      {!!links.length && (
        <div className="mt-3 max-h-40 space-y-1 overflow-y-auto border-t border-base-700 pt-2 text-xs">
          {links.map((e, i) => {
            const other = nodes.get(e.source === node.id ? e.target : e.source);
            const myPort = e.source === node.id ? e.source_port : e.target_port;
            return (
              <div key={i} className="flex items-center gap-2">
                <span className="h-0.5 w-4 shrink-0" style={{ background: EDGE[e.kind].color }} />
                <span className="flex-1 truncate text-slate-300">{other?.label}{myPort ? <span className="text-slate-500"> · {myPort}</span> : null}</span>
                {e.bps ? <span className="mono text-slate-400">{bps(e.bps)}</span> : null}
                {e.utilization != null && <Badge tone={e.utilization > 80 ? "red" : e.utilization > 50 ? "amber" : "slate"}>{e.utilization}%</Badge>}
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {node.device_id && <Link className="btn-ghost btn-sm" to={`/devices/${node.device_id}`}><ExternalLink size={12} /> {t("Device")}</Link>}
        {flowsLink && <Link className="btn-ghost btn-sm" to={flowsLink}><Waypoints size={12} /> {t("Traffic")}</Link>}
        {node.type === "neighbor" && isAdmin && <button className="btn-primary btn-sm" onClick={() => onAdd({ host: node.ip, name: node.label, platform: node.platform_guess || "snmp" })}><Plus size={12} /> {t("Add to the system")}</button>}
      </div>
    </motion.div>
  );
}

export default function NetworkMap() {
  const nav = useNavigate();
  const canMove = useAuth((s) => s.can("operator"));
  const [showEndpoints, setShowEndpoints] = useState(true);
  const [offline, setOffline] = useState(false);
  const [labels, setLabels] = useState(true);
  const [frozen, setFrozen] = useState(false);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState<DevicePrefill | null>(null);
  const chart = useRef<ECharts | null>(null);
  const positions = useRef(new Map<string, [number, number]>());
  const topo = useQuery({ queryKey: ["topology", offline, showEndpoints], queryFn: () => api.devices.topology(offline, showEndpoints), refetchInterval: 20_000 });
  useLive("devices", () => topo.refetch(), []);
  const theme = useTheme();
  const lang = useLang();
  const savePos = useAction((p: Record<string, [number, number] | null>) => api.devices.savePositions(p));

  const data = topo.data;
  const byId = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const match = q.trim().toLowerCase();

  const remember = useCallback(() => {
    const series = (chart.current as unknown as { getModel?: () => { getSeriesByIndex: (i: number) => { getData: () => { count: () => number; getId: (i: number) => string; getItemLayout: (i: number) => number[] | null } } | undefined } })?.getModel?.()?.getSeriesByIndex(0);
    const d = series?.getData();
    if (!d) return;
    for (let i = 0; i < d.count(); i++) {
      const xy = d.getItemLayout(i);
      if (xy && Number.isFinite(xy[0])) positions.current.set(d.getId(i), [xy[0], xy[1]]);
    }
  }, []);

  const option = useMemo(() => {
    if (!data) return null;
    remember();
    const c = chartColors();
    const small = data.nodes.length < 60;
    const nodes = data.nodes.map((n) => {
      const cat = category(n);
      const pinned = n.type === "device" && n.x != null && n.y != null;
      const known = positions.current.get(n.id);
      const hit = match && [n.label, n.ip, n.mac, n.vendor, n.hostname].join(" ").toLowerCase().includes(match);
      const down = n.type === "device" && (n.status === "down" || n.status === "error");
      return {
        id: n.id, name: n.label, category: cat, symbol: SYMBOL[n.type === "endpoint" || n.type === "neighbor" ? "other" : n.type === "internet" || n.type === "vpn" ? n.type : n.role ?? "other"],
        symbolSize: nodeSize(n), value: n.bps ?? 0, draggable: true,
        ...(pinned ? { x: n.x, y: n.y, fixed: true } : known ? { x: known[0], y: known[1] } : {}),
        ...(frozen && known ? { fixed: true } : {}),
        itemStyle: { borderColor: down ? "#ef4444" : hit ? "#facc15" : n.type === "neighbor" ? c.text : c.surface, borderWidth: down || hit ? 3 : 1.5,
          borderType: n.type === "neighbor" ? "dashed" : "solid", opacity: match && !hit ? 0.25 : 1, shadowBlur: hit ? 18 : 0, shadowColor: "#facc15" },
        label: { show: labels && (n.type !== "endpoint" || small || !!hit || (n.bps ?? 0) > 1e6), position: "bottom", color: c.fg, fontSize: n.type === "endpoint" ? 10 : 11,
          formatter: n.type === "device" || n.type === "child" ? `${n.label}${n.clients ? ` (${n.clients})` : ""}` : n.label },
      };
    });
    const links = data.edges.map((e) => {
      const s = EDGE[e.kind];
      const util = e.utilization ?? null;
      const w = e.kind === "link" ? (util != null ? 1.5 + util / 15 : e.bps ? 1.5 + Math.log10(Math.max(e.bps, 1)) / 3 : 2)
        : e.kind === "wan" || e.kind === "tunnel" ? 2.5 : e.bps ? Math.min(4, 0.8 + Math.log10(Math.max(e.bps, 1)) / 4) : 0.8;
      return {
        source: e.source, target: e.target,
        lineStyle: { color: util != null && util > 80 ? "#ef4444" : util != null && util > 50 ? "#f59e0b" : s.color, type: s.type, width: w,
          opacity: match ? 0.25 : e.kind === "wired" || e.kind === "wifi" ? 0.55 : 0.85, curveness: e.kind === "tunnel" ? 0.25 : 0 },
        label: { show: labels && e.kind === "link" && !!(e.source_port || e.bps), formatter: [e.source_port, e.bps ? bps(e.bps) : ""].filter(Boolean).join(" · "),
          color: c.text, fontSize: 9 },
        tip: e,
      };
    });
    return {
      backgroundColor: "transparent",
      tooltip: {
        backgroundColor: c.surface, borderColor: c.border, textStyle: { color: c.fg, fontSize: 12 },
        formatter: (p: { dataType: string; data: { id?: string; tip?: TopoEdge } }) => {
          if (p.dataType === "edge" && p.data.tip) {
            const e = p.data.tip;
            return `${byId.get(e.source)?.label ?? ""} ${e.source_port ? `(${e.source_port})` : ""} — ${byId.get(e.target)?.label ?? ""} ${e.target_port ? `(${e.target_port})` : ""}<br/>${t(EDGE[e.kind].label)}${e.protocol ? ` · ${e.protocol.toUpperCase()}` : ""}${e.bps ? `<br/>${bps(e.bps)}` : ""}${e.utilization != null ? ` · ${e.utilization}%` : ""}`;
          }
          const n = byId.get(p.data.id ?? "");
          if (!n) return "";
          return `<b>${n.label}</b>${n.ip ? `<br/>${n.ip}` : ""}${n.vendor ? `<br/>${n.vendor}` : ""}${n.bps ? `<br/>${bps(n.bps)}` : ""}`;
        },
      },
      legend: { data: CATEGORIES.map((x) => t(x.name)), bottom: 4, textStyle: { color: c.text, fontSize: 11 }, itemWidth: 12, itemHeight: 10 },
      animationDurationUpdate: 600,
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true, scaleLimit: { min: 0.2, max: 6 },
        categories: CATEGORIES.map((x) => ({ name: t(x.name), itemStyle: { color: x.color } })),
        zoom: small ? 1.6 : 1,
        force: { repulsion: small ? [160, 520] : [60, 420], edgeLength: small ? [50, 150] : [30, 140], gravity: small ? 0.1 : 0.06,
          friction: 0.15, layoutAnimation: !frozen },
        emphasis: { focus: "adjacency", lineStyle: { width: 4 } },
        data: nodes, links, edgeSymbol: ["none", "none"],
      }],
    };
  }, [data, labels, frozen, match, byId, remember, theme, lang]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { remember(); }, [remember]);

  const onEvents = useMemo(() => ({
    click: (p: { dataType: string; data: { id?: string } }) => { if (p.dataType === "node" && p.data.id) setSelected(p.data.id); },
    dblclick: (p: { dataType: string; data: { id?: string } }) => {
      const n = p.data.id ? byId.get(p.data.id) : undefined;
      if (n?.device_id) nav(`/devices/${n.device_id}`);
    },
    mouseup: (p: { dataType: string; data: { id?: string }; dataIndex: number }) => {
      if (p.dataType !== "node" || !p.data.id?.startsWith("dev:") || !canMove) return;
      remember();
      const xy = positions.current.get(p.data.id);
      if (xy) savePos.mutate({ [p.data.id.slice(4)]: xy });
    },
  }), [byId, nav, canMove, remember, savePos]);

  const unpinAll = () => {
    const ids = (data?.nodes ?? []).filter((n) => n.type === "device" && n.x != null).map((n) => n.device_id!);
    savePos.mutate(Object.fromEntries(ids.map((i) => [i, null])), { onSuccess: () => { positions.current.clear(); void topo.refetch(); } });
  };
  const s = data?.stats;
  const node = selected ? byId.get(selected) : undefined;

  return (
    <Page>
      <PageHeader title={t("Network map")} icon={<Network size={20} />}
        subtitle={t("Built from device data: LLDP / CDP / MNDP, controllers, switch ports, Wi-Fi and client traffic")}
        actions={<button className="btn-ghost" onClick={() => topo.refetch()}><RefreshCw size={14} className={topo.isFetching ? "animate-spin" : ""} /> {t("Refresh")}</button>} />
      <div className="card flex flex-wrap items-center gap-4 p-3 text-sm">
        <div className="relative min-w-[200px] flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input className="input pl-9" placeholder={t("Find on the map: name, IP, MAC, vendor…")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Checkbox checked={showEndpoints} onChange={setShowEndpoints} label={t("Clients")} />
        <Checkbox checked={offline} onChange={setOffline} label={t("Offline clients")} />
        <Checkbox checked={labels} onChange={setLabels} label={t("Labels")} />
        <button className="btn-ghost btn-sm" onClick={() => { remember(); setFrozen((f) => !f); }} title={t("Freeze the layout")}>
          {frozen ? <Lock size={13} /> : <Unlock size={13} />} {frozen ? t("Layout frozen") : t("Free layout")}</button>
        {canMove && <button className="btn-ghost btn-sm" onClick={unpinAll} title={t("Reset the pinned device positions")}><Crosshair size={13} /> {t("Reset positions")}</button>}
      </div>
      {s && (
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge tone="blue">{t("devices {n}", { n: s.devices })}</Badge>{!!s.children && <Badge tone="green">{t("via controllers {n}", { n: s.children })}</Badge>}
          {!!s.neighbors && <Badge>{t("neighbours discovered {n}", { n: s.neighbors })}</Badge>}<Badge tone="violet">{t("clients {n} · online {online}", { n: s.endpoints, online: s.online })}</Badge>
          {!!s.down && <Badge tone="red">{t("unreachable {n}", { n: s.down })}</Badge>}
          <Badge tone={s.traffic ? "green" : "slate"}>{s.traffic ? t("NetFlow traffic included") : t("no traffic data")}</Badge>
          <span className="ml-auto text-slate-500">{t("updated {ago} · double-click to open a device, drag a device to pin it", { ago: Fmt.ago(data?.generated_at) })}</span>
        </div>
      )}
      {topo.error && <ErrorState error={topo.error} />}
      <Card bodyClass="relative p-0">
        {!option ? <Skeleton className="h-[72vh]" /> : (
          <ReactECharts option={option} notMerge={false} lazyUpdate style={{ height: "72vh", width: "100%" }} onEvents={onEvents}
            onChartReady={(c: ECharts) => { chart.current = c; }} />
        )}
        <AnimatePresence>{node && <Details node={node} edges={data?.edges ?? []} nodes={byId} onClose={() => setSelected(null)} onAdd={(p) => setAdding(p)} />}</AnimatePresence>
        {data && data.nodes.length <= 2 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center text-sm text-slate-400">
            <Network size={28} className="text-slate-600" />
            {t("Add network devices — the map is built from their neighbours, ports and clients.")}
            <Link className="btn-primary btn-sm mt-2" to="/devices"><Plus size={12} /> {t("Go to devices")}</Link>
          </div>
        )}
      </Card>
      <div className="flex flex-wrap gap-4 text-xs text-slate-500">
        {Object.values(EDGE).map((e) => <span key={e.label} className="flex items-center gap-1.5"><span className="inline-block w-6 border-t-2" style={{ borderColor: e.color, borderStyle: e.type }} /> {t(e.label)}</span>)}
        <span className="flex items-center gap-1.5"><span className="inline-block w-6 border-t-2 border-red-500" /> {t("link utilization > 80%")}</span>
      </div>
      <DeviceForm open={!!adding} prefill={adding ?? undefined} onClose={() => setAdding(null)} />
    </Page>
  );
}
