export type Role = "admin" | "operator" | "viewer";
export type ClientStatus = "active" | "blocked" | "missing";
export type Severity = "info" | "warning" | "critical";
export type Range = "1h" | "24h" | "7d" | "30d" | "90d";

/** How people sign in: local users of the panel, or Keystone (SPA). */
export type AuthMode = "local" | "keystone";

/** A person who may use Network Control: a local user (password and TOTP kept in OpenBao) or a record synchronised from
 * Keystone (SPA). */
export interface PanelUser {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: Role;
  is_active: boolean;
  source: "spa" | "local";
  external_id: string | null;
  groups: string[];
  totp_enabled: boolean;
  must_change_password: boolean;
  locked: boolean;
  created_at: string | null;
  last_login_at: string | null;
  synced_at: string | null;
}

/** What the sign-in page needs to know: the mode, and for Keystone whether it is connected. */
export interface AuthConfig {
  mode: AuthMode;
  connected: boolean;
  issuer: string | null;
  account_url: string | null;
  error: string | null;
}

/** The setup wizard (before the first administrator exists). */
export type SetupStage = "storage" | "account" | "done";
export interface SetupState {
  stage: SetupStage;
  defaults: {
    postgres: { host: string; port: number; database: string; user: string };
    clickhouse: { url: string; user: string; database: string };
    openbao: { addr: string; mode: "bundled" | "external" };
  } | null;
  error: string | null;
}
export interface StorageInput {
  postgres: { host: string; port: number; database: string; user: string; password: string };
  clickhouse: { url: string; user: string; password: string; database: string };
  openbao: { addr: string; mode: "bundled" | "external"; root_token?: string; role_id?: string; secret_id?: string; mount?: string };
}
export interface StorageCheck {
  postgres: { ok: boolean; error?: string; version?: string; timescaledb?: boolean };
  clickhouse: { ok: boolean; error?: string; version?: string };
  openbao: { ok: boolean; error?: string; initialized?: boolean; sealed?: boolean; version?: string };
}

/** The infrastructure card of the settings page. */
export interface InfraInfo {
  ssh_public_key: string;
  storage: { postgres: string; timescaledb: boolean; clickhouse: string; openbao: string };
  gateway_client: boolean;
}

export interface ImportReport {
  imported: { username: string; id: string }[];
  conflicts: { username: string; reason: string }[];
  rejected: { username: string; reason: string }[];
}

/** The SPA connection as the settings page shows it. */
export interface SpaStatus {
  mode: AuthMode;
  user_import: boolean;
  last_import: { at: string; imported: number; conflicts: number; rejected: number } | null;
  connected: boolean;
  issuer: string | null;
  client_id: string | null;
  key_source: "env" | "file" | "openbao" | null;
  key_error: string | null;
  application: string | null;
  application_type: string | null;
  account_url: string | null;
  methods: { id: string; title: string }[];
  last_sync_at: string | null;
  last_attempt_at: string | null;
  error: string | null;
  counts: { users?: number; active?: number; spa?: number };
  last_result: { created?: number; updated?: number; disabled?: number; unchanged?: boolean };
  roles_published_at: string | null;
  roles: { id: Role; name: string; description: string }[];
  sync_seconds: number;
  redirect_uri: string | null;
}

export type ServerKind = "amnezia" | "mikrotik";
export interface VpnServer { kind: ServerKind; title: string; ready: boolean }
export const SERVER_LABEL: Record<ServerKind, string> = { amnezia: "Amnezia", mikrotik: "MikroTik" };

