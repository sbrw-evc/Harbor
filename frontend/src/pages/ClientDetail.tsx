import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Ban, CalendarPlus, CheckCircle2, Clock, Gauge, HardDrive, KeyRound, MapPin, Pencil, QrCode, RotateCcw, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/lib/api";
import { Fmt, REASON_LABEL, STATUS_LABEL } from "@/lib/format";
import { k, t } from "@/i18n";
import Flag from "@/components/ui/Flag";
import { useAction } from "@/lib/hooks";
import { SERVER_LABEL, type Range } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Card, Kpi, Modal, Page, PageHeader, Segmented, StatusDot, UsageBar } from "@/components/ui/Primitives";
import { ErrorState, PageLoader, Skeleton } from "@/components/ui/Feedback";
import { TrafficChart } from "@/components/charts/Charts";
import ClientForm from "@/components/clients/ClientForm";
import ConfigModal from "@/components/clients/ConfigModal";
import { useClientActions } from "@/components/clients/useClientActions";

/** Labels are English keys, drawn with t(). */
const RANGES: { value: Range; label: string }[] = [
  { value: "24h", label: k("24 h") }, { value: "7d", label: k("7 d") }, { value: "30d", label: k("30 d") }, { value: "90d", label: k("90 d") },
];

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-base-700/50 py-2 text-sm last:border-0">
      <span className="text-slate-500">{label}</span>
      <span className="text-right text-slate-200">{children}</span>
    </div>
  );
}

