import type {
  Alert, ApiTokenInfo, AuditItem, BackupItem, CustomPrefix, FlowField, FlowOverview, FlowQuery, FlowRecord, FlowResult,
  FlowStatus, RouteCheck, SavedFilter, ClientBundle, GeoData, HeatCell, LivePoint, PanelUser, Range, RouterStatus, SeriesPoint,
  MikrotikVpnInfo, OpnsenseVpnInfo, ServerInfo, ServerKind, Session, Settings, SiteListSummary, Summary, TopRow, VpnClient, VpnServer,
  AuthConfig, AuthMode, ImportReport, InfraInfo, Role, SetupStage, SetupState, SpaStatus, StorageCheck, StorageDraft, StorageInput,
  DeviceAction, DevicePlatform, DiscoveryResult, FlowExporter, FlowRule, FlowRuleKind, NetworkDevice, NetworkEndpoint,
  SavedView, ThreatInfo, Topology, Onboarding,
} from "./types";
import { t, tServer } from "@/i18n";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public violations?: string[]) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export class HttpClient {
  onUnauthorized: (() => void) | null = null;

  constructor(private readonly base = "") {}

  private url(path: string, query?: Query) {
    const qs = query
      ? Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&")
      : "";
    return this.base + path + (qs ? `?${qs}` : "");
  }

  async request<T>(method: string, path: string, opts: { body?: unknown; query?: Query; form?: FormData; silent401?: boolean; headers?: Record<string, string> } = {}): Promise<T> {
    const headers: Record<string, string> = { "X-Requested-With": "network-control", ...opts.headers };
    let body: BodyInit | undefined;
    if (opts.form) body = opts.form;
    else if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const r = await fetch(this.url(path, opts.query), { method, headers, body, credentials: "same-origin" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 401 && !opts.silent401) this.onUnauthorized?.();
      const detail = (data as { detail?: unknown }).detail;
      const text = typeof detail === "string" ? tServer(detail) : Array.isArray(detail) ? t("Check the entered values") : t("Error {status}", { status: r.status });
      throw new ApiError(r.status, text, (data as { code?: string }).code, (data as { violations?: string[] }).violations);
    }
    return data as T;
  }

  get<T>(path: string, query?: Query) { return this.request<T>("GET", path, { query }); }
  post<T>(path: string, body?: unknown) { return this.request<T>("POST", path, { body: body ?? {} }); }
  put<T>(path: string, body: unknown) { return this.request<T>("PUT", path, { body }); }
  patch<T>(path: string, body: unknown) { return this.request<T>("PATCH", path, { body }); }
  delete<T>(path: string) { return this.request<T>("DELETE", path); }
}

abstract class Resource {
  constructor(protected readonly http: HttpClient) {}
}

/** The setup wizard: every step but the state carries the one-time setup code from the log of the backend. */
export class SetupApi extends Resource {
  state() { return this.http.request<SetupState>("GET", "/api/setup", { silent401: true }); }
  private call<T>(method: string, path: string, code: string, body?: unknown) {
    return this.http.request<T>(method, path, { body, headers: { "X-Setup-Code": code }, silent401: true });
  }
  verify(code: string) { return this.call<{ ok: boolean; stage: SetupStage }>("POST", "/api/setup/verify", code, {}); }
  checkStorage(code: string, s: StorageInput) { return this.call<StorageCheck>("POST", "/api/setup/storage/check", code, s); }
  /** The storage the server keeps as a draft until the last step; its secrets are not sent back. */
  storageDraft(code: string) { return this.call<StorageDraft>("GET", "/api/setup/storage", code); }
  /** Checks the storage and keeps it as a draft: nothing is applied before the last step. */
  storage(code: string, s: StorageInput) { return this.call<{ stage: SetupStage }>("POST", "/api/setup/storage", code, s); }
  /** The last step: applies everything. The keys of an OpenBao initialised by it come back once. */
  account(code: string, body: { mode: AuthMode; public_base_url?: string; username?: string; display_name?: string; email?: string; password?: string; key?: string }) {
    return this.call<{ stage: SetupStage; user?: PanelUser; redirect: string; openbao: { root_token: string; recovery_key: string } | null }>(
      "POST", "/api/setup/account", code, body);
  }
  keystone(code: string, base: string) {
    return this.http.request<{ redirect_uri: string; post_logout_uri: string; roles: string[] }>("GET", "/api/setup/keystone",
      { query: { base }, headers: { "X-Setup-Code": code }, silent401: true });
  }
}

