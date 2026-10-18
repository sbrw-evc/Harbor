import { animate, AnimatePresence, motion, useMotionValue, useTransform } from "framer-motion";
import { X } from "lucide-react";
import clsx from "clsx";
import { useEffect, type ReactNode } from "react";
import { locale, t } from "@/i18n";

export function Page({ children }: { children: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.22, ease: "easeOut" }} className="space-y-5">
      {children}
    </motion.div>
  );
}

/** Page title row of the Umbrella shell: a 22px title, a muted subtitle under it and the actions on the right. */
export function PageHeader({ title, subtitle, actions, icon }: { title: string; subtitle?: ReactNode; actions?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <div className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-[11px] bg-accent-soft text-accent">{icon}</div>}
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-tight tracking-tight text-slate-100">{title}</h1>
          {subtitle && <div className="mt-1 text-sm text-slate-400">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className, bodyClass }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <motion.section layout="position" className={clsx("card", className)}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={clsx(bodyClass ?? "p-[18px]")}>{children}</div>
    </motion.section>
  );
}

/** Umbrella pills: a soft fill with coloured text, no border. */
const TONES = {
  green: "bg-emerald-500/15 text-emerald-300",
  red: "bg-red-500/15 text-red-300",
  amber: "bg-amber-500/15 text-amber-300",
  blue: "bg-blue-500/15 text-blue-300",
  violet: "bg-violet-500/15 text-violet-300",
  slate: "bg-base-800 text-slate-400",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "slate", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-semibold", TONES[tone], className)}>{children}</span>;
}

export function StatusDot({ on, warn, pulse = true }: { on: boolean; warn?: boolean; pulse?: boolean }) {
  const color = warn ? "bg-amber-400" : on ? "bg-emerald-400" : "bg-slate-600";
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {on && pulse && <span className={clsx("absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping2", color)} />}
      <span className={clsx("relative inline-flex h-2.5 w-2.5 rounded-full", color)} />
    </span>
  );
}

export function UsageBar({ percent, label, className }: { percent: number | null; label?: string; className?: string }) {
  const p = Math.min(100, Math.max(0, percent ?? 0));
  const color = p >= 90 ? "from-red-500 to-red-400" : p >= 70 ? "from-amber-500 to-amber-400" : "from-emerald-600 to-emerald-400";
  return (
    <div className={clsx("relative h-5 w-full min-w-[90px] overflow-hidden rounded bg-base-800", className)}>
      <motion.div className={clsx("h-full bg-gradient-to-r", color)} initial={{ width: 0 }} animate={{ width: `${p}%` }}
        transition={{ type: "spring", stiffness: 80, damping: 18 }} />
      <span className="absolute inset-0 flex items-center px-2 text-[11px] font-semibold text-fg">
        {percent === null ? "∞" : `${p.toFixed(0)}%`}{label && <span className="ml-1 font-normal text-slate-200/80">{label}</span>}
      </span>
    </div>
  );
}

export function AnimatedNumber({ value, format = (n) => Math.round(n).toLocaleString(locale()) }: { value: number; format?: (n: number) => string }) {
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => {
    const controls = animate(mv, value, { duration: 0.9, ease: "easeOut" });
    return controls.stop;
  }, [value, mv]);
  return <motion.span>{text}</motion.span>;
}

export function Kpi({ label, value, format, icon, hint, tone = "blue", delay = 0 }: {
  label: string; value: number; format?: (n: number) => string; icon: ReactNode; hint?: ReactNode; tone?: Tone; delay?: number;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ delay, type: "spring", stiffness: 260, damping: 24 }} whileHover={{ y: -2 }}
      className="card relative overflow-hidden p-[18px]">
      <div className="flex items-start justify-between gap-2">
        <div className="text-[13px] font-semibold text-slate-400">{label}</div>
        <div className={clsx("rounded-[9px] p-1.5", TONES[tone])}>{icon}</div>
      </div>
      <div className="mt-2 text-[26px] font-semibold leading-tight tabular-nums text-fg"><AnimatedNumber value={value} format={format} /></div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </motion.div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, layoutId }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; layoutId: string }) {
  return (
    <div className="inline-flex rounded-[10px] p-[3px]" style={{ background: "var(--segment-bg)" }}>
      {options.map((o) => (
        <button type="button" key={o.value} onClick={() => onChange(o.value)}
          className={clsx("relative rounded-[8px] px-3 py-1 text-[13px] font-semibold transition", value === o.value ? "text-slate-100" : "text-slate-500 hover:text-slate-200")}>
          {value === o.value && <motion.span layoutId={layoutId} className="absolute inset-0 rounded-[8px] shadow-sm" style={{ background: "var(--segment-on)" }}
            transition={{ type: "spring", stiffness: 400, damping: 32 }} />}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-center justify-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <button type="button" tabIndex={-1} aria-label={t("Close")} className="absolute inset-0 cursor-default backdrop-blur-[3px]" style={{ background: "var(--backdrop)" }} onClick={onClose} />
          <motion.div role="dialog" initial={{ opacity: 0, scale: 0.94, y: 16 }} animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }} transition={{ type: "spring", stiffness: 340, damping: 28 }}
            className={clsx("card relative max-h-[90vh] w-full overflow-auto shadow-2xl", wide ? "max-w-3xl" : "max-w-lg")}>
            <div className="sticky top-0 z-10 flex items-center justify-between gap-3 bg-base-900 px-5 pb-2 pt-4">
              <h3 className="text-[17px] font-semibold text-fg">{title}</h3>
              <button onClick={onClose} aria-label={t("Close")} className="rounded-control p-1.5 text-slate-400 hover:bg-base-850 hover:text-fg"><X size={18} /></button>
            </div>
            <div className="px-5 pb-5 pt-2">{children}</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="block" role="group" aria-label={label}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-slate-500">{hint}</span>}
    </div>
  );
}
