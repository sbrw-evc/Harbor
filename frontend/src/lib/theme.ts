import { useSyncExternalStore } from "react";

/** Light and dark themes of the Umbrella design; the choice is kept per browser, by default the system's. */
export type Theme = "light" | "dark";
const KEY = "network-control.theme";
const listeners = new Set<() => void>();

export const getTheme = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

function apply(next: Theme) {
  document.documentElement.dataset.theme = next;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next === "dark" ? "#070b14" : "#e9edf2");
  listeners.forEach((l) => l());
}

/** Switches the theme; with `from` the new theme opens in a circle from that point where the browser can. */
export function setTheme(next: Theme, from?: { x: number; y: number }) {
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* storage is off */
  }
  const doc = document as Document & { startViewTransition?: (cb: () => void) => { ready: Promise<void> } };
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!from || !doc.startViewTransition || reduce) return apply(next);
  const r = Math.hypot(Math.max(from.x, innerWidth - from.x), Math.max(from.y, innerHeight - from.y));
  void doc.startViewTransition(() => apply(next)).ready.then(() =>
    document.documentElement.animate(
      { clipPath: [`circle(0 at ${from.x}px ${from.y}px)`, `circle(${r}px at ${from.x}px ${from.y}px)`] },
      { duration: 420, easing: "cubic-bezier(.22,1,.36,1)", pseudoElement: "::view-transition-new(root)" },
    ),
  );
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getTheme,
    getTheme,
  );
}

/** Colours of charts and maps for the current theme. */
export function chartColors() {
  const css = getComputedStyle(document.documentElement);
  const rgb = (name: string) => `rgb(${css.getPropertyValue(`--c-${name}`).trim().split(/\s+/).join(",")})`;
  return {
    grid: css.getPropertyValue("--chart-grid").trim(),
    text: css.getPropertyValue("--chart-text").trim(),
    fg: rgb("fg"),
    surface: rgb("surface"),
    border: rgb("border"),
    accent: rgb("accent"),
    ok: rgb("ok"),
    warn: rgb("warn"),
    error: rgb("error"),
  };
}