/** Sign-in: local users (password and TOTP) or Keystone / SPA; the application keeps only its own sessions. */
export class AuthApi extends Resource {
  config() { return this.http.request<AuthConfig>("GET", "/api/auth/config", { silent401: true }); }
  login(username: string, password: string) {
    return this.http.request<{ requires_2fa: boolean; user: PanelUser | null }>("POST", "/api/auth/login", { body: { username, password }, silent401: true });
  }
  verify2fa(code: string) { return this.http.request<{ user: PanelUser }>("POST", "/api/auth/2fa", { body: { code }, silent401: true }); }
  changePassword(current: string, next: string) { return this.http.post("/api/auth/password", { current, new: next }); }
  totpSetup() { return this.http.post<{ secret: string; uri: string; qr_svg: string }>("/api/auth/totp/setup"); }
  totpEnable(code: string) { return this.http.post("/api/auth/totp/enable", { code }); }
  totpDisable() { return this.http.post("/api/auth/totp/disable"); }
  /** The address that starts the sign-in at SPA and comes back to `returnTo`. */
  startUrl(returnTo = "/", opts: { prompt?: "login" } = {}) {
    const q = new URLSearchParams({ return: returnTo, ...(opts.prompt ? { prompt: opts.prompt } : {}) });
    return `/api/auth/spa/start?${q}`;
  }
  logout() { return this.http.post<{ ok: boolean; redirect: string | null }>("/api/auth/logout"); }
  me() { return this.http.request<PanelUser>("GET", "/api/auth/me", { silent401: true }); }
  sessions() { return this.http.get<Session[]>("/api/auth/sessions"); }
  revokeSession(id: string) { return this.http.delete(`/api/auth/sessions/${id}`); }
}

export interface ClientInput {
  name: string; owner?: string; contact?: string; tags?: string[]; note?: string;
  expires_at?: string | null; quota_gb?: number | null; server?: ServerKind;
}

export class ClientsApi extends Resource {
  list(q?: { q?: string; status?: string; tag?: string; server?: string }) { return this.http.get<VpnClient[]>("/api/clients", q); }
  servers() { return this.http.get<VpnServer[]>("/api/clients/servers"); }
  get(id: string) { return this.http.get<VpnClient>(`/api/clients/${id}`); }
  tags() { return this.http.get<string[]>("/api/clients/tags"); }
  create(data: ClientInput) { return this.http.post<VpnClient>("/api/clients", data); }
  update(id: string, data: Partial<ClientInput>) { return this.http.patch<VpnClient>(`/api/clients/${id}`, data); }
  block(id: string) { return this.http.post<VpnClient>(`/api/clients/${id}/block`); }
  unblock(id: string) { return this.http.post<VpnClient>(`/api/clients/${id}/unblock`); }
  reissue(id: string) { return this.http.post<VpnClient>(`/api/clients/${id}/reissue`); }
  remove(id: string) { return this.http.delete(`/api/clients/${id}`); }
  bundle(id: string) { return this.http.get<ClientBundle>(`/api/clients/${id}/config`); }
  downloadUrl(id: string) { return `/api/clients/${id}/config.conf`; }
  share(id: string, hours?: number) { return this.http.post<{ url: string; expires_at: string }>(`/api/clients/${id}/share`, { hours }); }
  bulk(ids: string[], action: "block" | "unblock" | "delete") { return this.http.post<{ done: number; failed: { id: string; error: string }[] }>("/api/clients/bulk", { ids, action }); }
  sync() { return this.http.post<{ imported: number; missing: number; peers: number }>("/api/clients/sync"); }
  publicShare(token: string) { return this.http.request<ClientBundle>("GET", `/api/public/share/${token}`, { silent401: true }); }
}

