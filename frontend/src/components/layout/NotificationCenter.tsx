import { AnimatePresence, motion } from "framer-motion";
import { useAutoAnimate } from "@formkit/auto-animate/react";
import { AlertOctagon, AlertTriangle, Bell, CheckCheck, CheckCircle2, Info, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Fmt } from "@/lib/format";
import { useNotifications, type NoticeType } from "@/lib/notify";
import { k, locale, t } from "@/i18n";

const ICONS: Record<NoticeType, JSX.Element> = {
  success: <CheckCircle2 size={16} className="text-emerald-400" />,
  error: <AlertOctagon size={16} className="text-red-400" />,
  warning: <AlertTriangle size={16} className="text-amber-400" />,
  info: <Info size={16} className="text-blue-400" />,
};
const FILTERS: { value: NoticeType | "all"; label: string }[] = [
  { value: "all", label: k("All") }, { value: "error", label: k("Errors") }, { value: "warning", label: k("Warnings") },
  { value: "success", label: k("Successful") }, { value: "info", label: k("Info") },
];

export function NotificationBell({ collapsed, onOpen }: { collapsed?: boolean; onOpen: () => void }) {
  const unread = useNotifications((s) => s.items.filter((i) => !i.read).length);
  return (
    <button onClick={onOpen} title={t("Notifications")}
      className="relative rounded-md p-1.5 text-slate-400 transition hover:bg-base-800 hover:text-fg">
      <Bell size={16} />
      {unread > 0 && (
        <motion.span key={unread} initial={{ scale: 0.4 }} animate={{ scale: 1 }}
          className={clsx("absolute -right-1 -top-1 min-w-[16px] rounded-full bg-accent px-1 text-center text-[10px] font-bold leading-4 text-white", collapsed && "-right-0.5")}>
          {unread > 99 ? "99+" : unread}
        </motion.span>
      )}
    </button>
  );
}

export default function NotificationCenter({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { items, markAllRead, remove, clear } = useNotifications();
  const [filter, setFilter] = useState<NoticeType | "all">("all");
  const [list] = useAutoAnimate<HTMLDivElement>();
  const shown = useMemo(() => items.filter((i) => filter === "all" || i.type === filter), [items, filter]);

  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(markAllRead, 1500);
      return () => clearTimeout(timer);
    }
  }, [open, markAllRead]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <button type="button" tabIndex={-1} aria-label={t("Close")} className="absolute inset-0 cursor-default bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
          <motion.aside initial={{ x: 420 }} animate={{ x: 0 }} exit={{ x: 420 }} transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="absolute right-0 top-0 flex h-full w-full max-w-[400px] flex-col border-l border-base-700 bg-base-900 shadow-2xl">
            <div className="flex items-center gap-2 border-b border-base-700 px-4 py-3">
              <Bell size={16} className="text-accent" />
              <h3 className="flex-1 text-base font-semibold text-fg">{t("Notifications")}</h3>
              <button className="rounded-md p-1.5 text-slate-400 hover:bg-base-800 hover:text-fg" title={t("Mark all as read")} onClick={markAllRead}><CheckCheck size={16} /></button>
              <button className="rounded-md p-1.5 text-slate-400 hover:bg-base-800 hover:text-red-300" title={t("Clear")} onClick={clear}><Trash2 size={16} /></button>
              <button className="rounded-md p-1.5 text-slate-400 hover:bg-base-800 hover:text-fg" title={t("Close")} onClick={onClose}><X size={16} /></button>
            </div>
            <div className="flex flex-wrap gap-1.5 border-b border-base-700/60 px-4 py-2">
              {FILTERS.map((f) => (
                <button key={f.value} className={clsx("chip", filter === f.value && "chip-on")} onClick={() => setFilter(f.value)}>
                  {t(f.label)}<span className="text-[10px] text-slate-500">{f.value === "all" ? items.length : items.filter((i) => i.type === f.value).length}</span>
                </button>
              ))}
            </div>
            <div ref={list} className="flex-1 space-y-2 overflow-y-auto p-3">
              {shown.map((n) => (
                <div key={n.id} className={clsx("group flex gap-3 rounded-lg border p-3 text-sm transition",
                  n.read ? "border-base-700/60 bg-base-850/60" : "border-accent/40 bg-accent/5")}>
                  <div className="mt-0.5">{ICONS[n.type]}</div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-slate-100">{n.title}</div>
                    {n.description && <div className="mt-0.5 break-words text-xs text-slate-400">{n.description}</div>}
                    <div className="mt-1 text-[11px] text-slate-500" title={new Date(n.ts).toLocaleString(locale())}>{Fmt.ago(new Date(n.ts).toISOString())}</div>
                  </div>
                  <button className="self-start rounded p-1 text-slate-600 opacity-0 transition hover:text-slate-300 group-hover:opacity-100" onClick={() => remove(n.id)}><X size={13} /></button>
                </div>
              ))}
              {!shown.length && (
                <div className="flex flex-col items-center gap-2 py-16 text-center text-sm text-slate-500">
                  <Bell size={22} /> {t("No notifications")}
                </div>
              )}
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