export interface VpnClient {
  id: string;
  server: ServerKind;
  public_key: string;
  name: string;
  owner: string;
  contact: string;
  tags: string[];
  note: string;
  address: string;
  status: ClientStatus;
  block_reason: "manual" | "expired" | "quota" | null;
  managed: boolean;
  system: boolean;
  expires_at: string | null;
  quota_bytes: number | null;
  quota_used_bytes: number;
  quota_percent: number | null;
  month_rx: number;
  month_tx: number;
  total_rx: number;
  total_tx: number;
  online: boolean;
  last_handshake_at: string | null;
  endpoint: string | null;
  country_code: string | null;
  country: string | null;
  city: string | null;
  lat: number | null;
  lon: number | null;
  rate_rx: number;
  rate_tx: number;
  created_at: string | null;
  created_by: string;
  config_downloaded_at: string | null;
  connections?: Connection[];
}

export interface Connection {
  ts: string;
  ip: string;
  port: number | null;
  country_code: string | null;
  country: string | null;
  city: string | null;
}

export interface ClientBundle {
  name: string;
  config: string;
  amnezia_key: string;
  qr_svg: string;
}

export interface Summary {
  clients_total: number;
  clients_active: number;
  clients_blocked: number;
  clients_missing: number;
  online: number;
  traffic_today: number;
  traffic_7d: number;
  traffic_30d: number;
  rate_rx: number;
  rate_tx: number;
  server_ok: boolean;
  gateway_online: boolean;
  alerts: Partial<Record<Severity, number>>;
}

export interface LivePoint {
  t: string;
  rx: number;
  tx: number;
  online: number;
}

export interface SeriesPoint {
  t: string;
  rx: number;
  tx: number;
}

export interface TopRow {
  id: string;
  name: string;
  rx: number;
  tx: number;
  total: number;
}

export interface HeatCell {
  dow: number;
  hour: number;
  bytes: number;
}

export interface GeoData {
  points: { lat: number; lon: number; city: string | null; country: string | null; connections: number; clients: string[] }[];
  countries: { code: string; country: string; connections: number; clients: number }[];
  current: { id: string; name: string; lat: number; lon: number; city: string | null; country: string | null; online: boolean }[];
  server: { lat: number; lon: number; city: string | null; country: string | null } | null;
  recent: { ts: string; client: string; ip: string; city: string | null; country: string | null; country_code: string | null }[];
}

export interface Alert {
  id: number;
  key: string;
  ts: string;
  severity: Severity;
  title: string;
  message: string;
  target_id: string;
  acknowledged_by: string | null;
  resolved_at: string | null;
  link?: string;
}

export interface AuditItem {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  target_name: string;
  details: Record<string, unknown>;
  ip: string;
}

export interface MikrotikVpnInfo {
  configured: boolean; interface: string; network: string; server_ip: string; lan_cidr: string; port: number;
  public_key: string | null; running: boolean; public_address: string; endpoint_host: string;
  client_dns: string; allowed_ips: string; peers_total: number; peers_disabled: number;
}

export interface ServerInfo {
  port: string;
  network: string;
  params: Record<string, string>;
  peers_total: number;
  peers_disabled: number;
  public_key: string;
  versions: { tools: string; go: string };
}

export interface RouterStatus {
  reachable: boolean;
  gateway_ip?: string;
  lan_cidr?: string;
  vpn_host?: string;
  error?: string;
  count?: number;
  cpu?: number;
  uptime?: string;
  version?: string;
  board?: string;
  tunnel_active?: boolean;
  routes?: { gateway: string; active: boolean; distance: number }[];
  mangle?: { comment: string; bytes: number; packets: number }[];
}

export interface SiteListSummary {
  sites: number;
  prefixes: number;
  coverage_percent: number;
  groups: Record<string, number>;
  site_names?: string[];
}

export interface Session {
  id: string;
  created_at: string;
  last_seen_at: string;
  ip: string;
  user_agent: string;
  current: boolean;
}

export type Settings = Record<string, string | number | boolean | null>;

export interface ApiTokenInfo {
  id: number;
  name: string;
  prefix: string;
  role: Role;
  created_by: string;
  created_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  token?: string;
}