export class AnalyticsApi extends Resource {
  summary() { return this.http.get<Summary>("/api/analytics/summary"); }
  live() { return this.http.get<LivePoint[]>("/api/analytics/live"); }
  traffic(range: Range, clientId?: string) { return this.http.get<{ step: string; points: SeriesPoint[] }>("/api/analytics/traffic", { range, client_id: clientId }); }
  top(range: Range) { return this.http.get<TopRow[]>("/api/analytics/top", { range }); }
  heatmap(range: Range) { return this.http.get<HeatCell[]>("/api/analytics/heatmap", { range }); }
  geo(range: Range) { return this.http.get<GeoData>("/api/analytics/geo", { range }); }
}

export class OpsApi extends Resource {
  alerts(all = false) { return this.http.get<Alert[]>("/api/alerts", { all }); }
  ack(id: number) { return this.http.post(`/api/alerts/${id}/ack`); }
  audit(q: { limit?: number; offset?: number; action?: string; actor?: string; q?: string }) {
    return this.http.get<{ total: number; items: AuditItem[] }>("/api/audit", q);
  }
  serverInfo() { return this.http.get<ServerInfo>("/api/server/info"); }
  mikrotikVpn() { return this.http.get<MikrotikVpnInfo>("/api/mikrotik-vpn"); }
  setupMikrotikVpn() { return this.http.post<MikrotikVpnInfo>("/api/mikrotik-vpn/setup"); }
  opnsenseVpn() { return this.http.get<OpnsenseVpnInfo>("/api/opnsense-vpn"); }
  setOpnsenseKey(key: string, secret: string) { return this.http.put<OpnsenseVpnInfo>("/api/opnsense-vpn/credentials", { key, secret }); }
  removeOpnsenseKey() { return this.http.delete<OpnsenseVpnInfo>("/api/opnsense-vpn/credentials"); }
  serverConfig() { return this.http.get<{ config: string; backups: string[] }>("/api/server/config"); }
  saveServerConfig(config: string) { return this.http.put("/api/server/config", { config }); }
  backup(name: string) { return this.http.get<{ config: string }>(`/api/server/backups/${encodeURIComponent(name)}`); }
  routing() { return this.http.get<{ router: RouterStatus; site_list: SiteListSummary | null }>("/api/routing/status"); }
  uploadSiteList(file: File) {
    const form = new FormData();
    form.append("file", file);
    return this.http.request<SiteListSummary & { applied: number }>("POST", "/api/routing/site-list", { form });
  }
  settings() { return this.http.get<Settings>("/api/settings"); }
  saveSettings(s: Settings) { return this.http.put<Settings>("/api/settings", s); }
  infra() { return this.http.get<InfraInfo>("/api/infra"); }
  checkInfra(target: "router" | "vps") { return this.http.post<{ ok: boolean; detail: string }>("/api/infra/check", { target }); }
  gatewayClient() { return this.http.post<{ ok: boolean; client_id: string }>("/api/gateway/client"); }
}

export interface UserInput { username: string; display_name?: string; email?: string; role: Role; password: string }

