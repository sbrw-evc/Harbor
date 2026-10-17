import {
  Activity, Archive, BarChart3, Bell, BrickWall, FileClock, Globe2, HardDrive, LayoutDashboard, MonitorSmartphone, Network, Router,
  Server, Settings, Share2, Shield, UserCircle, UserCog, Users, Waypoints, type LucideIcon,
} from "lucide-react";
import type { Role } from "@/lib/types";

/**
 * The application menu, grouped like Umbrella's sidebar: each group is an accordion of pages. Labels are English
 * keys of the dictionary; the command palette searches the same list.
 */
export interface NavPage { to: string; label: string; icon: LucideIcon; role?: Role; badge?: "alerts" }
export interface NavGroup { id: string; title: string; icon: LucideIcon; pages: NavPage[] }

export const NAV: NavGroup[] = [
  {
    id: "monitoring", title: "Monitoring", icon: Activity, pages: [
      { to: "/", label: "Overview", icon: LayoutDashboard },
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
      { to: "/flows", label: "Flow analysis", icon: Waypoints },
      { to: "/network-map", label: "Network map", icon: Share2 },
      { to: "/map", label: "VPN connections map", icon: Globe2 },
      { to: "/alerts", label: "Alerts", icon: Bell, badge: "alerts" },
    ],
  },
  {
    id: "network", title: "Network", icon: Network, pages: [
      { to: "/devices", label: "Devices", icon: HardDrive },
      { to: "/endpoints", label: "Network clients", icon: MonitorSmartphone },
    ],
  },
  {
    id: "vpn", title: "VPN", icon: Shield, pages: [
      { to: "/clients", label: "VPN clients", icon: Users },
      { to: "/mikrotik-vpn", label: "VPN on MikroTik", icon: Router },
      { to: "/opnsense-vpn", label: "VPN on OPNsense", icon: BrickWall },
      { to: "/routing", label: "Routing", icon: Network },
    ],
  },
  {
    id: "admin", title: "Administration", icon: Settings, pages: [
      { to: "/users", label: "Users and access", icon: UserCog, role: "admin" },
      { to: "/server", label: "AWG server", icon: Server, role: "admin" },
      { to: "/audit", label: "Audit log", icon: FileClock, role: "admin" },
      { to: "/backups", label: "Backups", icon: Archive, role: "admin" },
      { to: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export const ALL_PAGES: NavPage[] = [...NAV.flatMap((g) => g.pages), { to: "/profile", label: "Profile", icon: UserCircle }];

/** The page of a path (the longest matching menu entry). */
export function pageOf(path: string): NavPage | undefined {
  return [...ALL_PAGES].sort((a, b) => b.to.length - a.to.length).find((p) => (p.to === "/" ? path === "/" : path === p.to || path.startsWith(`${p.to}/`)));
}
