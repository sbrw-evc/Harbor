import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, Search, User } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useFocus } from "@/lib/hooks";
import { StatusDot } from "@/components/ui/Primitives";
import { ALL_PAGES } from "./nav";
import { t, useLang } from "@/i18n";


export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const search = useFocus<HTMLInputElement>(open);
  const [idx, setIdx] = useState(0);
  const nav = useNavigate();
  const lang = useLang();
  const clients = useQuery({ queryKey: ["clients", "palette"], queryFn: () => api.clients.list(), enabled: open });

  const items = useMemo(() => {
    const s = q.toLowerCase();
    const pages = ALL_PAGES.map((p) => ({ ...p, label: t(p.label) })).filter((p) => p.label.toLowerCase().includes(s)).map((p) => ({ key: p.to, label: p.label, to: p.to, hint: t("page"), online: undefined as boolean | undefined }));
    const cl = (clients.data ?? []).filter((c) => !s || c.name.toLowerCase().includes(s) || c.address.includes(s))
      .slice(0, 8).map((c) => ({ key: c.id, label: c.name, to: `/clients/${c.id}`, hint: c.address, online: c.online }));
    return [...pages, ...cl];
  }, [q, clients.data, lang]);

  useEffect(() => { setIdx(0); }, [q, open]);
  useEffect(() => { if (!open) setQ(""); }, [open]);

  const go = (to: string) => { nav(to); onClose(); };

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <button type="button" tabIndex={-1} aria-label={t("Close")} className="absolute inset-0 cursor-default bg-black/50 backdrop-blur-sm" onClick={onClose} />
          <motion.div initial={{ y: -12, scale: 0.97 }} animate={{ y: 0, scale: 1 }} exit={{ y: -8, opacity: 0 }}
            className="card relative w-full max-w-xl overflow-hidden shadow-2xl">
            <div className="flex items-center gap-3 border-b border-base-700 px-4">
              <Search size={18} className="text-slate-500" />
              <input ref={search} value={q} onChange={(e) => setQ(e.target.value)} placeholder={`${t("Search pages and clients")}…`}
                className="h-12 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-slate-500"
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)); }
                  if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
                  if (e.key === "Enter" && items[idx]) go(items[idx].to);
                  if (e.key === "Escape") onClose();
                }} />
              <kbd className="rounded border border-base-600 px-1.5 text-[10px] text-slate-500">ESC</kbd>
            </div>
            <div className="max-h-80 overflow-y-auto p-2">
              {items.map((it, i) => (
                <button key={it.key} onMouseEnter={() => setIdx(i)} onClick={() => go(it.to)}
                  className={clsx("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", i === idx ? "bg-accent/15 text-fg" : "text-slate-300")}>
                  {it.online === undefined ? <CornerDownLeft size={14} className="text-slate-500" /> : <User size={14} className="text-slate-500" />}
                  <span className="flex-1 truncate">{it.label}</span>
                  {it.online !== undefined && <StatusDot on={it.online} pulse={false} />}
                  <span className="text-xs text-slate-500">{it.hint}</span>
                </button>
              ))}
              {!items.length && <div className="px-3 py-6 text-center text-sm text-slate-500">{t("Nothing found")}</div>}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