export class UsersApi extends Resource {
  list() { return this.http.get<PanelUser[]>("/api/users"); }
  create(d: UserInput) { return this.http.post<PanelUser>("/api/users", d); }
  update(id: number, d: { display_name?: string; email?: string; role?: Role; is_active?: boolean }) { return this.http.patch<PanelUser>(`/api/users/${id}`, d); }
  resetPassword(id: number, password: string) { return this.http.post(`/api/users/${id}/password`, { password }); }
  disableTotp(id: number) { return this.http.post(`/api/users/${id}/totp/disable`); }
  remove(id: number) { return this.http.delete(`/api/users/${id}`); }
  spa() { return this.http.get<SpaStatus>("/api/spa"); }
  sync() { return this.http.post<{ connected: boolean; created?: number; updated?: number; disabled?: number; unchanged?: boolean }>("/api/spa/sync"); }
  setKey(key: string) { return this.http.put<{ issuer: string; application: string | null; user_import: boolean }>("/api/spa/key", { key }); }
  removeKey() { return this.http.delete("/api/spa/key"); }
  importLocal() { return this.http.post<ImportReport>("/api/spa/import"); }
  switchToKeystone(purgeLocal: boolean) { return this.http.post<{ purged_local: number }>("/api/spa/switch", { purge_local: purgeLocal }); }
  switchToLocal() { return this.http.post("/api/auth/local"); }
}

export class AdminApi extends Resource {
  tokens() { return this.http.get<ApiTokenInfo[]>("/api/tokens"); }
  createToken(d: { name: string; role: string; days: number | null }) { return this.http.post<ApiTokenInfo>("/api/tokens", d); }
  revokeToken(id: number) { return this.http.delete(`/api/tokens/${id}`); }
  notifications() { return this.http.get<{ configured: boolean; bot: string | null }>("/api/notifications"); }
  setTelegramToken(token: string) { return this.http.put<{ ok: boolean; bot: string }>("/api/notifications/telegram", { token }); }
  testNotification() { return this.http.post("/api/notifications/test"); }
  backups() { return this.http.get<BackupItem[]>("/api/backups"); }
  createBackup() { return this.http.post<{ name: string }>("/api/backups"); }
  deleteBackup(name: string) { return this.http.delete(`/api/backups/${encodeURIComponent(name)}`); }
  backupUrl(name: string) { return `/api/backups/${encodeURIComponent(name)}`; }
  customPrefixes() { return this.http.get<CustomPrefix[]>("/api/routing/custom"); }
  addCustomPrefix(target: string, comment: string) { return this.http.post<CustomPrefix>("/api/routing/custom", { target, comment }); }
  removeCustomPrefix(cidr: string) { return this.http.delete(`/api/routing/custom/${cidr}`); }
  checkRoute(target: string) { return this.http.post<RouteCheck>("/api/routing/check", { target }); }
}

export class FlowsApi extends Resource {
  fields() { return this.http.get<FlowField[]>("/api/flows/fields"); }
  status() { return this.http.get<FlowStatus>("/api/flows/status"); }
  overview() { return this.http.get<FlowOverview>("/api/flows/overview"); }
  visualize(q: FlowQuery) { return this.http.post<FlowResult>("/api/flows/visualize", q); }
  recent(filter: string, limit = 100) { return this.http.post<FlowRecord[]>("/api/flows/recent", { filter, limit }); }
  filters() { return this.http.get<SavedFilter[]>("/api/flows/filters"); }
  saveFilter(name: string, filter: string, shared: boolean) { return this.http.post<{ id: number }>("/api/flows/filters", { name, filter, shared }); }
  deleteFilter(id: number) { return this.http.delete(`/api/flows/filters/${id}`); }
  exporters() { return this.http.get<FlowExporter[]>("/api/flows/exporters"); }
  ruleKinds() { return this.http.get<FlowRuleKind[]>("/api/flows/rules/kinds"); }
  rules() { return this.http.get<FlowRule[]>("/api/flows/rules"); }
  createRule(d: Partial<FlowRule>) { return this.http.post<FlowRule>("/api/flows/rules", d); }
  updateRule(id: number, d: Partial<FlowRule>) { return this.http.patch<FlowRule>(`/api/flows/rules/${id}`, d); }
  deleteRule(id: number) { return this.http.delete(`/api/flows/rules/${id}`); }
  testRule(id: number) { return this.http.post<{ group: string; label: string; value: number; text: string; link?: string }[]>(`/api/flows/rules/${id}/test`); }
  threats(hours = 24) { return this.http.get<ThreatInfo>("/api/flows/threats", { hours }); }
  refreshThreats() { return this.http.post<{ prefixes: number }>("/api/flows/threats/refresh"); }
  views() { return this.http.get<SavedView[]>("/api/flows/views"); }
  saveView(name: string, state: Record<string, unknown>, shared = true) { return this.http.post<{ id: number }>("/api/flows/views", { name, state, shared }); }
  deleteView(id: number) { return this.http.delete(`/api/flows/views/${id}`); }
}