export default function ClientDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const canEdit = useAuth((s) => s.can("operator"));
  const [range, setRange] = useState<Range>("7d");
  const [editing, setEditing] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const client = useQuery({ queryKey: ["client", id], queryFn: () => api.clients.get(id), refetchInterval: 15_000 });
  const series = useQuery({ queryKey: ["client-traffic", id, range], queryFn: () => api.analytics.traffic(range, id) });
  const actions = useClientActions(() => nav("/clients"));
  const update = useAction((d: Parameters<typeof api.clients.update>[1]) => api.clients.update(id, d), {
    success: t("Saved"), invalidate: [["client", id], ["clients"]], onSuccess: () => setEditing(false),
  });

  if (client.isLoading) return <PageLoader />;
  if (client.error || !client.data) return <ErrorState error={client.error} />;
  const c = client.data;

  return (
    <Page>
      <button className="flex items-center gap-1 text-sm text-slate-400 hover:text-fg" onClick={() => nav("/clients")}><ArrowLeft size={14} /> {t("Clients")}</button>
      <PageHeader
        icon={<StatusDot on={c.online} />}
        title={c.name}
        subtitle={<span className="flex flex-wrap items-center gap-2">
          <Badge tone={c.status === "active" ? "green" : c.status === "blocked" ? "red" : "amber"}>
            {t(STATUS_LABEL[c.status])}{c.block_reason && ` · ${t(REASON_LABEL[c.block_reason])}`}
          </Badge>
          <Badge tone="blue">{c.server === "amnezia" ? "Amnezia · AWG" : `${SERVER_LABEL[c.server]} · WireGuard`}</Badge>
          {c.system && <Badge tone="violet">{t("system gateway")}</Badge>}
          {!c.managed && <Badge tone="amber">{t("key not in the panel")}</Badge>}
          <span className="mono">{c.address}</span>
          {c.tags.map((tg) => <span key={tg} className="text-blue-300/80">#{tg}</span>)}
        </span>}
        actions={canEdit && <>
          <button className="btn-primary" disabled={!c.managed} onClick={() => setShowConfig(true)}><QrCode size={16} /> {t("Config")}</button>
          <button className="btn-ghost" onClick={() => setEditing(true)}><Pencil size={16} /> {t("Edit")}</button>
          {c.expires_at && <button className="btn-ghost" disabled={update.isPending} onClick={() => update.mutate({
            expires_at: new Date(Math.max(Date.parse(c.expires_at!), Date.now()) + 30 * 86400000).toISOString() })}>
            <CalendarPlus size={16} /> {t("+30 days")}</button>}
          {!c.system && c.status !== "missing" && (
            <button className="btn-ghost" onClick={() => actions.toggleBlock(c)}>
              {c.status === "blocked" ? <><CheckCircle2 size={16} /> {t("Unblock access")}</> : <><Ban size={16} /> {t("Block access")}</>}
            </button>
          )}
          {c.status !== "missing" && <button className="btn-ghost" onClick={() => actions.reissue(c)}><RotateCcw size={16} /> {t("Reissue")}</button>}
          {!c.system && <button className="btn-danger" onClick={() => actions.remove(c)}><Trash2 size={16} /></button>}
        </>}
      />

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Kpi label={t("Current speed")} value={c.rate_rx + c.rate_tx} format={Fmt.rate} icon={<Gauge size={16} />} tone="amber" hint={`↓ ${Fmt.rate(c.rate_rx)} · ↑ ${Fmt.rate(c.rate_tx)}`} />
        <Kpi label={t("This month")} value={c.month_rx + c.month_tx} format={(n) => Fmt.bytes(n)} icon={<HardDrive size={16} />} tone="violet" delay={0.05}
          hint={c.quota_bytes ? <UsageBar className="mt-1" percent={c.quota_percent} label={t("of {size}", { size: Fmt.bytes(c.quota_bytes, 0) })} /> : t("no quota")} />
        <Kpi label={t("Total")} value={c.total_rx + c.total_tx} format={(n) => Fmt.bytes(n)} icon={<HardDrive size={16} />} delay={0.1}
          hint={`↓ ${Fmt.bytes(c.total_rx)} · ↑ ${Fmt.bytes(c.total_tx)}`} />
        <Kpi label={t("Access")} value={c.expires_at ? Math.max(0, (Date.parse(c.expires_at) - Date.now()) / 86400000) : 0}
          format={(n) => (c.expires_at ? t("{n} d", { n: Math.ceil(n) }) : "∞")} icon={<Clock size={16} />} tone="green" delay={0.15}
          hint={c.expires_at ? t("until {date}", { date: Fmt.dateTime(c.expires_at) }) : t("no limit")} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={t("Traffic")} actions={<Segmented layoutId="client-range" value={range} onChange={setRange}
          options={RANGES.map((r) => ({ value: r.value, label: t(r.label) }))} />}>
          {series.data ? <TrafficChart points={series.data.points} height={300} /> : <Skeleton className="h-[300px]" />}
        </Card>
        <Card title={t("Information")}>
          <Meta label={t("Owner")}>{c.owner || "—"}</Meta>
          <Meta label={t("Contact")}>{c.contact || "—"}</Meta>
          <Meta label={t("Last handshake")}>{Fmt.ago(c.last_handshake_at)}</Meta>
          <Meta label="Endpoint"><span className="mono">{c.endpoint || "—"}</span></Meta>
          <Meta label={t("Geolocation")}>{c.country_code ? <><Flag code={c.country_code} /> {[c.city, c.country].filter(Boolean).join(", ")}</> : "—"}</Meta>
          <Meta label={t("Created")}>{Fmt.dateTime(c.created_at)} · {c.created_by || "—"}</Meta>
          <Meta label={t("Config last issued")}>{Fmt.ago(c.config_downloaded_at)}</Meta>
          <Meta label={t("Public key")}><span className="mono break-all text-[11px] text-slate-400"><KeyRound size={10} className="mr-1 inline" />{c.public_key}</span></Meta>
          {c.note && <div className="mt-3 whitespace-pre-wrap rounded-lg bg-base-950/60 p-3 text-sm text-slate-300">{c.note}</div>}
        </Card>
      </div>

      <Card title={<span className="flex items-center gap-2"><MapPin size={14} /> {t("Connection history")}</span>} bodyClass="p-0">
        <table className="table-nc">
          <thead><tr><th>{t("Time")}</th><th>IP</th><th>{t("Geolocation")}</th></tr></thead>
          <tbody>
            {(c.connections ?? []).map((e) => (
              <tr key={e.ts + e.ip}>
                <td className="whitespace-nowrap text-slate-400">{Fmt.dateTime(e.ts)}</td>
                <td className="mono text-slate-300">{e.ip}{e.port ? `:${e.port}` : ""}</td>
                <td><Flag code={e.country_code} /> {[e.city, e.country].filter(Boolean).join(", ") || "—"}</td>
              </tr>
            ))}
            {!c.connections?.length && <tr><td colSpan={3} className="py-8 text-center text-slate-500">{t("No connections recorded yet")}</td></tr>}
          </tbody>
        </table>
      </Card>

      <Modal open={editing} onClose={() => setEditing(false)} title={t("Editing · {name}", { name: c.name })} wide>
        <ClientForm initial={c} submitLabel={t("Save")} busy={update.isPending} onSubmit={(d) => update.mutate(d)} />
      </Modal>
      <ConfigModal clientId={showConfig ? c.id : null} server={c.server} onClose={() => setShowConfig(false)} />
    </Page>
  );
}
