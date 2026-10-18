import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { live } from "./live";

const EVENT_QUERIES: Record<string, string[]> = {
  live: ["clients", "client", "summary", "top", "traffic", "client-traffic"],
  clients: ["clients", "client", "summary", "top"],
  connection: ["geo", "client"],
  alert: ["alerts", "summary"],
  audit: ["audit", "users", "routing", "server-info", "sessions"],
};

class LiveQuerySync {
  private last = new Map<string, number>();

  constructor(private qc: QueryClient, private minIntervalMs = 3000) {}

  handle(event: string) {
    const now = Date.now();
    if (now - (this.last.get(event) ?? 0) < this.minIntervalMs) return;
    this.last.set(event, now);
    for (const key of EVENT_QUERIES[event] ?? []) void this.qc.invalidateQueries({ queryKey: [key] });
  }

  start(): () => void {
    const offs = Object.keys(EVENT_QUERIES).map((e) => live.on(e, () => this.handle(e)));
    let wasConnected = true;

    offs.push(live.onStatus((ok) => {
      if (ok && !wasConnected) void this.qc.invalidateQueries();
      wasConnected = ok;
    }));
    return () => offs.forEach((off) => off());
  }
}

export function useLiveQuerySync() {
  const qc = useQueryClient();
  useEffect(() => new LiveQuerySync(qc).start(), [qc]);
}
