import { AnimatePresence, motion } from "framer-motion";
import { Activity, Menu, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { notify } from "@/lib/notify";
import { live, useLive } from "@/lib/live";
import { t } from "@/i18n";
import { StatusDot } from "@/components/ui/Primitives";
import { ConfirmHost } from "@/components/ui/Confirm";
import { useLiveQuerySync } from "@/lib/liveSync";
import NotificationCenter, { NotificationBell } from "./NotificationCenter";
import Sidebar from "./Sidebar";
import CommandPalette from "./CommandPalette";
import UserMenu from "./UserMenu";

const KEY = "network-control.sidebar.collapsed";

/** The red rounded tile and the name, as the brand of every Umbrella app. */
export function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 rounded-control pr-2">
      <motion.span whileHover={{ rotate: -8, scale: 1.05 }} className="grid h-8 w-8 place-items-center rounded-[9px] bg-red-600 text-white shadow-sm">
        <Activity size={18} strokeWidth={2.4} />
      </motion.span>
      {!compact && <span className="text-[15px] font-semibold tracking-tight text-slate-100">Harbor</span>}
    </Link>
  );
}

/**
 * The Umbrella shell: the top bar sits on the window chrome (side toggle, brand, search, live state, notifications,
 * user menu); the page is a rounded block next to the sidebar and scrolls on its own.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const [notices, setNotices] = useState(false);
  const [connected, setConnected] = useState(true);
  useEffect(() => live.onStatus(setConnected), []);

  useEffect(() => {
    try { localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch { /* not remembered */ }
  }, [collapsed]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "") || (e.target as HTMLElement)?.isContentEditable;
      if (((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") || (e.key === "/" && !typing)) {
        e.preventDefault();
        setPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  useLiveQuerySync();
  useLive("alert", (d: { title?: string; severity?: string; resolved?: boolean }) => {
    if (!d.title) return;
    if (d.resolved) notify.success(t("Alert resolved"), d.title);
    else if (d.severity === "critical") notify.error(d.title, t("Critical alert"));
    else if (d.severity === "warning") notify.warning(d.title);
    else notify.info(d.title);
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-chrome">
      <header className="flex h-14 shrink-0 items-center gap-2 px-3">
        <button type="button" className="hidden rounded-control p-2 text-slate-400 hover:bg-base-900/70 hover:text-slate-100 lg:inline-flex"
          onClick={() => setCollapsed((v) => !v)} aria-label={collapsed ? t("Expand the menu") : t("Collapse the menu")} title={collapsed ? t("Expand the menu") : t("Collapse the menu")}>
          {collapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}
        </button>
        <button type="button" className="rounded-control p-2 text-slate-400 hover:bg-base-900/70 lg:hidden" onClick={() => setMobileOpen(true)} aria-label={t("Menu")}>
          <Menu size={20} />
        </button>
        <Brand />
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={() => setPalette(true)}
            className="hidden h-9 w-[280px] items-center gap-2 rounded-full border border-base-700 bg-base-900 px-3.5 text-sm text-slate-500 transition hover:border-base-600 md:flex">
            <Search size={16} />
            <span className="flex-1 text-left">{t("Search pages and clients")}</span>
            <kbd className="rounded border border-base-700 px-1.5 text-[11px]">/</kbd>
          </button>
          <button type="button" className="rounded-control p-2 text-slate-400 hover:bg-base-900/70 md:hidden" onClick={() => setPalette(true)} aria-label={t("Search")}>
            <Search size={18} />
          </button>
          <span className="hidden items-center gap-1.5 px-2 text-[12px] text-slate-500 sm:flex" title={connected ? t("Live updates are on") : t("Reconnecting…")}>
            <StatusDot on={connected} warn={!connected} /> {connected ? t("Live") : t("Offline")}
          </span>
          <NotificationBell onOpen={() => setNotices(true)} />
          <UserMenu />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <motion.aside className="hidden shrink-0 lg:block" animate={{ width: collapsed ? 64 : 260 }} transition={{ type: "spring", stiffness: 300, damping: 32 }}>
          <Sidebar collapsed={collapsed} />
        </motion.aside>
        <AnimatePresence>
          {mobileOpen && (
            <motion.div className="fixed inset-0 z-40 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <div className="absolute inset-0" style={{ background: "var(--backdrop)" }} onClick={() => setMobileOpen(false)} />
              <motion.div className="relative h-full w-[272px] bg-chrome pt-3" initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }}
                transition={{ type: "spring", stiffness: 320, damping: 32 }}>
                <Sidebar collapsed={false} onNavigate={() => setMobileOpen(false)} />
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
        <main className="mb-2 mr-2 min-w-0 flex-1 overflow-y-auto rounded-xl border border-base-700 bg-base-950 max-lg:ml-2">
          <div className="mx-auto w-full max-w-[1400px] px-4 py-6 lg:px-6">{children}</div>
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <NotificationCenter open={notices} onClose={() => setNotices(false)} />
      <ConfirmHost />
    </div>
  );
}