export interface BackupItem { name: string; size: number; created_at: string }
export interface CustomPrefix { cidr: string; kind?: "prefix" | "domain"; ips?: string[]; comment: string; added_by: string; added_at: string }
export interface RouteCheck { target: string; via_vpn: boolean; rule?: string | null; ips: { ip: string; via_vpn: boolean; prefix: string | null }[] }

export interface FlowField { name: string; label: string; kind: string }
export interface FlowStatus { flows_per_second: number; last_flow: string | null; total_flows: number; disk_bytes: number; ok: boolean }
export interface FlowWidgetRow { name: string; bytes: number; percent: number }
export interface FlowRecord {
  TimeReceived: string; Direction: string; Route: string; LocalAddr: string; RemoteAddr: string; Protocol: string;
  LocalPort: number; RemotePort: number; RemoteAS: number; RemoteASName: string; RemoteCountry: string; Site: string;
  Bytes: number | string; Packets: number | string; LocalHost?: string; Application?: string; Exporter?: string;
  InIfName?: string; OutIfName?: string; Flags?: string; Threat?: string; RemoteHost?: string;
}
export interface FlowOverview {
  widgets: Record<string, FlowWidgetRow[]>;
  routes: { route: string; dir: string; v: number }[];
  series: { t: string; dir: string; v: number }[];
  last_flow: FlowRecord | null;
  status: FlowStatus;
}
export interface FlowSeries {
  name: string; dimensions: string[]; direction: string; values: number[];
  stats: { min: number; max: number; last: number; avg: number; p95: number; total: number };
}
export interface FlowResult {
  graph: string; unit: string; unit_label: string; dimensions: string[];
  axis?: string[]; step?: number; series?: FlowSeries[]; previous?: { label: string; values: number[] } | null;
  bidirectional?: boolean; table?: string; nodes?: { name: string; label: string; depth: number }[];
  links?: { source: string; target: string; value: number }[];
}
export interface FlowQuery {
  start?: string; end?: string; dimensions: string[]; filter: string; limit: number; limitType: string;
  unit: string; graph: string; bidirectional: boolean; previousPeriod: boolean;
}
export interface SavedFilter { id: number; name: string; filter: string; shared: boolean; owner: string; mine: boolean }

