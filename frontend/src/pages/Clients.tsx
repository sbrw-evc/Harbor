import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { useAutoAnimate } from "@formkit/auto-animate/react";
import { Ban, CheckCircle2, Download, Lock, Plus, QrCode, RefreshCw, Search, ShieldOff, Trash2, Unlock, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { Fmt, REASON_LABEL } from "@/lib/format";
import { k, t, tn } from "@/i18n";
import Flag from "@/components/ui/Flag";
import { useAction } from "@/lib/hooks";
import { SERVER_LABEL, type ServerKind, type VpnClient } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { Badge, Modal, Page, PageHeader, StatusDot, UsageBar } from "@/components/ui/Primitives";
import { SetupGuide } from "@/components/onboarding/Steps";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/Feedback";
import { confirm } from "@/components/ui/Confirm";
import { Checkbox } from "@/components/ui/Controls";
import ClientForm from "@/components/clients/ClientForm";
import ConfigModal from "@/components/clients/ConfigModal";
import { useClientActions } from "@/components/clients/useClientActions";

type Filter = "all" | "online" | "active" | "blocked" | "missing" | "unmanaged";
/** Labels are English keys, drawn with t(). */
const FILTERS: { value: Filter; label: string; test: (c: VpnClient) => boolean }[] = [
  { value: "all", label: k("All"), test: () => true },
  { value: "online", label: k("Online"), test: (c) => c.online },
  { value: "active", label: k("Active only"), test: (c) => c.status === "active" },
  { value: "blocked", label: k("Blocked clients"), test: (c) => c.status === "blocked" },
  { value: "missing", label: k("Missing on the server"), test: (c) => c.status === "missing" },
  { value: "unmanaged", label: k("No key in the panel"), test: (c) => !c.managed },
];
type SortKey = "name" | "month" | "total" | "handshake" | "rate";

function exportCsv(clients: VpnClient[]) {
  const head = [t("Server"), t("Client name"), t("Owner"), t("Contact"), t("Tags"), t("Address"), t("Status"), t("Online"), t("Month, bytes"),
    t("Total, bytes"), t("Quota, bytes"), t("Access until"), t("Country"), t("City"), t("Last handshake"), t("Created")];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = clients.map((c) => [SERVER_LABEL[c.server], c.name, c.owner, c.contact, c.tags.join(" "), c.address, c.status, c.online ? t("yes") : t("no"),
    c.month_rx + c.month_tx, c.total_rx + c.total_tx, c.quota_bytes ?? "", c.expires_at ?? "", c.country ?? "", c.city ?? "",
    c.last_handshake_at ?? "", c.created_at ?? ""].map(esc).join(";"));
  const blob = new Blob(["﻿" + [head.map(esc).join(";"), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `amnezia-clients-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function Clients() {
  const nav = useNavigate();
  const canEdit = useAuth((s) => s.can("operator"));
  const [filter, setFilter] = useState<Filter>("all");
  const [tag, setTag] = useState<string | null>(null);
  const [server, setServer] = useState<ServerKind | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "rate", dir: -1 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [configFor, setConfigFor] = useState<string | null>(null);
  const [tbody] = useAutoAnimate<HTMLTableSectionElement>();

  const list = useQuery({ queryKey: ["clients"], queryFn: () => api.clients.list(), refetchInterval: 15_000 });
  const tags = useQuery({ queryKey: ["clients", "tags"], queryFn: () => api.clients.tags() });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.ops.settings(), enabled: creating });
  const servers = useQuery({ queryKey: ["vpn-servers"], queryFn: () => api.clients.servers(), staleTime: 30_000 });
  const actions = useClientActions();
  const create = useAction(api.clients.create.bind(api.clients), {
    success: t("Client created"), invalidate: [["clients"], ["summary"]],
    onSuccess: (c) => { setCreating(false); setConfigFor(c.id); },
  });
  const sync = useAction(() => api.clients.sync(), {
    success: (r) => t("Synced: {peers} peers, {imported} imported, {missing} missing", { peers: r.peers, imported: r.imported, missing: r.missing }),
    invalidate: [["clients"]],
  });
  const bulk = useAction(({ ids, action }: { ids: string[]; action: "block" | "unblock" | "delete" }) => api.clients.bulk(ids, action), {
    success: (r) => (r.failed.length ? t("Done: {done}, errors: {failed}", { done: r.done, failed: r.failed.length }) : t("Done: {done}", { done: r.done })),
    invalidate: [["clients"], ["summary"]],
    onSuccess: () => setSelected(new Set()),
  });

  const rows = useMemo(() => {
    const f = FILTERS.find((x) => x.value === filter)!.test;
    const s = q.trim().toLowerCase();
    const val = (c: VpnClient): number | string => ({
      name: c.name.toLowerCase(), month: c.month_rx + c.month_tx, total: c.total_rx + c.total_tx,
      handshake: c.last_handshake_at ? Date.parse(c.last_handshake_at) : 0, rate: c.rate_rx + c.rate_tx + (c.online ? 1e-9 : 0),
    })[sort.key];
    return (list.data ?? [])
      .filter((c) => f(c) && (!tag || c.tags.includes(tag)) && (!server || c.server === server))
      .filter((c) => !s || [c.name, c.owner, c.address, c.city ?? "", c.public_key].some((v) => v.toLowerCase().includes(s)))
      .sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * sort.dir);
  }, [list.data, filter, tag, server, q, sort]);

  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f.value, (list.data ?? []).filter(f.test).length])), [list.data]);
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const selectable = rows.filter((r) => !r.system);
  const allSelected = selectable.length > 0 && selectable.every((r) => selected.has(r.id));
  const th = (key: SortKey, label: string) => (
    <th className="cursor-pointer select-none hover:text-slate-300" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}>
      {label} {sort.key === key && (sort.dir === 1 ? "▲" : "▼")}
    </th>
  );

  return (
    <Page>
      <PageHeader title={t("VPN clients")} icon={<Users size={20} />}
        subtitle={`${tn(counts.all ?? 0, "{n} client", "{n} clients")} · ${t("{n} online", { n: counts.online ?? 0 })}`}
        actions={<>
          <button className="btn-ghost" onClick={() => exportCsv(rows)} disabled={!rows.length}><Download size={16} /> CSV</button>
          {canEdit && <>
          <button className="btn-ghost" onClick={() => sync.mutate(undefined)} disabled={sync.isPending}>
            <RefreshCw size={16} className={clsx(sync.isPending && "animate-spin")} /> {t("Synchronize")}
          </button>
          <button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} /> {t("New client")}</button>
          </>}
        </>} />
      <SetupGuide page="clients" actions={{ create: () => setCreating(true) }} />

      <div className="card space-y-3 p-[18px]">
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input className="input pl-9" placeholder={t("Search by name, owner, address, city, key…")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map((f) => (
            <button key={f.value} className={clsx("chip", filter === f.value && "chip-on")} onClick={() => setFilter(f.value)}>
              {f.value === "online" && <StatusDot on pulse={false} />}{t(f.label)}
              <span className="rounded bg-base-950/60 px-1 text-[10px] text-slate-400">{counts[f.value] ?? 0}</span>
            </button>
          ))}
          <span className="mx-1 h-5 w-px bg-base-700" />
          {(servers.data ?? []).map((s) => (
            <button key={s.kind} className={clsx("chip", server === s.kind && "chip-on")} onClick={() => setServer(server === s.kind ? null : s.kind)}>
              {SERVER_LABEL[s.kind]}
              <span className="rounded bg-base-950/60 px-1 text-[10px] text-slate-400">{(list.data ?? []).filter((c) => c.server === s.kind).length}</span>
            </button>
          ))}
          {!!tags.data?.length && <span className="mx-1 h-5 w-px bg-base-700" />}
          {tags.data?.map((tg) => (
            <button key={tg} className={clsx("chip", tag === tg && "chip-on")} onClick={() => setTag(tag === tg ? null : tg)}>#{tg}</button>
          ))}
        </div>
      </div>

      <AnimatePresence>
        {selected.size > 0 && canEdit && (
          <motion.div initial={{ opacity: 0, y: -8, height: 0 }} animate={{ opacity: 1, y: 0, height: "auto" }} exit={{ opacity: 0, y: -8, height: 0 }}
            className="card flex flex-wrap items-center gap-2 border-accent/40 p-3">
            <span className="text-sm text-slate-300">{t("Selected:")} <b className="text-fg">{selected.size}</b></span>
            <div className="ml-auto flex gap-2">
              <button className="btn-ghost btn-sm" onClick={() => bulk.mutate({ ids: [...selected], action: "block" })}><Lock size={14} /> {t("Block access")}</button>
              <button className="btn-ghost btn-sm" onClick={() => bulk.mutate({ ids: [...selected], action: "unblock" })}><Unlock size={14} /> {t("Unblock access")}</button>
              <button className="btn-danger btn-sm" onClick={async () => {
                if (await confirm({ title: t("Delete the clients?"), danger: true, confirmLabel: t("Delete"),
                  message: tn(selected.size, "{n} client will be deleted together with its keys.", "{n} clients will be deleted together with their keys.") }))
                  bulk.mutate({ ids: [...selected], action: "delete" });
              }}><Trash2 size={14} /> {t("Delete")}</button>
              <button className="btn-ghost btn-sm" onClick={() => setSelected(new Set())}>{t("Reset")}</button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {list.error && <ErrorState error={list.error} />}
      <div className="card overflow-hidden">
        <div className="max-h-[calc(100vh-320px)] overflow-auto">
          <table className="table-nc min-w-[1100px]">
            <thead>
              <tr>
                {canEdit && <th className="w-8"><Checkbox checked={allSelected} indeterminate={!allSelected && selected.size > 0} title={t("Select all")}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(rows.filter((r) => !r.system).map((r) => r.id)))} /></th>}
                {th("name", t("Client"))}
                <th>{t("Address")}</th>
                <th>{t("Status")}</th>
                {th("rate", t("Speed ↓/↑"))}
                {th("month", t("Month / quota"))}
                {th("total", t("Total"))}
                <th>{t("Geolocation")}</th>
                {th("handshake", t("Handshake"))}
                <th>{t("Access")}</th>
                <th className="w-px" />
              </tr>
            </thead>
            <tbody ref={tbody}>
              {list.isLoading && Array.from({ length: 6 }, (_, i) => <tr key={i}><td colSpan={11}><Skeleton className="h-6" /></td></tr>)}
              {rows.map((c) => (
                <tr key={c.id} className={clsx("cursor-pointer", selected.has(c.id) && "bg-accent/5")} onClick={() => nav(`/clients/${c.id}`)}>
                  {canEdit && <td onClick={(e) => e.stopPropagation()}><Checkbox checked={selected.has(c.id)} onChange={() => toggle(c.id)} disabled={c.system} /></td>}
                  <td>
                    <div className="flex items-center gap-2">
                      <StatusDot on={c.online} />
                      <span className="max-w-[260px] truncate font-medium text-slate-100" title={c.name}>{c.name}</span>
                      {c.server !== "amnezia" && <Badge tone="blue" className="!px-1">{SERVER_LABEL[c.server]}</Badge>}
                      {c.system && <Badge tone="violet">{t("gateway")}</Badge>}
                      {!c.managed && <Badge tone="amber" className="!px-1">{t("no key")}</Badge>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-1 pl-[18px] text-xs text-slate-500">
                      {c.owner && <span>{c.owner}</span>}
                      {c.tags.map((tg) => <span key={tg} className="text-blue-300/70">#{tg}</span>)}
                    </div>
                  </td>
                  <td className="mono text-slate-400">{c.address.replace("/32", "")}</td>
                  <td>
                    {c.status === "active" ? <Badge tone="green">{t("active")}</Badge>
                      : c.status === "blocked" ? <Badge tone="red"><Ban size={10} /> {t(REASON_LABEL[c.block_reason ?? "manual"])}</Badge>
                      : <Badge tone="amber">{t("not on the server")}</Badge>}
                  </td>
                  <td className="mono whitespace-nowrap text-slate-300">{c.online || c.rate_rx ? <>{Fmt.rate(c.rate_rx)} <span className="text-slate-600">/</span> {Fmt.rate(c.rate_tx)}</> : <span className="text-slate-600">—</span>}</td>
                  <td className="w-44">
                    {c.quota_bytes ? <UsageBar percent={c.quota_percent} label={`${Fmt.bytes(c.quota_used_bytes, 0)} / ${Fmt.bytes(c.quota_bytes, 0)}`} />
                      : <span className="mono text-slate-300">{Fmt.bytes(c.month_rx + c.month_tx)}</span>}
                  </td>
                  <td className="mono whitespace-nowrap text-slate-400">{Fmt.bytes(c.total_rx + c.total_tx)}</td>
                  <td className="whitespace-nowrap text-slate-400">{c.country_code ? <><Flag code={c.country_code} /> {c.city || c.country}</> : "—"}</td>
                  <td className="whitespace-nowrap text-xs text-slate-400">{Fmt.ago(c.last_handshake_at)}</td>
                  <td className="whitespace-nowrap text-xs">
                    {c.expires_at ? <span className={clsx(new Date(c.expires_at).getTime() - Date.now() < 3 * 86400000 ? "text-amber-300" : "text-slate-400")}>{Fmt.until(c.expires_at)}</span> : <span className="text-slate-600">∞</span>}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    {canEdit && (
                      <div className="flex justify-end gap-1">
                        <button className="rounded-md p-1.5 text-slate-400 hover:bg-base-700 hover:text-fg disabled:opacity-30" title={t("Config")} disabled={!c.managed} onClick={() => setConfigFor(c.id)}><QrCode size={15} /></button>
                        {!c.system && c.status !== "missing" && (
                          <button className="rounded-md p-1.5 text-slate-400 hover:bg-base-700 hover:text-fg" title={c.status === "blocked" ? t("Unblock access") : t("Block access")} onClick={() => actions.toggleBlock(c)}>
                            {c.status === "blocked" ? <CheckCircle2 size={15} /> : <ShieldOff size={15} />}
                          </button>
                        )}
                        {!c.system && <button className="rounded-md p-1.5 text-slate-400 hover:bg-red-500/10 hover:text-red-300" title={t("Delete")} onClick={() => actions.remove(c)}><Trash2 size={15} /></button>}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.data && !rows.length && <EmptyState title={t("No clients found")} hint={t("Change the filters or create a new client")} />}
        </div>
      </div>

      <Modal open={creating} onClose={() => setCreating(false)} title={t("New VPN client")} wide>
        <ClientForm key={settings.dataUpdatedAt} servers={servers.data} submitLabel={t("Create and issue the config")} busy={create.isPending} onSubmit={(d) => create.mutate(d)} initial={{
          quota_bytes: settings.data?.default_quota_gb ? Number(settings.data.default_quota_gb) * 1024 ** 3 : null,
          expires_at: settings.data?.default_valid_days ? new Date(Date.now() + Number(settings.data.default_valid_days) * 86400000).toISOString() : null,
        }} />
      </Modal>
      <ConfigModal clientId={configFor} server={list.data?.find((c) => c.id === configFor)?.server} onClose={() => setConfigFor(null)} />
    </Page>
  );
}
