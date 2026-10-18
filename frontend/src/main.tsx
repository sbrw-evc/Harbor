import React from "react";
import ReactDOM from "react-dom/client";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import App from "./App";
import { ApiError } from "./lib/api";
import { notify } from "./lib/notify";
import { useTheme } from "./lib/theme";
import { t, useLang } from "./i18n";
import "./index.css";

function reportQueryError(error: Error) {
  if (error instanceof ApiError && error.status === 401) return;
  notify.error(t("Could not load the data"), error.message, 60_000);
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: reportQueryError }),
  // Any change made in the app can tick off a step of the checklist or of a page guide: read them again.
  mutationCache: new MutationCache({
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["onboarding"] });
      void queryClient.invalidateQueries({ queryKey: ["guide"] });
    },
  }),
  defaultOptions: { queries: { staleTime: 5_000, refetchInterval: 30_000, refetchOnWindowFocus: true, refetchOnReconnect: true, retry: 1 } },
});

/** The language and theme are app-wide: a language switch re-renders everything in place (the data stays cached). */
function Root() {
  const lang = useLang();
  const theme = useTheme();
  return (
    <>
      <App key={lang} />
      <Toaster theme={theme} position="bottom-right" visibleToasts={5} expand closeButton richColors gap={10} offset={20} />
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <Root />
    </QueryClientProvider>
  </React.StrictMode>,
);
