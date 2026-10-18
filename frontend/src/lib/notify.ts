import { create } from "zustand";
import { toast } from "sonner";

export type NoticeType = "success" | "error" | "warning" | "info";

export interface Notice {
  id: string;
  type: NoticeType;
  title: string;
  description?: string;
  ts: number;
  read: boolean;
}

const STORAGE_KEY = "network-control.notifications";
const HISTORY_LIMIT = 100;
const DURATION: Record<NoticeType, number> = { success: 4000, info: 5000, warning: 7000, error: 9000 };
const DEDUPE_MS = 4000;

/** randomUUID needs a secure context (HTTPS or localhost); getRandomValues works on a plain-HTTP LAN address too. */
function newId(): string {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) => n.toString(16).padStart(8, "0")).join("-");
}

function load(): Notice[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Notice[]).slice(0, HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

function save(items: Notice[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, HISTORY_LIMIT)));
  } catch {
    return;
  }
}

interface NotificationState {
  items: Notice[];
  add: (n: Omit<Notice, "id" | "ts" | "read">) => Notice;
  markAllRead: () => void;
  remove: (id: string) => void;
  clear: () => void;
}

export const useNotifications = create<NotificationState>((set, get) => ({
  items: load(),
  add: (n) => {
    const notice: Notice = { ...n, id: newId(), ts: Date.now(), read: false };
    const items = [notice, ...get().items].slice(0, HISTORY_LIMIT);
    save(items);
    set({ items });
    return notice;
  },
  markAllRead: () => {
    const items = get().items.map((i) => ({ ...i, read: true }));
    save(items);
    set({ items });
  },
  remove: (id) => {
    const items = get().items.filter((i) => i.id !== id);
    save(items);
    set({ items });
  },
  clear: () => {
    save([]);
    set({ items: [] });
  },
}));

class Notifier {
  private recent = new Map<string, number>();

  private show(type: NoticeType, title: string, description?: string, dedupeMs = DEDUPE_MS) {
    const key = `${type}|${title}|${description ?? ""}`;
    const now = Date.now();
    if (now - (this.recent.get(key) ?? 0) < dedupeMs) return;
    this.recent.set(key, now);
    const notice = useNotifications.getState().add({ type, title, description });
    toast[type](title, { id: notice.id, description, duration: DURATION[type] });
  }

  success(title: string, description?: string) { this.show("success", title, description); }
  error(title: string, description?: string, dedupeMs?: number) { this.show("error", title, description, dedupeMs); }
  warning(title: string, description?: string) { this.show("warning", title, description); }
  info(title: string, description?: string) { this.show("info", title, description); }
}

export const notify = new Notifier();
