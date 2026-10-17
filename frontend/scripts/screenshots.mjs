// README screenshots of the main features in the light and the dark theme, taken from a running Harbor:
// the sign-in page, then a real sign-in through SPA and the overview, devices, network map, flow analysis and VPN
// clients. Start the demo first (scripts/screenshots.sh does all of it), then:
//
//   NC_URL=http://127.0.0.1:4173 node frontend/scripts/screenshots.mjs
//
// Uses Playwright with an installed Chromium (PLAYWRIGHT_BROWSERS_PATH); PNGs go to docs/screens/.
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
function playwright() {
  try {
    return require("playwright");
  } catch {
    return require(join(execSync("npm root -g").toString().trim(), "playwright"));
  }
}

const BASE = (process.env.NC_URL ?? "http://127.0.0.1:4173").replace(/\/$/, "");
const OUT = fileURLToPath(new URL("../../docs/screens/", import.meta.url));
const LANG = process.env.NC_LANG ?? "en";
const SIZE = { width: 1440, height: 900 };

const PAGES = [
  // The overview shows the "First steps" checklist above the dashboard: a taller window keeps both in the picture
  // (the page scrolls inside the layout, so a full-page screenshot would not).
  { name: "overview", path: "/", ready: "text=Throughput", height: 1560 },
  { name: "devices", path: "/devices", ready: "text=gw-core" },
  { name: "network-map", path: "/network-map", ready: ".react-flow, canvas, svg" },
  { name: "flows", path: "/flows", ready: "text=Flow analysis" },
  { name: "vpn-clients", path: "/clients", ready: "text=anna-iphone" },
];

// The app keeps an event stream open, so "network idle" may never come: wait for it briefly, then for the charts.
const settle = (page, ms = 2500) =>
  page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => {}).then(() => page.waitForTimeout(ms));

async function shoot(browser, theme) {
  const context = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1, colorScheme: theme, locale: LANG === "ru" ? "ru-RU" : "en-GB",
    reducedMotion: "reduce" });
  await context.addInitScript(([t, l]) => {
    try {
      localStorage.setItem("network-control.theme", t);
      localStorage.setItem("lang", l);
      // The sign-in page as it is after signing out: with its button (otherwise it goes on to SPA by itself).
      if (location.pathname === "/login") sessionStorage.setItem("nc_signed_out", "1");
    } catch {
      /* storage is off */
    }
  }, [theme, LANG]);
  const page = await context.newPage();
  // Toasts of alerts that happen to arrive while shooting are not part of the picture.
  page.on("load", () => page.addStyleTag({ content: "[data-sonner-toaster]{display:none!important}" }).catch(() => {}));
  const suffix = `${LANG === "en" ? "" : `-${LANG}`}-${theme}`;

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.locator("button.btn-primary").first().waitFor();
  await settle(page, 800);
  await page.screenshot({ path: join(OUT, `sign-in${suffix}.png`) });

  // The real sign-in: Harbor → SPA (authorization code with PKCE) → back with a session.
  await Promise.all([
    page.waitForURL((u) => new URL(u).pathname === "/", { timeout: 20_000, waitUntil: "domcontentloaded" }),
    page.locator("button.btn-primary").first().click({ noWaitAfter: true }),
  ]);

  for (const p of PAGES) {
    await page.setViewportSize({ ...SIZE, height: p.height ?? SIZE.height });
    await page.goto(`${BASE}${p.path}`, { waitUntil: "domcontentloaded" });
    await page.locator(p.ready).first().waitFor({ timeout: 20_000 }).catch(() => console.warn(`${p.name}: ${p.ready} not found`));
    await settle(page, p.name === "network-map" ? 4000 : 2500);
    await page.screenshot({ path: join(OUT, `${p.name}${suffix}.png`) });
    console.log(`${p.name}${suffix}.png`);
  }
  await context.close();
}

mkdirSync(OUT, { recursive: true });
const browser = await playwright().chromium.launch();
try {
  for (const theme of (process.env.NC_THEMES ?? "light,dark").split(",")) await shoot(browser, theme);
} finally {
  await browser.close();
}
