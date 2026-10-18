// What jsdom lacks and the components use: media queries (the theme, reduced motion) and the clipboard.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(() => Promise.resolve()) } });

// Downloads: the Blob handed to the browser is kept for the test to read; the click of the link does nothing in jsdom.
URL.createObjectURL = vi.fn(() => "blob:test");
URL.revokeObjectURL = vi.fn();
HTMLAnchorElement.prototype.click = vi.fn();

// The Web Animations API (auto-animate, the theme switch): an animation that is over at once.
Element.prototype.animate = vi.fn(() => ({ finished: Promise.resolve(), cancel() {}, addEventListener() {}, onfinish: null })) as never;

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

// Charts and maps draw on a canvas jsdom does not have: the pages still build their options.
vi.mock("echarts-for-react", async () => {
  const { createElement } = await import("react");
  return { default: () => createElement("div", { "data-testid": "chart" }) };
});
vi.mock("react-leaflet", async () => {
  const { createElement } = await import("react");
  const Pass = ({ children }: { children?: unknown }) => createElement("div", null, children as never);
  const LayersControl = Object.assign(Pass, { BaseLayer: Pass, Overlay: Pass });
  return { MapContainer: Pass, TileLayer: () => null, CircleMarker: Pass, Popup: Pass, Tooltip: Pass, LayersControl,
           useMap: () => ({ fitBounds: () => {}, setView: () => {} }) };
});