export type DeviceInput = Partial<Omit<NetworkDevice, "credentials">> & { credentials?: Record<string, string | null>; clear_credentials?: string[] };

export class DevicesApi extends Resource {
  platforms() { return this.http.get<DevicePlatform[]>("/api/devices/platforms"); }
  list() { return this.http.get<NetworkDevice[]>("/api/devices"); }
  get(id: string) { return this.http.get<NetworkDevice>(`/api/devices/${id}`); }
  create(d: DeviceInput) { return this.http.post<NetworkDevice>("/api/devices", d); }
  update(id: string, d: DeviceInput) { return this.http.patch<NetworkDevice>(`/api/devices/${id}`, d); }
  remove(id: string) { return this.http.delete(`/api/devices/${id}`); }
  test(d: DeviceInput & { id?: string }) { return this.http.post<{ ok: boolean; facts: Record<string, unknown>; interfaces: number; neighbors: number; endpoints: number; children: number }>("/api/devices/test", d); }
  poll(id: string) { return this.http.post<{ ok: boolean; error: string }>(`/api/devices/${id}/poll`); }
  action(id: string, action: DeviceAction, params: Record<string, unknown> = {}) { return this.http.post<{ ok: boolean; output: string }>(`/api/devices/${id}/actions`, { action, params }); }
  backupUrl(id: string) { return `/api/devices/${id}/backup`; }
  resetHostKey(id: string) { return this.http.post(`/api/devices/${id}/reset-host-key`); }
  discover(target: string, community = "public") { return this.http.post<DiscoveryResult[]>("/api/devices/discover", { target, community }); }
  endpoints(q?: { q?: string; online?: boolean; device_id?: string }) { return this.http.get<NetworkEndpoint[]>("/api/endpoints", q); }
  updateEndpoint(mac: string, d: { alias?: string; note?: string }) { return this.http.patch<NetworkEndpoint>(`/api/endpoints/${encodeURIComponent(mac)}`, d); }
  forgetEndpoint(mac: string) { return this.http.delete(`/api/endpoints/${encodeURIComponent(mac)}`); }
  blockEndpoint(mac: string, blocked: boolean) { return this.http.post<{ ok: boolean; output: string }>(`/api/endpoints/${encodeURIComponent(mac)}/block`, { blocked }); }
  topology(offline = false, endpoints = true) { return this.http.get<Topology>("/api/topology", { offline, endpoints }); }
  savePositions(p: Record<string, [number, number] | null>) { return this.http.put("/api/topology/positions", p); }
}

/** Business processes: the "First steps" checklist of the overview and the setup guides of the pages. */
export class OnboardingApi extends Resource {
  checklist() { return this.http.get<Onboarding>("/api/onboarding"); }
  guide(page: string) { return this.http.get<Onboarding>(`/api/guides/${encodeURIComponent(page)}`); }
}

export class ApiClient {
  readonly http = new HttpClient();
  readonly setup = new SetupApi(this.http);
  readonly auth = new AuthApi(this.http);
  readonly clients = new ClientsApi(this.http);
  readonly analytics = new AnalyticsApi(this.http);
  readonly ops = new OpsApi(this.http);
  readonly users = new UsersApi(this.http);
  readonly admin = new AdminApi(this.http);
  readonly flows = new FlowsApi(this.http);
  readonly devices = new DevicesApi(this.http);
  readonly onboarding = new OnboardingApi(this.http);
}

export const api = new ApiClient();
