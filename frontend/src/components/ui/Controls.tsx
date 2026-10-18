import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { t } from "@/i18n";

export interface SelectOption<T extends string | number> {
  value: T;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}

interface Position { left: number; width: number; top?: number; bottom?: number; maxHeight: number }

const MENU_GAP = 6;
const MENU_MAX = 280;

export function Select<T extends string | number>({ value, options, onChange, placeholder = t("Choose…"), disabled, size = "md", className, menuWidth }: {
  value: T | null | undefined;
  options: SelectOption<T>[];
  onChange: (v: T) => void;
  placeholder?: ReactNode;
  disabled?: boolean;
  size?: "sm" | "md";
  className?: string;
  menuWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<Position | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const selected = options.find((o) => o.value === value);

  const place = useCallback(() => {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const below = window.innerHeight - r.bottom - MENU_GAP - 8;
    const above = r.top - MENU_GAP - 8;
    const width = Math.max(r.width, menuWidth ?? 0);
    const left = Math.min(r.left, window.innerWidth - width - 8);
    setPos(below >= Math.min(MENU_MAX, 160) || below >= above
      ? { left, width, top: r.bottom + MENU_GAP, maxHeight: Math.min(MENU_MAX, below) }
      : { left, width, bottom: window.innerHeight - r.top + MENU_GAP, maxHeight: Math.min(MENU_MAX, above) });
  }, [menuWidth]);

  useLayoutEffect(() => { if (open) place(); }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!button.current?.contains(e.target as Node) && !menu.current?.contains(e.target as Node)) setOpen(false);
    };
    const onMove = (e: Event) => {
      if (menu.current?.contains(e.target as Node)) return;
      const r = button.current?.getBoundingClientRect();
      if (!r || r.bottom < 0 || r.top > window.innerHeight) setOpen(false);
      else place();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open, place]);

  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const openMenu = () => {
    if (disabled) return;
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  };
  const choose = (o: SelectOption<T>) => {
    if (o.disabled) return;
    onChange(o.value);
    setOpen(false);
    button.current?.focus();
  };
  const step = (from: number, dir: 1 | -1) => {
    for (let i = 1; i <= options.length; i++) {
      const n = (from + dir * i + options.length) % options.length;
      if (!options[n].disabled) return n;
    }
    return from;
  };
  const onKey = (e: KeyboardEvent) => {
    if (disabled) return;
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openMenu(); return; }
    if (!open) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => step(a, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => step(a, -1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(step(-1, 1)); }
    else if (e.key === "End") { e.preventDefault(); setActive(step(options.length, -1)); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (options[active]) choose(options[active]); }
    else if (e.key === "Escape" || e.key === "Tab") setOpen(false);
  };

  return (
    <>
      <button ref={button} type="button" disabled={disabled} onClick={() => (open ? setOpen(false) : openMenu())} onKeyDown={onKey}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={id}
        className={clsx("input flex items-center gap-2 text-left disabled:cursor-not-allowed disabled:opacity-50",
          size === "sm" ? "!py-1 text-xs" : "", open && "border-accent ring-2 ring-accent/25", className)}>
        <span className={clsx("min-w-0 flex-1 truncate", !selected && "text-slate-500")}>{selected?.label ?? placeholder}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.18 }} className="shrink-0 text-slate-500">
          <ChevronDown size={size === "sm" ? 14 : 16} />
        </motion.span>
      </button>
      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div ref={menu} id={id} role="listbox" onKeyDown={onKey}
              initial={{ opacity: 0, y: pos.top !== undefined ? -6 : 6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: pos.top !== undefined ? -4 : 4, scale: 0.98 }} transition={{ duration: 0.14 }}
              style={{ position: "fixed", left: pos.left, width: pos.width, top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight }}
              className="z-[100] overflow-y-auto rounded-card border border-base-700 bg-base-900 p-1 shadow-card">
              {options.map((o, i) => {
                const isSel = o.value === value;
                return (
                  <div key={String(o.value)} role="option" tabIndex={-1} aria-selected={isSel} aria-disabled={o.disabled} data-index={i}
                    onMouseEnter={() => !o.disabled && setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(o)}
                    className={clsx("flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm",
                      o.disabled ? "cursor-not-allowed opacity-40" : i === active ? "bg-accent/20 text-fg" : "text-slate-200",
                      size === "sm" && "py-1.5 text-xs")}>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{o.label}</span>
                      {o.hint && <span className="block truncate text-[11px] text-slate-500">{o.hint}</span>}
                    </span>
                    {isSel && <Check size={14} className="shrink-0 text-accent" />}
                  </div>
                );
              })}
              {!options.length && <div className="px-2.5 py-2 text-xs text-slate-500">{t("No options")}</div>}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}

