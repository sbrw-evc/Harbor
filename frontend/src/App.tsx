import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/store/auth";
import AppLayout from "@/components/layout/AppLayout";
import { PageLoader } from "@/components/ui/Feedback";
import type { Role } from "@/lib/types";
import LoginPage from "@/pages/Login";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Clients = lazy(() => import("@/pages/Clients"));
const MikrotikVpn = lazy(() => import("@/pages/MikrotikVpn"));
const OpnsenseVpn = lazy(() => import("@/pages/OpnsenseVpn"));
const ClientDetail = lazy(() => import("@/pages/ClientDetail"));
const Analytics = lazy(() => import("@/pages/Analytics"));
const MapPage = lazy(() => import("@/pages/MapPage"));
const Alerts = lazy(() => import("@/pages/Alerts"));
const Routing = lazy(() => import("@/pages/Routing"));
const Server = lazy(() => import("@/pages/Server"));
const Audit = lazy(() => import("@/pages/Audit"));
const Users = lazy(() => import("@/pages/Users"));
const SettingsPage = lazy(() => import("@/pages/Settings"));
const Profile = lazy(() => import("@/pages/Profile"));
const SharePage = lazy(() => import("@/pages/Share"));
const Flows = lazy(() => import("@/pages/Flows"));
const Backups = lazy(() => import("@/pages/Backups"));
const Devices = lazy(() => import("@/pages/Devices"));
const DeviceDetail = lazy(() => import("@/pages/DeviceDetail"));
const Endpoints = lazy(() => import("@/pages/Endpoints"));
const NetworkMap = lazy(() => import("@/pages/NetworkMap"));
const SetupPage = lazy(() => import("@/pages/Setup"));

function Guard({ role, children }: { role: Role; children: JSX.Element }) {
  const can = useAuth((s) => s.can);
  return can(role) ? children : <Navigate to="/" replace />;
}

function Private() {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <PageLoader full />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return (
    <AppLayout>
      <Suspense fallback={<PageLoader />}>
        <AnimatePresence mode="wait">
          <Routes location={location} key={location.pathname.split("/")[1]}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/clients" element={<Clients />} />
            <Route path="/clients/:id" element={<ClientDetail />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/flows" element={<Flows />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/devices/:id" element={<DeviceDetail />} />
            <Route path="/endpoints" element={<Endpoints />} />
            <Route path="/network-map" element={<NetworkMap />} />
            <Route path="/backups" element={<Guard role="admin"><Backups /></Guard>} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/routing" element={<Routing />} />
            <Route path="/mikrotik-vpn" element={<MikrotikVpn />} />
            <Route path="/opnsense-vpn" element={<OpnsenseVpn />} />
            <Route path="/server" element={<Guard role="admin"><Server /></Guard>} />
            <Route path="/audit" element={<Guard role="admin"><Audit /></Guard>} />
            <Route path="/users" element={<Guard role="admin"><Users /></Guard>} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AnimatePresence>
      </Suspense>
    </AppLayout>
  );
}

/** Until the setup wizard is finished every page leads to it (config links of clients excepted). */
function SetupGate({ children }: { children: JSX.Element }) {
  const location = useLocation();
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => api.setup.state(), retry: 2, refetchOnWindowFocus: false });
  if (location.pathname === "/setup" || location.pathname.startsWith("/s/")) return children;
  if (setup.isLoading) return <PageLoader full />;
  if (setup.data && setup.data.stage !== "done") return <Navigate to="/setup" replace />;
  return children;
}

export default function App() {
  const bootstrap = useAuth((s) => s.bootstrap);
  useEffect(() => { void bootstrap(); }, [bootstrap]);
  return (
    <BrowserRouter>
      <SetupGate>
        <Routes>
          <Route path="/setup" element={<Suspense fallback={<PageLoader full />}><SetupPage /></Suspense>} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/s/:token" element={<Suspense fallback={<PageLoader full />}><SharePage /></Suspense>} />
          <Route path="/*" element={<Private />} />
        </Routes>
      </SetupGate>
    </BrowserRouter>
  );
}