export type DeviceAction = "hostname" | "interface" | "dns" | "ntp" | "flow_export" | "reboot" | "endpoint_block" | "command" | "backup";
export interface DevicePlatform {
  key: string; vendor: string; title: string; segment: "home" | "smb" | "enterprise" | "any"; kind: string;
  transports: { name: string; port: number | null }[]; credentials: string[]; hint: string;
  capabilities: Record<string, DeviceAction[]>;
}
export interface DeviceFacts {
  hostname?: string; vendor?: string; model?: string; os?: string; version?: string; serial?: string; mac?: string;
  uptime_seconds?: number | null; cpu_percent?: number | null; memory_percent?: number | null; extra?: Record<string, unknown>;
}
export interface DeviceInterface {
  name: string; index: number | null; description: string; type: string; mac: string; admin_up: boolean | null;
  oper_up: boolean | null; speed_mbps: number | null; mtu: number | null; addresses: string[];
  rx_bytes: number | null; tx_bytes: number | null; rx_bps?: number | null; tx_bps?: number | null; utilization?: number | null;
}
export interface DeviceNeighbor { local_interface: string; remote_name: string; remote_ip: string; remote_mac: string; remote_interface: string; remote_platform: string; protocol: string }
export interface DeviceChild { mac: string; name: string; model: string; kind: string; ip: string; online: boolean; version: string; uplink_mac: string; uplink_port: string; clients: number | null }
export interface NetworkDevice {
  id: string; name: string; platform: string; platform_title: string; vendor: string; host: string; port: number | null;
  transport: string; verify_tls: boolean; site: string; location: string; role: string; tags: string[]; note: string;
  enabled: boolean; poll_seconds: number; flow_exporter_ip: string; use_system_key: boolean; ssh_host_key: boolean;
  status: "up" | "down" | "error" | "unknown"; last_poll_at: string | null; last_ok_at: string | null; last_error: string;
  facts: DeviceFacts; capabilities: DeviceAction[];
  counts: { interfaces: number; neighbors: number; children: number; interfaces_up: number };
  created_at: string | null; created_by: string;
  credentials?: Record<string, boolean | string>;
  interfaces?: DeviceInterface[]; neighbors?: DeviceNeighbor[]; children?: DeviceChild[]; endpoints?: NetworkEndpoint[];
}
export interface NetworkEndpoint {
  mac: string; ip: string; hostname: string; alias: string; name: string; note: string; vendor: string;
  device_id: string | null; device_name: string; interface: string; source: string; attached_to: string; port: string;
  ssid: string; signal: number | null; vlan: number | null; blocked: boolean; first_seen: string; last_seen: string; online: boolean;
}
export interface DiscoveryResult { host: string; reachable: boolean; ports: number[]; evidence: string[]; snmp: { descr?: string; vendor?: string; name?: string }; platforms: string[]; name: string; known?: boolean }
export interface TopoNode {
  id: string; type: "device" | "child" | "neighbor" | "endpoint" | "internet" | "vpn"; label: string; status?: string; role?: string;
  vendor?: string; model?: string; ip?: string; mac?: string; device_id?: string; platform?: string; platform_guess?: string;
  location?: string; x?: number | null; y?: number | null; clients?: number; bps?: number; rx_bps?: number; tx_bps?: number;
  ssid?: string; signal?: number | null; hostname?: string; alias?: string; blocked?: boolean; last_seen?: string; parent?: string;
  controller?: string; error?: string; interfaces?: number; version?: string;
}
export interface TopoEdge { source: string; target: string; kind: "link" | "managed" | "wifi" | "wired" | "wan" | "tunnel"; source_port?: string; target_port?: string; label?: string; protocol?: string; bps?: number | null; utilization?: number | null; speed?: number | null }
export interface Topology { nodes: TopoNode[]; edges: TopoEdge[]; stats: { devices: number; children: number; neighbors: number; endpoints: number; online: number; down: number; traffic: boolean }; generated_at: string }

export interface FlowExporter {
  address: string; name: string; flows_per_second: number; bps: number; last_seen: string; flows_24h: number; sampling_rate: number;
  interfaces: { index: number; name: string; in_bps: number; out_bps: number; speed_mbps: number | null; utilization: number | null }[];
}
export interface FlowRuleKind { kind: string; title: string; defaults: Record<string, unknown> }
export interface FlowRule {
  id: number; name: string; kind: string; kind_title: string; enabled: boolean; filter: string; severity: Severity;
  params: Record<string, unknown>; firing: number; last_eval_at: string | null; last_error: string; created_by: string;
}
export interface ThreatContact { list: string; local: string; host: string; remote: string; country: string; asname: string; direction: string; bytes: number; flows: number; last_seen: string }
export interface ThreatInfo { status: { lists: Record<string, number | string>; prefixes: number; updated_at: string } | null; enabled: boolean; lists: { name: string; url: string; enabled: boolean; terms?: string }[]; contacts: ThreatContact[] }
export interface SavedView { id: number; name: string; state: Record<string, unknown>; shared: boolean; owner: string; mine: boolean }

/** A step of the "First steps" checklist or of a page setup guide (GET /api/onboarding, /api/guides/{page}). */
export interface OnboardingStep {
  id: string;
  done: boolean;
  optional: boolean;
  /** The page that fixes the step ("" — none). */
  path: string;
  /** In a page guide: the button of the page itself that does it ("" — none). */
  action: string;
  /** Whether the signed-in role may do it. */
  can_fix: boolean;
}

export interface Onboarding {
  done: boolean;
  steps: OnboardingStep[];
}