export function Checkbox({ checked, onChange, label, disabled, indeterminate, className, title }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  indeterminate?: boolean;
  className?: string;
  title?: string;
}) {
  const on = checked || indeterminate;
  const box = (
    <button type="button" role="checkbox" aria-checked={indeterminate ? "mixed" : checked} disabled={disabled} title={title}
      onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      className={clsx("relative grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[5px] border transition",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-40",
        on ? "border-accent bg-accent" : "border-base-600 bg-base-900 hover:border-slate-500")}>
      <AnimatePresence initial={false}>
        {on && (
          <motion.svg key={indeterminate ? "i" : "c"} viewBox="0 0 16 16" className="h-3 w-3 text-white"
            initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.4, opacity: 0 }} transition={{ duration: 0.12 }}>
            {indeterminate
              ? <motion.path d="M3.5 8h9" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" fill="none" />
              : <motion.path d="M3 8.5l3.2 3L13 4.5" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"
                  fill="none" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.18 }} />}
          </motion.svg>
        )}
      </AnimatePresence>
    </button>
  );
  if (!label) return <span className={className}>{box}</span>;
  return (
    // A label: a click on its text is passed to the checkbox button by the browser, keyboard and screen readers included.
    <label className={clsx("inline-flex cursor-pointer select-none items-start gap-2.5 text-sm text-slate-200", disabled && "cursor-not-allowed opacity-60", className)}>
      <span className="mt-[1px]">{box}</span>
      <span>{label}</span>
    </label>
  );
}

export function Radio({ checked, onChange, label, hint, disabled }: {
  checked: boolean; onChange: () => void; label: ReactNode; hint?: ReactNode; disabled?: boolean;
}) {
  return (
    <button type="button" role="radio" aria-checked={checked} disabled={disabled} onClick={onChange}
      className={clsx("flex w-full items-start gap-3 rounded-lg border p-3 text-left transition disabled:opacity-50",
        checked ? "border-accent/60 bg-accent/10" : "border-base-700 hover:border-base-600")}>
      <span className={clsx("mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border transition",
        checked ? "border-accent" : "border-base-600 bg-base-950/70")}>
        <AnimatePresence>{checked && <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }} className="h-2 w-2 rounded-full bg-accent" />}</AnimatePresence>
      </span>
      <span>
        <span className="block text-sm text-fg">{label}</span>
        {hint && <span className="text-xs text-slate-500">{hint}</span>}
      </span>
    </button>
  );
}

export function NumberInput({ value, onChange, min, max, step = 1, placeholder, disabled, allowEmpty, className, size = "md" }: {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  disabled?: boolean;
  allowEmpty?: boolean;
  className?: string;
  size?: "sm" | "md";
}) {
  const [text, setText] = useState(value === null || value === undefined ? "" : String(value));
  useEffect(() => { setText(value === null || value === undefined ? "" : String(value)); }, [value]);

  const clamp = (n: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
  const decimals = (String(step).split(".")[1] ?? "").length;
  const commit = (raw: string) => {
    const normalized = raw.replace(",", ".").trim();
    if (normalized === "") {
      if (allowEmpty) onChange(null);
      else setText(value === null || value === undefined ? "" : String(value));
      return;
    }
    const n = Number(normalized);
    if (Number.isNaN(n)) { setText(value === null || value === undefined ? "" : String(value)); return; }
    const v = clamp(n);
    onChange(v);
    setText(String(v));
  };
  const bump = (dir: 1 | -1) => {
    const base = text === "" ? (min ?? 0) - (dir > 0 ? step : 0) : Number(text.replace(",", "."));
    const v = clamp(Number(((Number.isNaN(base) ? 0 : base) + dir * step).toFixed(decimals)));
    setText(String(v));
    onChange(v);
  };
  const atMin = min !== undefined && text !== "" && Number(text) <= min;
  const atMax = max !== undefined && text !== "" && Number(text) >= max;
  const btn = "grid w-8 shrink-0 place-items-center text-slate-400 transition hover:bg-base-800 hover:text-fg disabled:pointer-events-none disabled:opacity-30";

  return (
    <div className={clsx("flex h-[38px] items-stretch overflow-hidden rounded-control border border-base-600 bg-base-900 transition focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25",
      disabled && "opacity-50", className)}>
      <button type="button" tabIndex={-1} className={btn} disabled={disabled || atMin} onClick={() => bump(-1)} aria-label={t("Decrease")}><Minus size={14} /></button>
      <input inputMode="decimal" disabled={disabled} placeholder={placeholder} value={text}
        onChange={(e) => setText(e.target.value.replace(/[^\d.,-]/g, ""))}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit((e.target as HTMLInputElement).value);
          if (e.key === "ArrowUp") { e.preventDefault(); bump(1); }
          if (e.key === "ArrowDown") { e.preventDefault(); bump(-1); }
        }}
        className={clsx("min-w-0 flex-1 bg-transparent text-center text-slate-100 outline-none placeholder:text-slate-500 tabular-nums",
          size === "sm" ? "py-1 text-xs" : "py-2 text-sm")} />
      <button type="button" tabIndex={-1} className={btn} disabled={disabled || atMax} onClick={() => bump(1)} aria-label={t("Increase")}><Plus size={14} /></button>
    </div>
  );
}
